import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch

import db
import terraforming as service
from cogs.economy import _seed_buildings
from economy_engine import run_month,run_tick,forecast
from economy_services import build
from nation_access import set_coop
from test_regressions import DatabaseFixture
from test_province_geography import save_map
from world_service import world_lock


@contextmanager
def rollback():
    class Rollback(Exception):pass
    try:
        with db.atomic() as c:
            yield c
            raise Rollback()
    except Rollback:pass


class TerraformFixture(DatabaseFixture):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=20000,stability=100,tech_json=?,resources_json=?',
                (json.dumps(dict(economy=6,land=6,naval=6,colonial=6)),
                 json.dumps(dict(food=10000,wood=2000,stone=2000,iron=2000))))
            for cell in (10,11):
                c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,biome,terrain,base_resources_json) '
                    'VALUES(?,1,2000,?,?,?)',(cell,'Temperate Grassland','plains',
                     json.dumps(dict(horses=4,cloth=2,iron=5,algae=.5))))

    def rows(self,sql,params=()):
        with db.cursor() as c:
            c.execute(sql,params);return c.fetchall()

    def province(self,cell=10):
        return self.rows('SELECT * FROM provinces WHERE azgaar_cell_id=?',(cell,))[0]

    def projects(self):
        return self.rows('SELECT * FROM province_terraforming ORDER BY id')

    def settle(self,month):
        with db.atomic() as c:
            world_lock(c)
            return service.tick(c,month)


