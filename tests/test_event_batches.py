import asyncio
import json
import re
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import discord
from test_regressions import DatabaseFixture, interaction
from cogs import events
import config
import db
import event_drafts
import event_listing
import i18n


class EventFixture(DatabaseFixture):
    def event(self, nid=2, status='draft', text='A new event.', visibility=None):
        eid = db.insert_returning_id(
            'INSERT INTO events(nation_id,status,gm_final_text) VALUES(?,?,?)',
            (nid, status, text))
        if visibility:
            with db.cursor() as c:
                c.execute('INSERT INTO event_publications(event_id,visibility) VALUES(?,?)', (eid, visibility))
        return eid

    def saved(self):
        with db.cursor() as c:
            c.execute('SELECT * FROM events ORDER BY id')
            return c.fetchall()

    def gm(self):
        gm = interaction(999, [NS(id=10, name=config.GM_ROLE_NAME)])
        gm.edit_original_response = AsyncMock()
        return gm

    def decay(self, nid, status):
        with db.cursor() as c:
            c.execute('INSERT INTO nation_decay(nation_id,status,started_month,due_month,gm_id,former_owner) '
                      'VALUES(?,?,0,3,?,?)', (nid, status, '999', str(nid)))


class EventListTests(EventFixture, unittest.IsolatedAsyncioTestCase):
    async def test_compact_pages_have_all_events_in_order_and_no_flags(self):
        with db.cursor() as c:
            c.execute('UPDATE nations SET flag=? WHERE id=1', ('🇵🇱',))
            c.execute('UPDATE nations SET flag=? WHERE id=2', ('https://example.com/flag.png',))
        ids = [self.event(nid=1 + i % 2, text=f'Event number {i}') for i in range(35)]
        gm = self.gm()
        await events.EventsCog.event_list.callback(None, gm)
        sent = gm.response.send_message.call_args.kwargs
        view = sent['view']
        self.assertEqual([len(p.fields) for p in view.pages], [15, 15, 5])
        listed = [int(re.search(r'#(\d+)', f.name)[1]) for p in view.pages for f in p.fields]
        self.assertEqual(listed, ids[::-1])
        for page in view.pages:
            data = page.to_dict()
            self.assertNotIn('thumbnail', data)
            self.assertNotIn('image', data)
            self.assertNotIn('author', data)
            self.assertNotIn('🇵🇱', str(data))
            self.assertNotIn('flag.png', str(data))
        self.assertTrue(sent['ephemeral'])
        await view.next_page.callback(gm)
        self.assertEqual(view.index, 1)
        self.assertFalse(await view.interaction_check(interaction(2)))

    async def test_long_content_respects_discord_limits_without_losing_events(self):
        with db.cursor() as c:
            c.execute('UPDATE nations SET name=? WHERE id=2', ('Long **name** ' * 30,))
        for _ in range(35):
            self.event(text='*' * 4000)
        rows = event_listing.rows(2, True, None)
        pages = event_listing.pages(rows, 'pl', True)
        self.assertEqual(sum(len(p.fields) for p in pages), 35)
        for page in pages:
            self.assertLessEqual(len(page), 5500)
            self.assertLessEqual(len(page.fields), 15)
            for field in page.fields:
                self.assertLessEqual(len(field.name), 256)
                self.assertLessEqual(len(field.value), 1024)

    async def test_privacy_is_filtered_before_paging_and_player_defaults_to_own(self):
        public = self.event(status='resolved', visibility='public')
        legacy = self.event(status='posted')
        private = [self.event(status='active', visibility='private') for _ in range(30)]
        draft = self.event()
        outsider = interaction(1)
        await events.EventsCog.event_list.callback(None, outsider, 'B')
        fields = outsider.response.send_message.call_args.kwargs['embed'].fields
        self.assertEqual([int(re.search(r'#(\d+)', f.name)[1]) for f in fields], [legacy, public])
        owner = interaction(2)
        await events.EventsCog.event_list.callback(None, owner)
        pages = owner.response.send_message.call_args.kwargs['view'].pages
        ids = [int(re.search(r'#(\d+)', f.name)[1]) for p in pages for f in p.fields]
        self.assertEqual(ids, private[::-1] + [legacy, public])
        self.assertNotIn(draft, ids)
        self.assertTrue(all(r['nation_id'] == 2 for r in event_listing.rows(2, True, None)))

    async def test_empty_unknown_and_unassigned_player(self):
        for who, nation in ((self.gm(), ''), (interaction(2), 'missing'), (interaction(55), '')):
            await events.EventsCog.event_list.callback(None, who, nation)
            self.assertNotIn('embed', who.response.send_message.call_args.kwargs)
        self.event(status='posted', visibility='public')
        observer = interaction(55)
        await events.EventsCog.event_list.callback(None, observer, 'B')
        self.assertIn('embed', observer.response.send_message.call_args.kwargs)


