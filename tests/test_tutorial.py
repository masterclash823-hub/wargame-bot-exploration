import unittest
from test_regressions import DatabaseFixture, interaction
import db
import i18n
from tutorial import CHAPTERS, TutorialView
from cogs.panel import PlayerPanel


class TutorialTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def test_slash_and_panel_share_guide_and_support_players_without_nations(self):
        import bot
        i18n.set_user_language(99,'pl')
        request=interaction(99)
        request.command=bot.tutorial_cmd
        self.assertTrue(await bot.global_guild_check(request))
        await bot.tutorial_cmd.callback(request)
        slash=request.response.send_message.call_args.kwargs
        other=interaction(99)
        await PlayerPanel(bot.bot,99,'pl').dispatch(other,'tutorial')
        panel=other.response.send_message.call_args.kwargs
        self.assertEqual(slash['embed'].to_dict(),panel['embed'].to_dict())
        self.assertIsInstance(panel['view'],TutorialView)
        self.assertIn('Game Master',panel['embed'].fields[0].value)
        self.assertTrue(panel['ephemeral'])

    async def test_all_chapters_fit_and_navigation_follows_current_language(self):
        before=self.balances()
        for lang in ('pl','en'):
            for page in range(len(CHAPTERS)):
                view=TutorialView(None,1,lang,page)
                embed=view.embed()
                self.assertLessEqual(len(embed),6000)
                self.assertTrue(all(len(f.value)<=1024 for f in embed.fields))
                self.assertLessEqual(len(embed.description),4096)
                self.assertTrue(all(len(o.label)<=100 for o in view.chapters.options))
                self.assertEqual(view.previous.disabled,page==0)
                self.assertEqual(view.next_page.disabled,page==len(CHAPTERS)-1)
        view=TutorialView(None,1,'pl')
        self.assertFalse(await view.interaction_check(interaction(2)))
        i18n.set_user_language(1,'en')
        request=interaction(1)
        await view.next_page.callback(request)
        updated=request.response.edit_message.call_args.kwargs['view']
        self.assertEqual((updated.page,updated.lang),(1,'en'))
        self.assertEqual(before,self.balances())

    async def test_shortcut_opens_correct_private_category_without_game_action(self):
        with db.cursor() as c:c.execute("INSERT INTO game_config(key,value) VALUES('guild_active_100','1')")
        before=self.balances()
        for page in range(len(CHAPTERS)):
            view=TutorialView(None,1,'en',page)
            request=interaction(1)
            request.guild_id=100
            request.response.is_done=lambda:False
            await view.open_panel.callback(request)
            sent=request.response.send_message.call_args.kwargs
            self.assertEqual(sent['view'].section,CHAPTERS[page]['target'])
            self.assertTrue(sent['ephemeral'])
        self.assertEqual(before,self.balances())
