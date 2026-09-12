"""Authorization, atomic settlement and persistent world-state regression tests."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands
import db
import config
import i18n
import world_service as world
import treaty_service as treaties
import event_adventure as events
from economy_engine import run_tick, forecast
from economy_services import build, set_recurring
from trade_service import accept_trade
from cogs.economy import _seed_buildings
from cogs.nations import NationCog
from cogs.military import ShipDesignerView
from cogs.tech import TechCog
from cogs.world import WorldCog, MemoryView
from cogs.treaties import TreatiesCog, TreatyView, treaty_embed
from cogs.panel import PlayerPanel
from test_regressions import DatabaseFixture, interaction


class WorldFixture(DatabaseFixture):
    def query(self, sql, args=()):
        with db.cursor() as c:
            c.execute(sql, args)
            return c.fetchall()

    def province(self, cell, owner, buildings='[]'):
        return db.insert_returning_id(
            'INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,buildings_json) VALUES(?,?,2000,?)',
            (cell, owner, buildings))

    def third_nation(self):
        return world.create_nation(3, 'C', 'Lore', '', '', 999)

    def profile(self, nid=1):
        with db.cursor() as c: return world.profile(c, nid)

    def war(self):
        with db.atomic() as c: treaties.set_relation(c, 1, 2, 'war')


class TreatyTests(WorldFixture, unittest.TestCase):
    def test_draft_stays_private_until_author_sends_all_terms(self):
        tid=treaties.propose(1,1,2,'non_aggression',draft=True)
        with self.assertRaises(ValueError):treaties.get_treaty(tid,2)
        with self.assertRaises(ValueError):treaties.accept(tid,2,0)
        with self.assertRaises(ValueError):treaties.end(tid,2,'draft')
        treaties.amend_tribute(tid,1,10,2,'recipient')
        with self.assertRaises(ValueError):treaties.submit_proposal(tid,1,0)
        treaties.submit_proposal(tid,1,1)
        self.assertEqual(json.loads(treaties.get_treaty(tid,2)['terms_json'])['tribute_gold'],10)
        treaties.accept(tid,2,1)
        cancelled=treaties.propose(1,1,2,'alliance',draft=True)
        treaties.end(cancelled,1,'draft')
        with self.assertRaises(ValueError):treaties.get_treaty(cancelled,2)

    def test_consent_versions_and_concurrent_acceptance(self):
        before = self.balances()
        tid = treaties.propose(1, 1, 2, 'alliance', give_gold=10)
        self.assertEqual(before, self.balances())
        with self.assertRaises(ValueError): treaties.accept(tid, 1, 0)
        treaties.amend_tribute(tid, 1, 5, 2, 'recipient')
        with self.assertRaises(ValueError): treaties.accept(tid, 2, 0)
        def click(_):
            try: treaties.accept(tid, 2, 1); return True
            except ValueError: return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(click, range(2))), [False, True])
        self.assertEqual([n['treasury'] for n in self.balances()], [90, 110])
        with self.assertRaises(ValueError): treaties.end(tid, 1, 'proposed')
        self.assertEqual(treaties.get_treaty(tid, 1)['status'], 'active')
        self.assertEqual(self.profile()['reputation'], 50)

    def test_invalid_settlement_rolls_back_every_effect(self):
        self.war()
        self.province(10, 2)
        tid = treaties.propose(1, 1, 2, 'peace', receive_gold=25, receive_cells='10, 999')
        before = self.balances()
        with self.assertRaises(ValueError): treaties.accept(tid, 2, 0)
        self.assertEqual(before, self.balances())
        self.assertEqual(self.query('SELECT owner_nation_id FROM provinces')[0]['owner_nation_id'], 2)
        self.assertEqual(self.query('SELECT status FROM relations')[0]['status'], 'war')
        self.assertEqual(treaties.get_treaty(tid, 2)['status'], 'proposed')
        self.assertFalse(self.query('SELECT * FROM world_activity'))

    def test_peace_transfers_province_dependents_and_releases_routes(self):
        self.war()
        self.province(10, 1)
        pid = self.province(20, 2, '["port"]')
        uid = db.insert_returning_id('INSERT INTO military_units(nation_id,province_id,quantity) VALUES(2,?,5)', (pid,))
        with db.cursor() as c:
            c.execute('UPDATE nations SET capital_province_id=? WHERE id=2', (pid,))
            c.execute("INSERT INTO colonies(nation_id,province_id,name,status) VALUES(2,?,'Port','colony')", (pid,))
            c.execute("INSERT INTO megaprojects(nation_id,province_id,name,status) VALUES(2,?,'Dock','building')", (pid,))
            c.execute("INSERT INTO trade_routes(nation_id,name,from_cell_id,to_cell_id) VALUES(2,'Route',20,10)")
            route = c.lastrowid
            c.execute('INSERT INTO route_assignments(route_id,ship_id) VALUES(?,?)', (route,uid))
        tid = treaties.propose(1, 1, 2, 'peace', receive_gold=25, receive_cells='20')
        treaties.accept(tid, 2, 0)
        self.assertEqual([n['treasury'] for n in self.balances()], [125,75])
        self.assertEqual([n['population'] for n in self.balances()], [4000,0])
        self.assertIsNone(self.balances()[1]['capital_province_id'])
        for table in ('colonies','megaprojects'):
            self.assertEqual(self.query(f'SELECT nation_id FROM {table}')[0]['nation_id'],1)
        self.assertIsNone(self.query('SELECT province_id FROM military_units')[0]['province_id'])
        self.assertEqual(self.query('SELECT active FROM trade_routes')[0]['active'],0)
        self.assertFalse(self.query('SELECT * FROM route_assignments'))
        self.assertEqual(self.query('SELECT status FROM relations')[0]['status'],'peace')

    def test_instalments_survive_break_and_each_month_pays_once(self):
        self.war()
        tid = treaties.propose(1,1,2,'peace',tribute_gold=10,tribute_months=3)
        treaties.accept(tid,2,0)
        with db.atomic() as c: treaties.tick(c,13); treaties.tick(c,13)
        self.assertEqual([n['treasury'] for n in self.balances()],[110,90])
        treaties.end(tid,2,'active')
        with db.atomic() as c: treaties.tick(c,14); treaties.tick(c,15); treaties.tick(c,16)
        self.assertEqual([n['treasury'] for n in self.balances()],[130,70])
        self.assertEqual(self.profile(2)['reputation'],40)
        self.assertEqual(treaties.get_treaty(tid,1)['payments_left'],0)

    def test_unpaid_reparations_record_debt_without_negative_treasury(self):
        tid = treaties.propose(1,1,2,'non_aggression',tribute_gold=80,tribute_months=3)
        treaties.accept(tid,2,0)
        with db.atomic() as c: treaties.tick(c,13); treaties.tick(c,14); treaties.tick(c,15)
        t=treaties.get_treaty(tid,1)
        self.assertEqual((t['status'],t['arrears'],t['payments_left']),('broken',140,0))
        self.assertEqual([n['treasury'] for n in self.balances()],[200,0])
        self.assertEqual(self.profile(2)['reputation'],40)
        with db.atomic() as c:
            c.execute('UPDATE nations SET treasury=150 WHERE id=2')
            treaties.tick(c,16)
        self.assertEqual(treaties.get_treaty(tid,1)['arrears'],0)
        self.assertEqual(self.balances()[1]['treasury'],10)

    def test_war_breaks_all_pair_treaties_with_one_reputation_penalty(self):
        for kind in ('alliance','non_aggression','military_access'):
            treaties.accept(treaties.propose(1,1,2,kind),2,0)
        treaties.declare_war(1,1,2)
        self.assertEqual(self.profile()['reputation'],40)
        self.assertTrue(all(t['status']=='broken' for t in self.query('SELECT status FROM treaties')))
        self.assertEqual(self.query('SELECT status FROM relations')[0]['status'],'war')

    def test_military_access_requires_acceptance_and_expires(self):
        self.province(10,2)
        uid=db.insert_returning_id('INSERT INTO military_units(nation_id,quantity) VALUES(1,5)',())
        tid=treaties.propose(1,1,2,'military_access',duration=1)
        with self.assertRaises(ValueError): treaties.move_unit(1,1,uid,10)
        treaties.accept(tid,2,0)
        unit,p=treaties.move_unit(1,1,uid,10)
        self.assertEqual(unit['quantity'],5)
        with db.atomic() as c: treaties.tick(c,13)
        with self.assertRaises(ValueError): treaties.move_unit(1,1,uid,10)
        treaties.declare_war(1,1,2)
        treaties.move_unit(1,1,uid,10)

    def test_guarantee_requires_a_player_decision(self):
        self.third_nation()
        tid=treaties.propose(3,3,1,'guarantee');treaties.accept(tid,1,0)
        treaties.declare_war(2,2,1)
        call=self.query('SELECT * FROM guarantee_calls')[0]
        with db.cursor() as c: self.assertEqual(treaties.relation(c,3,2),'peace')
        with self.assertRaises(ValueError): treaties.respond_guarantee(call['id'],1,True)
        treaties.respond_guarantee(call['id'],3,True)
        with db.cursor() as c: self.assertEqual(treaties.relation(c,3,2),'war')
        self.assertEqual(self.profile(3)['reputation'],52)
        with self.assertRaises(ValueError): treaties.respond_guarantee(call['id'],3,True)

    def test_unanswered_guarantee_breaks_at_next_month_without_auto_war(self):
        self.third_nation()
        tid=treaties.propose(3,3,1,'guarantee');treaties.accept(tid,1,0)
        treaties.declare_war(2,2,1)
        with db.atomic() as c: treaties.tick(c,13);treaties.tick(c,13)
        with db.cursor() as c: self.assertEqual(treaties.relation(c,3,2),'peace')
        self.assertEqual(self.profile(3)['reputation'],40)
        self.assertEqual(treaties.get_treaty(tid,3)['status'],'broken')

    def test_private_treaty_is_not_published_or_visible_to_outsiders(self):
        tid=treaties.propose(1,1,2,'alliance',visibility='private',note='SECRET')
        treaties.accept(tid,2,0)
        self.assertFalse(self.query('SELECT * FROM world_activity'))
        with self.assertRaises(ValueError): treaties.get_treaty(tid,99)
        self.assertIn('SECRET',treaties.get_treaty(tid,99,True)['terms_json'])

    def test_invalid_terms_are_rejected_before_saving(self):
        cases=[{'give_gold':float('nan')},{'receive_gold':-1},{'give_cells':'1,1'},
               {'give_cells':'1','receive_cells':'1'},{'tribute_gold':1},
               {'tribute_gold':1,'tribute_months':13},{'note':'x'*2001}]
        for terms in cases:
            with self.subTest(terms=terms),self.assertRaises(ValueError):
                treaties.propose(1,1,2,'alliance',**terms)
        self.assertFalse(self.query('SELECT * FROM treaties'))


class IdentityGoalTests(WorldFixture,unittest.TestCase):
    def test_create_is_atomic_and_initializes_blueprints(self):
        with patch('cogs.military.seed_nation_blueprints',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError): self.third_nation()
        self.assertEqual(len(self.balances()),2)
        self.third_nation()
        self.assertTrue(self.query('SELECT * FROM blueprints WHERE nation_id=3'))
        with self.assertRaises(ValueError): world.create_nation(3,'D','Lore','','',999)
        with self.assertRaises(ValueError): world.create_nation(4,' c ','Lore','','',999)

    def test_transfer_keeps_state_and_accepted_obligations_cancels_proposals(self):
        active=treaties.propose(1,1,2,'alliance');treaties.accept(active,2,0)
        pending=treaties.propose(1,1,2,'non_aggression')
        set_recurring(self.trade_id,1);accept_trade(self.trade_id,2,monthly=True)
        pending_trade=db.insert_returning_id('INSERT INTO trades(from_nation_id,to_nation_id) VALUES(1,2)',())
        goal=world.choose_goal(1,1,'development')
        before=dict(self.balances()[0]);before['owner_id']='10'
        world.transfer_nation(1,10,1,999)
        self.assertEqual(self.balances()[0],before)
        self.assertEqual(treaties.get_treaty(active,10)['status'],'active')
        self.assertEqual(treaties.get_treaty(pending,10)['status'],'cancelled')
        self.assertEqual(self.query('SELECT status FROM trades WHERE id=?',(pending_trade,))[0]['status'],'cancelled')
        self.assertEqual(self.query('SELECT status FROM trade_contracts')[0]['status'],'active')
        self.assertEqual(self.query('SELECT id FROM nation_goals')[0]['id'],goal)
        with self.assertRaises(ValueError): world.transfer_nation(1,11,1,999)
        with self.assertRaises(ValueError): world.abandon_goal(1,1,goal)
        world.abandon_goal(1,10,goal)

    def test_development_goal_counts_new_actions_waits_three_months_and_rewards_once(self):
        _seed_buildings();self.province(10,1)
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=1000,resources_json=? WHERE id=1',('{"wood":500,"food":100}',))
        build(1,10,'farm')
        gid=world.choose_goal(1,1,'development')
        with self.assertRaises(ValueError): world.choose_goal(1,1,'scholarship')
        run_tick()
        self.assertEqual(json.loads(self.query('SELECT progress_json FROM nation_goals')[0]['progress_json'])['count'],0)
        build(1,10,'farm',True);build(1,10,'farm',True)
        run_tick();self.assertEqual(self.profile()['prestige'],0)
        forecast(1)
        self.assertEqual(self.profile()['prestige'],0)
        self.assertEqual(self.query('SELECT status FROM nation_goals WHERE id=?',(gid,))[0]['status'],'active')
        run_tick();run_tick()
        self.assertEqual(self.profile()['prestige'],10)
        self.assertEqual(len(self.query("SELECT * FROM world_activity WHERE kind='goal'")),1)

    def test_food_goal_requires_three_consecutive_months(self):
        world.choose_goal(1,1,'food_security')
        good=dict(food_needed=10,food_shortage=0,food_months=2,stability=60)
        bad=dict(good,food_shortage=1)
        with db.atomic() as c:
            for month,report in [(13,good),(14,good),(15,bad),(16,good),(17,good)]:world.progress_goals(c,1,month,report)
        self.assertEqual(self.profile()['prestige'],0)
        with db.atomic() as c:world.progress_goals(c,1,18,good);world.progress_goals(c,1,18,good)
        self.assertEqual(self.profile()['prestige'],10)

    def test_scientific_goal_needs_research_and_does_not_complete_from_drift_alone(self):
        world.choose_goal(1,1,'scholarship')
        with db.atomic() as c:
            c.execute('UPDATE nations SET tech_json=? WHERE id=1',('{"land":3.5}',))
            world.progress_goals(c,1,15,{})
        self.assertEqual(self.profile()['prestige'],0)
        with db.atomic() as c:
            world.activity(c,'research',1,'real-research',{'category':'land','completed':True})
            world.progress_goals(c,1,16,{})
        self.assertEqual(self.profile()['prestige'],10)

    def test_economy_preview_rolls_back_treaty_payment(self):
        tid=treaties.propose(1,1,2,'non_aggression',tribute_gold=10,tribute_months=1)
        treaties.accept(tid,2,0)
        forecast(1)
        self.assertEqual([n['treasury'] for n in self.balances()],[100,100])
        self.assertEqual(treaties.get_treaty(tid,1)['payments_left'],1)
        run_tick()
        self.assertEqual([n['treasury'] for n in self.balances()],[110,90])
        self.assertEqual(treaties.get_treaty(tid,1)['payments_left'],0)


class WorldUITests(WorldFixture,unittest.IsolatedAsyncioTestCase):
    async def test_creation_requires_live_gm_role_and_explicit_player(self):
        player=NS(id=3,bot=False)
        await NationCog.found.callback(None,interaction(1),player,'C','Lore')
        self.assertEqual(len(self.balances()),2)
        gm=interaction(999,[NS(id=20,name=config.GM_ROLE_NAME)])
        await NationCog.found.callback(None,gm,player,'C','L'*4000)
        self.assertEqual(self.balances()[2]['owner_id'],'3')
        embed=gm.response.send_message.call_args.kwargs['embed']
        self.assertEqual(sum(len(f.value) for f in embed.fields),4000)
        self.assertLessEqual(len(embed),6000)
        self.assertLessEqual(len(embed.description),4096)

    async def test_transfer_confirmation_rechecks_role(self):
        gm=interaction(999,[NS(id=20,name=config.GM_ROLE_NAME)])
        await NationCog.transfer.callback(None,gm,'A',NS(id=3,bot=False))
        view=gm.response.send_message.call_args.kwargs['view']
        await view.confirm.callback(interaction(999))
        self.assertEqual(self.balances()[0]['owner_id'],'1')
        await view.confirm.callback(gm)
        self.assertEqual(self.balances()[0]['owner_id'],'3')

    async def test_transfer_revokes_open_ship_design_and_research(self):
        ship=ShipDesignerView(1,'sloop','Test',2,{})
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=10000,resources_json=? WHERE id=1',('{"universal_knowledge":100}',))
        request=interaction(1)
        await TechCog.tech_research.callback(NS(bot=NS(get_channel=lambda _:None)),request,'army_logistics')
        view=request.response.send_message.call_args.kwargs['view']
        world.transfer_nation(1,10,1,999)
        before=self.balances()
        await ship._save(interaction(1))
        await view.confirm.callback(interaction(1))
        self.assertEqual(self.balances(),before)
        self.assertFalse(self.query('SELECT * FROM blueprints'))

    async def test_treaty_embed_shows_all_terms_and_stale_button_cannot_accept(self):
        tid=treaties.propose(1,1,2,'alliance',note='X'*2000)
        t=treaties.get_treaty(tid,2);e=treaty_embed(t)
        self.assertEqual(sum(f.value.count('X') for f in e.fields),2000)
        self.assertLessEqual(len(e),6000)
        view=TreatyView(2,t)
        treaties.amend_tribute(tid,1,50,2,'recipient')
        await view.accept.callback(interaction(2))
        self.assertEqual(treaties.get_treaty(tid,2)['status'],'proposed')

    async def test_panel_buttons_dispatch_to_new_cogs_and_no_self_found(self):
        cogs={'WorldCog':WorldCog(),'TreatiesCog':TreatiesCog()}
        view=PlayerPanel(NS(get_cog=lambda name:cogs[name]),1,'en')
        for action in ('goals','memories','treaties','calls'):
            i=interaction(1);i.response.is_done=lambda:False
            await view.dispatch(i,action)
            i.response.send_message.assert_awaited_once()
        no_nation=PlayerPanel(None,99,'pl')
        i=interaction(99);i.response.is_done=lambda:False
        await no_nation.dispatch(i,'stats')
        self.assertIn('Game Master',i.response.send_message.call_args.kwargs['content'])
        self.assertFalse(hasattr(no_nation,'found_modal'))

    async def new_event(self,visibility='private'):
        eid=db.insert_returning_id('INSERT INTO events(nation_id,gm_final_text,effects_json) VALUES(2,?,?)',
                                  ('Border negotiations','{"treasury":-500,"resources":{"iron":-80}}'))
        event=self.query('SELECT * FROM events WHERE id=?',(eid,))[0]
        with patch.object(events,'scene',AsyncMock(return_value=('Story',['Cautious','Balanced','Decisive']))):
            state=await events.prepare_run(event,self.balances()[1])
        state['visibility']=visibility
        return events.start_run(state)

    async def test_event_memory_saves_real_effects_once_and_enters_next_prompt(self):
        state=await self.new_event()
        async def assess(state,action,choice):return events.fallback_consequence(state,choice),'Baseline',False
        with patch.object(events,'scene',AsyncMock(return_value=('Story',['A','B','C']))),patch.object(events,'assess_consequence',side_effect=assess):
            for version in range(3):state=await events.decide(state['event_id'],version,2,choice=1)
        self.assertEqual(len(self.query('SELECT * FROM nation_memories')),1)
        remembered=world.memories(2,'Border negotiations')[0]
        self.assertEqual(remembered['outcome'],state['applied'])
        self.assertEqual(remembered['outcome']['treasury'],-100)
        self.assertEqual(remembered['outcome']['resources']['iron'],-50)
        self.assertFalse(self.query('SELECT * FROM world_activity'))
        before=self.balances();world.seed_world();world.seed_world();db.init_db()
        self.assertEqual(before,self.balances())
        self.assertEqual(len(self.query('SELECT * FROM nation_memories')),1)
        next_state=await self.new_event()
        self.assertEqual(next_state['memories'][0]['event_id'],state['event_id'])
        with patch.object(events,'_ai_json',AsyncMock(return_value={'text':'A','choices':['A','B','C']})) as ai:
            await events.scene(next_state)
        self.assertIn('Border negotiations',ai.call_args.args[0])
        self.assertIn('past_decisions',ai.call_args.args[0])

    async def test_event_transfer_moves_decisions_to_current_owner_and_guards_archive(self):
        state=await self.new_event('public')
        view=MemoryView(2,2,1,2)
        world.transfer_nation(2,20,2,999)
        self.assertFalse(await view.interaction_check(interaction(2)))
        with self.assertRaises(ValueError): await events.decide(state['event_id'],0,2,choice=1)
        async def assess(state,action,choice):return events.fallback_consequence(state,choice),'Baseline',False
        with patch.object(events,'scene',AsyncMock(return_value=('Story',['A','B','C']))),patch.object(events,'assess_consequence',side_effect=assess):
            for version in range(3):state=await events.decide(state['event_id'],version,20,choice=1)
        self.assertTrue(state['resolved'])
        self.assertEqual(self.query('SELECT payload_json FROM world_activity')[0]['payload_json'],'{}')
        self.assertEqual(self.query('SELECT source FROM nation_history')[0]['source'],'event_private')
