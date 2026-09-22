import base64
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock,patch

import config
import db
import event_adventure as flow
import event_media as media
from event_ui import respond,render_public_event,render_event
from cogs.events import EventsCog
from test_regressions import DatabaseFixture,interaction
from test_event_images import picture,candidate,context


class MediaTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.eid=db.insert_returning_id('INSERT INTO events(nation_id,gm_final_text,effects_json) VALUES(2,?,?)',
                                       ('A fire in the town.','{"treasury":-30}'))
        self.records=[]
        self.owner=NS(send=AsyncMock(side_effect=lambda **kw:self.capture('DM',kw)))
        self.gm=interaction(999,[NS(id=10,name=config.GM_ROLE_NAME)])
        self.gm.guild=NS(id=1,me=NS(id=500),default_role=NS(),get_member=lambda _:self.owner)
        self.gm.followup.send.side_effect=lambda *args,**kw:self.capture('GM',{'content':args[0],**kw} if args else kw)
        self.channel=NS(id=12,guild=self.gm.guild,send=AsyncMock(side_effect=lambda **kw:self.capture('public',kw)),
            permissions_for=lambda _:NS(view_channel=True,send_messages=True,embed_links=True,attach_files=True))
        self.bot=NS(user=NS(id=500),get_channel=lambda _:self.channel)
        self.cog=EventsCog(self.bot)
        self.image={**candidate(),'data':picture(),'extension':'png'}
        scene=patch('event_adventure.scene',AsyncMock(return_value=('Private scene',['A','B','C'])))
        scene.start();self.addCleanup(scene.stop)
        search=patch('cogs.events.find_event_image',AsyncMock(return_value=self.image))
        self.search=search.start();self.addCleanup(search.stop)

    def capture(self,where,kw):
        file=kw.get('file') or next(iter(kw.get('attachments',[])),None)
        self.records.append((where,kw,file.fp.read() if file else None))
        return NS(id=123)

    async def post(self,visibility='public',**kwargs):
        await self.cog.event_post.callback(self.cog,self.gm,self.eid,visibility,self.channel,**kwargs)

    def upload(self,color='blue'):
        data=picture(color)
        return NS(size=len(data),read=AsyncMock(return_value=data))

    async def test_public_dm_and_gm_receive_separate_real_attachments_with_flag(self):
        with db.cursor() as c:c.execute("UPDATE nations SET flag='🇵🇱' WHERE id=2")
        await self.post()
        self.assertEqual([r[0] for r in self.records],['public','DM','GM'])
        for where,message,data in self.records:
            self.assertEqual(data,picture())
            self.assertEqual(message['embed'].image.url,f'attachment://event-{self.eid}.png')
            if where=='public':self.assertEqual(len(message['embed'].fields),0)
            else:self.assertIsNotNone(message['embed'].thumbnail.url)
        self.assertEqual(len({id(r[1]['file']) for r in self.records}),3)
        self.assertEqual(media.message_id(self.eid),123)
        state=flow.load_run(self.eid)
        self.assertNotIn('data',state['public_image'])
        with db.cursor() as c:
            c.execute('SELECT data_base64 FROM event_media WHERE event_id=?',(self.eid,))
            self.assertEqual(base64.b64decode(c.fetchone()['data_base64']),picture())

    async def test_search_response_download_persistence_and_discord_attachment_together(self):
        import event_images as source
        info=dict(mime='image/png',url=candidate()['url'],descriptionurl=candidate()['source'],
                  extmetadata={'LicenseShortName':{'value':'Public domain'}})
        search_reply=NS(status=200,raise_for_status=Mock(),json=AsyncMock(return_value={
            'query':{'pages':{'1':{'title':'File:Fire.png','imageinfo':[info]}}}}))
        async def chunks():yield picture()
        file_reply=NS(status=200,raise_for_status=Mock(),content_length=len(picture()),
                      content=NS(iter_chunked=lambda _:chunks()))
        session=NS(get=Mock(side_effect=[context(search_reply),context(file_reply)]))
        with patch.object(source.aiohttp,'ClientSession',return_value=context(session)),\
                patch('cogs.events.find_event_image',source.find_event_image):
            await self.post()
        self.assertEqual(session.get.call_count,2)
        self.assertTrue(all(r[2]==picture() for r in self.records))
        self.assertTrue(all(r[1]['embed'].image.url.startswith('attachment://') for r in self.records))

    async def test_restart_play_and_three_decisions_keep_large_illustration_without_network(self):
        await self.post();db.init_db();self.search.reset_mock()
        player=interaction(2);player.followup.send.side_effect=lambda **kw:self.capture('player',kw)
        await self.cog.event_play.callback(self.cog,player,self.eid)
        async def impact(state,action,choice):return flow.fallback_consequence(state,choice),'Baseline',False
        with patch.object(flow,'assess_consequence',side_effect=impact):
            for _ in range(3):await respond(player,flow.load_run(self.eid),choice=0)
        self.assertTrue(flow.load_run(self.eid)['resolved'])
        for _,message,data in self.records[3:]:
            self.assertEqual(data,picture());self.assertIn('attachment://',message['embed'].image.url)
        self.search.assert_not_awaited()

    async def test_default_private_event_searches_and_keeps_illustration_private(self):
        await self.post('private')
        self.search.assert_awaited_once();self.channel.send.assert_not_awaited()
        self.assertEqual([r[0] for r in self.records],['DM','GM'])
        outsider=interaction(1)
        await self.cog.event_play.callback(self.cog,outsider,self.eid)
        self.assertNotIn('file',outsider.followup.send.call_args.kwargs)

    async def test_upload_bypasses_search_and_disabled_images_skip_upload_too(self):
        file=self.upload()
        await self.post('private',file=file)
        self.search.assert_not_awaited();file.read.assert_awaited_once()
        self.assertTrue(all(r[2]==picture('blue') for r in self.records))
        eid=db.insert_returning_id('INSERT INTO events(nation_id,gm_final_text) VALUES(2,?)',('Other event',))
        file.read.reset_mock();self.records.clear()
        await self.cog.event_post.callback(self.cog,self.gm,eid,'private',include_image=False,file=file)
        file.read.assert_not_awaited();self.search.assert_not_awaited()
        self.assertTrue(all(r[2] is None for r in self.records))
        self.assertNotIn('Neither source',self.records[-1][1]['content'])

    async def test_missing_attach_permission_leaves_draft_and_bad_upload_is_rejected(self):
        self.channel.permissions_for=lambda _:NS(view_channel=True,send_messages=True,embed_links=True,attach_files=False)
        await self.post();self.search.assert_not_awaited()
        self.assertIn('Attach Files',self.gm.followup.send.call_args.args[0])
        with db.cursor() as c:
            c.execute('SELECT status FROM events WHERE id=?',(self.eid,));self.assertEqual(c.fetchone()['status'],'draft')
        file=NS(size=20,read=AsyncMock(return_value=b'<html>404</html>'))
        # Error replies use positional content, unlike successful embed sends.
        self.gm.followup.send.side_effect=None
        await self.post('private',file=file)
        self.assertIn('valid JPG',self.gm.followup.send.call_args.args[0])
        self.owner.send.assert_not_awaited()

    async def test_repair_edits_public_message_without_replaying_or_resetting_event(self):
        await self.post();state=flow.load_run(self.eid);before=self.balances()
        message=NS(id=123,author=self.bot.user,embeds=[render_public_event(state)],
                   edit=AsyncMock(side_effect=lambda **kw:self.capture('edit',kw)))
        self.channel.fetch_message=AsyncMock(return_value=message)
        await self.cog.event_image.callback(self.cog,self.gm,self.eid,file=self.upload())
        self.assertEqual(flow.load_run(self.eid),state);self.assertEqual(self.balances(),before)
        message.edit.assert_awaited_once()
        edit=next(r for r in self.records if r[0]=='edit')
        self.assertEqual(edit[2],picture('blue'));self.assertEqual(len(edit[1]['embed'].fields),0)
        self.assertEqual(render_event(state).footer.text.split(' · ')[-1],'GM')

    async def test_legacy_message_link_is_verified_before_saving_or_fetching_images(self):
        await self.post()
        with db.cursor() as c:c.execute('UPDATE event_media SET public_message_id=NULL WHERE event_id=?',(self.eid,))
        state=flow.load_run(self.eid)
        message=NS(id=456,author=self.bot.user,embeds=[render_public_event(state)],
                   edit=AsyncMock(side_effect=lambda **kw:self.capture('edit',kw)))
        self.channel.fetch_message=AsyncMock(return_value=message)
        file=self.upload()
        # Capture simple error replies as well as embeds.
        self.gm.followup.send.side_effect=None
        await self.cog.event_image.callback(self.cog,self.gm,self.eid,file=file,message_link='https://discord.com/channels/999/12/456')
        file.read.assert_not_awaited();message.edit.assert_not_awaited()
        await self.cog.event_image.callback(self.cog,self.gm,self.eid,file=file,message_link='https://discord.com/channels/1/12/456')
        message.edit.assert_awaited_once();self.assertEqual(media.message_id(self.eid),456)

    async def test_repair_during_decision_does_not_get_overwritten_and_deletion_cascades(self):
        await self.post('private')
        async def impact(state,action,choice):
            with db.cursor() as c:media.save(c,self.eid,{**candidate(),'credit':'New painter','data':picture('blue')})
            return flow.fallback_consequence(state,choice),'Baseline',False
        with patch.object(flow,'assess_consequence',side_effect=impact):state=await flow.decide(self.eid,0,2,choice=0)
        self.assertIn('New painter',render_event(state).footer.text)
        file=media.attachment(self.eid)
        try:self.assertEqual(file.fp.read(),picture('blue'))
        finally:file.close()
        with db.cursor() as c:c.execute('DELETE FROM events WHERE id=?',(self.eid,))
        self.assertIsNone(media.attachment(self.eid))


if __name__=='__main__':unittest.main()
