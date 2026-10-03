import json
import random
import unittest

import db
import province_population as population
from test_regressions import DatabaseFixture
from test_province_geography import save_map
from test_azgaar_exchange import world,native
import azgaar_service


class PopulationTests(DatabaseFixture,unittest.TestCase):
    def rows(self):
        with db.cursor() as c:
            c.execute('SELECT * FROM provinces ORDER BY azgaar_cell_id')
            return c.fetchall()

    def test_distribution_exact_mean_bounds_variation_and_idempotence(self):
        rng=random.Random(17)
        for values in ([],[0],[1,0,100000],[500,500,4000,0],
                       [rng.randrange(0,20000) for _ in range(103)]):
            with self.subTest(size=len(values)):
                actual=population.distribution(values)
                self.assertEqual(sum(actual),2000*len(values))
                self.assertTrue(all(500<=v<=4000 for v in actual))
                self.assertEqual(population.distribution(actual),actual)
        self.assertGreater(len(set(population.distribution([500,1000,4000]))),1)

    def test_restart_repairs_only_unclaimed_active_land_once(self):
        with db.cursor() as c:
            c.executemany('INSERT INTO provinces(azgaar_cell_id,population,owner_nation_id,active,terrain) VALUES(?,?,?,?,?)',
                [(10,0,None,1,'plains'),(11,900,None,1,'plains'),(12,99999,None,1,'forest'),
                 (13,6000,1,1,'plains'),(14,0,2,1,'plains'),(15,0,None,0,'plains'),
                 (16,333,None,1,'water'),(17,333,None,1,'plains')])
            c.execute('DELETE FROM economy_meta WHERE key=?',(population.MIGRATION,))
        save_map({'pack':{'cells':[dict(i=17,h=10)]}})
        before=self.balances()
        db.init_db()
        rows=self.rows()
        self.assertEqual(sum(p['population'] for p in rows[:3]),6000)
        self.assertTrue(all(500<=p['population']<=4000 for p in rows[:3]))
        self.assertEqual([p['population'] for p in rows[3:]],[6000,0,0,0,0])
        self.assertEqual(before,self.balances())
        # A later GM change is not reset every time the bot restarts.
        with db.cursor() as c:c.execute('UPDATE provinces SET population=42 WHERE azgaar_cell_id=10')
        saved=self.rows()
        db.init_db()
        self.assertEqual(self.rows(),saved)

    def test_reimport_repairs_legacy_zeros_after_ownership_and_preserves_players(self):
        data=world()
        data['pack']['states'][1]['fullName']='A'
        for cell in data['pack']['cells']:
            if cell['i']>=2:cell['state']=0
        with db.cursor() as c:
            c.executemany('INSERT INTO provinces(azgaar_cell_id,population,biome,terrain,owner_nation_id) VALUES(?,?,?,?,?)',
                [(0,0,'Marine','water',None),(1,0,'Temperate Grassland','plains',1),
                 (2,0,'Temperate Grassland','plains',None),(3,900,'Temperate Grassland','plains',None)])
        raw=json.dumps(data).encode()
        result=azgaar_service.import_map(raw,native(data))
        rows=self.rows()
        self.assertEqual(rows[1]['population'],0)
        self.assertEqual(rows[1]['owner_nation_id'],1)
        self.assertEqual(rows[0]['population'],0)
        self.assertEqual(sum(p['population'] for p in rows[2:]),4000)
        self.assertTrue(all(p['population']>0 and p['owner_nation_id'] is None for p in rows[2:]))
        self.assertEqual(result['unclaimed_population']['land'],2)
        azgaar_service.import_map(raw)
        self.assertEqual(self.rows(),rows)


if __name__=='__main__':unittest.main()
