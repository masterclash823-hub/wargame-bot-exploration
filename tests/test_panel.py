import os
import tempfile
import unittest
from types import SimpleNamespace

import db
from cogs.panel import ACTIONS, PanelLauncher, PlayerPanel


class PlayerPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DB_PATH"] = os.path.join(self.tmp.name, "panel.db")
        db.USE_POSTGRES = False
        db.init_db()
        self.bot = SimpleNamespace(get_cog=lambda name: None)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_every_category_fits_discord_component_limits(self):
        view = PlayerPanel(self.bot, 123, "pl")
        for section in ACTIONS:
            view.section = section
            view.rebuild()
            self.assertLessEqual(len(view.children), 25)
            self.assertTrue(all(0 <= item.row <= 4 for item in view.children))
            self.assertEqual(view.children[0].type.value, 3)  # string select

    async def test_dashboard_uses_live_nation_counts(self):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO nations(owner_id,name,flag,treasury,stability,resources_json) VALUES(?,?,?,?,?,?)",
                ("123", "Testland", "🏴", 120, 71, '{"wood":5,"food":9}'),
            )
        view = PlayerPanel(self.bot, 123, "en")
        embed = view.embed()
        self.assertIn("Testland", embed.description)
        self.assertEqual(embed.fields[0].value, "120")
        self.assertIn("food 9", embed.fields[-1].value)

    async def test_launcher_is_persistent(self):
        view = PanelLauncher(self.bot)
        self.assertIsNone(view.timeout)
        self.assertEqual(view.children[0].custom_id, "wargame:player-panel:open")

    async def test_found_colony_asks_for_manual_province_id(self):
        captured = {}

        class Response:
            async def send_modal(self, modal):
                captured["modal"] = modal

        view = PlayerPanel(self.bot, 123, "pl")
        await view.choose_empty_province(SimpleNamespace(response=Response()))
        modal = captured["modal"]
        self.assertEqual(len(modal.children), 2)
        self.assertIn("ID prowincji", modal.children[0].label)
        self.assertIn("administratora", modal.children[0].placeholder)


if __name__ == "__main__":
    unittest.main()
