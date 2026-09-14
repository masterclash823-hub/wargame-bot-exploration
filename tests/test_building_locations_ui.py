import json
import unittest
from unittest.mock import patch
import db
import i18n
import technology as tech
import technology_ui as ui
from cogs.tech import TechCog
from cogs.panel import PlayerPanel
from cogs.companies import specialties
from cogs.economy import _seed_buildings
from test_regressions import DatabaseFixture, interaction


class BuildingLocationsTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,terrain,buildings_json) VALUES(10,1,2000,'plains','[]')")

    async def test_build_menu_has_localized_effects_below_names(self):
        for lang,food,tax in (('pl','żyw','podat'),('en','food','tax')):
            with i18n.using_language(lang):
                panel=PlayerPanel(None,1,lang)
                first=interaction(1)
                await panel.choose_build(first)
                provinces=first.response.send_message.call_args.kwargs['view']
                second=interaction(1)
                await provinces.handler(second,'10')
                buildings=second.response.send_message.call_args.kwargs['view']
                options={o.value:o for o in buildings.children[0].options}
                self.assertIn(food,options['farm'].description.lower())
                self.assertIn(tax,options['market'].description.lower())
                self.assertTrue(all(o.description and len(o.description)<=100 for o in options.values()))
                company_options=specialties(['farm','algae_farm'])
                self.assertIn(food,company_options[0].description.lower())
                self.assertIn('0,05' if lang=='pl' else '0.05',company_options[1].description)
                panel.stop();provinces.stop();buildings.stop()

    async def test_locations_survive_legacy_building_data_and_keep_language(self):
        tech.set_deposit(10,True)
        for raw in ('', 'broken', 'null', '{}', '["algae_farm"]'):
            with db.cursor() as c:c.execute('UPDATE provinces SET buildings_json=?',(raw,))
            for lang,title in (('pl','Stanowiska algae'),('en','Algae deposits')):
                with i18n.using_language(lang):
                    i=interaction(1)
                    await ui.show_locations(i)
                    i.response.defer.assert_awaited_once_with(ephemeral=True)
                    payload=i.followup.send.call_args.kwargs
                    self.assertTrue(payload['ephemeral'])
                    e=payload['embed']
                    self.assertIn(title,e.title)
                    self.assertIn('#10',e.fields[0].name)
                    if raw=='["algae_farm"]':
                        self.assertIn('farma istnieje' if lang=='pl' else 'farm exists',e.fields[0].value)
                    elif raw:
                        self.assertIn('nie można' if lang=='pl' else 'unreadable',e.fields[0].value)

    async def test_both_command_names_return_deferred_location_list(self):
        cog=object.__new__(TechCog)
        for command in (TechCog.algae_locations,TechCog.algae_locations_alias):
            i=interaction(1)
            await command.callback(cog,i)
            i.response.defer.assert_awaited_once_with(ephemeral=True)
            i.followup.send.assert_awaited_once()
            self.assertIn('No active deposits',i.followup.send.call_args.kwargs['embed'].fields[0].value)
        self.assertEqual(TechCog.algae_locations_alias.name,'algae_locations')
        self.assertEqual(TechCog.algae_locations.name,'locations')

    async def test_location_failure_returns_helpful_reply_without_leaking_error(self):
        i=interaction(1)
        with i18n.using_language('en'), patch.object(ui,'locations_embed',side_effect=RuntimeError('private-db-detail')), self.assertLogs('technology_ui',level='ERROR'):
            await ui.show_locations(i,notice='Deposit added.')
        payload=i.followup.send.call_args.kwargs
        self.assertIn('Deposit added.',payload['content'])
        self.assertIn('Could not read algae deposits',payload['content'])
        self.assertNotIn('private-db-detail',payload['content'])


if __name__=='__main__':
    unittest.main()
