import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import patch

import db
import i18n
import treaty_service as treaties
from economy_engine import military_cost,forecast,run_month
from economy_services import set_posture,assert_ready,submit_plan
from military_posture import activate_wartime_reserves
from test_world_features import WorldFixture
from test_regressions import interaction


class WarFixture(WorldFixture):
    def unit(self,nid,mode='reserve',kind='unit',ready=0):
        hull='sloop' if kind=='ship' else 'militia'
        bp=db.insert_returning_id('INSERT INTO blueprints(nation_id,type,name,hull,stats_json) VALUES(?,?,?,?,?)',
            (nid,kind,hull,hull,'{"attack":5,"defense":5,"hp":10,"cargo":2}'))
        uid=db.insert_returning_id('INSERT INTO military_units(nation_id,blueprint_id,unit_type,quantity) VALUES(?,?,?,10)',
            (nid,bp,hull))
        if mode is not None:
            with db.cursor() as c:
                c.execute('INSERT INTO military_posture(unit_id,mode,ready_month) VALUES(?,?,?)',(uid,mode,ready))
        return uid

    def posture(self,uid):
        return self.query("SELECT COALESCE(m.mode,'active') AS mode,COALESCE(m.ready_month,0) AS ready "
                          'FROM military_units u LEFT JOIN military_posture m ON m.unit_id=u.id WHERE u.id=?',(uid,))[0]

    def legacy_war(self,a=1,b=2):
        with db.cursor() as c:
            c.execute("INSERT INTO relations(nation_a_id,nation_b_id,status) VALUES(?,?,'war')",treaties.pair(a,b))


class WartimeReserveTests(WarFixture,unittest.TestCase):
    def test_declaration_activates_both_armies_and_fleets_without_changing_other_units(self):
        self.third_nation()
        activated=[self.unit(nid,mode,kind,999) for nid in (1,2) for kind in ('unit','ship')
                   for mode in ('reserve','mobilizing')]
        unchanged=[self.unit(1,'deployed'),self.unit(2,'active'),self.unit(2,None),
                   self.unit(3,'reserve'),self.unit(3,'mobilizing',ready=999)]
        before={uid:self.posture(uid) for uid in unchanged}
        troops=self.query('SELECT * FROM military_units ORDER BY id')
        treaties.declare_war(1,1,2)
        for uid in activated:
            self.assertEqual(self.posture(uid),dict(mode='active',ready=0))
        self.assertEqual({uid:self.posture(uid) for uid in unchanged},before)
        self.assertEqual(self.query('SELECT * FROM military_units ORDER BY id'),troops)
        with db.cursor() as c:
            for nid in (1,2):
                for row in troops:
                    if row['nation_id']==nid:assert_ready(c,nid,row['id'])
        # No tick or extra click is needed to commit the former reserve.
        pid,forces=submit_plan(1,[dict(unit_id=activated[0])],'10','Defend','')
        self.assertEqual(forces,[dict(unit_id=activated[0],qty=10)])
        self.assertEqual(self.posture(activated[0])['mode'],'deployed')
        self.assertGreater(pid,0)

    def test_accepted_guarantee_activates_guarantor_only_when_it_joins(self):
        self.third_nation()
        uid=self.unit(3)
        tid=treaties.propose(3,3,1,'guarantee');treaties.accept(tid,1,0)
        treaties.declare_war(2,2,1)
        self.assertEqual(self.posture(uid)['mode'],'reserve')
        call=self.query('SELECT id FROM guarantee_calls')[0]['id']
        treaties.respond_guarantee(call,3,True)
        self.assertEqual(self.posture(uid),dict(mode='active',ready=0))

    def test_alliance_and_declined_guarantee_do_not_activate_neutral_forces(self):
        self.third_nation()
        uid=self.unit(3)
        treaties.accept(treaties.propose(3,3,1,'alliance'),1,0)
        self.assertEqual(self.posture(uid)['mode'],'reserve')
        tid=treaties.propose(3,3,1,'guarantee');treaties.accept(tid,1,0)
        treaties.declare_war(2,2,1)
        call=self.query('SELECT id FROM guarantee_calls')[0]['id']
        treaties.respond_guarantee(call,3,False)
        self.assertEqual(self.posture(uid)['mode'],'reserve')

    def test_reserve_is_blocked_until_last_war_ends_without_automatic_demobilization(self):
        self.third_nation();uid=self.unit(1)
        treaties.declare_war(1,1,2);treaties.declare_war(3,3,1)
        with self.assertRaisesRegex(ValueError,'all wars'):set_posture(1,uid,'reserve')
        with db.atomic() as c:treaties.set_relation(c,1,2,'peace')
        with self.assertRaisesRegex(ValueError,'all wars'):set_posture(1,uid,'reserve')
        with db.atomic() as c:treaties.set_relation(c,3,1,'peace')
        self.assertEqual(self.posture(uid)['mode'],'active')
        self.assertEqual(set_posture(1,uid,'reserve'),'reserve')
        self.assertEqual(set_posture(1,uid,'active'),'mobilizing')

    def test_startup_fixes_existing_wars_and_keeps_neutral_reserves_and_deployments(self):
        self.third_nation()
        units=[self.unit(1),self.unit(2,'mobilizing',ready=999),self.unit(1,'deployed'),self.unit(3)]
        self.legacy_war()
        db.init_db()
        self.assertEqual([self.posture(uid)['mode'] for uid in units],['active','active','deployed','reserve'])
        before=self.query('SELECT * FROM military_posture ORDER BY unit_id')
        db.init_db()
        self.assertEqual(self.query('SELECT * FROM military_posture ORDER BY unit_id'),before)

    def test_forecast_and_tick_charge_full_upkeep_and_rollback_activation_on_failure(self):
        uid=self.unit(1)
        with db.cursor() as c:reserve_cost=military_cost(c,1)[0]
        self.legacy_war()
        before=self.balances()
        report=forecast(1)
        self.assertAlmostEqual(report['upkeep'],reserve_cost/.35)
        self.assertEqual(self.posture(uid)['mode'],'reserve')
        self.assertEqual(self.balances(),before)
        with patch('world_service.progress_goals',side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):run_month(expected_month=12)
        self.assertEqual(self.posture(uid)['mode'],'reserve')
        self.assertEqual(self.balances(),before)
        run_month(expected_month=12)
        self.assertEqual(self.posture(uid)['mode'],'active')
        self.assertEqual(self.balances()[0]['treasury'],report['treasury'])
        self.assertIsNone(run_month(expected_month=12))

    def test_failed_war_and_unauthorized_declaration_do_not_activate_any_units(self):
        uid=self.unit(1);other=self.unit(2)
        with self.assertRaises(ValueError):treaties.declare_war(1,2,2)
        with patch('treaty_service.activity',side_effect=RuntimeError('rollback after activation')):
            with self.assertRaises(RuntimeError):treaties.declare_war(1,1,2)
        self.assertEqual(self.posture(uid)['mode'],'reserve')
        self.assertEqual(self.posture(other)['mode'],'reserve')
        self.assertEqual(self.query('SELECT * FROM relations'),[])

    def test_war_preserves_existing_battle_plans_and_trade_route_assignments(self):
        deployed=self.unit(1,'deployed');ship=self.unit(1,'active','ship');reserve=self.unit(2)
        pid=db.insert_returning_id('INSERT INTO battle_plans(nation_id,forces_json,status) VALUES(?,?,?)',
                                  (1,json.dumps([dict(unit_id=deployed,qty=10)]),'matched'))
        rid=db.insert_returning_id('INSERT INTO trade_routes(nation_id,from_cell_id,to_cell_id) VALUES(1,10,20)',())
        with db.cursor() as c:c.execute('INSERT INTO route_assignments(route_id,ship_id) VALUES(?,?)',(rid,ship))
        plans=self.query('SELECT * FROM battle_plans');routes=self.query('SELECT * FROM route_assignments')
        treaties.declare_war(1,1,2)
        self.assertEqual(self.posture(deployed)['mode'],'deployed')
        self.assertEqual(self.posture(reserve)['mode'],'active')
        self.assertEqual(self.query('SELECT * FROM battle_plans'),plans)
        self.assertEqual(self.query('SELECT * FROM route_assignments'),routes)
        with db.atomic() as c:
            from world_service import world_lock
            world_lock(c)
            self.assertEqual(activate_wartime_reserves(c),0)
        with self.assertRaises(ValueError):set_posture(1,deployed,'active')
        with db.cursor() as c:
            with self.assertRaisesRegex(ValueError,'trade route'):assert_ready(c,1,ship)

    def test_concurrent_war_and_reserve_change_always_finish_active(self):
        uid=self.unit(1,'active')
        def reserve():
            try:return set_posture(1,uid,'reserve')
            except ValueError:return 'blocked'
        with ThreadPoolExecutor(2) as pool:
            war=pool.submit(treaties.declare_war,1,1,2)
            posture=pool.submit(reserve)
            war.result();self.assertIn(posture.result(),('reserve','blocked'))
        self.assertEqual(self.posture(uid),dict(mode='active',ready=0))

    def test_legacy_diplomacy_entry_points_share_the_wartime_rule(self):
        from cogs.combat import _set_relation
        from combat import _set_relation as legacy_set_relation
        uid=self.unit(1)
        _set_relation(2,1,'war')
        self.assertEqual(self.posture(uid)['mode'],'active')
        _set_relation(1,2,'peace')
        set_posture(1,uid,'reserve')
        legacy_set_relation(1,2,'war')
        self.assertEqual(self.posture(uid)['mode'],'active')


