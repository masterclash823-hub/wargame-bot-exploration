import unittest

from test_regressions import DatabaseFixture
import db
from province_admin import buy


class ProvincePurchaseTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=1000 WHERE id=1')
            c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population) VALUES(10,1,50)")
            c.execute('UPDATE nations SET capital_province_id=(SELECT id FROM provinces WHERE azgaar_cell_id=10) WHERE id=1')
            c.execute('INSERT INTO azgaar_cells(cell_id,state_id,culture_id,religion_id) VALUES(10,1,2,3)')
            for cell,culture,religion in ((20,2,3),(21,2,4),(22,4,3),(23,4,4)):
                c.execute('INSERT INTO provinces(azgaar_cell_id,population) VALUES(?,10)',(cell,))
                c.execute('INSERT INTO azgaar_cells(cell_id,state_id,culture_id,religion_id) VALUES(?,1,?,?)',
                          (cell,culture,religion))

    def test_discounts_stack_and_balance_population_update(self):
        for cell,cost in ((20,300),(21,400)):
            self.assertEqual(buy(cell,1)['cost'],cost)
        with db.cursor() as c:
            c.execute('SELECT treasury,population FROM nations WHERE id=1');n=c.fetchone()
        self.assertEqual((n['treasury'],n['population']),(300,70))
        with self.assertRaises(ValueError):buy(20,1)
        with self.assertRaises(ValueError):buy(22,1)
        self.assertEqual(self.balances()[0]['treasury'],300)

    def test_unclaimed_only_and_no_discount_without_map_identity(self):
        with db.cursor() as c:
            c.execute('INSERT INTO provinces(azgaar_cell_id) VALUES(24)')
        self.assertEqual(buy(24,1)['cost'],500)
        with self.assertRaises(ValueError):buy(10,1)
        with self.assertRaises(ValueError):buy(23,2)
