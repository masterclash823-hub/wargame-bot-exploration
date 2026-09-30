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
            # 21 is reachable only after buying 20; the rest border the capital.
            for first,second in ((10,20),(20,21),(10,22),(10,23)):
                c.executemany('INSERT INTO province_neighbors(cell_id,neighbor_cell_id) VALUES(?,?)',
                              ((first,second),(second,first)))

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
            c.executemany('INSERT INTO province_neighbors(cell_id,neighbor_cell_id) VALUES(?,?)',
                          ((10,24),(24,10)))
        self.assertEqual(buy(24,1)['cost'],500)
        with self.assertRaises(ValueError):buy(10,1)
        with self.assertRaises(ValueError):buy(23,2)

    def assert_rejected_without_changes(self,cell):
        with db.cursor() as c:
            c.execute('SELECT * FROM provinces ORDER BY id');provinces=c.fetchall()
            c.execute('SELECT * FROM nation_history ORDER BY id');history=c.fetchall()
        balances=self.balances()
        with self.assertRaisesRegex(ValueError,'adjacent'):
            buy(cell,1)
        self.assertEqual(self.balances(),balances)
        with db.cursor() as c:
            c.execute('SELECT * FROM provinces ORDER BY id');self.assertEqual(c.fetchall(),provinces)
            c.execute('SELECT * FROM nation_history ORDER BY id');self.assertEqual(c.fetchall(),history)

    def test_new_purchase_extends_the_border_but_cannot_skip_a_province(self):
        self.assert_rejected_without_changes(21)
        self.assertEqual(buy(20,1)['cost'],300)
        self.assertEqual(buy(21,1)['cost'],400)

    def test_foreign_inactive_or_unowned_neighbor_does_not_allow_purchase(self):
        for owner,active in ((2,1),(1,0),(None,1)):
            with self.subTest(owner=owner,active=active):
                with db.cursor() as c:
                    c.execute('UPDATE provinces SET owner_nation_id=?,active=? WHERE azgaar_cell_id=10',
                              (owner,active))
                self.assert_rejected_without_changes(20)

    def test_missing_adjacency_data_does_not_allow_purchase(self):
        with db.cursor() as c:
            c.execute('DELETE FROM province_neighbors')
        self.assert_rejected_without_changes(20)
