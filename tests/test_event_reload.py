import asyncio
import json
import unittest
from unittest.mock import AsyncMock,patch

import db
import i18n
import event_adventure as flow
from event_ui import EventView,render_event
from event_text import validate_language
from cogs import events
from cogs.economy import _seed_buildings
from test_regressions import DatabaseFixture


class ReloadTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    async def start(self):
        i18n.set_user_language(1,'pl')
        eid=db.insert_returning_id("INSERT INTO events(nation_id,gm_final_text,effects_json) VALUES(1,'Spór kupców',?)",('{"treasury":30}',))
        with db.cursor() as c:
            c.execute('SELECT * FROM events WHERE id=?',(eid,));event=c.fetchone()
        with patch.object(flow,'scene',AsyncMock(return_value=('The merchants wait for the council.',
                         ['Balanced action.','Decisive action','Cautious action']))):
            return flow.start_run(await flow.prepare_run(event,self.balances()[0]))

    async def test_legacy_generic_choices_are_hidden_blocked_and_reload_is_free(self):
        state=await self.start();before=self.balances()
        self.assertTrue(flow.needs_scene_retry(state))
        view=EventView(state)
        self.assertEqual({b.label for b in view.children},{'Własna odpowiedź','Załaduj odpowiedzi ponownie'})
        embed=render_event(state)
        self.assertNotIn('merchants',embed.description)
        self.assertFalse(any('Balanced action' in f.name for f in embed.fields))
        with self.assertRaises(ValueError):await flow.decide(state['event_id'],0,1,choice=0)
        with patch.object(flow,'scene',AsyncMock(return_value=('Kupcy czekają na radę.',
                      ['Zwołaj spotkanie kupców','Ustal zasady handlu','Wyznacz arbitra']))):
            updated=await flow.retry_scene(state['event_id'],0,1)
        self.assertEqual((updated['version'],len(updated['history'])),(1,0))
        self.assertEqual(before,self.balances())
        self.assertFalse(flow.needs_scene_retry(updated))

    async def test_parallel_reload_only_calls_ai_once_and_unlocks_after_failure(self):
        state=await self.start();entered=asyncio.Event();release=asyncio.Event()
        async def delayed(current):
            entered.set();await release.wait()
            raise ValueError('offline')
        with patch.object(flow,'scene',side_effect=delayed) as request:
            first=asyncio.create_task(flow.retry_scene(state['event_id'],0,1))
            await entered.wait()
            try:
                with self.assertRaises(ValueError):await flow.retry_scene(state['event_id'],0,1)
                self.assertEqual(request.await_count,1)
            finally:release.set()
            with self.assertRaises(ValueError):await first
        self.assertEqual(flow.load_run(state['event_id'])['version'],0)
        self.assertNotIn(state['event_id'],flow._retrying)

    async def test_changed_language_reopens_choices_without_spending_turn(self):
        state=await self.start()
        state.update(lang='en',text='Story',choices=['Inspect the road','Send an escort','Negotiate passage'])
        with db.cursor() as c:c.execute('UPDATE event_runs SET state_json=? WHERE event_id=?',(json.dumps(state),state['event_id']))
        self.assertEqual(flow.load_run(state['event_id'])['lang'],'pl')
        async def scene(current):
            current['scene_fallback']=False
            return 'Kupcy czekają.',['Zbadaj drogę','Wyślij eskortę','Uzgodnij warunki przejazdu']
        with patch.object(flow,'scene',side_effect=scene):updated=await flow.retry_scene(state['event_id'],0,1)
        self.assertFalse(flow.needs_scene_retry(updated))
        self.assertEqual(updated['history'],[])

    def test_compact_context_preserves_food_forecast_and_reduces_history(self):
        n=self.balances()[0]
        with db.cursor() as c:
            for index in range(20):
                c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(1,'gm',?)",(str(index)+' opis '*700,))
        full=events._build_nation_context(n,'trade')
        compact=events._build_nation_context(n,'trade',compact=True)
        self.assertLess(len(compact),len(full)*.5)
        food=lambda context:next(line for line in context.splitlines() if line.startswith('Food economy'))
        self.assertEqual(food(full),food(compact))
        self.assertIn('Private remembered decisions',compact)
        self.assertIn('19 opis',compact)

    def test_integer_food_migration_preserves_custom_values_and_is_idempotent(self):
        _seed_buildings()
        with db.cursor() as c:
            c.execute("DELETE FROM economy_meta WHERE key='food_whole_v5'")
            for key,effect in [('farm',{'food':77.7}),('pasture',{'food':8.4,'horses':1}),
                               ('fishing_wharf',{'food':15.75}),('plantation',{'food':6.3,'spices':3})]:
                c.execute('UPDATE building_defs SET effect_json=? WHERE key=?',(json.dumps(effect),key))
        _seed_buildings();_seed_buildings()
        with db.cursor() as c:
            c.execute("SELECT key,effect_json FROM building_defs WHERE key IN ('farm','pasture','fishing_wharf','plantation')")
            food={r['key']:json.loads(r['effect_json'])['food'] for r in c.fetchall()}
        self.assertEqual(food,dict(farm=77.7,pasture=8,fishing_wharf=16,plantation=6))

    def test_language_guard_preserves_polish_and_rejects_obvious_english(self):
        validate_language('Rada kupców w York ustala zasady handlu.','pl')
        with self.assertRaises(ValueError):validate_language('The council will send supplies to the village.','pl')
        validate_language('The council will send supplies to the village.','en')

    async def test_draft_checks_language_before_accepting_ai_output(self):
        i18n.set_user_language(1,'pl')
        nation=self.balances()[0]
        nation['event_brief']=dict(topic='trade',mood='positive',recent=[],previous_event_id=0)
        async def generated(prompt,**options):
            with self.assertRaises(ValueError):
                options['validate']('The merchants offer the council a trade agreement.\nEFFECTS: {"treasury":10}')
            self.assertTrue(prompt.startswith('Pisz całą narrację'))
            self.assertEqual(options['max_output_tokens'],1000)
            return 'Kupcy proponują radzie korzystną umowę.\nEFFECTS: {"treasury":10}'
        with patch('event_ai.generate_text',side_effect=generated):
            text,_=await events._generate_event(nation,strict=True)
        self.assertEqual(text,'Kupcy proponują radzie korzystną umowę.')
