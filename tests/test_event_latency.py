import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import db
import event_adventure as flow
import event_ai as ai
from cogs import events
from event_ui import EventAction, EventView
from nation_access import set_coop
from test_regressions import DatabaseFixture, interaction, NS


def result():
    return dict(stability=.5, treasury=-1, resources={'food':1}, reason='Repairs cost gold but protect supplies.',
                next_scene=dict(text='The bridge repairs can begin. Choose how to organize the work.',
                                choices=['Inspect the damaged supports', 'Hire the local bridge builders',
                                         'Send the engineers to rebuild the span']))


class ClickTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def start(self):
        eid = db.insert_returning_id('INSERT INTO events(nation_id,gm_final_text,effects_json) VALUES(?,?,?)',
                                    (2, 'A bridge needs repair.', '{"stability":6,"treasury":30,"resources":{"food":15}}'))
        with db.cursor() as c:
            c.execute('SELECT * FROM events WHERE id=?', (eid,))
            event = c.fetchone()
        with patch.object(flow, 'scene', AsyncMock(return_value=('The council requests a plan.', ['A', 'B', 'C']))):
            return flow.start_run(await flow.prepare_run(event, self.balances()[1]))

    async def test_choice_uses_one_ai_call_and_keeps_fresh_food_context(self):
        state = await self.start()
        before = self.balances()
        with patch.object(flow, '_ai_json', AsyncMock(return_value=result())) as request:
            updated = await flow.decide(state['event_id'], 0, 2, choice=1)
        self.assertEqual(request.await_count, 1)
        self.assertIn('Food economy for the next monthly tick', request.call_args.args[0])
        self.assertIn('next_scene', request.call_args.args[0])
        self.assertEqual(updated['choices'], result()['next_scene']['choices'])
        self.assertEqual(updated['history'][0]['impact']['treasury'], -1)
        self.assertNotIn('_next_scene', updated)
        self.assertNotIn('_next_scene', flow.load_run(state['event_id']))
        self.assertEqual(before, self.balances())  # Numeric effects still wait for decision 3.

    async def test_invalid_next_scene_repaired_without_changing_valid_impact(self):
        state = await self.start()
        bad = result()
        bad['next_scene']['choices'] = ['Cautious action', 'Balanced action', 'Decisive action']
        with patch.object(flow, '_ai_json', AsyncMock(side_effect=[bad, result()['next_scene']])) as request:
            updated = await flow.decide(state['event_id'], 0, 2, choice=0)
        self.assertEqual(request.await_count, 2)
        self.assertEqual(updated['history'][0]['impact']['treasury'], -1)
        self.assertFalse(updated['scene_fallback'])
        self.assertEqual(updated['choices'], result()['next_scene']['choices'])

    async def test_combined_scene_rejects_repeated_options(self):
        state = await self.start()
        bad = result()
        bad['next_scene']['choices'] = ['A', 'New bridge contract', 'Change the bridge toll']
        with patch.object(flow, '_ai_json', AsyncMock(side_effect=[bad, result()['next_scene']])) as request:
            updated = await flow.decide(state['event_id'], 0, 2, choice=0)
        self.assertEqual(request.await_count, 2)
        self.assertNotIn('A', updated['choices'])

    async def test_reconstructed_button_acknowledges_before_database_and_works_once(self):
        state = await self.start()
        view = EventView(state)
        self.assertTrue(view.is_persistent())
        old = view.children[0]
        player = interaction(2)
        player.message = NS(edit=AsyncMock(), id=9)
        restored = await EventAction.from_custom_id(player, old.item, old.template.fullmatch(old.custom_id))
        original_load = flow.load_run
        def checked_load(eid):
            player.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            return original_load(eid)
        with patch.object(flow, 'load_run', side_effect=checked_load), \
                patch.object(flow, '_ai_json', AsyncMock(return_value=result())) as request:
            await restored.callback(player)
        self.assertEqual(request.await_count, 1)
        self.assertEqual(original_load(state['event_id'])['version'], 1)
        self.assertEqual(player.followup.send.call_args.kwargs['view'].state['version'], 1)
        player.message.edit.assert_awaited_once_with(view=None)
        # A still-visible old message restores the current turn, without interpreting
        # its choice number as a choice for a different scene.
        with patch.object(flow, '_ai_json', AsyncMock()) as request:
            await restored.callback(player)
        request.assert_not_awaited()
        self.assertIn('no extra decision', player.followup.send.call_args.kwargs['content'])
        self.assertEqual(original_load(state['event_id'])['version'], 1)

    async def test_parallel_click_and_reload_do_not_duplicate_ai_or_decisions(self):
        state = await self.start()
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args, **kwargs):
            entered.set()
            await release.wait()
            return result()
        with patch.object(flow, '_ai_json', side_effect=slow) as request:
            first = asyncio.create_task(flow.decide(state['event_id'], 0, 2, choice=1))
            await entered.wait()
            try:
                with self.assertRaises(ValueError):
                    await flow.decide(state['event_id'], 0, 2, choice=2)
                with self.assertRaises(ValueError):
                    await flow.retry_scene(state['event_id'], 0, 2)
            finally:
                release.set()
            await first
        self.assertEqual(request.await_count, 1)
        self.assertEqual(len(flow.load_run(state['event_id'])['history']), 1)
        self.assertNotIn(state['event_id'], flow._retrying)

    async def test_cancelled_processing_releases_guard_without_saving(self):
        state = await self.start()
        with patch.object(flow, '_ai_json', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await flow.decide(state['event_id'], 0, 2, choice=1)
        self.assertNotIn(state['event_id'], flow._retrying)
        self.assertEqual(flow.load_run(state['event_id'])['version'], 0)
        with patch.object(flow, '_ai_json', AsyncMock(return_value=result())):
            self.assertEqual((await flow.decide(state['event_id'], 0, 2, choice=1))['version'], 1)

    async def test_live_permissions_checked_for_persistent_button(self):
        state = await self.start()
        button = EventView(state).children[0]
        set_coop(2, 2, 3)
        set_coop(2, 2, 3, remove=True)
        for uid in (1, 3):
            player = interaction(uid)
            with patch.object(flow, '_ai_json', AsyncMock()) as request:
                await button.callback(player)
            request.assert_not_awaited()
            self.assertNotIn('embed', player.followup.send.call_args.kwargs)
        self.assertEqual(flow.load_run(state['event_id'])['version'], 0)

    async def test_custom_modal_opens_without_database_and_submission_checks_access(self):
        state = await self.start()
        button = EventView(state).children[-1]
        player = interaction(1)
        player.response.send_modal = AsyncMock()
        with patch.object(flow, 'load_run', side_effect=AssertionError('DB before acknowledgement')):
            await button.callback(player)
        modal = player.response.send_modal.call_args.args[0]
        modal.answer._value = 'Hire the bridge builders.'
        with patch.object(flow, '_ai_json', AsyncMock()) as request:
            await modal.on_submit(player)
        request.assert_not_awaited()
        self.assertNotIn('embed', player.followup.send.call_args.kwargs)

    async def test_cog_registers_restart_routing_without_loading_each_event(self):
        bot = NS(add_dynamic_items=Mock(), add_cog=AsyncMock())
        await events.setup(bot)
        bot.add_dynamic_items.assert_called_once_with(EventAction)

    async def test_final_click_applies_once_without_generating_another_scene(self):
        state = await self.start()
        state['version'] = 2
        state['history'] = [dict(action='Inspect bridge', choice=1, impact=dict(stability=0, treasury=0, resources={'food':0}))] * 2
        with db.cursor() as c:
            c.execute('UPDATE event_runs SET version=2,state_json=? WHERE event_id=?',
                      (json.dumps(state), state['event_id']))
        player = interaction(2)
        button = EventView(state).children[0]
        with patch.object(flow, '_ai_json', AsyncMock(return_value=result())) as request:
            await button.callback(player)
            await button.callback(player)
        self.assertEqual(request.await_count, 1)
        self.assertEqual(request.call_args.kwargs['max_output_tokens'], 600)
        self.assertNotIn('next_scene', request.call_args.args[0])
        self.assertEqual(self.balances()[1]['treasury'], 90)
        self.assertTrue(flow.load_run(state['event_id'])['resolved'])


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_is_shared_across_requests_and_reset_afterwards(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(.04)
            return 'valid'
        ai._unavailable_until.clear()
        target = ai.Target('gemini', 'test', 'test')
        try:
            with patch.object(ai, 'targets', return_value=[target]), patch.object(ai, '_request', side_effect=slow) as request:
                start = time.monotonic()
                with ai.interactive_budget(.06):
                    self.assertEqual(await ai.generate_text('first'), 'valid')
                    with self.assertRaises(ai.EventAIError):
                        await ai.generate_text('second')
                self.assertLess(time.monotonic() - start, .3)
                self.assertLess(request.call_args_list[1].args[2], .035)
                self.assertIsNone(ai._interactive_deadline.get())
                ai._unavailable_until.clear()
                self.assertEqual(await ai.generate_text('outside click'), 'valid')
        finally:
            ai._unavailable_until.clear()

    async def test_expired_budget_does_not_start_another_provider_request(self):
        with ai.interactive_budget(0), patch.object(ai, '_request', AsyncMock()) as request:
            with self.assertRaises(ai.EventAIError):
                await ai.generate_text('too late')
        request.assert_not_awaited()
