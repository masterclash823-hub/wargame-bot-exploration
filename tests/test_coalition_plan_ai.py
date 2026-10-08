import asyncio
import json
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import battle_coalitions as coalitions
import battle_plan_text
import battle_resolution as resolution
import config
import db
import treaty_service
import war_service as wars
from cogs import combat
from test_player_workflows import WarFixture, ui


class CoalitionPlanAITests(WarFixture, unittest.IsolatedAsyncioTestCase):
    def join(self,leader=1):
        with db.cursor() as c:
            c.execute('DELETE FROM battle_plans WHERE id=?',(self.plans[3],))
            c.execute("INSERT INTO relations(nation_a_id,nation_b_id,status) VALUES(?,3,'alliance')",(leader,))
        coalitions.invite(self.plans[leader],leader,3)
        coalitions.join(self.plans[leader],3,[self.units[3]])

    def assessment(self):
        return dict(attacker_modifier=1.3,defender_modifier=.9,reasoning='SECRET assessment',
                    attacker_exposure={str(self.units[1]):dict(weight=.25,reason='SECRET rear'),
                                       str(self.units[3]):dict(weight=4,reason='SECRET flank')})

    def unsettled(self,eid):
        self.assertFalse(self.query('SELECT * FROM battles'))
        self.assertEqual(self.query('SELECT status FROM war_engagements WHERE id=?',(eid,))[0]['status'],'pending')
        self.assertEqual(self.query('SELECT status FROM battle_plans WHERE id=?',(self.plans[1],))[0]['status'],'offered')
        self.assertEqual([x['quantity'] for x in self.query('SELECT quantity FROM military_units ORDER BY id')],[10,10,10])

    async def test_ai_receives_full_coalition_plan_and_bonus_applies_to_whole_army(self):
        self.join()
        orders='SECRET flanking orders\n'*4000
        with db.cursor() as c:
            c.execute('UPDATE battle_plans SET orders_text=? WHERE id=?',
                      (battle_plan_text.pack(orders,'10','Allied infantry'),self.plans[1]))
            c.execute('UPDATE nations SET tech_json=? WHERE id=3',('{"land":6,"naval":2}',))
        eid=self.attack()
        context=wars._prepare_defense(2,eid,self.plans[2])
        with db.atomic() as c:
            attack=resolution._power(c,context['plans'][0],context['nations'][0][0])[0]
            defense=resolution._power(c,context['plans'][1],context['nations'][1][0])[1]

        def generate(**kwargs):
            self.assertIsNone(db._transaction.get())
            return NS(text=json.dumps(self.assessment()))
        generate=Mock(side_effect=generate)
        client=Mock(return_value=NS(models=NS(generate_content=generate)))
        with patch.dict(sys.modules,{'google':NS(genai=NS(Client=client))}),patch('battle_resolution.random.uniform',return_value=1):
            bid,report=await wars.defend_with_ai(2,eid,self.plans[2])
        client.assert_called_once_with(api_key=config.GEMINI_API_KEY)
        self.assertEqual(generate.call_args.kwargs['model'],config.GEMINI_MODEL)
        prompt=generate.call_args.kwargs['contents']
        self.assertIn(orders,prompt)
        for uid in self.units.values():self.assertIn(f'"unit_id": {uid}',prompt)
        self.assertIn('"land": 6',prompt)
        self.assertIn('Allied infantry',prompt)
        self.assertIn('"cell_id": 10',prompt)
        self.assertEqual(report['eff_attack'],round(attack*1.3,1))
        self.assertEqual(report['eff_defense'],round(defense*.9,1))
        self.assertEqual(report['tactical_modifiers'],{'attacker':1.3,'defender':.9})
        self.assertEqual([n['id'] for n in report['attacker_nations']],[1,3])
        losses={x['unit_id']:x['lost'] for x in report['attacker_losses']}
        self.assertGreater(losses[self.units[3]],losses[self.units[1]])
        for unit,lost in losses.items():
            self.assertEqual(self.query('SELECT quantity FROM military_units WHERE id=?',(unit,))[0]['quantity'],10-lost)
        saved=json.loads(self.query('SELECT ai_modifier_json FROM battles WHERE id=?',(bid,))[0]['ai_modifier_json'])
        self.assertEqual(saved['attacker_modifier'],1.3)

    async def test_defender_coalition_also_activates_review_for_both_sides(self):
        self.join(2)
        ai=AsyncMock(return_value=dict(attacker_modifier=.7,defender_modifier=1.4,reasoning='Defense plan'))
        with patch.object(combat,'_get_ai_modifier',ai),patch('battle_resolution.random.uniform',return_value=1):
            _,report=await wars.defend_with_ai(2,self.attack(),self.plans[2])
        self.assertEqual([n['id'] for n in report['defender_nations']],[2,3])
        self.assertEqual(report['tactical_modifiers'],{'attacker':.7,'defender':1.4})
        self.assertEqual(len(ai.call_args.args[5]),1)
        self.assertEqual(len(ai.call_args.args[6]),2)
        self.assertEqual(report['winner'],'defender')
        self.assertEqual(len(report['defender_losses']),2)

    async def test_double_confirmation_uses_one_ai_review_and_one_settlement(self):
        self.join();eid=self.attack()
        entered=asyncio.Event();release=asyncio.Event()
        async def assess(*args,**kwargs):
            self.assertIsNone(db._transaction.get())
            entered.set();await release.wait()
            return self.assessment()
        ai=AsyncMock(side_effect=assess)
        with patch.object(combat,'_get_ai_modifier',ai):
            first=asyncio.create_task(wars.defend_with_ai(2,eid,self.plans[2]))
            try:
                await asyncio.wait_for(entered.wait(),2)
                with self.assertRaisesRegex(ValueError,'already being reviewed'):
                    await wars.defend_with_ai(2,eid,self.plans[2])
                self.unsettled(eid)
            finally:release.set()
            await first
            with self.assertRaises(ValueError):await wars.defend_with_ai(2,eid,self.plans[2])
        ai.assert_awaited_once()
        self.assertEqual(len(self.query('SELECT * FROM battles')),1)
        self.assertNotIn(eid,wars._active_reviews)

    async def test_changed_units_plan_terrain_technology_or_owner_require_new_review(self):
        self.join();eid=self.attack()
        edits=[('military_units','quantity',self.units[3],9),
               ('battle_plans','orders_text',self.plans[2],battle_plan_text.pack('Changed','10','')),
               ('provinces','fortification_level',self.query('SELECT id FROM provinces WHERE azgaar_cell_id=10')[0]['id'],3),
               ('nations','tech_json',3,'{"land":7}'),('nations','owner_id',3,'44')]
        for table,column,key,value in edits:
            old=self.query(f'SELECT {column} FROM {table} WHERE id=?',(key,))[0][column]
            async def assess(*args,**kwargs):
                with db.cursor() as c:c.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(value,key))
                return self.assessment()
            with self.subTest(column=column),patch.object(combat,'_get_ai_modifier',AsyncMock(side_effect=assess)):
                with self.assertRaisesRegex(ValueError,'changed during AI review'):
                    await wars.defend_with_ai(2,eid,self.plans[2])
                with db.cursor() as c:c.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(old,key))
                self.unsettled(eid)
        with patch.object(combat,'_get_ai_modifier',AsyncMock(return_value=self.assessment())):
            await wars.defend_with_ai(2,eid,self.plans[2])

    async def test_peace_during_ai_review_cancels_without_losses(self):
        self.join();eid=self.attack()
        async def assess(*args,**kwargs):
            with db.atomic() as c:treaty_service.set_relation(c,1,2,'peace')
            return self.assessment()
        with patch.object(combat,'_get_ai_modifier',AsyncMock(side_effect=assess)),self.assertRaises(ValueError):
            await wars.defend_with_ai(2,eid,self.plans[2])
        self.assertEqual(self.query('SELECT status FROM war_engagements')[0]['status'],'cancelled')
        self.assertFalse(self.query('SELECT * FROM battles'))
        self.assertEqual([x['quantity'] for x in self.query('SELECT quantity FROM military_units')],[10,10,10])

    async def test_invalid_or_unavailable_ai_leaves_challenge_pending_and_retry_works(self):
        self.join();eid=self.attack()
        responses=['{}','[]','not JSON','{"attacker_modifier":true,"defender_modifier":1,"reasoning":"x"}',
                   '{"attacker_modifier":NaN,"defender_modifier":1,"reasoning":"x"}',
                   TimeoutError('timeout'),RuntimeError('offline')]
        for response in responses:
            generate=Mock(side_effect=response) if isinstance(response,Exception) else Mock(return_value=NS(text=response))
            google=NS(genai=NS(Client=Mock(return_value=NS(models=NS(generate_content=generate)))))
            with self.subTest(response=str(response)),patch.dict(sys.modules,{'google':google}):
                with self.assertRaisesRegex(ValueError,'has not been settled'):
                    await wars.defend_with_ai(2,eid,self.plans[2])
                self.unsettled(eid)
                self.assertNotIn(eid,wars._active_reviews)
        with patch.object(combat,'_get_ai_modifier',AsyncMock(return_value=self.assessment())):
            await wars.defend_with_ai(2,eid,self.plans[2])

    async def test_cancelled_review_can_be_retried_without_partial_battle(self):
        self.join();eid=self.attack()
        with patch.object(combat,'_get_ai_modifier',AsyncMock(side_effect=asyncio.CancelledError)),self.assertRaises(asyncio.CancelledError):
            await wars.defend_with_ai(2,eid,self.plans[2])
        self.unsettled(eid)
        with patch.object(combat,'_get_ai_modifier',AsyncMock(return_value=self.assessment())):
            await wars.defend_with_ai(2,eid,self.plans[2])

    async def test_solo_battle_reviews_both_plans_and_modifiers_change_the_winner(self):
        eid=self.attack()
        assessment=dict(attacker_modifier=.7,defender_modifier=1.4,reasoning='Defense plan')
        with patch.object(combat,'_get_ai_modifier',AsyncMock(return_value=assessment)) as ai,patch('battle_resolution.random.uniform',return_value=1):
            with self.assertRaises(ValueError):await wars.defend_with_ai(1,eid,self.plans[2])
            ai.assert_not_awaited()
            bid,report=await wars.defend_with_ai(2,eid,self.plans[2])
        ai.assert_awaited_once()
        self.assertEqual(ai.call_args.args[0]['orders_text'],'SECRET 1')
        self.assertEqual(ai.call_args.args[1]['orders_text'],'SECRET 2')
        self.assertEqual([len(side) for side in ai.call_args.args[5:7]],[1,1])
        self.assertTrue(ai.call_args.kwargs['required'])
        self.assertEqual(report['tactical_modifiers'],{'attacker':.7,'defender':1.4})
        self.assertEqual(report['winner'],'defender')
        saved=json.loads(self.query('SELECT ai_modifier_json FROM battles WHERE id=?',(bid,))[0]['ai_modifier_json'])
        self.assertEqual(saved['attacker_modifier'],.7)

    async def test_solo_battle_cannot_settle_without_ai_and_can_retry_after_ai_error(self):
        eid=self.attack()
        with self.assertRaisesRegex(ValueError,'AI review'):wars.defend(2,eid,self.plans[2])
        self.unsettled(eid)
        for generate in (Mock(return_value=NS(text='{}')),Mock(side_effect=TimeoutError('timeout'))):
            google=NS(genai=NS(Client=Mock(return_value=NS(models=NS(generate_content=generate)))))
            with patch.dict(sys.modules,{'google':google}),self.assertRaisesRegex(ValueError,'has not been settled'):
                await wars.defend_with_ai(2,eid,self.plans[2])
            self.unsettled(eid)
        with patch.object(combat,'_get_ai_modifier',AsyncMock(return_value=self.assessment())):
            await wars.defend_with_ai(2,eid,self.plans[2])

    async def test_confirmation_and_reports_show_bonuses_without_enemy_orders_or_ai_reasons(self):
        import war_ui
        self.join();eid=self.attack();i=ui(2)
        await war_ui.preview(i,eid,self.plans[2])
        view=i.followup.send.call_args.kwargs['view']
        confirm=ui(2)
        with patch.object(combat,'_get_ai_modifier',AsyncMock(return_value=self.assessment())):
            await view.confirm.callback(confirm)
        confirm.response.defer.assert_awaited_once_with(ephemeral=True)
        shown=str(confirm.followup.send.call_args.kwargs['embed'].to_dict())
        self.assertIn('ATK ×1.30 | DEF ×0.90',shown)
        self.assertNotIn('SECRET',shown)
        bid=self.query('SELECT id FROM battles')[0]['id']
        for uid,enemy in ((1,'SECRET 2'),(2,'SECRET 1'),(3,'SECRET 2')):
            viewer=ui(uid)
            await combat.CombatCog.battle_view.callback(None,viewer,bid)
            shown=str(viewer.response.send_message.call_args.kwargs['embed'].to_dict())
            self.assertIn('ATK ×1.30 | DEF ×0.90',shown)
            self.assertNotIn(enemy,shown)
            self.assertNotIn('SECRET assessment',shown)
            self.assertNotIn('SECRET flank',shown)


if __name__=='__main__':unittest.main()
