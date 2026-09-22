import json
import unittest
from collections import Counter
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from test_regressions import DatabaseFixture, interaction
import config
import db
import event_adventure as adventure
import event_drafts
import event_variety as variety
from cogs import events
from world_service import world_lock


class VarietyTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def save(self, brief, nid=1, text='An event.'):
        with db.atomic() as c:
            world_lock(c)
            eid = db.insert_returning_id('INSERT INTO events(nation_id,gm_final_text) VALUES(?,?)', (nid, text))
            variety.record(c, eid, nid, brief)
            return eid

    def rows(self):
        with db.cursor() as c:
            c.execute('SELECT g.*,e.nation_id FROM event_generation g JOIN events e ON e.id=g.event_id ORDER BY event_id')
            return c.fetchall()

    async def test_balanced_blocks_and_topic_rotation_survive_restart(self):
        topics, moods = [], []
        for index in range(20):
            brief = variety.plan(1)
            self.assertNotIn(brief['topic'], topics[-3:])
            self.save(brief, text=f'Event number {index}')
            topics.append(brief['topic']); moods.append(brief['mood'])
            if index == 6:
                db.init_db()
        for start in range(0, 20, 5):
            self.assertEqual(Counter(moods[start:start+5]), Counter(positive=2, negative=2, mixed=1))
        self.assertEqual(len(set(topics[:10])), 10)
        self.assertEqual(len(self.rows()), 20)
        self.assertEqual(variety.plan(2)['previous_event_id'], 0)

    async def test_stale_generation_is_rolled_back_and_batch_reuses_existing_draft(self):
        n, existing = event_drafts.candidate(1)
        self.assertIsNone(existing)
        brief = n['event_brief']
        eid = self.save(brief)
        with self.assertRaisesRegex(ValueError, 'Another event'):
            self.save(brief, text='A competing draft')
        self.assertEqual(event_drafts.save_missing(n, 'Batch competing draft', '{}'), (eid, 'existing'))
        self.assertEqual(len(self.rows()), 1)
        with db.cursor() as c:
            c.execute('SELECT COUNT(*) AS n FROM events')
            self.assertEqual(c.fetchone()['n'], 1)

    async def test_legacy_events_are_avoided_without_fabricated_metadata(self):
        old = 'The old merchants return to collect the unpaid customs duties.'
        db.insert_returning_id('INSERT INTO events(nation_id,gm_final_text) VALUES(?,?)', (1, old))
        brief = variety.plan(1)
        self.assertIn(old, brief['recent'])
        self.assertFalse(self.rows())
        brief['mood'] = 'positive'
        with self.assertRaisesRegex(ValueError, 'repeats'):
            variety.parse_draft(old.upper() + '\nEFFECTS: {"treasury":20}', brief)

    async def test_effect_direction_is_validated_without_changing_model_values(self):
        fixtures = [('positive', {'stability': 2, 'treasury': 15}),
                    ('negative', {'resources': {'food': -30}}),
                    ('mixed', {'stability': 2, 'treasury': -15})]
        for mood, effects in fixtures:
            brief = dict(topic='culture', mood=mood, recent=[])
            raw = 'A new situation in the capital.\nEFFECTS: ' + json.dumps(effects)
            self.assertEqual(json.loads(variety.parse_draft(raw, brief)[1]), effects)
            for other in variety.MOODS:
                if other != mood:
                    with self.assertRaises(ValueError):
                        variety.parse_draft(raw, dict(brief, mood=other))
        for effects in ('{}', '{"stability":50}', '{"resources":{"gold":1}}', '{"treasury":NaN}'):
            with self.assertRaises(ValueError):
                variety.parse_draft('Story\nEFFECTS: ' + effects, dict(mood='positive', recent=[]))

    async def test_prompt_uses_selected_topic_and_theme_without_negative_example(self):
        n = self.balances()[0]
        n['event_brief'] = dict(topic='craft', mood='positive', recent=['A recent flood.'], previous_event_id=0)
        ai = AsyncMock(return_value='New workshop tools improve production.\nEFFECTS: {"treasury":20}')
        with patch('event_ai.generate_text', ai):
            await events._generate_event(n, theme='A harsh winter', strict=True)
        prompt = ai.call_args.args[0]
        self.assertIn('craftsmanship', prompt)
        self.assertIn('A harsh winter', prompt)
        self.assertIn('Assigned opening mood: positive', prompt)
        self.assertIn('A recent flood.', prompt)
        self.assertNotIn('A drought has struck', prompt)
        self.assertNotIn('Labor policy:', prompt)
        self.assertNotIn('Recent expedition outcomes:', prompt)
        self.assertNotIn('Active dynastic bonds', prompt)
        with self.assertRaises(ValueError):
            ai.call_args.kwargs['validate']('A benefit.\nEFFECTS: {"treasury":-5}')

    async def test_ruins_are_an_explicit_theme_and_context_keeps_player_memory(self):
        brief = variety.plan(1, ruins=True)
        self.assertEqual(brief['topic'], 'ruins')
        self.assertIn('explicitly selected neighboring ruins', variety.instructions(brief))
        n = self.balances()[0]
        with db.cursor() as c:
            for _ in range(20):
                c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)', (1, 'gm', 'x' * 4000))
        context = events._build_nation_context(n, 'craft')
        self.assertLess(len(context), 5000)
        self.assertIn('Private remembered decisions', context)

    async def test_real_batch_saves_each_brief_and_preserves_balances(self):
        before = self.balances()
        async def generated(prompt, **kwargs):
            mood = next(m for m in variety.MOODS if f'Assigned opening mood: {m}.' in prompt)
            effects = {'positive': {'stability': 1}, 'negative': {'treasury': -5},
                       'mixed': {'stability': 1, 'treasury': -5}}[mood]
            return 'A fresh local development asks for a decision.\nEFFECTS: ' + json.dumps(effects)
        with patch('event_ai.generate_text', side_effect=generated):
            results = await event_drafts.generate_all(events._generate_event)
        self.assertEqual([r['status'] for r in results], ['created', 'created'])
        self.assertEqual({r['nation_id'] for r in self.rows()}, {1, 2})
        self.assertEqual(before, self.balances())

    async def test_failed_generation_does_not_save_placeholder_or_spend_rotation(self):
        gm = interaction(999, [NS(id=10, name=config.GM_ROLE_NAME)])
        cog = events.EventsCog(None)
        with patch('event_ai.generate_text', AsyncMock(side_effect=RuntimeError('offline'))):
            await cog.event_generate.callback(cog, gm, 'A')
        self.assertIn('Could not prepare', gm.followup.send.call_args.args[0])
        self.assertFalse(self.rows())
        with db.cursor() as c:
            c.execute('SELECT COUNT(*) AS n FROM events')
            self.assertEqual(c.fetchone()['n'], 0)

    async def test_saved_opening_brief_reaches_scenes_without_forcing_final_effects(self):
        brief = variety.plan(1)
        brief['mood'] = 'positive'
        eid = self.save(brief, text='The town offers a new cultural initiative.')
        with db.cursor() as c:
            c.execute('UPDATE events SET effects_json=? WHERE id=?', ('{"treasury":10}', eid))
            c.execute('SELECT * FROM events WHERE id=?', (eid,))
            event = c.fetchone()
        mock = AsyncMock(return_value={'text': 'The town awaits your response.', 'choices': ['A', 'B', 'C']})
        with patch.object(adventure, '_ai_json', mock):
            state = await adventure.prepare_run(event, self.balances()[0])
        self.assertEqual(state['opening_brief']['mood'], 'positive')
        self.assertIn('do not invent a hidden crisis', mock.call_args.args[0])
        state['text'] = 'The ruler rejects the town and confiscates its funds.'
        with patch.object(adventure, '_ai_json', AsyncMock(return_value={
                'stability': 0, 'treasury': -1, 'resources': {}, 'reason': 'The policy damages the project.'})):
            impact, _, fallback = await adventure.assess_consequence(state, 'Confiscate everything', 2)
        self.assertFalse(fallback)
        self.assertEqual(impact['treasury'], -1)