class EventBatchTests(EventFixture, unittest.IsolatedAsyncioTestCase):
    async def test_each_owner_language_theme_and_drafts_without_effects_or_publication(self):
        i18n.set_user_language(1, 'pl')
        i18n.set_user_language(2, 'en')
        before = self.balances()
        seen = {}
        async def generate(n, *, theme, strict):
            await asyncio.sleep(0)
            seen[n['id']] = (i18n.current_language(), theme, strict)
            return f"Story for {n['name']}", '{"treasury":-10}'
        results = await event_drafts.generate_all(generate, 'A harsh winter')
        self.assertEqual(seen, {1: ('pl', 'A harsh winter', True), 2: ('en', 'A harsh winter', True)})
        self.assertEqual([r['status'] for r in results], ['created', 'created'])
        self.assertEqual([e['status'] for e in self.saved()], ['draft', 'draft'])
        self.assertEqual(before, self.balances())
        with db.cursor() as c:
            for table in ('event_runs', 'event_publications', 'nation_history'):
                c.execute(f'SELECT COUNT(*) AS n FROM {table}')
                self.assertEqual(c.fetchone()['n'], 0)

    async def test_existing_drafts_preserved_ruins_skipped_decaying_included(self):
        old = self.event(nid=1, text='GM edited draft')
        self.decay(2, 'decaying')
        nid = db.insert_returning_id('INSERT INTO nations(owner_id,name) VALUES(?,?)', ('ruins:3', 'Ruins'))
        self.decay(nid, 'ruins')
        generate = AsyncMock(return_value=('Fresh story', '{}'))
        results = await event_drafts.generate_all(generate)
        self.assertEqual([(r['id'], r['status']) for r in results], [(1, 'existing'), (2, 'created')])
        generate.assert_awaited_once()
        self.assertEqual(generate.call_args.args[0]['id'], 2)
        self.assertEqual(self.saved()[0]['id'], old)
        self.assertEqual(self.saved()[0]['gm_final_text'], 'GM edited draft')

    async def test_one_ai_failure_does_not_stop_others_and_retry_fills_only_missing(self):
        async def generate(n, **kwargs):
            if n['id'] == 1:
                raise RuntimeError('offline')
            return 'Story for B', '{}'
        with self.assertLogs(level='ERROR'):
            results = await event_drafts.generate_all(generate)
        self.assertEqual([r['status'] for r in results], ['failed', 'created'])
        kept = self.saved()[0]
        retry = AsyncMock(return_value=('Story for A', '{}'))
        results = await event_drafts.generate_all(retry)
        self.assertEqual([r['status'] for r in results], ['created', 'existing'])
        retry.assert_awaited_once()
        self.assertEqual(retry.call_args.args[0]['id'], 1)
        self.assertEqual(self.saved()[0], kept)
        self.assertEqual(len(self.saved()), 2)

    async def test_invalid_ai_payload_is_not_saved_as_success(self):
        generate = AsyncMock(return_value=('Story', '{"stability":1000}'))
        with self.assertLogs(level='ERROR'):
            results = await event_drafts.generate_all(generate)
        self.assertTrue(all(r['status'] == 'failed' for r in results))
        self.assertEqual(self.saved(), [])
        n, _ = event_drafts.candidate(1)
        for text in ('', ' ' * 10, 'x' * 4001, None):
            with self.subTest(text_length=len(text) if text else 0), self.assertRaises(ValueError):
                event_drafts.save_missing(n, text, '{}')
        self.assertEqual(self.saved(), [])

    async def test_changes_during_generation_reject_stale_draft(self):
        for change in ('owner', 'language', 'ruins', 'deleted'):
            with self.subTest(change=change):
                uid = 'player-' + change
                nid = db.insert_returning_id('INSERT INTO nations(owner_id,name) VALUES(?,?)', (uid, change))
                n, _ = event_drafts.candidate(nid)
                if change == 'ruins':
                    self.decay(nid, 'ruins')
                elif change == 'language':
                    i18n.set_user_language(uid, 'pl')
                else:
                    with db.cursor() as c:
                        if change == 'deleted':
                            c.execute('DELETE FROM nations WHERE id=?', (nid,))
                        else:
                            c.execute('UPDATE nations SET owner_id=? WHERE id=?', ('new-owner', nid))
                with self.assertRaises(ValueError):
                    event_drafts.save_missing(n, 'Stale story', '{}')
        self.assertEqual(self.saved(), [])

    async def test_concurrent_batches_save_only_one_draft(self):
        n, _ = event_drafts.candidate(1)
        results = await asyncio.gather(*(asyncio.to_thread(event_drafts.save_missing, n, 'Story', '{}') for _ in range(2)))
        self.assertEqual(sorted(status for _, status in results), ['created', 'existing'])
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(len(self.saved()), 1)

    async def test_command_gm_gate_and_private_polish_summary(self):
        cog = events.EventsCog(NS())
        with patch.object(events, '_generate_event', AsyncMock(return_value=('Story', '{}'))) as generate:
            player = interaction(2)
            await cog.event_all.callback(cog, player)
            generate.assert_not_awaited()
            self.assertEqual(self.saved(), [])
            gm = self.gm()
            i18n.set_user_language(999, 'pl')
            await cog.event_all.callback(cog, gm, 'Zima')
        sent = gm.followup.send.call_args.kwargs
        self.assertTrue(sent['ephemeral'])
        self.assertIn('Nowe szkice: 2', sent['embed'].description)
        self.assertEqual(len(sent['embed'].fields), 2)
        self.assertTrue(all('Nowy szkic #' in f.value for f in sent['embed'].fields))
        self.assertFalse(cog._all_running)

    async def test_overlapping_command_is_rejected_and_guard_resets_on_failure(self):
        cog = events.EventsCog(NS())
        started, release = asyncio.Event(), asyncio.Event()
        async def hold(*args):
            started.set()
            await release.wait()
            return []
        with patch('event_drafts.generate_all', side_effect=hold) as generate:
            first = asyncio.create_task(cog.event_all.callback(cog, self.gm()))
            try:
                await asyncio.wait_for(started.wait(), 1)
                second = self.gm()
                await cog.event_all.callback(cog, second)
                self.assertIn('already running', second.response.send_message.call_args.args[0])
                generate.assert_awaited_once()
            finally:
                release.set()
                await first
        self.assertFalse(cog._all_running)
        gm = self.gm()
        gm.response.defer.side_effect = RuntimeError('disconnected')
        with self.assertRaises(RuntimeError):
            await cog.event_all.callback(cog, gm)
        self.assertFalse(cog._all_running)

    async def test_expired_interaction_does_not_discard_saved_drafts(self):
        cog, gm = events.EventsCog(NS()), self.gm()
        expired = discord.NotFound(NS(status=404, reason='Not Found'), {'message': 'Unknown webhook', 'code': 10015})
        gm.edit_original_response.side_effect = expired
        gm.followup.send.side_effect = [None, expired]
        with patch.object(events, '_generate_event', AsyncMock(return_value=('Story', '{}'))):
            await cog.event_all.callback(cog, gm)
        self.assertEqual(len(self.saved()), 2)
        self.assertFalse(cog._all_running)

    async def test_strict_generation_uses_theme_and_rejects_fallback(self):
        n, _ = event_drafts.candidate(1)
        generate = Mock(return_value=NS(text='Narrative\nEFFECTS: {"stability":1}'))
        google = NS(genai=NS(Client=Mock(return_value=NS(models=NS(generate_content=generate)))))
        with patch.dict(sys.modules, {'google': google}):
            text, effects = await events._generate_event(n, theme='A harsh winter', strict=True)
        self.assertEqual(text, 'Narrative')
        self.assertEqual(json.loads(effects), {'stability': 1})
        self.assertIn('A harsh winter', generate.call_args.kwargs['contents'])
        google.genai.Client.side_effect = RuntimeError('offline')
        with patch.dict(sys.modules, {'google': google}), self.assertRaises(RuntimeError):
            await events._generate_event(n, strict=True)


if __name__ == '__main__':
    unittest.main()