class TerraformTests(TerraformFixture,unittest.TestCase):
    def test_preview_is_read_only_start_pays_once_and_does_not_change_land(self):
        before=self.balances();province=self.province()
        data=service.preview(1,10)
        self.assertEqual(data['quote']['reasons'],[])
        self.assertEqual(data['quote']['cost'],dict(gold=800,wood=100,stone=50))
        self.assertEqual(self.balances(),before)
        self.assertEqual(self.projects(),[])
        project=service.start(1,10,'afforest',expected_biome=province['biome'],expected_terrain=province['terrain'])
        self.assertEqual(project['due'],18)
        self.assertEqual(self.balances()[0]['treasury'],19200)
        self.assertEqual(json.loads(self.balances()[0]['resources_json'])['wood'],1900)
        self.assertEqual(self.province(),province)
        self.assertEqual(self.projects()[0]['status'],'building')
        paid=self.balances()
        for cell in (10,11):
            with self.assertRaisesRegex(ValueError,'already'):service.start(1,cell,'afforest')
        self.assertEqual(self.balances(),paid)

    def test_two_concurrent_starts_cannot_double_spend_or_run_two_projects(self):
        def attempt(cell):
            try:service.start(1,cell,'afforest');return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(attempt,(10,11))),[False,True])
        self.assertEqual(len(self.projects()),1)
        self.assertEqual(self.balances()[0]['treasury'],19200)

    def test_technology_stability_population_and_funds_are_rechecked_without_charge(self):
        cases=[('UPDATE nations SET tech_json=? WHERE id=1','{"economy":3}','technology'),
               ('UPDATE nations SET stability=? WHERE id=1',39,'Stability'),
               ('UPDATE provinces SET population=? WHERE azgaar_cell_id=10',999,'population'),
               ('UPDATE nations SET treasury=? WHERE id=1',799,'Missing'),
               ('UPDATE nations SET resources_json=? WHERE id=1','{}','Missing')]
        for sql,value,reason in cases:
            with self.subTest(reason=reason):
                with rollback() as c:
                    c.execute(sql,(value,))
                    before=self.balances()
                    self.assertTrue(any(reason in r for r in service.preview(1,10)['quote']['reasons']))
                    with self.assertRaisesRegex(ValueError,reason):service.start(1,10,'afforest')
                    self.assertEqual(self.balances(),before)
                    self.assertEqual(self.projects(),[])

    def test_ownership_coop_and_stale_biome_are_checked_at_confirmation(self):
        set_coop(1,1,77)
        service.preview(77,10,nation_id=1)
        set_coop(1,1,77,remove=True)
        before=self.balances()
        for uid,nid in ((2,1),(77,1),(1,2)):
            with self.assertRaises(ValueError):service.start(uid,10,'afforest',nation_id=nid)
        with self.assertRaisesRegex(ValueError,'changed'):
            service.start(1,10,'afforest',expected_biome='Desert')
        self.assertEqual(self.balances(),before)
        with db.cursor() as c:c.execute('UPDATE provinces SET owner_nation_id=2 WHERE azgaar_cell_id=10')
        with self.assertRaises(ValueError):service.start(1,10,'afforest')

    def test_water_mountains_glacier_and_arbitrary_conversions_are_forbidden(self):
        for biome,terrain,height in (('Marine','plains',30),('Temperate Grassland','water',30),
                ('Temperate Grassland','plains',10),('Temperate Grassland','mountains',80),
                ('Temperate Grassland','plains',80),('Glacier','plains',30)):
            with self.subTest(biome=biome,terrain=terrain,height=height),rollback() as c:
                c.execute('UPDATE provinces SET biome=?,terrain=? WHERE azgaar_cell_id=10',(biome,terrain))
                save_map({'pack':{'cells':[dict(i=10,h=height)]}})
                before=self.balances()
                with self.assertRaises(ValueError):service.start(1,10,'afforest')
                self.assertEqual(before,self.balances())
        with self.assertRaises(ValueError):service.start(1,10,'restore_tundra')

    def test_all_projects_have_cost_duration_requirements_and_expected_conversion(self):
        for key,rule in service.PROJECTS.items():
            with self.subTest(project=key),rollback() as c:
                from cogs.provinces import BIOME_RESOURCES,_terrain_label
                source=rule['sources'][0]
                c.execute('UPDATE provinces SET biome=?,terrain=?,base_resources_json=? WHERE azgaar_cell_id=10',
                          (source,_terrain_label(20,source),json.dumps(BIOME_RESOURCES[source])))
                q=service.preview(1,10)['quote']
                self.assertEqual(q['key'],key)
                self.assertGreaterEqual(q['cost']['gold'],800)
                self.assertTrue(6<=q['months']<=12)
                project=service.start(1,10,key)
                self.settle(project['due'])
                self.assertEqual(self.province()['biome'],rule['target'])
                self.assertEqual(self.province()['population'],2000)

    def test_completion_uses_calendar_after_production_and_next_month_new_yield(self):
        service.start(1,10,'afforest')
        run_tick(5)
        self.assertEqual(self.province()['biome'],'Temperate Grassland')
        run_month(expected_month=17)
        self.assertEqual(self.province()['biome'],'Temperate Deciduous Forest')
        report=json.loads(self.rows('SELECT report_json FROM economy_months WHERE month_index=18')[0]['report_json'])['1']
        self.assertEqual(report['production'].get('wood',0),0)
        self.assertEqual(report['terraforming'][0]['status'],'complete')
        run_month(expected_month=18)
        report=json.loads(self.rows('SELECT report_json FROM economy_months WHERE month_index=19')[0]['report_json'])['1']
        self.assertGreater(report['production']['wood'],0)

    def test_forecast_and_failed_calendar_roll_back_completion_and_retry_once(self):
        service.start(1,10,'afforest')
        run_tick(5)
        before=self.balances();province=self.province();jobs=self.projects()
        forecast(1)
        self.assertEqual(self.province(),province)
        self.assertEqual(self.projects(),jobs)
        self.assertEqual(self.balances(),before)
        with patch('world_service.progress_goals',side_effect=RuntimeError('rollback after terraform')):
            with self.assertRaises(RuntimeError):run_month(expected_month=17)
        self.assertEqual(self.province(),province)
        self.assertEqual(self.projects(),jobs)
        self.assertEqual(self.balances(),before)
        run_month(expected_month=17)
        completed=self.province()
        self.assertIsNone(run_month(expected_month=17))
        self.assertEqual(self.province(),completed)
        self.assertEqual(len(self.rows("SELECT * FROM nation_history WHERE entry_text LIKE '%: complete%'")),1)

    def test_cooldown_and_conversion_preserve_deposits_without_resource_farming(self):
        before=self.province()
        service.start(1,10,'afforest')
        self.settle(18)
        result=self.province()
        self.assertEqual(json.loads(result['base_resources_json']),dict(wood=6,iron=5,algae=.5))
        self.assertEqual(result['population'],before['population'])
        self.assertEqual(self.settle(18),{})
        with self.assertRaisesRegex(ValueError,'Next terraforming'):service.start(1,10,'clear_forest')
        with db.cursor() as c:
            c.execute("INSERT INTO game_config(key,value) VALUES('current_year','2') ON CONFLICT(key) DO UPDATE SET value='2'")
            c.execute("INSERT INTO game_config(key,value) VALUES('current_month','7') ON CONFLICT(key) DO UPDATE SET value='7'")
        job=service.start(1,10,'clear_forest')
        self.settle(job['due'])
        self.assertEqual(json.loads(self.province()['base_resources_json']),json.loads(before['base_resources_json']))
        self.assertEqual(self.province()['population'],2000)

    def test_incompatible_existing_buildings_and_future_construction_are_blocked(self):
        with db.cursor() as c:c.execute('UPDATE provinces SET buildings_json=? WHERE azgaar_cell_id=10',('["farm"]',))
        with self.assertRaisesRegex(ValueError,'incompatible'):service.start(1,10,'afforest')
        with db.cursor() as c:c.execute("UPDATE provinces SET buildings_json='[]' WHERE azgaar_cell_id=10")
        service.start(1,10,'afforest')
        paid=self.balances()
        with self.assertRaisesRegex(ValueError,'planned terraforming'):build(1,10,'farm',uid=1)
        self.assertEqual(self.balances(),paid)

        # Company and automated construction use the same quote guard.
        import companies as co
        co.create(1,1,'Fields','Farming company',['farm'])
        with db.cursor() as c:s=co.state(c,1)
        co.configure(1,1,s['version'],1000,'off')
        with db.cursor() as c:s=co.state(c,1)
        paid=self.balances()
        with self.assertRaisesRegex(ValueError,'planned terraforming'):co.manual_invest(1,1,s['version'],10,'farm')
        self.assertEqual(self.balances(),paid)

    def test_legacy_biome_spelling_replaces_yields_and_preserves_hills(self):
        with db.cursor() as c:
            c.execute("UPDATE provinces SET biome='temperate grassland',terrain='Hills' WHERE azgaar_cell_id=10")
        service.start(1,10,'afforest')
        self.settle(18)
        province=self.province()
        self.assertEqual(province['terrain'],'hills')
        self.assertEqual(json.loads(province['base_resources_json']),dict(wood=6,iron=5,algae=.5))

    def test_world_changes_cancel_without_refund_or_new_owner_benefit(self):
        cases=[("UPDATE provinces SET owner_nation_id=2 WHERE azgaar_cell_id=10",'ownership'),
               ("UPDATE provinces SET active=0 WHERE azgaar_cell_id=10",'ownership'),
               ("UPDATE provinces SET terrain='water' WHERE azgaar_cell_id=10",'water'),
               ("UPDATE provinces SET biome='Desert' WHERE azgaar_cell_id=10",'source'),
               ("UPDATE provinces SET buildings_json='[\"farm\"]' WHERE azgaar_cell_id=10",'buildings'),
               ("INSERT INTO nation_decay(nation_id,status,started_month,due_month,gm_id,former_owner) VALUES(1,'ruins',12,13,'99','1')",'ruins')]
        for sql,reason in cases:
            with self.subTest(reason=reason),rollback() as c:
                service.start(1,10,'afforest')
                paid=self.balances()
                c.execute(sql)
                province=self.province()
                self.settle(13)
                self.assertEqual(self.projects()[0]['status'],'cancelled')
                self.assertEqual(self.projects()[0]['reason'],reason)
                self.assertEqual(self.province(),province)
                self.assertEqual(self.balances(),paid)
                self.assertEqual(self.settle(18),{})


