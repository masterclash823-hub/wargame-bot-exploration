import asyncio
import importlib
import json
import re
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from test_regressions import DatabaseFixture, interaction
import discord
from discord import app_commands
import config
import db
import i18n
import event_adventure
from command_locale import PolishTranslator
from cogs.economy import EconomyCog, HelpView, _building_choices, _seed_buildings
from cogs.military import ShipDesignerView, MilitaryCog
from trade_service import parse_resources


class LocalizationTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def test_language_selection_without_english_and_before_activation(self):
        import bot
        inter = interaction(1)
        inter.locale = discord.Locale.american_english
        inter.command = bot.translate_cmd
        self.assertTrue(await bot.global_guild_check(inter))
        await bot.translate_cmd.callback(inter)
        self.assertEqual(i18n.get_user_language(1, 'en-US'), 'pl')
        self.assertIn('polski', inter.response.send_message.call_args.args[0].lower())
        await MilitaryCog.mil_list.callback(None, inter)
        self.assertIn('nie ma jednostek', inter.followup.send.call_args.args[0])

    async def test_concurrent_languages_and_context_reset(self):
        i18n.set_user_language(1, 'pl')
        i18n.set_user_language(2, 'en')
        @i18n.localized
        async def show(interaction):
            await asyncio.sleep(0)
            return i18n.text('Commands')
        with i18n.using_language('en'):
            self.assertEqual(await asyncio.gather(show(interaction(1)), show(interaction(2))), ['Komendy','Commands'])
            self.assertEqual(i18n.current_language(), 'en')

    async def test_all_command_payloads_have_polish_metadata(self):
        import bot
        roots = list(bot.tree.get_commands())
        for module_name in bot.COGS:
            module = importlib.import_module(module_name)
            for value in vars(module).values():
                if isinstance(value, type) and value.__module__ == module_name:
                    roots.extend(getattr(value, '__cog_app_commands__', []))
        seen = set()
        def check(payload):
            name = payload['name']
            self.assertIn('pl', payload.get('name_localizations', {}), name)
            translated = payload['name_localizations']['pl']
            self.assertEqual(translated, name)
            self.assertRegex(translated, r'^[a-z_]+$')
            self.assertTrue(1 <= len(translated) <= 32, translated)
            description = payload.get('description_localizations', {}).get('pl')
            self.assertTrue(description and len(description) <= 100, (name,description))
            siblings = []
            for option in payload.get('options', []):
                if option['type'] in (1, 2):
                    check(option)
                else:
                    self.assertIn('pl', option.get('name_localizations', {}), option)
                    self.assertIn('pl', option.get('description_localizations', {}), option)
                    self.assertLessEqual(len(option['description_localizations']['pl']), 100)
                    for choice in option.get('choices', []):
                        self.assertIn('pl', choice.get('name_localizations', {}), choice)
                        self.assertLessEqual(len(choice['name_localizations']['pl']), 100)
                siblings.append(option['name_localizations']['pl'])
            self.assertEqual(len(siblings), len(set(siblings)))
        for root in roots:
            if id(root) in seen: continue
            seen.add(id(root))
            payload = await root.get_translated_payload(bot.tree, PolishTranslator())
            check(payload)
        self.assertGreater(len(seen), 15)

    async def test_polish_resources_and_effects_keep_database_keys(self):
        with i18n.using_language('pl'):
            self.assertEqual(parse_resources('{"drewno":50,"żelazo":20,"zywnosc":1}'), {'wood':50,'iron':20,'food':1})
            with self.assertRaisesRegex(ValueError, 'Powtórzone'):
                parse_resources('{"drewno":50,"wood":20}')
            effects = event_adventure.validate_effects('{"stabilność":5,"skarbiec":100,"zasoby":{"żywność":50}}')
            self.assertEqual(effects, {'stability':5,'treasury':100,'resources':{'food':50}})
            self.assertIn('Żywność', event_adventure.effects_text(effects, 'pl'))

    async def test_building_autocomplete_and_polish_build_persist_canonical_key(self):
        i18n.set_user_language(1, 'pl')
        _seed_buildings()
        options = await _building_choices(interaction(1), 'farma')
        self.assertTrue(any(o.name=='Farma' and o.value=='farm' for o in options))
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=1000 WHERE id=1')
            c.execute('INSERT INTO provinces(azgaar_cell_id,name,owner_nation_id,terrain) VALUES(?,?,?,?)',(10,'Wood Nation',1,'plains'))
        inter = interaction(1)
        await EconomyCog.build.callback(None, inter, 10, 'farma')
        embed = inter.response.send_message.call_args.kwargs['embed']
        self.assertIn('Farma', embed.description)
        self.assertIn('Wood Nation', embed.description)
        with db.cursor() as c:
            c.execute('SELECT buildings_json FROM provinces WHERE azgaar_cell_id=10')
            self.assertIn('farm', json.loads(c.fetchone()['buildings_json']))

    async def test_ship_buttons_callbacks_help_and_player_text(self):
        i18n.set_user_language(1, 'pl')
        with i18n.using_language('pl'):
            view = ShipDesignerView(1, 'sloop', 'Trade {wood} Ship', 2, {})
            self.assertTrue(any('Lekkie działo' in c.label for c in view.children))
            self.assertTrue(all(len(c.label)<=80 for c in view.children))
            self.assertIn('Slup', view._embed().description)
            self.assertIn('Trade {wood} Ship', view._embed().title)
            help_view = HelpView(True, lang='pl')
            self.assertIn('Wojsko', [c.label for c in help_view.children])
        inter=interaction(1)
        await view._add_cb('light_cannon')(inter)
        self.assertIn('Projektowanie', inter.response.edit_message.call_args.kwargs['embed'].title)

    async def test_private_trade_uses_recipient_language(self):
        i18n.set_user_language(1,'en')
        i18n.set_user_language(2,'pl')
        member=NS(send=AsyncMock())
        inter=interaction(1)
        inter.guild.get_member=lambda uid: member
        await EconomyCog.trade_offer.callback(None, inter, 'B', '{"wood":5}', 0, '{"iron":1}', 0, 'Keep English names', 'Private {gold}')
        dm=member.send.call_args.kwargs['embed']
        self.assertIn('Oferta wymiany', dm.title)
        self.assertIn('Drewno', dm.fields[0].value)
        self.assertEqual(dm.fields[-1].value,'Private {gold}')
        self.assertIn('Trade Offer', inter.response.send_message.call_args.kwargs['embed'].title)

    async def test_template_placeholders_and_opaque_values(self):
        for source, translated in i18n._ui.items():
            self.assertEqual(sorted(re.findall(r'\{p\d+[^}]*\}',source)),
                             sorted(re.findall(r'\{p\d+[^}]*\}',translated)), source)
        name='Gold Trade {p0} /battle plan'
        self.assertIn(name,i18n.text('Trade Offer #{p0} from {p1}',lang='pl',p0=2,p1=name))
        self.assertEqual(i18n.text('Use a JSON resource object, e.g. {"wood":50}.',lang='pl'),
                         'Podaj zasoby jako obiekt JSON, np. {"drewno":50}.')
