import json
import unittest
from unittest.mock import patch

import battle_resolution
import db
from battle_coalitions import committed,entries,invite,join,leave,match,participants
from test_regressions import DatabaseFixture


class JointBattleTests(DatabaseFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        with db.cursor() as c:
            c.execute("INSERT INTO nations(owner_id,name,treasury,resources_json) VALUES('3','C',100,'{}')")
            c.execute("INSERT INTO nations(owner_id,name,treasury,resources_json) VALUES('4','D',100,'{}')")
            c.execute("INSERT INTO relations(nation_a_id,nation_b_id,status) VALUES(1,3,'alliance')")
            self.units={}
            for nid in (1,2,3,4):
                c.execute("INSERT INTO blueprints(nation_id,type,name,hull,stats_json) VALUES(?, 'unit', ?, 'infantry', ?)",
                          (nid,f'Infantry {nid}',json.dumps({'attack':100,'defense':100})))
                bp=c.lastrowid
                c.execute('INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(?,?,10)',(nid,bp))
                self.units[nid]=c.lastrowid

    def plan(self,nid,status='unmatched'):
        return db.insert_returning_id(
            'INSERT INTO battle_plans(nation_id,forces_json,provinces_json,orders_text,status) VALUES(?,?,?,?,?)',
            (nid,json.dumps([{'unit_id':self.units[nid],'qty':10}]),'["Field"]','Advance',status))

    def test_invitation_requires_consent_and_releases_only_ally_units(self):
        pid=self.plan(1)
        invite(pid,1,3)
        with db.cursor() as c:
            c.execute('SELECT status FROM battle_plan_allies WHERE plan_id=?',(pid,))
            self.assertEqual(c.fetchone()['status'],'invited')
        with self.assertRaises(ValueError):join(pid,1,[self.units[3]])
        with self.assertRaises(ValueError):join(pid,3,[self.units[1]])
        joined=join(pid,3,[self.units[3]])
        self.assertEqual(joined['ally'],3)
        with db.cursor() as c:
            c.execute('SELECT * FROM battle_plans WHERE id=?',(pid,));plan=c.fetchone()
            self.assertEqual(participants(c,plan),{1,3})
            self.assertEqual(committed(c,self.units[3]),pid)
            c.execute('SELECT mode FROM military_posture WHERE unit_id=?',(self.units[3],))
            self.assertEqual(c.fetchone()['mode'],'deployed')
        self.assertEqual(leave(pid,3),1)
        with db.cursor() as c:
            c.execute('SELECT * FROM battle_plans WHERE id=?',(pid,));plan=c.fetchone()
            self.assertEqual(entries(plan),[{'unit_id':self.units[1],'nation_id':1,'qty':10}])
            c.execute('SELECT mode FROM military_posture WHERE unit_id=?',(self.units[3],))
            self.assertEqual(c.fetchone()['mode'],'active')

    def test_only_allies_or_common_war_partners_can_join(self):
        pid=self.plan(1)
        with self.assertRaises(ValueError):invite(pid,1,4)
        with db.cursor() as c:
            c.execute("INSERT INTO relations(nation_a_id,nation_b_id,status) VALUES(1,2,'war')")
            c.execute("INSERT INTO relations(nation_a_id,nation_b_id,status) VALUES(2,4,'war')")
        invite(pid,1,4)
        join(pid,4,[self.units[4]])
        with self.assertRaises(ValueError):invite(pid,1,3)

    def test_match_rejects_a_nation_on_both_sides(self):
        pa=self.plan(1);invite(pa,1,3);join(pa,3,[self.units[3]])
        pc=self.plan(3)
        with self.assertRaises(ValueError):match(pa,pc)

    def test_resolution_combines_power_and_tracks_each_owners_losses(self):
        pa=self.plan(1);invite(pa,1,3);join(pa,3,[self.units[3]])
        pb=self.plan(2)
        bid,_,_=match(pa,pb)
        with patch.object(battle_resolution.random,'uniform',return_value=1):
            settled=battle_resolution.resolve(bid,{},final_location='Field')
        result=settled['result']
        self.assertEqual([n['id'] for n in result['attacker_nations']],[1,3])
        self.assertEqual([x['unit_id'] for x in result['attacker_losses']],[self.units[1],self.units[3]])
        self.assertEqual(result['captives_available'],0)
        self.assertGreater(settled['atk_power'],settled['def_power'])
        with db.cursor() as c:
            c.execute("SELECT DISTINCT nation_id FROM nation_history WHERE entry_text LIKE 'Battle #%'")
            self.assertEqual({r['nation_id'] for r in c.fetchall()},{1,2,3})


if __name__=='__main__':unittest.main()
