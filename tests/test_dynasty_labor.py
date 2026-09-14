import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from test_regressions import DatabaseFixture, interaction
import db
import i18n
import dynasty
import labor_regimes as labor
import treaty_service as treaties
import world_service as world
from economy_engine import forecast, run_tick
from cogs.economy import _seed_buildings


class Fixture(DatabaseFixture):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            c.execute('UPDATE nations SET resources_json=?', ('{"food":1000}',))
            for nid in (1,2):
                c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,terrain,buildings_json) VALUES(?,?,2000,'plains','[\"farm\"]')", (nid,nid))

    def query(self, sql, args=()):
        with db.cursor() as c:
            c.execute(sql,args);return c.fetchall()

    def alliance(self, a=1, b=2, duration=12, visibility='public'):
        tid=treaties.propose(a,a,b,'alliance',duration,visibility)
        treaties.accept(tid,b,0)
        return tid

    def marry(self, tid=None, own='self', other='daughter', uid=1, recipient=2):
        tid=tid or self.alliance()
        mid=dynasty.propose(tid,uid,own,other)
        dynasty.answer(mid,recipient,True)
        return tid,mid

    def regime(self):
        with db.cursor() as c:return labor.state(c,1)

    def reputation(self, nid=1):
        with db.cursor() as c:return world.profile(c,nid)['reputation']


class MarriageTests(Fixture, unittest.TestCase):
    def test_alliance_and_recipient_consent_required(self):
        tid=treaties.propose(1,1,2,'alliance')
        with self.assertRaises(ValueError):dynasty.propose(tid,1,'self','daughter')
        treaties.accept(tid,2,0)
        with self.assertRaises(ValueError):dynasty.propose(tid,999,'self','daughter')
        mid=dynasty.propose(tid,1,'self','daughter')
        self.assertEqual(forecast(1)['dynasty_stability'],0)
        for uid in (1,999):
            with self.assertRaises(ValueError):dynasty.answer(mid,uid,True)
        dynasty.answer(mid,2,True)
        self.assertEqual(forecast(1)['dynasty_stability'],.25)
        self.assertEqual(forecast(2)['dynasty_stability'],.25)
        with self.assertRaises(ValueError):dynasty.answer(mid,2,True)

    def test_either_alliance_party_can_propose(self):
        tid=self.alliance()
        mid=dynasty.propose(tid,2,'son','self')
        dynasty.answer(mid,1,True)
        self.assertEqual(self.query('SELECT proposer_id FROM dynastic_marriages')[0]['proposer_id'],2)

    def test_old_alliance_break_button_cannot_charge_new_penalty(self):
        tid=self.alliance()
        old=treaties.get_treaty(tid,1)
        self.marry(tid)
        with self.assertRaises(ValueError):treaties.end(tid,1,'active',old['version'])
        self.assertEqual(self.reputation(),50)
        self.assertEqual(treaties.get_treaty(tid,1)['status'],'active')

    def test_competing_marriages_atomically_reserve_people(self):
        world.create_nation(3,'C','Lore','','',999)
        first=self.alliance();second=self.alliance(1,3)
        one=dynasty.propose(first,1,'self','daughter')
        two=dynasty.propose(second,1,'self','son')
        def accept(args):
            try:dynasty.answer(*args,True);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(accept,[(one,2),(two,3)])),[False,True])
        self.assertEqual(len(self.query("SELECT id FROM dynastic_marriages WHERE status='active'")),1)

    def test_breach_penalty_and_slot_release(self):
        tid,mid=self.marry()
        treaties.end(tid,1,'active')
        self.assertEqual(self.reputation(),30)
        self.assertEqual(self.balances()[0]['stability'],45)
        self.assertEqual(self.query('SELECT status FROM dynastic_marriages')[0]['status'],'ended')
        self.assertEqual(forecast(1)['dynasty_stability'],0)
        with db.cursor() as c:dynasty.available(c,1,'self')

    def test_war_charges_one_base_penalty_plus_dynastic_penalty(self):
        pact=treaties.propose(1,1,2,'non_aggression');treaties.accept(pact,2,0)
        self.marry()
        treaties.declare_war(1,1,2)
        self.assertEqual(self.reputation(),30)
        self.assertEqual(self.balances()[0]['stability'],45)
        self.assertEqual(forecast(1)['dynasty_stability'],0)

    def test_missed_reparations_end_marriage_and_penalize_payer(self):
        tid=treaties.propose(1,1,2,'alliance')
        treaties.amend_tribute(tid,1,100,3,'recipient')
        treaties.accept(tid,2,1)
        self.marry(tid)
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=0 WHERE id=2')
        run_tick(2)
        self.assertEqual(treaties.get_treaty(tid,1)['status'],'broken')
        self.assertEqual(self.reputation(2),30)
        self.assertEqual(self.reputation(1),50)
        self.assertEqual(forecast(1)['dynasty_stability'],0)

    def test_expiry_has_no_penalty_and_no_last_month_bonus(self):
        self.marry(self.alliance(duration=1))
        before=self.balances()
        self.assertEqual(forecast(1)['dynasty_stability'],0)
        self.assertEqual(before,self.balances())
        run_tick()
        self.assertEqual(self.reputation(),50)
        self.assertEqual(self.query('SELECT status FROM dynastic_marriages')[0]['status'],'ended')

    def test_transfer_cancels_pending_but_keeps_accepted_bonds(self):
        tid=self.alliance();mid=dynasty.propose(tid,1,'self','son')
        world.transfer_nation(2,22,'2',999)
        for uid in (2,22):
            with self.assertRaises(ValueError):dynasty.answer(mid,uid,True)
        mid=dynasty.propose(tid,1,'self','son');dynasty.answer(mid,22,True)
        world.transfer_nation(2,23,'22',999)
        self.assertEqual(forecast(2)['dynasty_stability'],.25)

    def test_decline_and_expired_proposals_have_no_effect(self):
        tid=self.alliance();mid=dynasty.propose(tid,1,'daughter','son')
        dynasty.answer(mid,2,False)
        self.assertEqual(forecast(1)['dynasty_stability'],0)
        mid=dynasty.propose(tid,1,'daughter','son')
        run_tick(6)
        with self.assertRaises(ValueError):dynasty.answer(mid,2,True)
        self.assertEqual(self.query('SELECT status FROM dynastic_marriages WHERE id=?',(mid,))[0]['status'],'expired')

    def test_bonus_cap_and_private_publication(self):
        for uid,slot in ((2,'self'),(3,'son'),(4,'daughter')):
            if uid>2:world.create_nation(uid,str(uid),'Lore','','',999)
            self.marry(self.alliance(1,uid,visibility='private'),slot,'self',1,uid)
        self.assertEqual(forecast(1)['dynasty_stability'],.5)
        self.assertFalse(self.query("SELECT * FROM world_activity WHERE kind='marriage'"))
        self.assertEqual(len(self.query("SELECT * FROM nation_history WHERE source='diplomacy_private' AND nation_id=1")),3)