class TerraformMapTests(TerraformFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        from test_azgaar_exchange import world,native
        import azgaar_service
        self.data=world()
        self.data['pack']['states'][1]['fullName']='A'
        self.data['pack']['biomes'].append(dict(i=2,name='Temperate deciduous forest'))
        self.raw=json.dumps(self.data).encode()
        azgaar_service.import_map(self.raw,native(self.data))

    def test_json_and_native_exports_include_only_completed_biome_and_keep_geometry(self):
        import azgaar_format as fmt
        import azgaar_service
        service.start(1,1,'afforest')
        pending=json.loads(azgaar_service.export_map(native_format=False))
        self.assertEqual(pending['pack']['cells'][1]['biome'],1)
        self.settle(18)
        exported=json.loads(azgaar_service.export_map(native_format=False))
        self.assertEqual(exported['pack']['cells'][1]['biome'],2)
        self.assertEqual(fmt.geometry_key(exported),fmt.geometry_key(self.data))
        native=fmt.decode(azgaar_service.export_map())
        self.assertEqual(native['_native_records'][fmt.NATIVE_CELLS['biome']].split(',')[1],'2')
        # Importing the old template must not undo the player's paid improvement.
        azgaar_service.import_map(self.raw)
        self.assertEqual(self.province(1)['biome'],'Temperate Deciduous Forest')
        self.assertEqual(json.loads(azgaar_service.export_map(native_format=False))['pack']['cells'][1]['biome'],2)

    def test_missing_map_biome_blocks_start_and_cancels_pending_work(self):
        self.data['pack']['biomes'].pop()
        save_map(self.data)
        before=self.balances()
        with self.assertRaisesRegex(ValueError,'catalogue'):service.start(1,1,'afforest')
        self.assertEqual(before,self.balances())
        self.data['pack']['biomes'].append(dict(i=2,name='Temperate deciduous forest'))
        save_map(self.data)
        service.start(1,1,'afforest')
        self.data['pack']['biomes'].pop()
        save_map(self.data)
        self.settle(13)
        self.assertEqual(self.projects()[0]['reason'],'map')
        self.assertEqual(self.province(1)['biome'],'Temperate Grassland')


if __name__=='__main__':unittest.main()
