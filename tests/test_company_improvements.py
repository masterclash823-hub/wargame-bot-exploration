import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands
import db
import i18n
import companies as co
import company_message as messages
from cogs.companies import CompanyCog, review, project_embed
from cogs.economy import _seed_buildings
from economy_engine import forecast, run_month
from test_regressions import DatabaseFixture, interaction


TEXT='We reorganise the mills with better tools, training and documented maintenance.'


def request(uid=1):
    i=interaction(uid)
    i.guild_id=123
    i.guild.id=123
    i.response.is_done=lambda: i.response.defer.await_count > 0
    i.response.send_modal=AsyncMock()
    return i


def message(uid=1,text=TEXT):
    return NS(id=456,guild=NS(id=123),channel=NS(id=789),author=NS(id=uid,bot=False),content=text)


class ImprovementTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=5000,stability=100,tech_json=?,resources_json=?',
                      (json.dumps({'economy':4,'land':4,'naval':4,'colonial':4}),json.dumps({'food':500,'wood':2000,'coal':100,'copper':100})))
            c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,terrain,population,buildings_json) VALUES(10,1,'plains',2000,'[\"powder_mill\"]')")
        co.create(1,1,'Mills','National industry',['farm','powder_mill','algae_farm'])
        co.assign(1,1,self.state()['version'],10,'powder_mill')

    def state(self):
        with db.cursor() as c:return co.state(c,1)

    def propose(self,key='powder_mill',text=TEXT):
        return co.improvement(1,1,self.state()['version'],key,text)

    async def test_message_command_snapshots_full_text_and_source(self):
        text=('Detailed idea\n\n'+('A long description. '*190)).strip()
        i18n.set_user_language(1,'pl')
        i=request()
        original=message(text=text)
        cog=CompanyCog()
        await cog.improve_message(i,original)
        menu=i.followup.send.call_args.kwargs['view']
        original.content='Edited after submission'
        chosen=request()
        await menu.handler(chosen,'powder_mill')
        p=self.state()['improvements'][0]
        self.assertEqual(p['description'],text)
        self.assertEqual(p['source']['message_id'],'456')
        self.assertEqual(p['effects'],{})
        with i18n.using_language('pl'):
            embed=project_embed(self.state(),p)
        self.assertEqual(embed.description,text)
        self.assertIn('ustali GM',embed.fields[0].value)
        menu.stop()

    async def test_foreign_author_and_cross_server_messages_cannot_be_submitted(self):
        with self.assertRaises(ValueError):messages.message_proposal(message(uid=2),1,123)
        with self.assertRaises(ValueError):messages.message_proposal(message(),1,999)
        with self.assertRaises(ValueError):messages.message_proposal(message(text='x'*4001),1,123)
        denied=request()
        await CompanyCog().improve_message(denied,message(uid=2))
        self.assertEqual(self.state()['improvements'],[])
        denied.response.send_message.assert_awaited_once()

    async def test_slash_message_link_checks_visibility_before_reading(self):
        i=request()
        channel=NS(guild=i.guild,permissions_for=lambda u:NS(view_channel=False,read_message_history=False),fetch_message=AsyncMock(return_value=message()))
        i.client=NS(get_channel=lambda _:channel)
        with self.assertRaises(ValueError):await messages.from_link(i,'https://discord.com/channels/123/789/456')
        channel.fetch_message.assert_not_awaited()
        channel.permissions_for=lambda u:NS(view_channel=True,read_message_history=True)
        await CompanyCog.company_improve.callback(None,i,'powder_mill','https://discord.com/channels/123/789/456')
        i.response.defer.assert_awaited_once()
        self.assertEqual(self.state()['improvements'][0]['source']['author_id'],'1')

    async def test_prefix_reply_requires_activated_server_and_saves_idea(self):
        ctx=NS(author=NS(id=1),guild=NS(id=123),channel=NS(id=789,fetch_message=AsyncMock(return_value=message())),
               message=NS(reference=NS(message_id=456,channel_id=789)),reply=AsyncMock())
        await CompanyCog.improvement_reply.callback(None,ctx,'powder_mill')
        self.assertEqual(self.state()['improvements'],[])
        ctx.channel.fetch_message.assert_not_awaited()
        with db.cursor() as c:c.execute("INSERT INTO game_config(key,value) VALUES('guild_active_123','1')")
        await CompanyCog.improvement_reply.callback(None,ctx,'powder_mill')
        self.assertEqual(self.state()['improvements'][0]['description'],TEXT)
        self.assertFalse(ctx.reply.call_args.kwargs['mention_author'])
        # Replaying a command cannot create or award a duplicate.
        await CompanyCog.improvement_reply.callback(None,ctx,'powder_mill')
        self.assertEqual(len(self.state()['improvements']),1)

    async def test_gm_decision_checks_role_on_modal_submit_and_records_mixed_effects(self):
        identifier=self.propose()
        i=request(99)
        with patch('cogs.companies.gm_only',return_value=True):await review(i)
        menu=i.response.send_message.call_args.kwargs['view']
        selected=request(99)
        with patch('cogs.companies.gm_only',return_value=True):await menu.handler(selected,'1:'+identifier)
        controls=selected.response.send_message.call_args.kwargs['view']
        opened=request(99)
        with patch('cogs.companies.gm_only',return_value=True):await controls.children[0].callback(opened)
        modal=opened.response.send_modal.call_args.args[0]
        self.assertEqual(len(modal.children),5)
        revoked=request(99)
        with patch('cogs.companies.gm_only',return_value=False):
            await modal.submitter(revoked,'10','5','0','-5','0')
        self.assertEqual(self.state()['improvements'][0]['status'],'proposed')
        accepted=request(99)
        with patch('cogs.companies.gm_only',return_value=True):
            await modal.submitter(accepted,'10','5','0','-5','0')
        p=self.state()['improvements'][0]
        self.assertEqual(p['effects'],{'production':10,'inputs':5,'construction':-5})
        self.assertEqual(p['gm_id'],'99')
        with self.assertRaises(ValueError):co.review(1,identifier,True,99,{'production':50})
        menu.stop();controls.stop()

    async def test_negative_effects_change_actual_output_and_inputs_after_completion(self):
        identifier=self.propose()
        before=forecast(1)
        co.review(1,identifier,True,99,{'production':-20,'inputs':10,'maintenance':20})
        self.assertEqual(forecast(1)['production'],before['production'])
        co.start_improvement(1,1,self.state()['version'],identifier,True)
        with self.assertRaises(ValueError):co.start_improvement(1,1,self.state()['version'],identifier,True)
        run_month()
        self.assertEqual(self.state()['improvements'][0]['status'],'running')
        run_month()
        self.assertEqual(self.state()['improvements'][0]['status'],'complete')
        after=forecast(1)
        self.assertLess(after['production']['gunpowder'],before['production']['gunpowder'])
        self.assertLess(after['production']['coal'],before['production']['coal'])
        self.assertGreater(after['upkeep'],before['upkeep'])
        self.assertAlmostEqual(after['production']['gunpowder']/before['production']['gunpowder'],.85/1.05)

    async def test_validation_stale_review_and_legacy_improvements(self):
        identifier=self.propose('algae_farm')
        for effects in ({},{'production':10},{'construction':float('nan')},{'maintenance':51}):
            with self.assertRaises(ValueError):co.review(1,identifier,True,99,effects)
        version=self.state()['version']
        co.pause(1,1,version)
        with self.assertRaises(ValueError):co.review(1,identifier,True,99,{'maintenance':-5},version)
        co.review(1,identifier,True,99,{'maintenance':-5})
        p=self.state()['improvements'][0]
        self.assertEqual(p['effects'],{'maintenance':-5})
        s=self.state();s['paused']=False
        s['improvements']=[dict(status='complete',building='farm',kind='production',amount=.5),
                           dict(status='complete',building='farm',effects={'production':-10})]
        self.assertAlmostEqual(co.bonus({'tech_json':'{"economy":4}'},s,'farm')['production'],.05)
        for lang in ('pl','en'):
            with i18n.using_language(lang):
                legacy=dict(id='old',status='approved',building='farm',kind='inputs',amount=.05,
                            description=TEXT,cost={'gold':100},months=2)
                self.assertIn('-5%',project_embed(s,legacy).fields[0].value)

    async def test_context_menu_registers_unloads_and_uses_activation_gate(self):
        import bot as main
        bot=commands.Bot(command_prefix='!',intents=discord.Intents.default())
        async with bot:
            cog=CompanyCog(bot)
            await bot.add_cog(cog)
            context=bot.tree.get_command('company_improve',type=discord.AppCommandType.message)
            slash=bot.tree.get_command('company_improve')
            self.assertIsNotNone(context)
            self.assertIsNotNone(slash)
            i=request();i.command=context
            self.assertFalse(await main.global_guild_check(i))
            await bot.remove_cog('CompanyCog')
            self.assertIsNone(bot.tree.get_command('company_improve',type=discord.AppCommandType.message))


if __name__=='__main__':unittest.main()
