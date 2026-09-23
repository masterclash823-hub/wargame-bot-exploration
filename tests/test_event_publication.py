import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from test_regressions import DatabaseFixture, interaction
import db
import config
from cogs.events import EventsCog
from cogs.combat import CombatCog
from event_images import pick_image, search_topic
from event_ui import render_event, render_public_event
from flags import flag_url


class PublicationTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.eid = db.insert_returning_id('INSERT INTO events(nation_id,gm_final_text,effects_json) VALUES(?,?,?)',
                                        (2, 'A fire in the town.', '{"treasury":-30}'))
        self.owner = NS(send=AsyncMock())
        self.channel = NS(id=12, guild=NS(id=1), send=AsyncMock(),
            permissions_for=lambda _: NS(view_channel=True, send_messages=True, embed_links=True, attach_files=True))
        self.gm = interaction(999, [NS(id=10, name=config.GM_ROLE_NAME)])
        self.gm.guild = NS(id=1, me=NS(), default_role=NS(), get_member=lambda _: self.owner)
        self.cog = EventsCog(NS(get_channel=lambda _: self.channel))
        scene = patch('event_adventure.scene', AsyncMock(return_value=('Private scene', ['A', 'B', 'C'])))
        scene.start(); self.addCleanup(scene.stop)
        images=patch('cogs.events.find_event_image',AsyncMock(return_value=None))
        images.start();self.addCleanup(images.stop)
        self.image = {'url':'https://upload.wikimedia.org/test.jpg', 'source':'https://commons.wikimedia.org/wiki/File:Fire.jpg',
                      'credit':'Painter', 'license':'Public domain'}

    async def test_public_is_only_opening_and_image(self):
        with db.cursor() as c:c.execute('UPDATE nations SET flag=? WHERE id=2',('🇵🇱',))
        with patch('cogs.events.find_event_image', AsyncMock(return_value=self.image)):
            await self.cog.event_post.callback(self.cog, self.gm, self.eid, 'public', self.channel)
        sent = self.channel.send.call_args.kwargs
        self.assertNotIn('view', sent)
        self.assertEqual(sent['embed'].description, 'A fire in the town.')
        self.assertEqual(len(sent['embed'].fields), 0)
        self.assertEqual(sent['embed'].image.url, self.image['url'])
        owner_embed=self.owner.send.call_args.kwargs['embed']
        self.assertEqual(owner_embed.image.url,self.image['url'])
        self.assertEqual(owner_embed.thumbnail.url,flag_url('🇵🇱'))
        self.assertEqual(len(self.owner.send.call_args.kwargs['view'].children), 4)

    async def test_illustration_is_large_with_any_flag_or_no_flag(self):
        import event_adventure as flow
        with patch('cogs.events.find_event_image', AsyncMock(return_value=self.image)):
            await self.cog.event_post.callback(self.cog,self.gm,self.eid,'public',self.channel)
        state=flow.load_run(self.eid)
        for flag in ('','🇵🇱','https://example.com/flag.png','invalid flag','flag:'+'a'*64):
            state['flag']=flag
            for render in (render_event,render_public_event):
                embed=render(state)
                self.assertEqual(embed.image.url,self.image['url'])
                self.assertNotEqual(embed.thumbnail.url,self.image['url'])
                self.assertIn(self.image['credit'],embed.footer.text)
                self.assertIn(self.image['license'],embed.footer.text)
                self.assertEqual(embed.url,self.image['source'])
        state['public_image']=None
        with db.cursor() as c:c.execute('DELETE FROM event_media WHERE event_id=?',(self.eid,))
        self.assertIsNone(render_event(state).image.url)

    async def test_resumed_decisions_and_final_result_keep_image_and_progress_footer(self):
        import event_adventure as flow
        with patch('cogs.events.find_event_image', AsyncMock(return_value=self.image)):
            await self.cog.event_post.callback(self.cog,self.gm,self.eid,'public',self.channel)
        async def consequence(state,action,choice):
            return flow.fallback_consequence(state,choice),'Baseline',False
        with patch.object(flow,'assess_consequence',side_effect=consequence):
            for version in range(3):
                await flow.decide(self.eid,version,2,choice=0)
                db.init_db()
                player=interaction(2)
                await self.cog.event_play.callback(self.cog,player,self.eid)
                sent=player.followup.send.call_args.kwargs
                self.assertTrue(sent['ephemeral'])
                self.assertEqual(sent['embed'].image.url,self.image['url'])
                self.assertIn(self.image['credit'],sent['embed'].footer.text)
                self.assertIn('3/3' if version==2 else f'{version+2}/3',sent['embed'].footer.text)
                if version==2:self.assertIn('Finished',sent['embed'].footer.text)

    async def test_private_can_search_but_never_sends_publicly_and_is_hidden(self):
        with patch('cogs.events.find_event_image', AsyncMock(return_value=self.image)) as search:
            await self.cog.event_post.callback(self.cog, self.gm, self.eid, 'private', self.channel)
        search.assert_awaited_once()
        self.assertEqual(self.owner.send.call_args.kwargs['embed'].image.url,self.image['url'])
        self.channel.send.assert_not_awaited()
        outsider = interaction(1)
        await self.cog.event_list.callback(self.cog, outsider, 'B')
        self.assertNotIn('embed', outsider.response.send_message.call_args.kwargs)
        owner = interaction(2)
        await self.cog.event_list.callback(self.cog, owner, '')
        self.assertIn('embed', owner.response.send_message.call_args.kwargs)
        with self.assertRaises(ValueError):
            import event_adventure
            await event_adventure.decide(self.eid, 0, 1, choice=0)

    async def test_image_failure_still_publishes_event(self):
        with patch('cogs.events.find_event_image', AsyncMock(return_value=None)):
            await self.cog.event_post.callback(self.cog, self.gm, self.eid, 'public', self.channel)
        with db.cursor() as c:
            c.execute('SELECT status FROM events WHERE id=?', (self.eid,))
            self.assertEqual(c.fetchone()['status'], 'active')
        self.channel.send.assert_awaited_once()
        self.assertIsNone(self.channel.send.call_args.kwargs['embed'].image.url)
        self.owner.send.assert_awaited_once()

    async def test_disabled_image_skips_search_even_with_query(self):
        with patch('cogs.events.find_event_image', AsyncMock()) as search:
            await self.cog.event_post.callback(self.cog, self.gm, self.eid, 'public',
                                               self.channel, 'old search', include_image=False)
            search.assert_not_awaited()
        self.channel.send.assert_awaited_once()
        self.assertIsNone(self.channel.send.call_args.kwargs['embed'].image.url)
        self.assertNotIn('illustration', self.gm.followup.send.call_args.kwargs['content'])
        self.owner.send.assert_awaited_once()

    async def test_private_resolution_history_is_private(self):
        import event_adventure as flow
        from cogs.nations import NationCog
        await self.cog.event_post.callback(self.cog, self.gm, self.eid)
        async def consequence(state, action, choice):
            return flow.fallback_consequence(state, choice), 'Baseline', False
        with patch.object(flow, 'assess_consequence', side_effect=consequence):
            for version in range(3):
                await flow.decide(self.eid, version, 2, choice=0)
        with db.cursor() as c:
            c.execute('SELECT source FROM nation_history WHERE nation_id=2')
            self.assertEqual(c.fetchone()['source'], 'event_private')
        outsider = interaction(1)
        await NationCog.history.callback(None, outsider, 'B')
        self.assertNotIn('embed', outsider.response.send_message.call_args.kwargs)
        owner = interaction(2)
        await NationCog.history.callback(None, owner, 'B')
        self.assertTrue(owner.response.send_message.call_args.kwargs['ephemeral'])
        self.assertIn('embed', owner.response.send_message.call_args.kwargs)

    async def test_configured_channel_is_used(self):
        self.channel.mention = '<#12>'
        await self.cog.event_channel.callback(self.cog, self.gm, self.channel)
        with patch('cogs.events.find_event_image', AsyncMock(return_value=self.image)):
            await self.cog.event_post.callback(self.cog, self.gm, self.eid, 'public')
        self.channel.send.assert_awaited_once()

    async def test_unassigned_plan_warns_player_and_gm(self):
        player = interaction(2)
        await CombatCog.battle_plan.callback(None, player, 'Town', 'Defend', 'Army in prose', '')
        self.assertTrue(any('No units assigned' in f.value for f in player.response.send_message.call_args.kwargs['embed'].fields))
        await CombatCog.plans_pending.callback(None, self.gm)
        embed = self.gm.followup.send.call_args.kwargs['embed']
        self.assertIn('⚠️', embed.fields[0].name)

    async def test_image_filter_rejects_ai_and_nonpublic_license(self):
        info = {'mime':'image/jpeg', 'url':self.image['url'], 'descriptionurl':self.image['source'],
                'extmetadata':{'LicenseShortName':{'value':'Public domain'}}}
        data = {'query':{'pages':{'1':{'title':'Fire painting', 'imageinfo':[info]}}}}
        self.assertIsNotNone(pick_image(data))
        data['query']['pages']['1']['title'] = 'AI-generated fire'
        self.assertIsNone(pick_image(data))
        self.assertIn('fire', search_topic('Pożar miasta'))