class LaborTests(Fixture, unittest.TestCase):
    def test_existing_nations_remain_free_and_migration_is_repeatable(self):
        before=self.balances();base=forecast(1)
        db.init_db();db.init_db()
        self.assertEqual(before,self.balances())
        self.assertEqual(self.regime()['mode'],'free')
        self.assertEqual(forecast(1),base)
        self.assertEqual(base['production']['food'],17.5)

    def test_slavery_production_costs_and_preview_never_write(self):
        labor.change(1,1,'slavery',0,50)
        self.assertEqual(self.reputation(),40)
        self.assertEqual(self.balances()[0]['treasury'],50)
        self.assertEqual(self.balances()[0]['stability'],45)
        with db.cursor() as c:c.execute('UPDATE nations SET stability=50 WHERE id=1')
        before=self.balances();preview=forecast(1)
        self.assertEqual(before,self.balances())
        self.assertEqual(self.reputation(),40)
        self.assertAlmostEqual(preview['production']['food'],19.25)
        self.assertEqual(preview['labor_upkeep'],2)
        self.assertEqual(preview['upkeep'],4)
        self.assertEqual(preview['policy']['unrest'],2)
        self.assertEqual(preview['stability'],49.5)
        run_tick()
        self.assertEqual(self.reputation(),39)
        n=self.balances()[0]
        self.assertEqual(n['treasury'],preview['treasury'])
        self.assertEqual(n['stability'],preview['stability'])
        self.assertEqual(json.loads(n['resources_json']),preview['resources'])

    def test_emancipation_three_months_and_population_conserved(self):
        labor.change(1,1,'slavery',0,50)
        pop=self.query('SELECT SUM(population) AS n FROM provinces')[0]['n']
        labor.change(1,1,'free',1,40)
        self.assertEqual(pop,self.query('SELECT SUM(population) AS n FROM provinces')[0]['n'])
        self.assertEqual(self.balances()[0]['treasury'],10)
        with db.cursor() as c:c.execute('UPDATE nations SET stability=50 WHERE id=1')
        for remaining in (3,2,1):
            before=self.regime()
            preview=forecast(1)
            self.assertEqual(self.regime(),before)
            self.assertEqual(preview['labor']['transition_months'],remaining)
            self.assertAlmostEqual(preview['production']['food'],16.625)
            self.assertEqual(preview['labor_upkeep'],0)
            run_tick()
        self.assertEqual(self.regime()['transition_months'],0)
        self.assertEqual(forecast(1)['production']['food'],17.5)
        self.assertEqual(self.reputation(),40)

    def test_atomic_cost_owner_version_and_quote_checks(self):
        before=self.balances()
        for args in ((1,2,'slavery',0,50),(1,1,'slavery',9,50),(1,1,'slavery',0,49),(1,1,'bad',0,50)):
            with self.assertRaises(ValueError):labor.change(*args)
        self.assertEqual(before,self.balances())
        def click(_):
            try:labor.change(1,1,'slavery',0,50);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(click,range(2))),[False,True])
        with db.cursor() as c:c.execute('UPDATE provinces SET population=3000 WHERE owner_nation_id=1')
        before=self.balances()
        with self.assertRaises(ValueError):labor.change(1,1,'free',1,40)
        with self.assertRaises(ValueError):labor.change(1,1,'free',1,60)
        self.assertEqual(before,self.balances())
        self.assertEqual(self.regime()['mode'],'slavery')

    def test_no_algae_bonus_or_extra_workers_and_manual_assignment_respected(self):
        with db.cursor() as c:
            c.execute("UPDATE provinces SET buildings_json='[\"farm\",\"algae_farm\"]' WHERE owner_nation_id=1")
            c.execute('SELECT id FROM provinces WHERE owner_nation_id=1');pid=c.fetchone()['id']
            c.execute('INSERT INTO algae_sites(province_id) VALUES(?)',(pid,))
            c.execute('INSERT INTO province_labor(province_id,nation_id,allocations_json) VALUES(?,?,?)',(pid,1,'{"farm":100,"algae_farm":250}'))
        base=forecast(1)
        labor.change(1,1,'slavery',0,50)
        with db.cursor() as c:c.execute('UPDATE nations SET stability=50 WHERE id=1')
        r=forecast(1)
        self.assertEqual(r['staffing'],base['staffing'])
        self.assertEqual(r['production']['algae'],base['production']['algae'])
        self.assertAlmostEqual(r['production']['food'],base['production']['food']*1.1)


