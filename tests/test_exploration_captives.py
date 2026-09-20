import asyncio
import json
import unittest
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from concurrent.futures import ThreadPoolExecutor
from test_dynasty_labor import Fixture
from test_regressions import interaction
import db
import i18n
import config
import exploration_service as exp
import exploration_publication as pub
import captivity
import treaty_service as treaties
import labor_regimes as labor
import world_service as world
from economy_engine import forecast,run_tick


SCENE={'text':'A storm blocks the pass. What does the expedition do?','finished':False,'success':None}
SUCCESS={'text':'The expedition crossed the pass and reached its objective.','finished':True,'success':True}


class ExplorationTests(Fixture,unittest.IsolatedAsyncioTestCase):
    async def start(self):
        with patch.object(exp,'_ai_json',AsyncMock(return_value=SCENE)):
            return await exp.start(1,1,100,200,99,'We provision a small expedition to explore the northern pass.')

    async def test_story_can_resolve_after_three_replies_and_final_is_public_pending(self):
        r=await self.start()
        self.assertEqual(r['publication_status'],'waiting')
        for version,response in enumerate((SCENE,SCENE,SUCCESS)):
            with patch.object(exp,'_ai_json',AsyncMock(return_value=response)):
                r=await exp.answer(r['id'],version,1,100,'We adapt our route and scout ahead.')
        self.assertEqual((r['status'],r['version'],r['channel_id']),('resolved',3,'200'))
        self.assertEqual(r['publication_status'],'pending')
        self.assertEqual(len(json.loads(r['state_json'])['history']),3)
        with self.assertRaises(ValueError):await exp.answer(r['id'],3,1,100,'Again')
        self.assertEqual(len(self.query("SELECT * FROM nation_history WHERE source='exploration_private'")),1)

    async def test_early_failure_and_no_numeric_rewards(self):
        r=await self.start();before=self.balances()
        with patch.object(exp,'_ai_json',AsyncMock(return_value=dict(SUCCESS,success=False))):
            r=await exp.answer(r['id'],0,1,100,'We turn back because the route is impassable.')
        self.assertEqual(r['status'],'resolved')
        self.assertFalse(json.loads(r['state_json'])['success'])
        self.assertEqual(self.balances(),before)

    async def test_outage_and_invalid_narration_do_not_consume_turn(self):
        r=await self.start()
        for value in (RuntimeError('offline'),{'text':'x','finished':True,'success':'yes'}):
            mock=AsyncMock(side_effect=value) if isinstance(value,Exception) else AsyncMock(return_value=value)
            with patch.object(exp,'_ai_json',mock),self.assertRaises(ValueError):await exp.answer(r['id'],0,1,100,'We wait.')
            self.assertEqual(exp.load(r['id'],1,100)['version'],0)
        with patch.object(exp,'_ai_json',AsyncMock(return_value=SCENE)):
            await exp.answer(r['id'],0,1,100,'We scout.');await exp.answer(r['id'],1,1,100,'We navigate.')
        with patch.object(exp,'_ai_json',AsyncMock(return_value=dict(SCENE,text='x'*901))),self.assertRaises(ValueError):
            await exp.answer(r['id'],2,1,100,'We continue.')
        self.assertEqual(exp.load(r['id'],1,100)['version'],2)

    async def test_unfinished_story_continues_beyond_three_and_resumes_saved_history(self):
        r=await self.start();before=self.balances()
        for version in range(7):
            with patch.object(exp,'_ai_json',AsyncMock(return_value=SCENE)):
                r=await exp.answer(r['id'],version,1,100,f'Our next action is to survey route {version}.')
            self.assertEqual((r['status'],r['publication_status']),('active','waiting'))
            # Reload persisted state just as after a restart; no new state keys are required.
            r=exp.load(r['id'],1,100)
            self.assertEqual(r['version'],version+1)
            self.assertEqual(len(json.loads(r['state_json'])['history']),version+1)
        self.assertFalse(self.query("SELECT * FROM nation_history WHERE source='exploration_private'"))
        with patch.object(exp,'_ai_json',AsyncMock(return_value=SUCCESS)):
            r=await exp.answer(r['id'],7,1,100,'We cross the surveyed pass and reach the destination.')
        self.assertEqual((r['status'],r['version'],r['publication_status']),('resolved',8,'pending'))
        state=json.loads(r['state_json'])
        self.assertEqual(state['history'][0]['answer'],'Our next action is to survey route 0.')
        self.assertEqual(len(state['history']),8)
        self.assertEqual(self.balances(),before)
        self.assertEqual(len(self.query("SELECT * FROM nation_history WHERE source='exploration_private'")),1)
        with self.assertRaises(ValueError):await exp.answer(r['id'],8,1,100,'Again')

    async def test_pacing_prompt_shortens_scenes_and_keeps_objective_and_decisions(self):
        for count,limit in ((0,1600),(3,900),(5,600),(9,600)):
            with self.subTest(replies=count):
                state=dict(SCENE,nation='A',lang='pl',preparations='Find the northern pass.',
                           history=[{'obstacle':f'Obstacle {n}','answer':f'Action {n}'} for n in range(count)])
                mock=AsyncMock(return_value=SCENE)
                with patch.object(exp,'_ai_json',mock):await exp.narrate(state,initial=count==0)
                prompt=mock.call_args.args[0]
                self.assertIn(f'at most {limit} characters',prompt)
                self.assertIn('Polish',prompt)
                self.assertIn('Find the northern pass.',prompt)
                self.assertIn('no fixed reply limit',prompt)
                if count:
                    self.assertIn('Action 0',prompt)
                    self.assertIn(f'Action {count-1}',prompt)
                    self.assertNotIn('At reply 3 you MUST finish',prompt)
                if count>=3:
                    self.assertIn('Do not',prompt)
                    self.assertIn('side quests' if count>=5 else 'new subplots',prompt)
                    self.assertIn('decision',prompt)

    async def test_short_late_scenes_and_full_final_summary_are_validated_without_truncation(self):
        state=dict(SCENE,nation='A',lang='en',preparations='Find the northern pass.',
                   history=[{'obstacle':'The route is blocked.','answer':'Scout.'}]*5)
        with patch.object(exp,'_ai_json',AsyncMock(return_value=dict(SCENE,text='x'*600))):
            self.assertEqual(len((await exp.narrate(state))['text']),600)
        with patch.object(exp,'_ai_json',AsyncMock(return_value=dict(SCENE,text='x'*601))),self.assertRaises(ValueError):
            await exp.narrate(state)
        summary='The expedition reached the destination. '+('Its journey was difficult. '*40)
        with patch.object(exp,'_ai_json',AsyncMock(return_value=dict(SUCCESS,text=summary))):
            result=await exp.narrate(state)
        self.assertEqual(result['text'],summary.strip())
        self.assertGreater(len(result['text']),600)
        with patch.object(exp,'_ai_json',AsyncMock(return_value=dict(SUCCESS,text='x'*1601))),self.assertRaises(ValueError):
            await exp.narrate(state)

    async def test_duplicate_answers_and_owner_transfer(self):
        r=await self.start()
        with patch.object(exp,'_ai_json',AsyncMock(return_value=SCENE)):
            results=await asyncio.gather(exp.answer(r['id'],0,1,100,'We wait.'),exp.answer(r['id'],0,1,100,'We scout.'),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,ValueError) for x in results),1)
        with self.assertRaises(ValueError):exp.load(r['id'],1,101)
        with self.assertRaises(ValueError):exp.load(r['id'],2,100)
        world.transfer_nation(1,11,'1',999)
        with self.assertRaises(ValueError):exp.load(r['id'],1,100)
        self.assertEqual(exp.load(r['id'],11,100)['version'],1)

    async def test_owner_is_rechecked_after_ai_and_one_active_expedition(self):
        r=await self.start()
        with patch.object(exp,'_ai_json',AsyncMock(return_value=SCENE)):
            with self.assertRaises(ValueError):await exp.start(1,1,100,300,99,'Another expedition through the mountains.')
        async def transfer(prompt):
            world.transfer_nation(1,11,'1',999);return SUCCESS
        with patch.object(exp,'_ai_json',side_effect=transfer),self.assertRaises(ValueError):
            await exp.answer(r['id'],0,1,100,'Continue')
        self.assertEqual(exp.load(r['id'],11,100)['version'],0)

    async def test_input_lengths_and_no_outcome_during_opening(self):
        for text in ('short','a'*4001):
            with self.assertRaises(ValueError):await exp.start(1,1,100,200,99,text)
        with patch.object(exp,'_ai_json',AsyncMock(return_value=SUCCESS)),self.assertRaises(ValueError):
            await exp.start(1,1,100,200,99,'A carefully prepared expedition.')
        self.assertFalse(self.query('SELECT * FROM explorations'))

    async def test_language_follows_player_and_prompt_contains_actual_history(self):
        i18n.set_user_language(1,'pl');r=await self.start()
        mock=AsyncMock(return_value=SCENE)
        with patch.object(exp,'_ai_json',mock):await exp.answer(r['id'],0,1,100,'Przeczekujemy burzę.')
        prompt=mock.call_args.args[0]
        self.assertIn('Polish',prompt);self.assertIn('Przeczekujemy burzę.',prompt)


