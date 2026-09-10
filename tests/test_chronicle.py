import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import discord
import db
import i18n
import config
import world_service as world
import chronicle_service as news
from cogs.chronicle import ChronicleCog
from test_regressions import DatabaseFixture, interaction


class ChronicleFixture(DatabaseFixture):
    now=datetime(2026,9,10,18,0,tzinfo=timezone.utc)

    def query(self,sql,args=()):
        with db.cursor() as c:
            c.execute(sql,args);return c.fetchall()

    def activity(self,kind,nid,key,payload=None,hours=1):
        with db.atomic() as c:
            world.activity(c,kind,nid,key,payload,2 if nid==1 else 1)
            c.execute('UPDATE world_activity SET occurred_at=? WHERE source_key=?',
                      ((self.now-timedelta(hours=hours)).isoformat(),key))

    def due(self):
        news.configure(100,200,18,'pl',self.now-timedelta(hours=1))
        news.prepare_due(self.now)
        return self.query('SELECT * FROM news_deliveries')[0]


class ChronicleTests(ChronicleFixture,unittest.TestCase):
    def test_time_is_utc_and_same_hour_configuration_waits_until_tomorrow(self):
        self.assertEqual(news.configure(100,200,18,'pl',self.now),self.now+timedelta(days=1))
        news.prepare_due(self.now)
        self.assertFalse(self.query('SELECT * FROM news_deliveries'))
        news.prepare_due(self.now+timedelta(days=1))
        self.assertEqual(len(self.query('SELECT * FROM news_deliveries')),1)

    def test_ranking_uses_two_public_actions_and_prefers_distinct_nations(self):
        self.activity('war',1,'war')
        self.activity('battle',1,'battle',{'winner':'attacker'})
        self.activity('building',2,'building',{'building':'farm','level':1,'cell':20})
        self.activity('war',2,'old',hours=25)
        self.activity('war',2,'future',hours=-1)
        with db.cursor() as c:rows=news.top_actions(c,self.now)
        self.assertEqual([r['source_key'] for r in rows],['war','building'])

    def test_private_payload_is_filtered_and_empty_day_has_no_fabricated_actions(self):
        with db.cursor() as c:self.assertEqual(news.top_actions(c,self.now),[])
        self.activity('event',1,'event',{'orders':'SECRET','choices':'SECRET','treasury':12345})
        with db.cursor() as c:rows=news.top_actions(c,self.now)
        self.assertEqual(len(rows),1)
        self.assertEqual(json.loads(rows[0]['payload_json']),{})
        embed=news.render({'language':'pl','slot':self.now.isoformat(),'report_json':json.dumps(rows)})
        self.assertNotIn('SECRET',json.dumps(embed.to_dict()))
        self.assertNotIn('12345',json.dumps(embed.to_dict()))
        self.assertEqual(len(embed.fields),1)

    def test_preparing_concurrently_creates_one_delivery(self):
        news.configure(100,200,18,'pl',self.now-timedelta(hours=1))
        with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:news.prepare_due(self.now),range(2)))
        self.assertEqual(len(self.query('SELECT * FROM news_deliveries')),1)
        with ThreadPoolExecutor(2) as pool:claimed=list(pool.map(lambda _:news.claim(100,self.now.isoformat()),range(2)))
        self.assertEqual(sum(d is not None for d in claimed),1)

    def test_downtime_creates_only_latest_digest(self):
        news.configure(100,200,18,'pl',self.now-timedelta(days=10,hours=1))
        news.prepare_due(self.now+timedelta(minutes=10))
        rows=self.query('SELECT * FROM news_deliveries')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['slot'],self.now.isoformat())

    def test_pause_or_channel_change_cancels_pending_delivery(self):
        d=self.due()
        with db.cursor() as c:c.execute('UPDATE news_settings SET enabled=0')
        self.assertIsNone(news.claim(100,d['slot']))
        self.assertEqual(self.query('SELECT status FROM news_deliveries')[0]['status'],'cancelled')

    def test_all_activity_kinds_render_in_both_languages_and_flags_are_images(self):
        payloads={'building':{'building':'farm','level':3,'cell':10},'colony':{'cell':10},'expansion':{'cell':20},
                  'research':{'category':'land'},'battle':{'winner':'draw'},'goal':{'code':'development'},
                  'treaty':{'kind':'peace'},'breach':{'kind':'non_aggression'}}
        with db.cursor() as c:c.execute("UPDATE nations SET flag='https://example.org/flag.png',name='@everyone' WHERE id=1")
        for kind in world.SCORES:
            self.activity(kind,1,kind,payloads.get(kind,{}))
            row=self.query('SELECT x.*,n.name,n.flag,o.name AS other_name FROM world_activity x JOIN nations n ON n.id=x.nation_id '
                           'LEFT JOIN nations o ON o.id=x.other_nation_id WHERE x.source_key=?',(kind,))[0]
            for lang in ('pl','en'):
                embed=news.render({'language':lang,'slot':self.now.isoformat(),'report_json':json.dumps([row])})
                self.assertLessEqual(len(embed),6000)
                self.assertEqual(embed.thumbnail.url,'https://example.org/flag.png')
                self.assertNotIn('https://',embed.fields[0].name)
                self.assertNotIn('@everyone',embed.fields[0].name)


