import base64
import copy
import gzip
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import db
import i18n
import azgaar_format as fmt
import azgaar_service as service


def world():
    cells = [dict(i=i, h=10 if i == 0 else 30, t=-1 if i == 0 else 1,
                  biome=0 if i == 0 else 1, c=[j for j in range(4) if abs(i-j) == 1],
                  p=[i*10, 10], g=i, pop=0 if i == 0 else 12.5, area=100,
                  state=0 if i == 0 else 1 if i < 3 else 2, culture=0 if i == 0 else 1,
                  religion=0 if i < 2 else 1, province=0 if i == 0 else 1, burg=i if i else 0)
             for i in range(4)]
    grid = dict(points=[r['p'] for r in cells], boundary=[[0, -10]], cells=copy.deepcopy(cells))
    return dict(info=dict(version='1.153.1', mapId=1234, seed='42', width=100, height=100), grid=grid,
                pack=dict(cells=cells, biomes=[dict(i=0, name='Marine'), dict(i=1, name='Temperate grassland')],
                          states=[dict(i=0, name='Neutral'), dict(i=1, name='A', fullName='Kingdom A', capital=1),
                                  dict(i=2, name='B', capital=3)],
                          cultures=[dict(i=0, name='Wildlands'), dict(i=1, name='Culture A', color='#123456')],
                          religions=[dict(i=0, name='No religion'), dict(i=1, name='Religion A', deity='Moon')],
                          burgs=[0]+[dict(i=i, cell=i, name=f'Town{i}', state=cells[i]['state'], culture=1,
                                          capital=int(i in (1,3)), x=i*10, y=10, population=2) for i in range(1,4)],
                          provinces=[0, dict(i=1, state=1, name='District', center=1, burg=1)],
                          markers=[dict(i=1, note='Keep me')], rivers=[dict(i=1, name='River')]))


def native(data):
    lines = ['']*53
    lines[0] = '1.153.1|File can be loaded in azgaar.github.io/Fantasy-Map-Generator|2026-9-27|42|100|100|1234'
    lines[1] = '{"seed":"42","graph":{"width":100,"height":100}}'
    lines[3] = json.dumps(data['pack']['biomes'])
    lines[5] = '<svg id="map" xmlns="http://www.w3.org/2000/svg">\n<g id="states"/>\n</svg>'
    lines[6] = json.dumps({k:v for k,v in data['grid'].items() if k != 'cells'})
    for key,index in [('h',7),('t',10)]:
        lines[index] = ','.join(str(r[key]) for r in data['grid']['cells'])
    for kind,index in fmt.NATIVE_ENTITIES.items():
        lines[index] = json.dumps(data['pack'][kind])
    for key,index in fmt.NATIVE_CELLS.items():
        lines[index] = ','.join(str(r.get(key,0)) for r in data['pack']['cells'])
    lines[35] = json.dumps(data['pack']['markers'])
    lines[51] = '{}'
    return '\r\n'.join(lines).encode()


class MapExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DB_PATH=str(Path(self.tmp.name)/'map.db'))
        self.env.start()
        self.pg = patch.object(db, 'USE_POSTGRES', False); self.pg.start()
        db.init_db()
        with db.cursor() as c:
            c.execute("INSERT INTO nations(owner_id,name,treasury,resources_json) VALUES('1','Kingdom A',999,'{\"wood\":50}')")
        self.data = world()
        self.raw = json.dumps(self.data).encode()
        self.map = native(self.data)

    def tearDown(self):
        self.pg.stop();self.env.stop();self.tmp.cleanup()

    def rows(self, sql, params=()):
        with db.cursor() as c:
            c.execute(sql,params)
            return c.fetchall()

    def test_full_import_binds_names_keeps_pending_and_water_unpopulated(self):
        stats = service.import_map(self.raw, self.map)
        self.assertEqual((stats['states'],stats['cultures'],stats['religions'],stats['pending']), (2,1,1,1))
        rows = self.rows('SELECT * FROM provinces ORDER BY azgaar_cell_id')
        self.assertEqual((rows[0]['terrain'],rows[0]['population']),('water',0))
        self.assertEqual([r['owner_nation_id'] for r in rows],[None,1,1,None])
        self.assertEqual(sum(r['population'] for r in rows),6000)
        self.assertEqual(rows[1]['biome'],'Temperate Grassland')
        self.assertEqual(service.identity(2)['religions'],'Religion A')
        self.assertEqual(len(self.rows('SELECT * FROM nations')),1)
        self.assertEqual(service.catalog('states')[2]['linked'],0)
        self.assertEqual(json.loads(rows[1]['base_resources_json'])['horses'],4)

    def test_purchase_recognizes_imported_identity_without_an_assigned_capital(self):
        from province_admin import buy,purchase_options
        service.import_map(self.raw,self.map)
        nation,rows=purchase_options(1)
        self.assertIsNone(nation['capital_province_id'])
        self.assertEqual([(p['azgaar_cell_id'],p['cost']) for p in rows],[(3,300)])
        self.assertEqual(json.loads(rows[0]['culture_json'])['name'],'Culture A')
        self.assertEqual(json.loads(rows[0]['religion_json'])['name'],'Religion A')
        self.assertEqual(buy(3,1,expected_cost=rows[0]['cost'])['cost'],300)
        self.assertEqual(self.rows('SELECT treasury FROM nations WHERE id=1')[0]['treasury'],699)

    def test_roundtrip_borders_cultures_religions_capitals_and_economy(self):
        service.import_map(self.raw,self.map)
        with db.cursor() as c:
            c.execute("INSERT INTO nations(owner_id,name) VALUES('2','B')")
        service.bind(2,'B')
        service.set_identity(2,0,0)
        with db.cursor() as c:
            c.execute('UPDATE provinces SET owner_nation_id=2 WHERE azgaar_cell_id=2')
            c.execute('UPDATE nations SET capital_province_id=(SELECT id FROM provinces WHERE azgaar_cell_id=2) WHERE id=2')
            c.execute("UPDATE provinces SET population=3456,buildings_json='[\"farm\"]',base_resources_json='{\"algae\":3}' WHERE azgaar_cell_id=1")
            from province_admin import totals
            totals(c, [1,2])
        before = self.rows('SELECT * FROM nations')
        # Simulated process restart: all templates and IDs come from the database.
        out = service.export_map()
        decoded = fmt.decode(out)
        updated = fmt.native_overlay(copy.deepcopy(self.data),decoded)
        rows = updated['pack']['cells']
        self.assertEqual([r['state'] for r in rows],[0,1,2,2])
        self.assertEqual((rows[2]['culture'],rows[2]['religion']),(0,0))
        self.assertEqual(updated['pack']['burgs'][2]['state'],2)
        self.assertEqual(updated['pack']['states'][2]['capital'],2)
        self.assertEqual(updated['pack']['states'][1]['fullName'],'Kingdom A')
        self.assertEqual(updated['pack']['religions'][1]['deity'],'Moon')
        for cell in rows:
            if cell['province']:
                self.assertEqual(updated['pack']['provinces'][cell['province']]['state'],cell['state'])
        original = fmt.decode(self.map)['_native_records']
        rewritten = decoded['_native_records']
        touched=set(fmt.NATIVE_CELLS.values())|set(fmt.NATIVE_ENTITIES.values())
        for i,value in enumerate(original):
            if i not in touched:self.assertEqual(value,rewritten[i],i)
        service.import_map(out,resync=True,sync_owners=True)
        self.assertEqual(self.rows('SELECT * FROM nations'),before)
        p=self.rows('SELECT * FROM provinces WHERE azgaar_cell_id=1')[0]
        self.assertEqual((p['population'],p['buildings_json'],p['base_resources_json']), (3456,'["farm"]','{"algae":3}'))
        self.assertEqual(len(self.rows('SELECT * FROM azgaar_states')),3)

    def test_resync_preserves_borders_unless_gm_explicitly_syncs(self):
        service.import_map(self.raw,self.map)
        with db.cursor() as c:c.execute('UPDATE provinces SET owner_nation_id=NULL WHERE azgaar_cell_id=1')
        service.import_map(self.raw,resync=True)
        self.assertIsNone(self.rows('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=1')[0]['owner_nation_id'])
        service.import_map(self.raw,resync=True,sync_owners=True)
        self.assertEqual(self.rows('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=1')[0]['owner_nation_id'],1)

    def test_new_bot_nation_exports_with_stable_ids_and_deleted_stays_deleted(self):
        service.import_map(self.raw,self.map)
        with db.cursor() as c:
            c.execute("INSERT INTO nations(owner_id,name) VALUES('3','New Nation')")
            c.execute('UPDATE provinces SET owner_nation_id=2 WHERE azgaar_cell_id=2')
        first=json.loads(service.export_map(native_format=False))
        second=json.loads(service.export_map(native_format=False))
        self.assertEqual(first,second)
        self.assertEqual(first['pack']['cells'][2]['state'],3)
        self.assertEqual(first['pack']['states'][3]['name'],'New Nation')
        self.assertEqual(first['pack']['cells'][3]['state'],2) # pending map state stays visible
        with db.cursor() as c:c.execute('DELETE FROM nations WHERE id=2')
        deleted=json.loads(service.export_map(native_format=False))
        self.assertEqual(deleted['pack']['cells'][2]['state'],0)
        self.assertTrue(deleted['pack']['states'][3]['removed'])
        service.import_map(self.raw)
        self.assertEqual(len(self.rows('SELECT * FROM nations')),1)

    def test_mismatch_and_invalid_import_are_atomic(self):
        service.import_map(self.raw,self.map)
        before=self.rows('SELECT * FROM azgaar_world')
        other=copy.deepcopy(self.data);other['pack']['cells'][1]['p'][0]+=1
        with self.assertRaisesRegex(ValueError,'geometry'):service.import_map(json.dumps(other).encode(),resync=True)
        wrong= self.map.replace(b'|1234\r\n',b'|9999\r\n')
        with self.assertRaisesRegex(ValueError,'same map'):service.import_map(self.raw,wrong)
        broken=copy.deepcopy(self.data);broken['pack']['cells'][1]['religion']=999
        with self.assertRaises(ValueError):service.import_map(json.dumps(broken).encode())
        partial=copy.deepcopy(self.data);partial.pop('grid')
        with self.assertRaisesRegex(ValueError,'Full Data'):service.import_map(json.dumps(partial).encode())
        with patch.object(service,'dumps',side_effect=RuntimeError('disk fail')):
            with self.assertRaises(RuntimeError):service.import_map(self.raw)
        self.assertEqual(self.rows('SELECT * FROM azgaar_world'),before)
        self.assertEqual(len(self.rows('SELECT * FROM provinces')),4)

    def test_json_only_then_add_template_and_native_without_geometry_rejected(self):
        with self.assertRaisesRegex(ValueError,'Full Data JSON'):service.import_map(self.map)
        service.import_map(self.raw)
        with self.assertRaisesRegex(ValueError,'original .map'):service.export_map()
        self.assertEqual(json.loads(service.export_map(native_format=False))['info']['mapId'],1234)
        service.export_map(map_raw=self.map)
        self.assertTrue(service.export_map().startswith(b'1.153.1|'))

    def test_bind_does_not_steal_and_identity_validation_rolls_back(self):
        service.import_map(self.raw,self.map)
        with db.cursor() as c:
            c.execute("INSERT INTO nations(owner_id,name) VALUES('2','B')")
            c.execute('UPDATE provinces SET owner_nation_id=1 WHERE azgaar_cell_id=3')
        self.assertEqual(service.bind(2,'B'),0)
        self.assertEqual(self.rows('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=3')[0]['owner_nation_id'],1)
        with self.assertRaises(ValueError):service.bind(1,'B')
        with self.assertRaises(ValueError):service.set_identity(2,culture_id=0,religion_id=500)
        self.assertEqual(service.identity(2)['cultures'],'Culture A')

    def test_old_arrays_gzip_base64_and_svg_newline_formats(self):
        parallel=copy.deepcopy(self.data)
        rows=parallel['pack']['cells'];parallel['pack']['cells']={key:[r.get(key,0) for r in rows] for key in rows[0]}
        service.import_map(gzip.compress(json.dumps(parallel).encode()))
        for payload in (gzip.compress(self.map),base64.b64encode(quote(self.map.decode()).encode()),self.map.replace(b'\r\n',b'\n')):
            parsed=fmt.decode(payload)
            self.assertEqual(len(parsed['_native_records']),53)
            service.import_map(payload)
        with patch.object(fmt,'MAX_BYTES',30):
            with self.assertRaises(ValueError):fmt.decode(gzip.compress(b' '*1000))

    def test_native_project_from_repository_parses_without_splitting_svg(self):
        saved=fmt.decode(Path('start1809.map').read_bytes())['_native_records']
        self.assertEqual(len(saved),53)
        self.assertTrue(saved[5].startswith('<svg'))
        self.assertIsInstance(json.loads(saved[14]),list)
        self.assertGreater(len(saved[25].split(',')),1000)


class MapCommandTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self,uid=1):
        return NS(user=NS(id=uid),locale=NS(value='pl'),guild=NS(filesize_limit=10*1024*1024),
                  response=NS(send_message=AsyncMock(),defer=AsyncMock()),followup=NS(send=AsyncMock()),edit_original_response=AsyncMock())

    async def test_permissions_confirmation_and_single_application(self):
        from azgaar_ui import ImportConfirmation,import_command,export_command
        i=self.interaction()
        with patch('azgaar_ui.gm_only',return_value=False),patch('azgaar_ui.read_source',new_callable=AsyncMock) as reader:
            await import_command(i,None,None,None,False,False)
            await export_command(i)
            reader.assert_not_awaited()
        with i18n.using_language('pl'):
            view=ImportConfirmation(1,b'data',None,False,False)
        with patch('azgaar_ui.gm_only',return_value=True),patch('i18n.get_user_language',return_value='pl'),patch.object(service,'import_map',return_value=dict(inserted=4,updated=0,states=2,cultures=1,religions=1,pending=1,owners_changed=0,unclaimed_population=dict(changed=1))) as apply:
            await view.children[0].callback(self.interaction(2));apply.assert_not_called()
            await view.children[0].callback(i);await view.children[0].callback(i)
            self.assertEqual(apply.call_count,1)
            self.assertTrue(view.children[0].disabled)
            self.assertIn('Import zakończony',i.edit_original_response.call_args.kwargs['content'])

    async def test_upload_export_and_admin_panel_fit(self):
        from azgaar_ui import export_command
        from admin_panel import AdminPanel
        i=self.interaction()
        with patch('azgaar_ui.gm_only',return_value=True),patch.object(service,'export_map',return_value=b'project'):
            await export_command(i)
        upload=i.followup.send.call_args.kwargs['file']
        self.assertEqual(upload.filename,'wargame.map')
        self.assertEqual(upload.fp.read(),b'project')
        view=AdminPanel(None,1,section='map')
        self.assertTrue(all(child.row<=4 for child in view.children))
        self.assertLessEqual(len(view.children),25)
