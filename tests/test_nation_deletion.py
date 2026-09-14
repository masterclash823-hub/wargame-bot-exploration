import unittest
import sqlite3
from test_regressions import DatabaseFixture
import db
from nation_deletion import delete_nation


class NationDeletionTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with db.cursor() as c:
            c.execute("UPDATE nations SET name='Cesarstwo Azteków' WHERE id=1")
            c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id) VALUES(10,1)")
        self.plans=[]
        for nid in (1,2,2,2):
            self.plans.append(db.insert_returning_id(
                "INSERT INTO battle_plans(nation_id,status) VALUES(?,'matched')",(nid,)))
        for a,b,status in ((0,1,'pending'),(0,2,'resolved'),(2,3,'resolved')):
            db.insert_returning_id("INSERT INTO battles(plan_a_id,plan_b_id,status) VALUES(?,?,?)",
                                   (self.plans[a],self.plans[b],status))

    def test_reproduces_fk_block_then_deletes_only_related_records(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with db.cursor() as c:c.execute('DELETE FROM nations WHERE id=1')
        self.assertTrue(delete_nation(1))
        with db.cursor() as c:
            c.execute('SELECT id FROM nations')
            self.assertEqual([r['id'] for r in c.fetchall()],[2])
            c.execute('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=10')
            self.assertIsNone(c.fetchone()['owner_nation_id'])
            c.execute('SELECT status FROM battle_plans WHERE id=?',(self.plans[1],))
            self.assertEqual(c.fetchone()['status'],'unmatched')
            c.execute('SELECT plan_a_id,plan_b_id FROM battles')
            remaining=c.fetchall()
            self.assertEqual(len(remaining),1)
            self.assertEqual(remaining[0]['plan_a_id'],self.plans[2])
            c.execute('PRAGMA foreign_key_check')
            self.assertEqual(c.fetchall(),[])
        self.assertFalse(delete_nation(1))

    def test_failure_rolls_back_battles_plans_and_province_ownership(self):
        with db.cursor() as c:
            c.execute("CREATE TRIGGER block_delete BEFORE DELETE ON nations BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):delete_nation(1)
        with db.cursor() as c:
            c.execute('SELECT COUNT(*) AS n FROM battles')
            self.assertEqual(c.fetchone()['n'],3)
            c.execute('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=10')
            self.assertEqual(c.fetchone()['owner_nation_id'],1)
            c.execute('SELECT status FROM battle_plans WHERE id=?',(self.plans[1],))
            self.assertEqual(c.fetchone()['status'],'matched')


if __name__=='__main__':
    unittest.main()