class PublicationTests(Fixture,unittest.IsolatedAsyncioTestCase):
    start=ExplorationTests.start
    async def setup_delivery(self,finish=True):
        r=await self.start()
        if finish:
            with patch.object(exp,'_ai_json',AsyncMock(return_value=SUCCESS)):r=await exp.answer(r['id'],0,1,100,'We cross safely.')
        role=NS(id=99,name='Game Master',mention='<@&99>',mentionable=True)
        guild=NS(id=100,roles=[role],me=NS(),get_role=lambda rid:role if rid==99 else None)
        perms=NS(view_channel=True,send_messages=True,embed_links=True,read_message_history=True,mention_everyone=False)
        channel=NS(id=200,guild=guild,permissions_for=lambda _:perms,send=AsyncMock(return_value=NS(id=999)))
        bot=NS(user=NS(id=777),get_guild=lambda gid:guild if gid==100 else None,get_channel=lambda cid:channel if cid==200 else None,fetch_channel=AsyncMock())
        return r,bot,channel,role

    async def test_original_channel_single_delivery_and_only_role_ping(self):
        r,bot,channel,role=await self.setup_delivery()
        with patch.object(config,'GM_ROLE_ID',''),patch.object(config,'GM_ROLE_NAME','Game Master'):
            await asyncio.gather(pub.publish(bot,r['id']),pub.publish(bot,r['id']))
            await pub.publish(bot,r['id'])
        channel.send.assert_awaited_once()
        args=channel.send.call_args.kwargs
        self.assertEqual(args['content'],'<@&99>')
        self.assertFalse(args['allowed_mentions'].everyone)
        self.assertFalse(args['allowed_mentions'].users)
        self.assertEqual(args['allowed_mentions'].roles,[role])
        self.assertNotIn('rules',args['embed'].description)
        self.assertEqual(exp.load(r['id'],1,100)['publication_status'],'sent')

    async def test_long_expedition_is_published_only_when_story_actually_ends(self):
        r,bot,channel,role=await self.setup_delivery(finish=False)
        for version in range(5):
            with patch.object(exp,'_ai_json',AsyncMock(return_value=SCENE)):
                r=await exp.answer(r['id'],version,1,100,'We carefully survey the remaining route.')
            await pub.publish(bot,r['id'])
        channel.send.assert_not_awaited()
        with patch.object(exp,'_ai_json',AsyncMock(return_value=SUCCESS)):
            r=await exp.answer(r['id'],5,1,100,'We cross the pass and arrive at the destination.')
        with patch.object(config,'GM_ROLE_ID',''),patch.object(config,'GM_ROLE_NAME','Game Master'):
            await pub.publish(bot,r['id'])
            await pub.publish(bot,r['id'])
        channel.send.assert_awaited_once()
        self.assertEqual(channel.send.call_args.kwargs['content'],'<@&99>')
        self.assertEqual(channel.send.call_args.kwargs['embed'].description,SUCCESS['text'])
        self.assertEqual(exp.load(r['id'],1,100)['publication_status'],'sent')

    async def test_uncertain_send_recovers_without_duplicate_ping(self):
        r,bot,channel,role=await self.setup_delivery()
        channel.send.side_effect=TimeoutError('unknown delivery')
        with patch.object(config,'GM_ROLE_ID',''),patch.object(config,'GM_ROLE_NAME','Game Master'):
            with self.assertRaises(TimeoutError):await pub.publish(bot,r['id'])
            with db.cursor() as c:c.execute('UPDATE explorations SET publication_started=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat(),r['id']))
            async def history(**kwargs):
                yield NS(id=999,author=NS(id=777),embeds=[pub.render(r)])
            channel.history=history
            await pub.publish(bot,r['id'])
        channel.send.assert_awaited_once()
        self.assertEqual(exp.load(r['id'],1,100)['publication_status'],'sent')

    async def test_missing_role_or_ping_permission_does_not_publish(self):
        r,bot,channel,role=await self.setup_delivery();role.mentionable=False
        with patch.object(config,'GM_ROLE_ID',''),patch.object(config,'GM_ROLE_NAME','Game Master'),self.assertRaises(ValueError):await pub.publish(bot,r['id'])
        channel.send.assert_not_awaited()
        self.assertEqual(exp.load(r['id'],1,100)['publication_status'],'pending')

    async def test_resuming_from_another_channel_publishes_to_original_channel(self):
        from cogs.exploration import ExplorationCog
        r,bot,channel,role=await self.setup_delivery()
        i=interaction(1);i.guild_id=100;i.channel_id=300;i.client=bot
        with patch.object(config,'GM_ROLE_ID',''),patch.object(config,'GM_ROLE_NAME','Game Master'):
            await ExplorationCog.exploration.callback(None,i,'',r['id'])
        channel.send.assert_awaited_once()
        view=i.followup.send.call_args.kwargs['view']
        self.assertEqual(len(view.children),0)
        self.assertIn('<#200>',str(i.followup.send.call_args.kwargs['embed'].to_dict()))


class CaptiveTests(Fixture,unittest.TestCase):
    def claim(self,kind='battle',capacity=10):
        with db.atomic() as c:captivity.add_opportunity(c,kind,1,1,2,capacity)
        return self.query('SELECT id FROM captive_opportunities')[0]['id']

    def slave(self):labor.change(1,1,'slavery',0,50)

    def test_single_use_population_conserved_and_abolition_frees_people(self):
        oid=self.claim();self.slave()
        before=sum(x['population'] for x in self.query('SELECT population FROM provinces'))
        captivity.take(1,1,oid,1,6)
        self.assertEqual([r['population'] for r in self.query('SELECT population FROM provinces ORDER BY azgaar_cell_id')],[2006,1994])
        self.assertEqual(sum(x['population'] for x in self.query('SELECT population FROM provinces')),before)
        self.assertEqual(self.reputation(),35)
        with self.assertRaises(ValueError):captivity.take(1,1,oid,1,1)
        with db.cursor() as c:s=labor.state(c,1)
        self.assertEqual(s['captives'],6)
        labor.change(1,1,'free',1,40.12)
        self.assertFalse(self.query('SELECT * FROM province_captives'))
        self.assertEqual(sum(x['population'] for x in self.query('SELECT population FROM provinces')),before)

    def test_policy_owner_destination_expiry_and_cost_guards(self):
        oid=self.claim()
        with self.assertRaises(ValueError):captivity.take(1,1,oid,1,5)
        self.slave();before=self.balances()
        for args in ((1,2,oid,1,1),(2,2,oid,2,1),(1,1,oid,2,1),(1,1,oid,1,11),(1,1,oid,1,0)):
            with self.assertRaises(ValueError):captivity.take(*args)
        self.assertEqual(before,self.balances())
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=0 WHERE id=1')
        with self.assertRaises(ValueError):captivity.take(1,1,oid,1,10)
        self.assertEqual(self.query('SELECT used FROM captive_opportunities')[0]['used'],0)
        run_tick(6)
        with self.assertRaises(ValueError):captivity.release(1,1,oid)

    def test_two_claim_clicks_transfer_only_once(self):
        oid=self.claim();self.slave()
        def click(_):
            try:captivity.take(1,1,oid,1,10);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(click,range(2))),[False,True])
        self.assertEqual(self.query('SELECT SUM(quantity) AS n FROM province_captives')[0]['n'],10)

    def test_battle_pool_excludes_ships_draws_and_unapplied_losses(self):
        result={'winner':'attacker','casualties_applied':True,'defender_losses':[{'unit_id':1,'lost':20},{'unit_id':2,'lost':80}]}
        with db.atomic() as c:
            self.assertEqual(captivity.battle_pool(c,1,result,1,2,{1}),5)
            self.assertEqual(captivity.battle_pool(c,2,dict(result,winner='draw'),1,2,{1}),0)
            self.assertEqual(captivity.battle_pool(c,3,dict(result,casualties_applied=False),1,2,{1}),0)
            captivity.battle_pool(c,1,result,1,2,{1})
        self.assertEqual(len(self.query('SELECT * FROM captive_opportunities')),1)

    def test_actual_battle_resolution_records_captive_subset_once(self):
        import battle_resolution as resolution
        plans=[]
        for nid in (1,2):
            bp=db.insert_returning_id('INSERT INTO blueprints(nation_id,type,name,hull,stats_json) VALUES(?,?,?,?,?)',(nid,'unit','Foot','musketeers','{"attack":100,"defense":100,"hp":100}'))
            uid=db.insert_returning_id('INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(?,?,?)',(nid,bp,100))
            plans.append(db.insert_returning_id('INSERT INTO battle_plans(nation_id,forces_json,status) VALUES(?,?,?)',(nid,json.dumps([{'unit_id':uid,'qty':100}]),'matched')))
        bid=db.insert_returning_id('INSERT INTO battles(plan_a_id,plan_b_id) VALUES(?,?)',tuple(plans))
        with patch.object(resolution.random,'uniform',return_value=1):r=resolution.resolve(bid,{})['result']
        self.assertEqual(r['winner'],'attacker')
        self.assertEqual(r['captives_available'],sum(x['lost'] for x in r['defender_losses'])//4)
        self.assertGreater(r['captives_available'],0)
        self.assertEqual(self.query('SELECT capacity FROM captive_opportunities')[0]['capacity'],r['captives_available'])
        before=self.query('SELECT * FROM military_units')
        self.slave();oid=self.query('SELECT id FROM captive_opportunities')[0]['id']
        captivity.take(1,1,oid,1,1)
        self.assertEqual(self.query('SELECT * FROM military_units'),before)

    def test_peace_winner_requires_acceptance_and_stale_terms_fail(self):
        self.war_if_needed()
        tid=treaties.propose(1,1,2,'peace')
        with self.assertRaises(ValueError):treaties.set_war_outcome(tid,2,'recipient')
        treaties.set_war_outcome(tid,1,'proposer')
        self.assertFalse(self.query('SELECT * FROM captive_opportunities'))
        with self.assertRaises(ValueError):treaties.accept(tid,2,0)
        treaties.accept(tid,2,1)
        r=self.query('SELECT * FROM captive_opportunities')[0]
        self.assertEqual((r['winner_id'],r['loser_id'],r['capacity']),(1,2,20))
        with self.assertRaises(ValueError):treaties.accept(tid,2,1)

    def war_if_needed(self):
        with db.atomic() as c:treaties.set_relation(c,1,2,'war')

    def test_peace_without_winner_gives_no_captives(self):
        self.war_if_needed();tid=treaties.propose(1,1,2,'peace');treaties.accept(tid,2,0)
        self.assertFalse(self.query('SELECT * FROM captive_opportunities'))

    def test_preview_and_population_decline_preserve_valid_captive_counts(self):
        oid=self.claim();self.slave();captivity.take(1,1,oid,1,10)
        with db.cursor() as c:c.execute('UPDATE provinces SET population=5 WHERE owner_nation_id=1')
        before=self.query('SELECT * FROM province_captives')
        forecast(1)
        self.assertEqual(self.query('SELECT * FROM province_captives'),before)
        run_tick()
        self.assertLessEqual(self.query('SELECT quantity FROM province_captives')[0]['quantity'],5)


class ExplorationUITests(Fixture,unittest.IsolatedAsyncioTestCase):
    async def test_active_view_shows_pacing_instead_of_three_reply_limit(self):
        from cogs.exploration import render,ExpeditionView
        for lang in ('pl','en'):
            for count in (0,3,5,9):
                with self.subTest(lang=lang,count=count),i18n.using_language(lang):
                    row={'id':1,'name':'A','flag':'','status':'active','version':count,
                         'state_json':json.dumps(dict(SCENE,lang=lang,history=[{}]*count))}
                    embed=render(row)
                    self.assertNotIn('/3',embed.footer.text)
                    self.assertIn(f'{count} ·',embed.footer.text)
                    self.assertEqual(len(ExpeditionView(None,row).children),1)
                    if count==3:
                        self.assertIn('Droga do finału' if lang=='pl' else 'Approaching the conclusion',embed.footer.text)
                    elif count>=5:
                        self.assertIn('Domykanie wątku' if lang=='pl' else 'Closing the story',embed.footer.text)

    async def test_text_only_modal_limits_and_publication_render_both_languages(self):
        from cogs.exploration import PreparationModal,AnswerModal,ExpeditionView
        for lang in ('pl','en'):
            with i18n.using_language(lang):
                m=PreparationModal();self.assertLessEqual(len(m.text.label),45)
                self.assertEqual(m.text.max_length,4000)
                r={'id':1,'status':'active','version':0,'state_json':json.dumps(dict(SCENE,lang=lang,history=[]))}
                v=ExpeditionView(None,r)
                self.assertEqual(len(v.children),1)
                self.assertEqual(AnswerModal(r).text.max_length,4000)

    async def test_captive_confirmation_is_required(self):
        from cogs.captives import preview
        labor.change(1,1,'slavery',0,50)
        with db.atomic() as c:captivity.add_opportunity(c,'battle',1,1,2,10)
        oid=self.query('SELECT id FROM captive_opportunities')[0]['id']
        before=self.balances();i=interaction(1)
        await preview(i,oid,1,5)
        self.assertEqual(before,self.balances())
        view=i.response.send_message.call_args.kwargs['view']
        await view.confirm.callback(i)
        self.assertEqual(self.query('SELECT quantity FROM province_captives')[0]['quantity'],5)
