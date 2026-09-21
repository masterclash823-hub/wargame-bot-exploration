import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import discord
from test_regressions import DatabaseFixture, interaction
import config
import db
import i18n
import rankings
from cogs.rankings import RankingsCog
from cogs.economy import _seed_buildings
from economy_engine import DEFAULT_POLICY, forecast, run_tick, save_policy
from economy_services import set_recurring
from trade_service import accept_trade


class RankingTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        for key,value in [('GM_ROLE_ID',''),('GM_ROLE_NAME','Game Master')]:
            p=patch.object(config,key,value);p.start();self.addCleanup(p.stop)
        self.gm=interaction(999,[NS(id=9,name='Game Master')])
        self.gm.guild.id=10;self.gm.guild.me=NS(id=500)
        self.channel=NS(guild=self.gm.guild,mention='<#100>',send=AsyncMock(),
                        permissions_for=lambda member:NS(view_channel=True,send_messages=True,embed_links=True,attach_files=True))

    def database_dump(self):
        conn=db._sqlite_conn()
        try:return '\n'.join(conn.iterdump())
        finally:conn.close()

    def scores(self,category):return {r['id']:r['value'] for r in rankings.load(category)['rows']}

    def unit(self,nid,kind,stats,qty,mode=None):
        bid=db.insert_returning_id('INSERT INTO blueprints(nation_id,type,name,hull,stats_json) VALUES(?,?,?,?,?)',
                                  (nid,kind,'Design','sloop' if kind=='ship' else 'infantry',json.dumps(stats)))
        uid=db.insert_returning_id('INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(?,?,?)',(nid,bid,qty))
        if mode:
            with db.cursor() as c:c.execute('INSERT INTO military_posture(unit_id,mode) VALUES(?,?)',(uid,mode))
        return uid

    async def test_current_provinces_colonies_and_fallen_nations(self):
        with db.cursor() as c:
            c.execute('UPDATE nations SET population=99999 WHERE id=1')
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population) VALUES(10,1,500)')
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population) VALUES(20,1,300)')
            c.execute("INSERT INTO colonies(nation_id,province_id,name,status) SELECT 1,id,'Colony','outpost' FROM provinces WHERE azgaar_cell_id=20")
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,active) VALUES(30,1,9000,0)')
            c.execute('INSERT INTO provinces(azgaar_cell_id,population) VALUES(40,7000)')
            c.execute("INSERT INTO nation_decay(nation_id,status,started_month,due_month,gm_id,former_owner) VALUES(2,'ruins',12,15,'999','2')")
        self.assertEqual(self.scores('population'),{1:800})
        self.assertEqual(self.scores('provinces'),{1:2})
        with db.cursor() as c:c.execute("UPDATE nation_decay SET status='decaying' WHERE nation_id=2")
        self.assertEqual(self.scores('population'),{1:800,2:0})

    async def test_prestige_fractional_technology_and_deterministic_ties(self):
        with db.cursor() as c:
            c.execute('INSERT INTO nation_profiles(nation_id,prestige) VALUES(1,30)')
            c.execute('UPDATE nations SET tech_json=? WHERE id=1',('{"economy":4.5}',))
            c.execute("INSERT INTO nations(owner_id,name,treasury) VALUES('3','C',-10)")
        self.assertEqual(self.scores('prestige'),{1:30,2:0,3:0})
        self.assertEqual(self.scores('technology')[1],3.38)
        result=rankings.load('treasury')['rows']
        self.assertEqual([(r['id'],r['place']) for r in result],[(1,1),(2,1),(3,3)])
        rows=rankings.rank_rows([dict(id=2,name='B',value=1.001),dict(id=1,name='A',value=1.002)])
        self.assertEqual([(r['id'],r['place']) for r in rows],[(1,1),(2,1)])

    async def test_income_uses_one_world_forecast_and_rolls_everything_back(self):
        with db.cursor() as c:
            c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,buildings_json) VALUES(10,1,2000,?)',('["farm"]',))
        set_recurring(self.trade_id,1);accept_trade(self.trade_id,2,monthly=True)
        before=self.database_dump();balances={n['id']:n['treasury'] for n in self.balances()}
        with patch('rankings.forecast',wraps=forecast) as generate:
            result=self.scores('income')
        generate.assert_called_once_with()
        self.assertEqual(self.database_dump(),before)
        run_tick()
        for n in self.balances():self.assertAlmostEqual(result[n['id']],round(n['treasury']-balances[n['id']],2))

    async def test_military_uses_battle_bonuses_morale_and_readiness(self):
        with db.cursor() as c:
            c.execute('UPDATE nations SET tech_json=? WHERE id=1',('{"land":4,"naval":6}',))
            c.execute("INSERT INTO research_discoveries(nation_id,code,completed_month) VALUES(1,'field_drill',12)")
            save_policy(c,1,{**DEFAULT_POLICY,'unpaid_months':2})
        ready=self.unit(1,'unit',{'attack':5,'defense':3},10)
        self.unit(1,'unit',{'attack':500,'defense':300},100,'reserve')
        self.unit(1,'unit',{'attack':500,'defense':300},100,'mobilizing')
        self.unit(1,'ship',{'attack':10,'hp':200},2,'deployed')
        merchant=self.unit(1,'ship',{'attack':500,'hp':3000},100)
        route=db.insert_returning_id('INSERT INTO trade_routes(nation_id,from_cell_id,to_cell_id) VALUES(1,10,20)',())
        with db.cursor() as c:c.execute('INSERT INTO route_assignments(route_id,ship_id) VALUES(?,?)',(route,merchant))
        before=self.database_dump()
        self.assertAlmostEqual(self.scores('army')[1],79.2)  # (5*1.05 + 3) * 10 * 1.2 * .8
        self.assertAlmostEqual(self.scores('navy')[1],62.4)  # (10 + 200*.1) * 2 * 1.3 * .8
        self.assertEqual(self.database_dump(),before)
        # PostgreSQL previews must not issue FOR UPDATE; SQLite rejects that SQL.
        from battle_resolution import _power
        with db.cursor() as c:
            c.execute('SELECT * FROM nations WHERE id=1');nation=c.fetchone()
            with patch.object(db,'USE_POSTGRES',True):
                attack,defense,_=_power(c,{'forces_json':json.dumps([{'unit_id':ready,'qty':10}])},nation,lock_units=False)
        self.assertAlmostEqual(attack+defense,79.2)

    async def test_splitting_units_cannot_increase_rank(self):
        self.unit(1,'unit',{'attack':5,'defense':3},10)
        for _ in range(10):self.unit(2,'unit',{'attack':5,'defense':3},1)
        rows=rankings.load('army')['rows']
        self.assertEqual([r['place'] for r in rows],[1,1])
        self.assertEqual([r['value'] for r in rows],[92,92])

    async def test_gm_role_is_required_before_any_ranking_or_publication(self):
        with patch('rankings.load') as load:
            await RankingsCog.ranking.callback(None,interaction(2),'treasury',10,self.channel)
        load.assert_not_called();self.channel.send.assert_not_called()
        for case in ('foreign','permissions'):
            if case=='foreign':self.channel.guild=NS(id=999)
            else:
                self.channel.guild=self.gm.guild
                self.channel.permissions_for=lambda _:NS(view_channel=True,send_messages=True,embed_links=True,attach_files=False)
            with patch('rankings.load') as load:
                await RankingsCog.ranking.callback(None,self.gm,'treasury',10,self.channel)
            load.assert_not_called();self.channel.send.assert_not_called()

    async def test_private_preview_and_explicit_public_channel_in_gm_language(self):
        for language,word in [('pl','Ranking państw'),('en','Nation ranking')]:
            i18n.set_user_language(999,language)
            await RankingsCog.ranking.callback(None,self.gm)
            message=self.gm.followup.send.call_args.kwargs
            self.assertTrue(message['ephemeral']);self.assertIn(word,message['embed'].title)
            self.assertIsNone(message['embed'].thumbnail.url)
            self.channel.send.assert_not_called()
        i18n.set_user_language(999,'pl')
        await RankingsCog.ranking.callback(None,self.gm,'population',20,self.channel)
        self.channel.send.assert_awaited_once()
        sent=self.channel.send.call_args.kwargs
        self.assertIn('Ludność',sent['embed'].title)
        self.assertEqual(sent['allowed_mentions'].to_dict(),{'parse':[]})
        self.assertTrue(self.gm.followup.send.call_args.kwargs['ephemeral'])

    async def test_full_attachment_long_names_negative_values_and_embed_limits(self):
        rows=[dict(id=i,name=('@everyone **[link](https://example.com)\n'+str(i))*20,value=100-i) for i in range(120)]
        result=dict(category='income',month=25,rows=rankings.rank_rows(rows))
        with i18n.using_language('pl'):
            embed,file=rankings.render(result,25)
            try:full=file.fp.read().decode('utf-8')
            finally:file.close()
        self.assertLessEqual(len(embed.description),4096);self.assertLessEqual(len(embed),6000)
        self.assertIn('25/120',embed.footer.text)
        self.assertNotIn('@everyone',embed.description)
        self.assertIn('120. '+' '.join(rows[-1]['name'].split()),full)
        self.assertIn('— -19',full)

    async def test_empty_failed_forecast_and_discord_error_never_post_fake_or_duplicate_rankings(self):
        with patch('rankings.load',return_value=dict(rows=[])):
            await RankingsCog.ranking.callback(None,self.gm,'income',10,self.channel)
        self.channel.send.assert_not_called()
        with patch('rankings.load',side_effect=RuntimeError('offline')),self.assertLogs('cogs.rankings',level='ERROR'):
            await RankingsCog.ranking.callback(None,self.gm,'income',10,self.channel)
        self.channel.send.assert_not_called()
        self.channel.send.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'Missing permission')
        await RankingsCog.ranking.callback(None,self.gm,'treasury',10,self.channel)
        self.channel.send.assert_awaited_once()
        self.assertIn('before retrying',self.gm.followup.send.call_args.args[0])