class UITests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def test_pending_marriage_is_visible_in_treaty_list(self):
        from cogs.treaties import TreatiesCog
        tid=self.alliance();dynasty.propose(tid,1,'son','daughter')
        i=interaction(2)
        await TreatiesCog.list_treaties.callback(None,i,1)
        view=i.response.send_message.call_args.kwargs['view']
        self.assertEqual(view.choose.options[0].value,str(tid))
        self.assertIn('💍',view.choose.options[0].description)

    async def test_marriage_selection_consent_and_localization(self):
        from dynasty_ui import show
        from cogs.treaties import TreatyView
        tid=self.alliance()
        for lang in ('pl','en'):
            with i18n.using_language(lang):
                i=interaction(1);await show(i,tid)
                view=i.response.send_message.call_args.kwargs['view']
                self.assertLess(len(i.response.send_message.call_args.kwargs['embed']),6000)
                self.assertEqual(len(view.own.options),3)
        i=interaction(1);await show(i,tid)
        view=i.response.send_message.call_args.kwargs['view']
        view.own_person='son';view.other_person='daughter'
        await view.send.callback(i)
        m=self.query('SELECT * FROM dynastic_marriages')[0]
        self.assertEqual((m['proposer_person'],m['recipient_person']),('son','daughter'))
        i=interaction(2);await show(i,tid)
        view=i.response.send_message.call_args.kwargs['view']
        self.assertFalse(view.accept.disabled)
        await view.accept.callback(i)
        t=treaties.get_treaty(tid,2);view=TreatyView(2,t)
        self.assertIn('20',view.end.label)
        self.assertFalse(await view.interaction_check(interaction(999)))

    async def test_labor_panel_cost_confirmation_and_stale_owner(self):
        from labor_policy_ui import show
        for lang in ('pl','en'):
            with i18n.using_language(lang):
                before=self.balances();i=interaction(1);await show(i)
                self.assertEqual(before,self.balances())
                self.assertLess(len(i.response.send_message.call_args.kwargs['embed']),6000)
        view=i.response.send_message.call_args.kwargs['view']
        self.assertFalse(await view.interaction_check(interaction(2)))
        await view.confirm.callback(i)
        self.assertEqual(self.regime()['mode'],'slavery')
        i=interaction(1);await show(i)
        view=i.response.send_message.call_args.kwargs['view']
        world.transfer_nation(1,11,'1',999)
        await view.confirm.callback(i)
        self.assertEqual(self.regime()['mode'],'slavery')

    async def test_public_news_and_event_context(self):
        from cogs.events import _build_nation_context
        from chronicle_service import render
        self.marry();labor.change(1,1,'slavery',0,50)
        context=_build_nation_context(self.balances()[0])
        self.assertIn('slavery',context);self.assertIn('daughter',context)
        rows=self.query("SELECT x.*,n.name,n.flag,o.name AS other_name FROM world_activity x JOIN nations n ON n.id=x.nation_id LEFT JOIN nations o ON o.id=x.other_nation_id WHERE kind IN ('marriage','labor_reform')")
        for lang in ('pl','en'):
            e=render({'language':lang,'report_json':json.dumps(rows),'slot':'2026-09-13T12:00:00+00:00'})
            self.assertEqual(len(e.fields),2)
            self.assertTrue(all(f.value!='🏛️' for f in e.fields))
