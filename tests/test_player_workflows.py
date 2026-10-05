import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch

import db
import i18n
import config
import nation_applications as applications
import war_service as wars
import treaty_service as treaties
from economy_services import submit_plan,set_posture
from test_world_features import WorldFixture
from test_regressions import interaction


def ui(uid=3,gm=False,guild=100):
    i=interaction(uid,[NS(id=20,name=config.GM_ROLE_NAME)] if gm else [])
    i.guild_id=guild;i.response.send_modal=AsyncMock();i.done=False
    async def defer(**kwargs):i.done=True
    i.response.defer=AsyncMock(side_effect=defer)
    i.response.is_done=lambda:i.done
    return i


class ApplicationFixture(WorldFixture):
    def setUp(self):
        super().setUp()
        for cell,owner in ((10,None),(11,None),(12,None),(20,1)):
            self.province(cell,owner)
        with db.cursor() as c:
            c.execute("INSERT INTO provinces(azgaar_cell_id,terrain,biome) VALUES(30,'water','marine')")
            c.execute('INSERT INTO province_neighbors(cell_id,neighbor_cell_id) VALUES(10,11)')

    def apply(self,uid=3,name='C',ids='10,11',version=None):
        return applications.submit(uid,100,name,'Lore','🏳️','Republic',ids,version)


class ApplicationTests(ApplicationFixture,unittest.TestCase):
    def test_submission_waits_without_assets_then_approval_initializes_everything_once(self):
        before=self.balances();aid=self.apply()
        self.assertEqual(self.balances(),before)
        self.assertIsNone(self.query('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=10')[0]['owner_nation_id'])
        db.init_db();self.assertEqual(applications.latest(3,100)['status'],'pending')
        nid=applications.decide(aid,100,999,0,'approve')
        n=self.query('SELECT * FROM nations WHERE id=?',(nid,))[0]
        self.assertEqual((n['owner_id'],n['population'],n['treasury']),('3',4000,620))
        self.assertEqual(json.loads(n['resources_json'])['food'],240)
        self.assertEqual(self.query('SELECT azgaar_cell_id FROM provinces WHERE id=?',(n['capital_province_id'],))[0]['azgaar_cell_id'],10)
        self.assertTrue(self.query('SELECT id FROM blueprints WHERE nation_id=?',(nid,)))
        with self.assertRaises(ValueError):applications.decide(aid,100,999,0,'approve')
        self.assertEqual(n,self.query('SELECT * FROM nations WHERE id=?',(nid,))[0])

    def test_invalid_territory_and_existing_owner_or_pending_name_do_not_create(self):
        for ids in ('30','20','999','10,12','10,10','a','','2147483648','-1'):
            with self.subTest(ids=ids),self.assertRaises(ValueError):self.apply(ids=ids)
        with self.assertRaises(ValueError):self.apply(uid=1)
        self.apply(name='Łódź')
        with self.assertRaises(ValueError):self.apply(uid=4,name='ŁÓDŹ',ids='12')
        with self.assertRaises(ValueError):self.apply()
        self.assertEqual(len(applications.pending(100)),1)

    def test_edit_invalidates_gm_preview_and_withdraw_is_owner_only(self):
        aid=self.apply();self.apply(name='New C',version=0)
        with self.assertRaises(ValueError):applications.decide(aid,100,999,0,'approve')
        with self.assertRaises(ValueError):applications.decide(aid,200,999,1,'approve')
        with self.assertRaises(ValueError):applications.decide(aid,100,4,1,'withdraw')
        applications.decide(aid,100,3,1,'withdraw')
        self.assertEqual(applications.get(aid,100,3)['status'],'withdrawn')
        self.assertGreater(self.apply(),aid)

    def test_rejection_is_visible_and_allows_new_submission(self):
        aid=self.apply()
        with self.assertRaises(ValueError):applications.decide(aid,100,999,0,'reject')
        applications.decide(aid,100,999,0,'reject','Please revise lore')
        self.assertEqual(applications.latest(3,100)['reason'],'Please revise lore')
        self.assertGreater(self.apply(),aid)

    def test_concurrent_approval_and_overlapping_land_cannot_duplicate_grants(self):
        aid=self.apply();other=self.apply(uid=4,name='D')
        def approve(aid):
            try:return applications.decide(aid,100,999,0,'approve')
            except ValueError:return None
        with ThreadPoolExecutor(3) as pool:results=list(pool.map(approve,[aid,aid,other]))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(len(self.balances()),3)

    def test_claimed_land_or_new_coop_access_blocks_approval(self):
        aid=self.apply()
        with db.cursor() as c:c.execute('UPDATE provinces SET owner_nation_id=2 WHERE azgaar_cell_id=11')
        with self.assertRaises(ValueError):applications.decide(aid,100,999,0,'approve')
        self.assertEqual(len(self.balances()),2)
        with db.cursor() as c:
            c.execute('UPDATE provinces SET owner_nation_id=NULL WHERE azgaar_cell_id=11')
            c.execute('INSERT INTO nation_coops(user_id,nation_id) VALUES(?,?)',('3',1))
        with self.assertRaises(ValueError):applications.decide(aid,100,999,0,'approve')

    def test_failed_initialization_rolls_back_nation_land_and_review(self):
        aid=self.apply()
        with patch('cogs.military.seed_nation_blueprints',side_effect=RuntimeError('failed')),self.assertRaises(RuntimeError):
            applications.decide(aid,100,999,0,'approve')
        self.assertEqual(len(self.balances()),2)
        self.assertEqual(applications.get(aid,100)['status'],'pending')
        self.assertIsNone(self.query('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=10')[0]['owner_nation_id'])


