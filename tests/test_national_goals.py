"""National goal progress, GM authorization and exactly-once rewards."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import patch

import config
import db
import i18n
import companies as co
import world_service as world
from cogs.economy import _seed_buildings
from cogs.world import WorldCog, goal_state
from cogs.nations import NationCog
from economy_engine import run_tick, forecast
from economy_services import build
from test_regressions import DatabaseFixture, interaction


class GoalFixture(DatabaseFixture):
    def query(self, sql, args=()):
        with db.cursor() as c:
            c.execute(sql,args)
            return c.fetchall()

    def prestige(self):
        with db.cursor() as c:return world.profile(c,1)['prestige']

    def state(self, nid=1):
        with db.cursor() as c:return co.state(c,nid)


class GoalTests(GoalFixture,unittest.TestCase):
    def test_custom_goal_milestone_renders_in_chronicle_without_private_details(self):
        from chronicle_service import render
        gid=world.gm_create_goal(1,999,'SECRET TITLE','SECRET CONDITIONS')
        world.gm_complete_goal(1,gid,999)
        rows=self.query("SELECT a.*,n.name,n.flag FROM world_activity a JOIN nations n ON n.id=a.nation_id WHERE kind='goal'")
        for language in ('pl','en'):
            embed=render(dict(language=language,report_json=json.dumps(rows),slot='2026-09-20T18:00:00+00:00'))
            self.assertIn('+10',embed.fields[0].value)
            self.assertNotIn('SECRET',str(embed.to_dict()))

    def test_live_building_and_research_progress_does_not_settle_rewards(self):
        _seed_buildings()
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=1000,resources_json=? WHERE id=1',('{"wood":500}',))
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population) VALUES(10,1,2000)')
        gid=world.choose_goal(1,1,'development')
        build(1,10,'farm');build(1,10,'farm',True)
        with i18n.using_language('en'):
            embed,_=goal_state(1)
        self.assertIn('2/2',next(f.value for f in embed.fields if f.name=='Progress'))
        self.assertEqual(self.prestige(),0)
        self.assertEqual(self.query('SELECT progress_json FROM nation_goals')[0]['progress_json'],'{}')
        world.abandon_goal(1,1,gid)
        world.choose_goal(1,1,'scholarship')
        with db.atomic() as c:
            world.activity(c,'research',1,'new-research',{'completed':True})
        with i18n.using_language('en'):
            embed,_=goal_state(1)
        self.assertIn('1/1',next(f.value for f in embed.fields if f.name=='Progress'))
        self.assertEqual(self.prestige(),0)

    def test_food_goal_includes_real_foreign_company_deliveries(self):
        _seed_buildings()
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=5000,stability=100,tech_json=?,resources_json=?',
                      ('{"economy":4}','{"food":50,"wood":1000}'))
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population) VALUES(10,1,2000)')
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,buildings_json) VALUES(20,2,2000,?)',('["farm"]',))
        co.create(1,1,'Farm company','Food abroad',['farm'])
        co.configure(1,1,self.state()['version'],1000,'off')
        grant=co.propose_concession(1,1,self.state()['version'],2,[20],['farm'],70,12,10)
        co.respond_concession(grant,2,'accept')
        co.assign(1,1,self.state()['version'],20,'farm')
        world.choose_goal(1,1,'food_security')
        preview=forecast(1)
        self.assertGreaterEqual(preview['food_months'],2)
        self.assertLess(preview['food_months']-preview['company']['received']['food']/20,2)
        self.assertEqual(self.query('SELECT progress_json FROM nation_goals')[0]['progress_json'],'{}')
        run_tick()
        progress=json.loads(self.query('SELECT progress_json FROM nation_goals')[0]['progress_json'])
        self.assertEqual(progress['count'],1)

    def test_food_streak_restarts_after_skipped_month(self):
        world.choose_goal(1,1,'food_security')
        good=dict(food_needed=20,food_shortage=0,food_months=2,stability=60)
        with db.atomic() as c:
            for month in (13,14,16):world.progress_goals(c,1,month,good)
        progress=json.loads(self.query('SELECT progress_json FROM nation_goals')[0]['progress_json'])
        self.assertEqual(progress['count'],1)
        self.assertEqual(self.prestige(),0)

    def test_custom_goal_needs_gm_and_completion_is_atomic(self):
        gid=world.gm_create_goal(1,999,'Trade agreement','Conclude an agreement with B.')
        with db.atomic() as c:
            world.activity(c,'research',1,'research',{'completed':True})
            for month in (13,14,15,16):world.progress_goals(c,1,month,{})
        self.assertEqual(self.query('SELECT status FROM nation_goals')[0]['status'],'active')
        with self.assertRaises(ValueError):world.gm_complete_goal(2,gid,999)
        self.assertEqual(self.prestige(),0)
        with patch.object(world,'activity',side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):world.gm_complete_goal(1,gid,999)
        self.assertEqual(self.prestige(),0)
        self.assertEqual(self.query('SELECT status FROM nation_goals')[0]['status'],'active')
        def confirm(_):
            try:world.gm_complete_goal(1,gid,999);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(confirm,range(2))),[False,True])
        self.assertEqual(self.prestige(),10)
        self.assertEqual(len(self.query("SELECT * FROM world_activity WHERE kind='goal'")),1)
        history=[json.loads(r['entry_text']) for r in self.query("SELECT entry_text FROM nation_history WHERE source='goal_private'")]
        self.assertEqual(history[-1]['gm_id'],'999')
        self.assertEqual(history[-1]['reward_prestige'],10)

    def test_gm_and_monthly_completion_cannot_double_reward(self):
        gid=world.choose_goal(1,1,'development')
        with db.atomic() as c:
            c.execute('UPDATE nation_goals SET start_month=9')
            world.activity(c,'building',1,'build1',{})
            world.activity(c,'building',1,'build2',{})
        def finish(gm):
            try:
                world.gm_complete_goal(1,gid,999) if gm else run_tick()
            except ValueError:
                if not gm:raise
        with ThreadPoolExecutor(2) as pool:list(pool.map(finish,[True,False]))
        self.assertEqual(self.prestige(),10)
        self.assertEqual(len(self.query("SELECT * FROM world_activity WHERE kind='goal'")),1)

    def test_validation_existing_goal_and_stale_id(self):
        for title,description in [('', 'x'),('x',' '),('x'*81,'x'),('x','x'*1501)]:
            with self.assertRaises(ValueError):world.gm_create_goal(1,999,title,description)
        with self.assertRaises(ValueError):world.gm_create_goal(999,999,'x','x')
        old=world.gm_create_goal(1,999,'First','Requirements')
        with self.assertRaises(ValueError):world.gm_create_goal(1,999,'Second','Requirements')
        with self.assertRaises(ValueError):world.choose_goal(1,1,'development')
        world.abandon_goal(1,1,old)
        new=world.choose_goal(1,1,'development')
        with self.assertRaises(ValueError):world.gm_complete_goal(1,old,999)
        self.assertEqual(self.prestige(),0)
        world.gm_complete_goal(1,new,999)  # GM may override the three-month timer.
        self.assertEqual(self.prestige(),10)


class GoalUITests(GoalFixture,unittest.IsolatedAsyncioTestCase):
    def gm(self,uid=999):
        return interaction(uid,[NS(id=20,name=config.GM_ROLE_NAME)])

    async def test_goal_conditions_in_history_are_owner_and_gm_only(self):
        world.gm_create_goal(1,999,'Private objective','SECRET CONDITIONS')
        outsider=interaction(2)
        await NationCog.history.callback(None,outsider,'A')
        self.assertNotIn('embed',outsider.response.send_message.call_args.kwargs)
        for viewer in (interaction(1),self.gm()):
            await NationCog.history.callback(None,viewer,'A')
            message=viewer.response.send_message.call_args.kwargs
            self.assertTrue(message['ephemeral'])
            self.assertIn('Private objective',message['embed'].description)

    async def test_player_cannot_create_inspect_other_nation_or_confirm(self):
        player=interaction(1)
        await WorldCog.create.callback(None,player,'A','Goal','Requirements')
        self.assertFalse(self.query('SELECT * FROM nation_goals'))
        gid=world.gm_create_goal(1,999,'Goal','Requirements')
        await WorldCog.status.callback(None,player,'A')
        await WorldCog.complete.callback(None,player,'A',gid)
        self.assertEqual(self.prestige(),0)
        self.assertTrue(all('view' not in call.kwargs for call in player.response.send_message.call_args_list))

    async def test_full_description_preview_and_role_checked_at_click(self):
        gm=self.gm()
        await WorldCog.create.callback(None,gm,'A','G'*80,'X'*1500)
        data=gm.response.send_message.call_args.kwargs
        self.assertTrue(data['ephemeral'])
        self.assertEqual(data['embed'].description.count('X'),1500)
        self.assertLessEqual(len(data['embed'].description),4096)
        gid=self.query('SELECT id FROM nation_goals')[0]['id']
        await WorldCog.complete.callback(None,gm,'A',gid)
        view=gm.response.send_message.call_args.kwargs['view']
        self.assertEqual(self.prestige(),0)
        await view.complete.callback(interaction(999))
        await view.complete.callback(self.gm(998))
        self.assertEqual(self.prestige(),0)
        await view.complete.callback(gm)
        self.assertEqual(self.prestige(),10)
        self.assertTrue(gm.response.edit_message.call_args.kwargs['view'].complete.disabled)
        await view.complete.callback(gm)
        self.assertEqual(self.prestige(),10)
        player=interaction(1)
        await WorldCog.status.callback(None,player)
        self.assertIn('X'*1500,player.response.send_message.call_args.kwargs['embed'].description)

    async def test_status_allows_gm_without_nation_and_stale_button_cannot_finish_new_goal(self):
        old=world.gm_create_goal(1,999,'Old','Requirements')
        gm=self.gm()
        await WorldCog.status.callback(None,gm,'A')
        view=gm.response.send_message.call_args.kwargs['view']
        world.abandon_goal(1,1,old)
        world.gm_create_goal(1,999,'New','New requirements')
        await view.complete.callback(gm)
        self.assertEqual(self.prestige(),0)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM nation_goals WHERE status='active'")[0]['n'],1)
