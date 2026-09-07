import unittest
from types import SimpleNamespace

import discord
from test_regressions import DatabaseFixture, interaction
import db
from flags import flag_text, flagged_embed
from cogs.panel import PlayerPanel
from cogs.nations import NationCog
from cogs.economy import EconomyCog


class FlagTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.urls = ['https://example.com/a.png', 'https://example.com/b.png']
        with db.cursor() as c:
            for nid, url in enumerate(self.urls, 1):
                c.execute('UPDATE nations SET flag=? WHERE id=?', (url, nid))

    def assert_no_text_urls(self, embed):
        text = (embed.title or '') + (embed.description or '')
        text += ''.join(field.name + field.value for field in embed.fields)
        for url in self.urls:
            self.assertNotIn(url, text)

    async def test_panel_and_stats_render_url_as_image(self):
        panel = PlayerPanel(SimpleNamespace(), 2, 'pl')
        card = panel.embed()
        self.assertEqual(card.thumbnail.url, self.urls[1])
        self.assert_no_text_urls(card)
        request = interaction(2)
        await NationCog.stats.callback(None, request)
        card = request.response.send_message.call_args.kwargs['embed']
        self.assertEqual(card.thumbnail.url, self.urls[1])
        self.assert_no_text_urls(card)

    async def test_nation_list_preserves_each_flag_on_its_page(self):
        request = interaction(2)
        await NationCog.nation_list.callback(None, request)
        pages = request.response.send_message.call_args.kwargs['view'].pages
        self.assertEqual([page.thumbnail.url for page in pages], self.urls)
        for page in pages:
            self.assert_no_text_urls(page)

    async def test_trade_renders_both_flags(self):
        request = interaction(2)
        await EconomyCog.trade_view.callback(None, request, self.trade_id)
        card = request.response.send_message.call_args.kwargs['embed']
        self.assertEqual(card.thumbnail.url, self.urls[0])
        self.assertEqual(card.author.icon_url, self.urls[1])
        self.assertEqual(card.author.name, 'B')
        self.assert_no_text_urls(card)

    async def test_emoji_and_missing_flags_remain_supported(self):
        for value in ('🇵🇱', '<:flag:123456789012345678>', ''):
            self.assertEqual(flag_text(value), value)
            card = flagged_embed(discord.Embed(title='Nation'), (value, 'Nation'))
            self.assertIsNone(card.thumbnail.url)
        self.assertEqual(flag_text('  HTTPS://example.com/flag.png  '), '')