class WarFixture(WorldFixture):
    def setUp(self):
        super().setUp();self.third_nation();self.war();self.province(10,2)
        self.units={};self.plans={}
        with db.cursor() as c:
            for nid in (1,2,3):
                c.execute('INSERT INTO blueprints(nation_id,type,name,hull,stats_json) VALUES(?,?,?,?,?)',
                    (nid,'unit',f'Troops {nid}','militia','{"attack":100,"defense":100}'))
                bp=c.lastrowid
                c.execute('INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(?,?,10)',(nid,bp))
                self.units[nid]=c.lastrowid
        for nid in self.units:self.plans[nid]=submit_plan(nid,[{'unit_id':self.units[nid]}],'10',f'SECRET {nid}','')[0]

    def attack(self):return wars.challenge(1,2,self.plans[1],10)


class WarTests(WarFixture,unittest.TestCase):
    def test_two_players_settle_without_gm_and_keep_enemy_orders_private(self):
        eid=self.attack()
        self.assertEqual(self.query('SELECT status FROM battle_plans WHERE id=?',(self.plans[1],))[0]['status'],'offered')
        with patch('battle_resolution.random.uniform',return_value=1):bid,r=wars.defend(2,eid,self.plans[2])
        self.assertEqual(self.query('SELECT status FROM battles WHERE id=?',(bid,))[0]['status'],'resolved')
        self.assertTrue(r['attacker_losses']);self.assertTrue(r['defender_losses'])
        self.assertEqual(r['battlefield']['cell_id'],10)
        self.assertTrue(r['attacker_forces']);self.assertTrue(r['defender_forces'])
        self.assertEqual(wars.dashboard(1)[2][0]['status'],'resolved')
        self.assertNotIn('SECRET',json.dumps(r))
        self.assertEqual(self.query('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=10')[0]['owner_nation_id'],2)

    def test_permissions_peace_foreign_land_and_double_commit_are_checked(self):
        with self.assertRaises(ValueError):wars.challenge(3,2,self.plans[3],10)
        with self.assertRaises(ValueError):wars.challenge(1,2,self.plans[2],10)
        with self.assertRaises(ValueError):wars.challenge(1,2,self.plans[1],999)
        eid=self.attack()
        with self.assertRaises(ValueError):wars.defend(1,eid,self.plans[2])
        with self.assertRaises(ValueError):wars.defend(3,eid,self.plans[3])
        with self.assertRaises(ValueError):wars.close(3,eid)
        with self.assertRaises(ValueError):set_posture(1,self.units[1],'active')
        with self.assertRaises(ValueError):submit_plan(1,[{'unit_id':self.units[1]}],'10','Again','')

    def test_decline_expiry_peace_and_transfer_unlock_without_losses(self):
        from world_service import transfer_nation
        for action in ('decline','expire','peace','transfer'):
            with self.subTest(action=action):
                eid=self.attack()
                if action=='decline':wars.close(2,eid)
                elif action=='expire':
                    e=self.query('SELECT * FROM war_engagements WHERE id=?',(eid,))[0]
                    with db.atomic() as c:wars.expire(c,e['expires_month'])
                elif action=='peace':
                    with db.atomic() as c:treaties.set_relation(c,1,2,'peace')
                else:transfer_nation(2,4,'2',999)
                self.assertEqual(self.query('SELECT status FROM battle_plans WHERE id=?',(self.plans[1],))[0]['status'],'unmatched')
                self.assertEqual([r['quantity'] for r in self.query('SELECT quantity FROM military_units')],[10,10,10])
                with db.atomic() as c:treaties.set_relation(c,1,2,'war')

    def test_concurrent_confirmations_apply_losses_once(self):
        eid=self.attack()
        def defend(_):
            try:return wars.defend(2,eid,self.plans[2])
            except ValueError:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(defend,range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(len(self.query('SELECT * FROM battles')),1)

    def test_calendar_expires_only_at_deadline_and_rolls_back_on_failure(self):
        from economy_engine import run_month
        eid=self.attack()
        created=self.query('SELECT created_month FROM war_engagements WHERE id=?',(eid,))[0]['created_month']
        run_month(expected_month=created)
        self.assertEqual(self.query('SELECT status FROM war_engagements WHERE id=?',(eid,))[0]['status'],'pending')
        with patch('world_service.progress_goals',side_effect=RuntimeError('rollback')),self.assertRaises(RuntimeError):
            run_month(expected_month=created+1)
        self.assertEqual(self.query('SELECT status FROM war_engagements WHERE id=?',(eid,))[0]['status'],'pending')
        self.assertEqual(self.query('SELECT status FROM battle_plans WHERE id=?',(self.plans[1],))[0]['status'],'offered')
        run_month(expected_month=created+1)
        self.assertEqual(self.query('SELECT status FROM war_engagements WHERE id=?',(eid,))[0]['status'],'expired')
        self.assertEqual(self.query('SELECT status FROM battle_plans WHERE id=?',(self.plans[1],))[0]['status'],'unmatched')
        self.assertFalse(self.query('SELECT * FROM battles'))

    def test_failed_resolution_rolls_back_match_and_can_retry(self):
        eid=self.attack()
        with patch('war_service.resolution.attach_narrative',side_effect=RuntimeError('failed')),self.assertRaises(RuntimeError):
            wars.defend(2,eid,self.plans[2])
        self.assertFalse(self.query('SELECT * FROM battles'))
        self.assertEqual(self.query('SELECT status FROM war_engagements')[0]['status'],'pending')
        self.assertEqual([r['quantity'] for r in self.query('SELECT quantity FROM military_units')],[10,10,10])
        wars.defend(2,eid,self.plans[2])

    def test_joint_army_is_supported_and_frozen(self):
        import battle_coalitions as co
        with db.cursor() as c:
            c.execute('DELETE FROM battle_plans WHERE id=?',(self.plans[3],))
            c.execute("INSERT INTO relations(nation_a_id,nation_b_id,status) VALUES(1,3,'alliance')")
        co.invite(self.plans[1],1,3);co.join(self.plans[1],3,[self.units[3]])
        eid=self.attack()
        with self.assertRaises(ValueError):co.leave(self.plans[1],3)
        bid,r=wars.defend(2,eid,self.plans[2])
        self.assertEqual([n['id'] for n in r['attacker_nations']],[1,3])
        self.assertEqual(len(r['attacker_losses']),2)

    def test_removed_defender_releases_attacker_plan(self):
        from nation_deletion import delete_nation
        self.attack();delete_nation(2)
        self.assertEqual(self.query('SELECT status FROM battle_plans WHERE id=?',(self.plans[1],))[0]['status'],'unmatched')


class ApplicationUITests(ApplicationFixture,unittest.IsolatedAsyncioTestCase):
    async def test_form_submits_only_pending_and_gm_acceptance_rechecks_role_and_guild(self):
        from cogs.nations import NationCog
        from nation_application_ui import ApplicationView
        i=ui();await NationCog.found.callback(None,i)
        form=i.response.send_modal.call_args.args[0]
        j=ui();await form.submitter(j,'C','Lore','Republic','🏳️','10,11')
        aid=applications.latest(3,100)['id']
        self.assertEqual(len(self.balances()),2)
        self.assertTrue(j.followup.send.call_args.kwargs['ephemeral'])
        a=applications.get(aid,100)
        view=ApplicationView(999,100,a,True)
        await view.children[0].callback(ui(999))
        await view.children[0].callback(ui(999,True,200))
        self.assertEqual(len(self.balances()),2)
        await view.children[0].callback(ui(999,True))
        self.assertEqual(len(self.balances()),3)

    async def test_rejected_status_preview_and_paged_queue_are_private(self):
        from nation_application_ui import embed,review_queue,show
        aid=self.apply();applications.decide(aid,100,999,0,'reject','Change territory')
        i=ui();await show(i)
        self.assertIn('Change territory',str(i.followup.send.call_args.kwargs['embed'].to_dict()))
        for lang in ('pl','en'):
            with i18n.using_language(lang):
                a=applications.get(aid,100);a['history']='L'*4000;a['name']='N'*80;a['flag']='F'*512;a['government']='G'*80;a['reason']='R'*500
                a['cells_json']=json.dumps(list(range(2147483623,2147483648)));a['player_id']='9'*20;a['id']=2147483647
                self.assertLessEqual(len(embed(a)),6000)
        denied=ui();await review_queue(denied)
        self.assertIsNone(denied.response.send_message.call_args.kwargs.get('view'))

    async def test_home_panel_exposes_application_and_gm_queue(self):
        from cogs.panel import PlayerPanel
        from admin_panel import AdminPanel
        with i18n.using_language('pl'):
            labels=[getattr(x,'label','') for x in PlayerPanel(None,3,'pl').children]
            self.assertIn('Zgłoś państwo',labels);self.assertIn('Moje zgłoszenie',labels)
            labels=[getattr(x,'label','') for x in AdminPanel(None,999,section='nations').children]
            self.assertIn('Zgłoszenia państw',labels);self.assertNotIn('Utwórz państwo',labels)


class WarUITests(WarFixture,unittest.IsolatedAsyncioTestCase):
    async def test_preview_does_not_settle_and_confirm_is_defender_only(self):
        import war_ui
        eid=self.attack();i=ui(2)
        await war_ui.preview(i,eid,self.plans[2])
        view=i.followup.send.call_args.kwargs['view']
        self.assertFalse(self.query('SELECT * FROM battles'))
        self.assertNotIn('SECRET 1',str(i.followup.send.call_args))
        await view.confirm.callback(ui(1))
        self.assertFalse(self.query('SELECT * FROM battles'))
        j=ui(2);await view.confirm.callback(j)
        self.assertTrue(j.followup.send.call_args.kwargs['ephemeral'])
        self.assertEqual(len(self.query('SELECT * FROM battles')),1)

    async def test_battle_view_does_not_reveal_opponent_plan(self):
        from cogs.combat import CombatCog
        bid,r=wars.defend(2,self.attack(),self.plans[2])
        for uid,own,enemy in ((1,'SECRET 1','SECRET 2'),(2,'SECRET 2','SECRET 1')):
            i=ui(uid);await CombatCog.battle_view.callback(None,i,bid)
            shown=str(i.response.send_message.call_args.kwargs['embed'].to_dict())
            self.assertIn(own,shown);self.assertNotIn(enemy,shown)

    async def test_dashboard_defers_and_lists_next_action_after_restart(self):
        import war_ui
        self.attack();db.init_db()
        i=ui(2);await war_ui.show(i,NS())
        i.response.defer.assert_awaited_once()
        self.assertIn('incoming',str(i.followup.send.call_args.kwargs['embed'].to_dict()))
        self.assertLessEqual(len(i.followup.send.call_args.kwargs['embed']),6000)
        self.assertLessEqual(len(i.followup.send.call_args.kwargs['view'].children),25)


if __name__=='__main__':unittest.main()
