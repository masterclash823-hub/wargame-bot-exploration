import json
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import db
from test_regressions import DatabaseFixture
from cogs.economy import _seed_buildings
from economy_engine import project,snapshot,forecast,run_month,run_tick,set_policy
from economy_services import build,set_posture,assert_ready,normalize_population,set_recurring,settle_contracts,create_route
from trade_service import accept_trade
import i18n


class EconomyTests(DatabaseFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            c.execute("UPDATE nations SET stability=100,resources_json='{}' WHERE id=1")
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,name,terrain,population,buildings_json) VALUES(?,?,?,?,?,?)',
                      (10,1,'Test','plains',2000,'["farm"]'))
            self.pid=c.lastrowid

    def nation(self):
        with db.cursor() as c:
            c.execute('SELECT * FROM nations WHERE id=1');return c.fetchone()

    def test_basic_balance_and_preview_never_writes(self):
        before=self.nation()
        r=forecast(1)
        self.assertEqual(self.nation(),before)
        self.assertEqual(r['taxes'],24)
        self.assertEqual(r['balance'],22)
        self.assertEqual(r['food_shortage'],0)
        with db.cursor() as c:
            c.execute('SELECT COUNT(*) AS n FROM economy_months');self.assertEqual(c.fetchone()['n'],0)
        run_tick()
        self.assertEqual(self.nation()['treasury'],r['treasury'])

    def test_inputs_limit_output_and_stock_never_negative(self):
        with db.cursor() as c:
            c.execute('UPDATE provinces SET buildings_json=? WHERE id=?',('["powder_mill"]',self.pid))
            c.execute('UPDATE nations SET resources_json=? WHERE id=1',('{"coal":1,"copper":1}',))
        r=forecast(1)
        self.assertEqual(r['resources']['gunpowder'],2)
        self.assertEqual(r['resources']['coal'],0)
        self.assertEqual(r['resources']['copper'],.5)
        self.assertTrue(all(v>=0 for v in r['resources'].values()))

    def test_workers_prioritize_food(self):
        with db.cursor() as c:
            c.execute('UPDATE provinces SET population=500,buildings_json=? WHERE id=?',('["farm","university"]',self.pid))
        r=forecast(1)
        self.assertEqual(r['resources'].get('universal_knowledge',0),0)
        self.assertEqual(r['staffing'][0]['building'],'farm')
        self.assertEqual(r['staffing'][0]['staff'],1)

    def test_luxuries_need_consumption_or_real_exports(self):
        with db.cursor() as c:c.execute('UPDATE nations SET resources_json=? WHERE id=1',('{"silk":250,"spices":250}',))
        set_policy(1,'luxury','stockpile')
        r=forecast(1)
        self.assertEqual(r['luxury_income'],0)
        self.assertEqual(r['resources']['silk'],250)
        set_policy(1,'luxury','consume')
        self.assertEqual(forecast(1)['resources']['silk'],248)

    def test_megaproject_preserves_same_month_production(self):
        with db.cursor() as c:
            c.execute('UPDATE provinces SET base_resources_json=? WHERE id=?',('{"wood":20}',self.pid))
            c.execute("INSERT INTO megaprojects(nation_id,name,status,duration_months,effect_json) VALUES(1,'X','building',1,?)",('{"resources_once":{"wood":10},"gold_once":100}',))
        r=forecast(1);self.assertEqual(r['resources']['wood'],30)
        run_tick();self.assertEqual(json.loads(self.nation()['resources_json'])['wood'],30)
        run_tick();r=json.loads(self.nation()['resources_json'])
        self.assertEqual(r['wood'],50)
        self.assertNotIn('gold_once',r);self.assertNotIn('resources_once',r)

    def test_failure_rolls_back_clock_every_nation_and_colonies(self):
        before=self.balances()
        with patch('cogs.colonialism.tick_colonies',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):run_tick()
        self.assertEqual(self.balances(),before)
        with db.cursor() as c:
            c.execute('SELECT COUNT(*) AS n FROM economy_months');self.assertEqual(c.fetchone()['n'],0)
        self.assertEqual(run_tick()[:2],(2,1))

    def test_concurrent_same_scheduled_month_pays_once(self):
        now=datetime(2026,9,9,tzinfo=timezone.utc)
        start=now-timedelta(hours=24)
        with db.cursor() as c:
            for k,v in [('calendar_running','1'),('hours_per_month','24'),('last_tick_ts',start.isoformat())]:
                c.execute('INSERT INTO game_config(key,value) VALUES(?,?)',(k,v))
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:run_month(scheduled_at=now),range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.nation()['treasury'],122)

    def test_catchup_preserves_remaining_months_and_fraction(self):
        now=datetime(2026,9,9,tzinfo=timezone.utc)
        start=now-timedelta(hours=24*10+5)
        with db.cursor() as c:
            for k,v in [('calendar_running','1'),('last_tick_ts',start.isoformat())]:c.execute('INSERT INTO game_config(key,value) VALUES(?,?)',(k,v))
        for _ in range(3):run_month(scheduled_at=now)
        with db.cursor() as c:
            c.execute("SELECT value FROM game_config WHERE key='last_tick_ts'")
            self.assertEqual(datetime.fromisoformat(c.fetchone()['value']),start+timedelta(hours=72))

    def test_seed_preserves_gm_changes_and_population(self):
        with db.cursor() as c:c.execute("UPDATE building_defs SET effect_json='{"+'"gold":999'+"}' WHERE key='market'")
        _seed_buildings()
        with db.cursor() as c:
            c.execute("SELECT effect_json FROM building_defs WHERE key='market'")
            self.assertEqual(json.loads(c.fetchone()['effect_json'])['gold'],999)
        self.assertEqual(normalize_population(1)[0]['new'],2000)

    def test_upgrade_cost_and_one_per_type(self):
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=1000,resources_json=? WHERE id=1',('{"wood":500}',))
        self.assertEqual(build(1,10,'farm',True)[0],2)
        self.assertEqual(self.nation()['treasury'],850)
        with self.assertRaises(ValueError):build(1,10,'farm')
        self.assertEqual(build(1,10,'farm',True)[0],3)
        with self.assertRaises(ValueError):build(1,10,'farm',True)

    def test_reserve_mobilizes_after_one_month(self):
        uid=db.insert_returning_id('INSERT INTO military_units(nation_id,quantity) VALUES(1,10)',())
        set_posture(1,uid,'reserve')
        with db.cursor() as c:
            from economy_engine import military_cost
            self.assertEqual(military_cost(c,1)[0],7)
            with self.assertRaises(ValueError):assert_ready(c,1,uid)
        self.assertEqual(set_posture(1,uid,'active'),'mobilizing')
        run_tick()
        with db.cursor() as c:assert_ready(c,1,uid)

    def test_debt_is_recorded_and_desertion_has_grace(self):
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=0 WHERE id=1')
            c.execute('INSERT INTO military_units(nation_id,quantity) VALUES(1,100)')
        run_tick();run_tick()
        with db.cursor() as c:
            c.execute('SELECT quantity FROM military_units');self.assertEqual(c.fetchone()['quantity'],100)
            c.execute('SELECT arrears FROM economy_policy WHERE nation_id=1');self.assertGreater(c.fetchone()['arrears'],0)
        run_tick()
        with db.cursor() as c:
            c.execute('SELECT quantity FROM military_units');self.assertEqual(c.fetchone()['quantity'],98)

    def test_recurring_contract_requires_consent_and_pays_once(self):
        with db.cursor() as c:c.execute('UPDATE nations SET resources_json=? WHERE id=1',('{"wood":100}',))
        set_recurring(self.trade_id,1)
        with self.assertRaises(ValueError):accept_trade(self.trade_id,2)
        accept_trade(self.trade_id,2,monthly=True)
        with db.atomic() as c:settle_contracts(c,13)
        balances=self.balances()
        with db.atomic() as c:settle_contracts(c,13)
        self.assertEqual(self.balances(),balances)
        self.assertEqual(json.loads(balances[0]['resources_json'])['wood'],80)

    def test_route_requires_port_and_ship_cannot_be_reused(self):
        with db.cursor() as c:
            c.execute('INSERT INTO provinces(azgaar_cell_id,population) VALUES(11,2000)')
            c.execute('INSERT INTO blueprints(nation_id,name,type,stats_json) VALUES(1,?,\'ship\',?)',('Cargo','{"cargo":10}'))
            bid=c.lastrowid
        uid=db.insert_returning_id('INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(1,?,1)',(bid,))
        with self.assertRaises(ValueError):create_route(1,'X',10,11,uid)
        with db.cursor() as c:c.execute('UPDATE provinces SET buildings_json=? WHERE id=?',('["port"]',self.pid))
        create_route(1,'X',10,11,uid)
        with self.assertRaises(ValueError):create_route(1,'Y',10,11,uid)
        with db.cursor() as c:
            with self.assertRaises(ValueError):assert_ready(c,1,uid)

    def test_pending_plan_automatically_deploys_and_cannot_double_commit(self):
        from economy_services import submit_plan
        from economy_engine import military_cost
        uid=db.insert_returning_id('INSERT INTO military_units(nation_id,quantity) VALUES(1,10)',())
        pid,forces=submit_plan(1,[{'unit_id':uid,'qty':1000}]*2,'Town','Defend','')
        self.assertIsInstance(pid,int)
        self.assertEqual(forces,[{'unit_id':uid,'qty':10}])
        with db.cursor() as c:self.assertEqual(military_cost(c,1)[0],30)
        with self.assertRaises(ValueError):submit_plan(1,forces,'Town','Attack','')
        with self.assertRaises(ValueError):set_posture(1,uid,'reserve')

    def test_initial_migration_changes_only_unchanged_defaults(self):
        with db.cursor() as c:
            c.execute("DELETE FROM economy_meta WHERE key='buildings_v2'")
            c.execute("UPDATE building_defs SET effect_json=?,cost_json=?,name='Custom market' WHERE key='market'",('{"gold":15}','{"gold":17}'))
            c.execute("UPDATE building_defs SET effect_json=? WHERE key='farm'",('{"food":99}',))
        before=self.nation()
        _seed_buildings()
        self.assertEqual(self.nation(),before)
        with db.cursor() as c:
            c.execute("SELECT * FROM building_defs WHERE key='market'");m=c.fetchone()
            self.assertEqual(m['name'],'Custom market');self.assertEqual(json.loads(m['effect_json']),{})
            self.assertEqual(json.loads(m['cost_json']),{'gold':17})
            c.execute("SELECT effect_json FROM building_defs WHERE key='farm'")
            self.assertEqual(json.loads(c.fetchone()['effect_json']),{'food':99})

    def test_map_resync_preserves_existing_people(self):
        from cogs.provinces import _upsert_provinces
        _upsert_provinces([dict(cell_id=10,biome='Grassland',terrain='plains',resources={},pop=5000,name='New map name')],resync=True)
        with db.cursor() as c:
            c.execute('SELECT population FROM provinces WHERE id=?',(self.pid,))
            self.assertEqual(c.fetchone()['population'],2000)

    def test_shortage_contract_waits_without_partial_payment_and_can_stop(self):
        from economy_services import cancel_contract
        with db.cursor() as c:c.execute('UPDATE nations SET resources_json=? WHERE id=1',('{"wood":10}',))
        set_recurring(self.trade_id,1);accept_trade(self.trade_id,2,monthly=True)
        balances=self.balances()
        with db.atomic() as c:settle_contracts(c,13)
        self.assertEqual(self.balances(),balances)
        with db.cursor() as c:
            c.execute('SELECT status FROM trade_contracts');self.assertEqual(c.fetchone()['status'],'waiting')
        cancel_contract(self.trade_id,2)
        with db.cursor() as c:c.execute('UPDATE nations SET resources_json=? WHERE id=1',('{"wood":50}',))
        balances=self.balances()
        with db.atomic() as c:settle_contracts(c,14)
        self.assertEqual(self.balances(),balances)

    def test_monthly_megaproject_gold_enters_treasury_and_preview_rolls_back(self):
        with db.cursor() as c:
            c.execute("INSERT INTO megaprojects(nation_id,name,status,effect_json) VALUES(1,'X','complete',?)",('{"resources_per_tick":{"gold":7,"wood":4}}',))
        r=forecast(1)
        self.assertEqual(r['treasury'],129)
        self.assertNotIn('gold',r['resources'])
        self.assertEqual(r['resources']['wood'],4)
        self.assertEqual(self.nation()['treasury'],100)

    def test_settlers_move_people_and_need_ships_free_of_battle_commitments(self):
        from economy_services import found_colony,submit_plan
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=1000 WHERE id=1')
            c.execute('INSERT INTO provinces(azgaar_cell_id,population) VALUES(11,50)')
        bid=db.insert_returning_id("INSERT INTO blueprints(nation_id,name,type,stats_json) VALUES(1,'Cargo','ship',?)",('{"cargo":10}',))
        uid=db.insert_returning_id('INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(1,?,1)',(bid,))
        pid,_=submit_plan(1,[{'unit_id':uid}],'Sea','Patrol','')
        with self.assertRaises(ValueError):found_colony(1,11,'Outpost')
        self.assertEqual(self.nation()['treasury'],1000)
        with db.cursor() as c:c.execute("UPDATE battle_plans SET status='resolved' WHERE id=?",(pid,))
        self.assertIsInstance(found_colony(1,11,'Outpost'),int)
        with db.cursor() as c:
            c.execute('SELECT population FROM provinces ORDER BY azgaar_cell_id')
            self.assertEqual([p['population'] for p in c.fetchall()],[1700,350])
        self.assertEqual(self.nation()['population'],2050)
        self.assertEqual(self.nation()['treasury'],500)


class EconomyViewTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    async def test_private_dashboard_language_buttons_and_owner(self):
        from test_regressions import interaction
        from economy_ui import EconomyControlCog
        i18n.set_user_language(1,'pl')
        _seed_buildings()
        before=self.balances()
        inter=interaction(1)
        await EconomyControlCog.status.callback(None,inter)
        sent=inter.followup.send.call_args.kwargs
        self.assertTrue(sent['ephemeral'])
        self.assertIn('Gospodarka',sent['embed'].title)
        self.assertEqual(sent['view'].details.label,'Szczegóły')
        self.assertLessEqual(len(sent['view'].settings.options),25)
        self.assertFalse(await sent['view'].interaction_check(interaction(2)))
        self.assertEqual(self.balances(),before)