class WartimeReserveUITests(WarFixture,unittest.IsolatedAsyncioTestCase):
    async def test_panel_hides_reserve_at_war_and_old_choice_is_rejected(self):
        from cogs.panel import PlayerPanel
        from economy_ui import EconomyControlCog
        uid=self.unit(1)
        i18n.set_user_language(1,'pl')
        cog=EconomyControlCog(None)
        bot=NS(get_cog=lambda _:cog)
        async def worker(fn,*args,**kwargs):return fn(*args,**kwargs)
        with patch('economy_ui.asyncio.to_thread',side_effect=worker):
            panel=PlayerPanel(bot,1,'pl','military')
            async def options():
                i=interaction(1);i.response.is_done=lambda:False
                await panel.choose_posture(i)
                units=i.response.send_message.call_args.kwargs['view']
                chosen=interaction(1);chosen.response.is_done=lambda:False
                await units.handler(chosen,str(uid))
                return chosen.response.send_message.call_args.kwargs['view']
            old=await options()
            self.assertIn('reserve',[option.value for option in old.children[0].options])
            treaties.declare_war(1,1,2)
            current=await options()
            self.assertEqual([option.value for option in current.children[0].options],['active','deployed'])
            stale=interaction(1);stale.response.is_done=lambda:False
            with i18n.using_language('pl'):
                await old.handler(stale,'reserve')
            self.assertEqual(self.posture(uid)['mode'],'active')
            self.assertTrue(stale.followup.send.call_args.kwargs['ephemeral'])
            self.assertIn('woj',stale.followup.send.call_args.args[0])


if __name__=='__main__':unittest.main()
