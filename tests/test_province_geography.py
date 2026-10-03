import json
import math
import unittest

import db
import province_geography as geography
from province_admin import buy,purchase_options
from test_province_purchase import ProvincePurchaseFixture


def save_map(data):
    with db.cursor() as c:
        c.execute("INSERT INTO azgaar_world(id,data_json,geometry_hash) VALUES(1,?,'test') "
                  'ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json',(json.dumps(data),))


def map_data():
    return {'pack':{'cells':[dict(i=cell,p=xy,h=30) for cell,xy in
            ((10,[100,100]),(20,[110,90]),(21,[120,80]),(22,[100,120]),(23,[90,100]))],
            'burgs':[0,dict(i=1,cell=10)]}}


class GeographyTests(ProvincePurchaseFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.map=map_data()
        save_map(self.map)

    def test_compass_uses_map_coordinates_and_capital_province_not_cell_id(self):
        before=self.balances()
        rows=purchase_options(1)[1]
        self.assertEqual(self.balances(),before)
        self.assertEqual([row['location']['direction'] for row in rows],['NE','S','W'])
        place=rows[0]['location']
        self.assertEqual(place['reference'],dict(kind='capital',cell=10,point=(100,100)))
        self.assertAlmostEqual(place['distance'],math.sqrt(200))
        self.assertEqual(place['point'],(110,90))

    def test_all_eight_directions_and_same_point(self):
        for xy,direction in (((0,-1),'N'),((1,-1),'NE'),((1,0),'E'),((1,1),'SE'),
                             ((0,1),'S'),((-1,1),'SW'),((-1,0),'W'),((-1,-1),'NW'),((0,0),'center')):
            with self.subTest(xy=xy):
                result=geography.location({'azgaar_cell_id':20},{20:{'p':xy}},dict(point=(0,0)))
                self.assertEqual(result['direction'],direction)

    def test_map_capital_is_used_when_game_capital_is_not_assigned(self):
        with db.cursor() as c:
            c.execute('UPDATE nations SET capital_province_id=NULL WHERE id=1')
            c.execute("INSERT INTO azgaar_states(state_id,nation_id,linked,data_json) VALUES(1,1,1,?)",
                      (json.dumps(dict(i=1,capital=1)),))
        origin=purchase_options(1)[1][0]['location']['reference']
        self.assertEqual(origin,dict(kind='map_capital',cell=10,point=(100,100)))

    def test_foreign_or_inactive_map_capital_is_not_used(self):
        with db.cursor() as c:
            c.execute('UPDATE nations SET capital_province_id=NULL WHERE id=1')
            c.execute("INSERT INTO azgaar_states(state_id,nation_id,linked,data_json) VALUES(1,1,1,?)",
                      (json.dumps(dict(i=1,capital=2)),))
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id) VALUES(30,2)')
        self.map['pack']['cells'].append(dict(i=30,p=[999,999],h=30))
        self.map['pack']['burgs'].append(dict(i=2,cell=30))
        save_map(self.map)
        for owner,active in ((2,1),(1,0)):
            with self.subTest(owner=owner,active=active):
                with db.cursor() as c:
                    c.execute('UPDATE provinces SET owner_nation_id=?,active=? WHERE azgaar_cell_id=30',(owner,active))
                origin=purchase_options(1)[1][0]['location']['reference']
                self.assertEqual(origin,dict(kind='center',cell=None,point=(100,100)))

    def test_center_fallback_uses_only_owned_active_land_with_coordinates(self):
        with db.cursor() as c:
            c.execute('UPDATE nations SET capital_province_id=NULL WHERE id=1')
            for cell,owner,active in ((30,1,1),(31,2,1),(32,1,0),(33,1,1),(34,1,1)):
                c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,active) VALUES(?,?,?)',(cell,owner,active))
        self.map['pack']['cells']+=[dict(i=30,p=[120,100],h=30),dict(i=31,p=[999,999],h=30),
            dict(i=32,p=[999,999],h=30),dict(i=33,p=[999,999],h=10),dict(i=34,h=30)]
        save_map(self.map)
        place=purchase_options(1)[1][0]['location']
        self.assertEqual(place['reference'],dict(kind='center',cell=None,point=(110,100)))
        self.assertEqual(place['direction'],'N')

    def test_missing_coordinates_do_not_invent_a_direction(self):
        self.map['pack']['cells'][0].pop('p')
        save_map(self.map)
        place=purchase_options(1)[1][0]['location']
        self.assertIsNone(place['reference'])
        self.assertIsNone(place['direction'])
        self.assertEqual(place['point'],(110,90))
        self.map['pack']['cells'][1]['p']=['bad',90]
        save_map(self.map)
        self.assertIsNone(purchase_options(1)[1][0]['location'])

    def test_map_water_cannot_be_bought_even_when_saved_terrain_says_land(self):
        preview=purchase_options(1)[1][0]
        self.assertEqual(preview['azgaar_cell_id'],20)
        before=self.balances()
        self.map['pack']['cells'][1]['h']=19
        save_map(self.map)
        self.assertNotIn(20,[row['azgaar_cell_id'] for row in purchase_options(1)[1]])
        with self.assertRaisesRegex(ValueError,'water'):
            buy(20,1,expected_cost=preview['cost'])
        self.assertEqual(self.balances(),before)
        with db.cursor() as c:
            c.execute('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=20')
            self.assertIsNone(c.fetchone()['owner_nation_id'])
            c.execute('SELECT * FROM nation_history')
            self.assertEqual(c.fetchall(),[])

    def test_water_terrain_and_marine_biome_are_blocked_without_map_metadata(self):
        with db.cursor() as c:c.execute('DELETE FROM azgaar_world')
        for terrain,biome in (('water','unknown'),('sea','unknown'),('ocean','unknown'),
                              (' lake ','unknown'),('LAKES','unknown'),('plains',' marine ')):
            with self.subTest(terrain=terrain,biome=biome):
                with db.cursor() as c:
                    c.execute('UPDATE provinces SET terrain=?,biome=? WHERE azgaar_cell_id=20',(terrain,biome))
                self.assertNotIn(20,[row['azgaar_cell_id'] for row in purchase_options(1)[1]])
                with self.assertRaisesRegex(ValueError,'water'):buy(20,1)
        self.assertEqual(self.balances()[0]['treasury'],1000)

    def test_coastal_land_and_rivers_remain_purchasable(self):
        self.map['pack']['cells'][1].update(h=20,haven=99,r=5)
        save_map(self.map)
        self.assertIn(20,[row['azgaar_cell_id'] for row in purchase_options(1)[1]])
        self.assertEqual(buy(20,1)['cost'],300)

    def test_water_does_not_supply_cultural_or_religious_discounts(self):
        self.map['pack']['cells'][0]['h']=10
        save_map(self.map)
        row=purchase_options(1)[1][0]
        self.assertEqual((row['cost'],row['culture'],row['religion']),(500,False,False))
