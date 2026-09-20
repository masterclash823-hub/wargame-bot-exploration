"""Maintenance, populated extraction, coasts and the silk migration."""
import copy
import json
import unittest

import db
import i18n
import companies as co
from coastal import coast_cells
from cogs.economy import _seed_buildings
from cogs.provinces import _process_azgaar, _upsert_provinces
from economy_engine import project, snapshot, forecast, run_tick
from economy_services import build, create_route
from economy_ui import dashboard
from test_regressions import DatabaseFixture


class BalanceTests(DatabaseFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=5000,stability=100,tech_json=?,resources_json=?',
                      ('{"economy":4,"naval":4}',json.dumps(dict(food=10000,wood=1000,cloth=1000,coal=100,copper=100))))
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,terrain) VALUES(10,1,2000,?)',('plains',))
            self.pid=c.lastrowid

    def query(self,sql,args=()):
        with db.cursor() as c:
            c.execute(sql,args);return c.fetchall()

    def data(self):
        with db.cursor() as c:return snapshot(c,1)

    def coast(self,value):
        with db.cursor() as c:
            c.execute('INSERT INTO province_coasts(province_id,coastal) VALUES(?,?) '
                      'ON CONFLICT(province_id) DO UPDATE SET coastal=excluded.coastal',(self.pid,value))

    def state(self):
        with db.cursor() as c:return co.state(c,1)

    def test_base_resources_scale_with_population_and_keep_colony_modifier(self):
        data=self.data();p=data[1][0]
        p['base_resources_json']='{"wood":10,"gold":8,"algae":100}'
        for population,factor in ((0,0),(300,.15),(1000,.5),(2000,1),(4000,1)):
            with self.subTest(population=population):
                p['population']=population
                result=project(*data)
                self.assertAlmostEqual(result['production']['wood'],10*factor)
                self.assertAlmostEqual(result['income']-result['taxes'],8*factor)
                self.assertNotIn('algae',result['production'])
        p['population']=1000;p['colony_status']='outpost'
        self.assertAlmostEqual(project(*data)['production']['wood'],2.5)

    def test_unpaid_months_grace_decline_preview_and_full_repayment(self):
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=0 WHERE id=1')
            c.execute('UPDATE provinces SET buildings_json=? WHERE id=?',('["powder_mill"]',self.pid))
            c.execute("UPDATE building_defs SET upkeep_json=? WHERE key='powder_mill'",('{"gold":100}',))
        for factor in (1,1,.75,.5,.25,0):
            before=self.balances()
            preview=forecast(1)
            self.assertEqual(self.balances(),before)
            self.assertEqual(preview['maintenance_factor'],factor)
            self.assertAlmostEqual(preview['production']['gunpowder'],4*factor)
            self.assertAlmostEqual(preview['production']['coal'],-2*factor)
            self.assertEqual(preview['upkeep'],100)
            run_tick()
            report=json.loads(self.query('SELECT report_json FROM economy_months ORDER BY month_index DESC LIMIT 1')[0]['report_json'])['1']
            self.assertEqual(report['production'],preview['production'])
            self.assertEqual(report['treasury'],preview['treasury'])
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=10000 WHERE id=1')
        preview=forecast(1)
        self.assertEqual(preview['maintenance_factor'],1)
        self.assertEqual(preview['policy']['arrears'],0)
        self.assertEqual(preview['policy']['unpaid_months'],0)
        run_tick()
        self.assertEqual(self.query('SELECT arrears FROM economy_policy WHERE nation_id=1')[0]['arrears'],0)

    def test_same_month_income_restores_production_and_free_or_prepaid_plants_are_exempt(self):
        data=self.data();n,provs,defs,prefs=data[:4]
        n['treasury']=0;provs[0]['buildings_json']='["farm"]'
        prefs.update(arrears=5,unpaid_months=5)
        self.assertEqual(project(*data)['maintenance_factor'],1)  # Taxes pay both current and old bills.
        prefs['arrears']=1000
        self.assertEqual(project(*data)['production']['food'],0)
        defs['farm']['upkeep_json']='{}'
        self.assertEqual(project(*data)['production']['food'],20)
        defs['farm']['upkeep_json']='{"gold":2}'
        provs[0]['company_plants']={'farm':dict(foreign=True,upkeep=2,inputs={},share=.7,nation_id=2)}
        result=project(*data)
        self.assertAlmostEqual(result['production']['food'],6)
        self.assertAlmostEqual(result['company_transfers'][0]['resources']['food'],14)
        self.assertEqual(result['upkeep'],0)

    def test_paid_services_are_reduced_together_with_production(self):
        data=list(self.data());n,provs,defs,prefs=data[:4]
        n['treasury']=0;n['resources_json']='{"food":1000,"silk":100}'
        provs[0]['buildings_json']='["market","port","granary"]'
        defs['market']['upkeep_json']='{"gold":2}'
        prefs.update(arrears=1000,luxury='sell')
        data[-1]=20
        full=project(*data)
        prefs['unpaid_months']=3
        half=project(*data)
        self.assertAlmostEqual(half['luxury_income'],full['luxury_income']/2)
        self.assertLess(half['taxes'],full['taxes'])
        self.assertGreater(half['spoilage'],full['spoilage'])

    def test_coast_parser_supports_water_zero_land_only_exports_and_parallel_arrays(self):
        rows=[dict(i=0,h=10),dict(i=1,h=30,c=[0],haven=0),dict(i=2,h=40,c=[1],haven=0),
              dict(i=3,h=25,t=1),dict(i=4,h=35,haven=5),dict(i=5,h=10),
              dict(i=6,h=30,haven=0),dict(i=7),dict(i='8',h='30',c=['0'])]
        original=copy.deepcopy(rows)
        self.assertEqual(coast_cells(rows),{0:False,1:True,2:False,3:True,4:True,5:False,6:False,8:True})
        self.assertEqual(rows,original)
        arrays=dict(i=[0,1,2],h=[10,30,35],c=[[1],[0,2],[1]],t=[-1,1,2],haven=[0,0,0])
        self.assertEqual(coast_cells(arrays),{0:False,1:True,2:False})
        for raw in (rows,arrays):
            provinces,error=_process_azgaar({'pack':{'cells':raw}})
            self.assertIsNone(error)
            by_id={p['cell_id']:p for p in provinces}
            self.assertTrue(by_id[1]['coastal'])
            self.assertFalse(by_id[2]['coastal'])

    def test_resync_preserves_population_and_unknown_metadata_but_updates_known_coast(self):
        row=dict(cell_id=10,name='Coast',biome='Grassland',terrain='plains',resources={},pop=9000,coastal=True)
        _upsert_provinces([row],resync=True)
        self.assertEqual(self.query('SELECT population FROM provinces')[0]['population'],2000)
        self.assertEqual(self.query('SELECT coastal FROM province_coasts')[0]['coastal'],1)
        row.pop('coastal');_upsert_provinces([row],resync=True)
        self.assertEqual(self.query('SELECT coastal FROM province_coasts')[0]['coastal'],1)
        row['coastal']=False;_upsert_provinces([row],resync=True)
        self.assertEqual(self.query('SELECT coastal FROM province_coasts')[0]['coastal'],0)
        db.init_db()
        self.assertEqual(self.query('SELECT coastal FROM province_coasts')[0]['coastal'],0)

    def test_new_buildings_and_upgrades_require_coastal_land_before_payment(self):
        for key in ('port','fishing_wharf'):
            with self.subTest(key=key):
                before=self.balances()
                with self.assertRaisesRegex(ValueError,'map_resync'):build(1,10,key)
                self.coast(0)
                with self.assertRaisesRegex(ValueError,'coastal'):build(1,10,key)
                self.assertEqual(self.balances(),before)
                self.coast(1);build(1,10,key)
                self.coast(0);before=self.balances()
                with self.assertRaises(ValueError):build(1,10,key,True)
                self.assertEqual(self.balances(),before)
                with db.cursor() as c:c.execute('DELETE FROM province_coasts')

    def test_inland_legacy_buildings_no_longer_produce_cost_or_export(self):
        with db.cursor() as c:
            c.execute('UPDATE provinces SET buildings_json=? WHERE id=?',('["port","fishing_wharf"]',self.pid))
            c.execute('INSERT INTO provinces(azgaar_cell_id,population) VALUES(20,2000)')
            c.execute('INSERT INTO blueprints(nation_id,type,name,stats_json) VALUES(1,?,?,?)',('ship','Cargo','{"cargo":20}'))
            bid=c.lastrowid
            c.execute('INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(1,?,1)',(bid,))
            uid=c.lastrowid
        create_route(1,'Legacy route',10,20,uid)
        self.assertEqual(self.data()[-1],20)
        self.assertEqual(project(*self.data())['production']['food'],15)
        self.coast(0)
        data=list(self.data());self.assertEqual(data[-1],0)
        data[4]=0;result=project(*data)
        self.assertEqual(result['production'].get('food',0),0)
        self.assertEqual(result['upkeep'],0)
        self.assertTrue(all(s.get('blocked')=='inland' and s['workers']==0 for s in result['staffing']))
        with self.assertRaisesRegex(ValueError,'port'):create_route(1,'Invalid',10,20,uid)

    def test_company_investment_uses_coast_gate_and_skips_inland_foreign_escrow(self):
        co.create(1,1,'Fisheries','Coastal fisheries',['fishing_wharf'])
        co.configure(1,1,self.state()['version'],1000,'auto')
        self.coast(0);before=self.balances()
        with self.assertRaisesRegex(ValueError,'coastal'):
            co.manual_invest(1,1,self.state()['version'],10,'fishing_wharf')
        self.assertEqual(self.balances(),before)
        preview=forecast(1)
        self.assertEqual(preview['company']['investments'],[])
        self.coast(1)
        co.manual_invest(1,1,self.state()['version'],10,'fishing_wharf')
        co.configure(1,1,self.state()['version'],1000,'off')
        with db.cursor() as c:
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,buildings_json) VALUES(20,2,2000,?)',('["fishing_wharf"]',))
            host=c.lastrowid
            c.execute('INSERT INTO province_coasts(province_id,coastal) VALUES(?,1)',(host,))
        grant=co.propose_concession(1,1,self.state()['version'],2,[20],['fishing_wharf'],70,12,10)
        co.respond_concession(grant,2,'accept');co.assign(1,1,self.state()['version'],20,'fishing_wharf')
        with db.cursor() as c:c.execute('UPDATE province_coasts SET coastal=0 WHERE province_id=?',(host,))
        run_tick()
        self.assertEqual(self.state()['report']['costs'],{})
        self.assertEqual(self.state()['report']['received'],{})

    def test_silk_upkeep_migration_preserves_custom_values_and_does_not_repeat(self):
        self.assertEqual(json.loads(self.query("SELECT upkeep_json FROM building_defs WHERE key='silk_workshop'")[0]['upkeep_json']),{'gold':2})
        for old,expected in (({'gold':4},{'gold':2}),({'gold':7},{'gold':7}),({'gold':4,'wood':1},{'gold':4,'wood':1})):
            with db.cursor() as c:
                c.execute("DELETE FROM economy_meta WHERE key='silk_upkeep_v3'")
                c.execute("UPDATE building_defs SET upkeep_json=? WHERE key='silk_workshop'",(json.dumps(old),))
            before=self.balances();_seed_buildings()
            self.assertEqual(self.balances(),before)
            self.assertEqual(json.loads(self.query("SELECT upkeep_json FROM building_defs WHERE key='silk_workshop'")[0]['upkeep_json']),expected)
        with db.cursor() as c:c.execute("UPDATE building_defs SET upkeep_json=? WHERE key='silk_workshop'",('{"gold":4}',))
        _seed_buildings()
        self.assertEqual(json.loads(self.query("SELECT upkeep_json FROM building_defs WHERE key='silk_workshop'")[0]['upkeep_json']),{'gold':4})

    def test_dashboard_warning_fields_stay_within_discord_limits(self):
        data=self.data();n=data[0];report=project(*data)
        report.update(balance=-100,food_shortage=10,maintenance_factor=.5,
                      staffing=[dict(blocked='inland',staff=0),dict(blocked='algae_site_or_tech',staff=0),dict(staff=.2)],
                      algae_programs=[dict(enabled=True,funded=False,category=k) for k in ('economy','land','naval','colonial')])
        report['policy']['arrears']=100
        for language in ('pl','en'):
            with i18n.using_language(language):embed=dashboard(n,report)
            self.assertTrue(all(len(f.value)<=1024 for f in embed.fields))
            self.assertLessEqual(len(embed),6000)