class ChronicleDeliveryTests(ChronicleFixture,unittest.IsolatedAsyncioTestCase):
    def bot(self,messages=()):
        async def history(**kwargs):
            for message in messages:yield message
        channel=NS(send=AsyncMock(return_value=NS(id=999)),history=history)
        return NS(get_channel=lambda cid:channel if cid==200 else None,user=NS(id=42)),channel

    async def test_no_configuration_means_no_send(self):
        bot,ch=self.bot()
        await news.run_reports(bot,self.now)
        ch.send.assert_not_awaited()

    async def test_concurrent_dispatch_sends_once_with_mentions_disabled(self):
        d=self.due();bot,ch=self.bot()
        await asyncio.gather(news.deliver(bot,d),news.deliver(bot,d))
        ch.send.assert_awaited_once()
        mention_policy=ch.send.call_args.kwargs['allowed_mentions'].to_dict()
        self.assertEqual(mention_policy['parse'],[])
        row=self.query('SELECT * FROM news_deliveries')[0]
        self.assertEqual((row['status'],row['message_id']),('sent','999'))

    async def test_restart_recovers_published_report_without_sending_again(self):
        d=self.due();news.claim(100,d['slot'])
        message=NS(author=NS(id=42),embeds=[news.render(d)],id=1234)
        bot,ch=self.bot([message])
        with db.cursor() as c:c.execute('UPDATE news_deliveries SET updated_at=?',((self.now-timedelta(minutes=6)).isoformat(),))
        await news.run_reports(bot,self.now)
        ch.send.assert_not_awaited()
        self.assertEqual(self.query('SELECT message_id FROM news_deliveries')[0]['message_id'],'1234')
        # A stale recovery worker must not undo a confirmed delivery.
        news.finish(d,'uncertain')
        self.assertEqual(self.query('SELECT status FROM news_deliveries')[0]['status'],'sent')

    async def test_timeout_never_retries_automatically(self):
        d=self.due();bot,ch=self.bot()
        ch.send.side_effect=asyncio.TimeoutError()
        await news.deliver(bot,d)
        self.assertEqual(self.query('SELECT status FROM news_deliveries')[0]['status'],'uncertain')
        with db.cursor() as c:c.execute('UPDATE news_deliveries SET updated_at=?',((self.now-timedelta(minutes=6)).isoformat(),))
        await news.run_reports(bot,self.now)
        ch.send.assert_awaited_once()
        news.retry(100,d['slot'])
        ch.send.side_effect=None
        await news.deliver(bot,d)
        self.assertEqual(self.query('SELECT status FROM news_deliveries')[0]['status'],'sent')

    async def test_forbidden_marks_failed_and_requires_explicit_retry(self):
        d=self.due();bot,ch=self.bot()
        ch.send.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'Denied')
        await news.deliver(bot,d)
        self.assertEqual(self.query('SELECT status FROM news_deliveries')[0]['status'],'failed')
        await news.deliver(bot,d)
        ch.send.assert_awaited_once()

    async def test_configure_requires_gm_and_preview_has_no_send_or_settings_side_effect(self):
        await ChronicleCog.configure.callback(None,interaction(1),None)
        self.assertFalse(self.query('SELECT * FROM news_settings'))
        gm=interaction(999,[NS(id=20,name=config.GM_ROLE_NAME)])
        gm.guild=NS(id=100,me=NS(id=42),get_member=lambda _:None)
        channel=NS(id=200,guild=gm.guild,mention='<#200>',permissions_for=lambda _:NS(
            view_channel=True,send_messages=True,embed_links=True,read_message_history=True))
        with patch.object(news,'utcnow',return_value=self.now):
            await ChronicleCog.configure.callback(None,gm,channel,18,'pl')
        self.assertEqual(self.query('SELECT channel_id FROM news_settings')[0]['channel_id'],'200')
        await ChronicleCog.preview.callback(None,gm)
        self.assertFalse(self.query('SELECT * FROM news_deliveries'))
