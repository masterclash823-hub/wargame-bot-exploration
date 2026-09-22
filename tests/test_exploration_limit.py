import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from test_dynasty_labor import Fixture
from test_regressions import interaction
import calendar_service as calendar
import db
import exploration_service as exp
import i18n
import world_service as world
from economy_engine import run_month, run_tick


PREPARATIONS = 'We provision a small expedition to explore the northern pass.'
SCENE = {'text': 'A storm blocks the pass. What do you do?', 'finished': False, 'success': None}
FINAL = {'text': 'The expedition reached the pass.', 'finished': True, 'success': True}


class ExpeditionLimitTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def start(self, nid=1, uid=None, guild_id=100, channel_id=200):
        with patch.object(exp, '_ai_json', AsyncMock(return_value=SCENE)):
            return await exp.start(nid, uid or nid, guild_id, channel_id, 99, PREPARATIONS)

    async def finish(self, row, success=True, uid=None):
        with patch.object(exp, '_ai_json', AsyncMock(return_value=dict(FINAL, success=success))):
            return await exp.answer(row['id'], row['version'], uid or row['owner_id'], row['guild_id'], 'We proceed carefully.')

    async def assert_limit(self, nid=1, uid=None, guild_id=100, channel_id=200):
        with patch.object(exp, '_ai_json', AsyncMock()) as narrator:
            with self.assertRaisesRegex(ValueError, 'already started an expedition this tick'):
                await exp.start(nid, uid or nid, guild_id, channel_id, 99, PREPARATIONS)
            narrator.assert_not_awaited()

    async def test_success_and_failure_consume_separate_national_allowances(self):
        first = await self.start()
        second = await self.start(2)
        await self.finish(first)
        await self.finish(second, success=False)
        await self.assert_limit()
        await self.assert_limit(2)
        # Neither a different channel nor a different server grants a new allowance.
        await self.assert_limit(guild_id=101, channel_id=300)
        self.assertEqual(len(self.query('SELECT * FROM exploration_starts')), 2)

    async def test_restart_transfer_and_language_keep_the_limit(self):
        await self.finish(await self.start())
        db.init_db()
        world.transfer_nation(1, 11, '1', 999)
        await self.assert_limit(uid=11)
        with i18n.using_language('pl'), self.assertRaisesRegex(ValueError, 'w tym ticku'):
            exp.check_start(1, 11)
        with self.assertRaises(ValueError):
            exp.check_start(1, 1)

    async def test_next_tick_allows_start_but_active_expedition_can_still_be_continued(self):
        first = await self.start()
        run_tick()
        with self.assertRaisesRegex(ValueError, 'active expedition'):
            await self.start()
        await self.finish(exp.load(first['id'], 1, 100))
        second = await self.start()
        self.assertNotEqual(first['id'], second['id'])
        await self.finish(second)
        await self.assert_limit()
        run_tick(2)
        await self.finish(await self.start())
        # Skipping a month does not accumulate allowances.
        await self.assert_limit()

    async def test_failed_or_not_due_tick_does_not_renew_allowance(self):
        await self.finish(await self.start())
        now = datetime(2026, 9, 22, tzinfo=timezone.utc)
        calendar.configure(24, 200, now=now)
        calendar.start(now)
        self.assertIsNone(run_month(scheduled_at=now + timedelta(hours=1)))
        await self.assert_limit()
        with patch('cogs.colonialism.tick_colonies', side_effect=RuntimeError('tick failed')):
            with self.assertRaises(RuntimeError):
                run_month(scheduled_at=now + timedelta(days=1))
        await self.assert_limit()
        self.assertIsNotNone(run_month(scheduled_at=now + timedelta(days=1)))
        await self.start()

    async def test_narrator_and_database_failures_do_not_consume_allowance(self):
        with patch.object(exp, '_ai_json', AsyncMock(side_effect=RuntimeError('offline'))):
            with self.assertRaises(ValueError):
                await exp.start(1, 1, 100, 200, 99, PREPARATIONS)
        self.assertFalse(self.query('SELECT * FROM exploration_starts'))
        save = db.insert_returning_id

        def fail_after_insert(*args):
            save(*args)
            raise RuntimeError('save failed')

        with patch.object(db, 'insert_returning_id', side_effect=fail_after_insert):
            with self.assertRaises(RuntimeError):
                await self.start()
        self.assertFalse(self.query('SELECT * FROM exploration_starts'))
        self.assertFalse(self.query('SELECT * FROM explorations'))
        await self.start()

    async def test_concurrent_starts_commit_one_expedition(self):
        ready = asyncio.Event()
        calls = 0

        async def narrate(_prompt):
            nonlocal calls
            calls += 1
            if calls == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 5)
            return SCENE

        with patch.object(exp, '_ai_json', side_effect=narrate):
            results = await asyncio.gather(
                exp.start(1, 1, 100, 200, 99, PREPARATIONS),
                exp.start(1, 1, 100, 300, 99, PREPARATIONS), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual(len(self.query('SELECT * FROM explorations')), 1)
        self.assertEqual(len(self.query('SELECT * FROM exploration_starts')), 1)

    async def test_limit_is_rechecked_if_competing_expedition_already_finished(self):
        async def narrate(_prompt):
            await self.finish(await self.start())
            return SCENE

        with patch.object(exp, '_ai_json', side_effect=narrate):
            with self.assertRaisesRegex(ValueError, 'already started an expedition this tick'):
                await exp.start(1, 1, 100, 200, 99, PREPARATIONS)
        self.assertEqual(len(self.query('SELECT * FROM explorations')), 1)
        self.assertEqual(len(self.query('SELECT * FROM exploration_starts')), 1)

    async def test_tick_during_narration_counts_the_month_of_successful_save(self):
        with db.cursor() as c:
            initial = world.month_index(c)

        async def narrate(_prompt):
            run_tick()
            return SCENE

        with patch.object(exp, '_ai_json', side_effect=narrate):
            row = await exp.start(1, 1, 100, 200, 99, PREPARATIONS)
        self.assertEqual(self.query('SELECT month_index FROM exploration_starts')[0]['month_index'], initial + 1)
        await self.finish(row)
        await self.assert_limit()
        run_tick()
        await self.start()

    async def test_schema_upgrade_keeps_legacy_expeditions_resumable(self):
        state = dict(SCENE, nation='A', preparations=PREPARATIONS, history=[], lang='en')
        with db.cursor() as c:
            c.execute('DROP TABLE exploration_starts')
            c.execute('INSERT INTO explorations(nation_id,guild_id,channel_id,role_id,state_json) VALUES(?,?,?,?,?)',
                      (1, '100', '200', '99', json.dumps(state)))
        eid = self.query('SELECT id FROM explorations')[0]['id']
        db.init_db()
        self.assertFalse(self.query('SELECT * FROM exploration_starts'))
        with self.assertRaisesRegex(ValueError, 'active expedition'):
            exp.check_start(1, 1)
        await self.finish(exp.load(eid, 1, 100))
        await self.finish(await self.start())
        await self.assert_limit()

    async def test_command_panel_and_stale_form_all_enforce_the_limit(self):
        from cogs.exploration import ExplorationCog, PreparationModal
        from cogs.panel import PlayerPanel

        # The user opened this form before another interaction started the expedition.
        form = PreparationModal()
        form.text._value = PREPARATIONS
        await self.finish(await self.start())
        bot = NS(get_cog=lambda name: ExplorationCog)
        panel = PlayerPanel(bot, 1, 'en', section='events')

        def request():
            i = interaction(1)
            i.guild_id, i.channel_id, i.channel, i.client = 100, 200, NS(), bot
            i.response.send_modal = AsyncMock()
            return i

        with patch('cogs.exploration.check_channel', return_value=NS(id=99)), \
                patch.object(exp, '_ai_json', AsyncMock()) as narrator:
            for entry in ('command', 'panel', 'form', 'preparations'):
                with self.subTest(entry=entry):
                    i = request()
                    if entry == 'command':
                        await ExplorationCog.exploration.callback(None, i)
                    elif entry == 'panel':
                        await panel.dispatch(i, 'exploration')
                    elif entry == 'form':
                        await form.on_submit(i)
                    else:
                        await ExplorationCog.exploration.callback(None, i, PREPARATIONS)
                    sender = i.followup.send if entry in ('form', 'preparations') else i.response.send_message
                    self.assertIn('already started an expedition this tick', sender.call_args.args[0])
                    self.assertTrue(sender.call_args.kwargs['ephemeral'])
                    i.response.send_modal.assert_not_awaited()
            narrator.assert_not_awaited()

    async def test_command_resumes_active_expedition_even_when_allowance_is_used(self):
        from cogs.exploration import ExplorationCog

        row = await self.start()
        i = interaction(1)
        i.guild_id, i.client = 100, NS()
        with patch('cogs.exploration.check_channel') as channel, patch.object(exp, '_ai_json', AsyncMock()) as narrator:
            await ExplorationCog.exploration.callback(None, i)
            channel.assert_not_called()
            narrator.assert_not_awaited()
        self.assertIn(f"#{row['id']}", i.followup.send.call_args.kwargs['embed'].title)
        self.assertEqual(len(i.followup.send.call_args.kwargs['view'].children), 1)


if __name__ == '__main__':
    unittest.main()
