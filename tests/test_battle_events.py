import json
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from test_regressions import DatabaseFixture, interaction
import config
import db
import i18n
from utils import short_date
from cogs.combat import CombatCog
from cogs import events
import event_adventure


class BattleEventTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def gm(self):
        return interaction(999, [NS(id=12, name=config.GM_ROLE_NAME)])

    def nation(self):
        with db.cursor() as c:
            c.execute("SELECT * FROM nations WHERE id=2")
            return c.fetchone()

    async def test_dates(self):
        for value in (datetime(2026, 9, 6, tzinfo=timezone.utc), "2026-09-06 12:00:00"):
            self.assertEqual(short_date(value), "2026-09-06")
        self.assertEqual(short_date(None), "—")

    async def test_pending_empty_and_denied(self):
        inter = self.gm()
        await CombatCog.plans_pending.callback(None, inter)
        self.assertIn("No unmatched", inter.followup.send.call_args.args[0])
        self.assertTrue(inter.response.defer.call_args.kwargs["ephemeral"])
        player = interaction()
        await CombatCog.plans_pending.callback(None, player)
        player.response.send_message.assert_awaited_once()
        player.followup.send.assert_not_awaited()

    async def test_pending_postgres_dates_numeric_locations_and_pages(self):
        with db.cursor() as c:
            for index in range(60):
                c.execute("INSERT INTO battle_plans(nation_id,provinces_json,orders_text,status) VALUES(?,?,?,?)",
                          (2, json.dumps([123] if index % 2 else ["Harbor"]), "Orders " * 100,
                           "matched" if index == 59 else "unmatched"))
        original = db._UnifiedCursor.fetchall
        def pg_dates(cursor):
            rows = original(cursor)
            for row in rows:
                if "submitted_at" in row:
                    row["submitted_at"] = datetime(2026, 9, 6, tzinfo=timezone.utc)
            return rows
        inter = self.gm()
        with patch.object(db._UnifiedCursor, "fetchall", pg_dates):
            await CombatCog.plans_pending.callback(None, inter)
        view = inter.followup.send.call_args.kwargs["view"]
        self.assertGreater(len(view.pages), 1)
        self.assertEqual(sum(len(page.fields) for page in view.pages), 59)
        for page in view.pages:
            self.assertLessEqual(len(page), 6000)
            self.assertLessEqual(len(page.fields), 25)
            for field in page.fields:
                self.assertIn("2026-09-06", field.name)
                self.assertLessEqual(len(field.value), 1024)
        await view.next_page.callback(inter)
        self.assertEqual(view.index, 1)
        self.assertFalse(await view.interaction_check(interaction(2)))

    async def test_generation_uses_owner_language_not_gm(self):
        for language, expected in (("pl", "Polish"), ("en", "English")):
            i18n.set_user_language(2, language)
            i18n.set_user_language(999, "en" if language == "pl" else "pl")
            generate = Mock(return_value=NS(text='Narrative\nEFFECTS: {"stability":1}'))
            client = NS(models=NS(generate_content=generate))
            google = NS(genai=NS(Client=Mock(return_value=client)))
            with patch.dict(sys.modules, {"google": google}):
                text, effects = await events._generate_event(self.nation())
            prompt = generate.call_args.kwargs["contents"]
            self.assertIn(f"special_note in {expected}", prompt)
            self.assertIn("all JSON keys/resource identifiers in English", prompt)
            self.assertEqual(json.loads(effects), {"stability":1})

    async def test_event_fallback_and_default_language(self):
        self.assertEqual(events._event_language(self.nation()), config.DEFAULT_LANGUAGE)
        i18n.set_user_language(2, "pl")
        with patch.dict(sys.modules, {"google": NS(genai=NS(Client=Mock(side_effect=RuntimeError("offline"))))}):
            text, effects = await events._generate_event(self.nation())
        self.assertIn("AI niedostępne", text)
        self.assertEqual(effects, "{}")

    async def test_history_with_postgres_timestamp(self):
        with db.cursor() as c:
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)", (2, "gm", "History"))
        original = db._UnifiedCursor.fetchall
        def pg_dates(cursor):
            rows = original(cursor)
            for row in rows:
                if "timestamp" in row:
                    row["timestamp"] = datetime(2026, 9, 6, tzinfo=timezone.utc)
            return rows
        with patch.object(db._UnifiedCursor, "fetchall", pg_dates):
            context = events._build_nation_context(self.nation())
        self.assertIn("2026-09-06 GM", context)

    async def test_generate_post_and_list_polish(self):
        i18n.set_user_language(2, "pl")
        owner = NS(send=AsyncMock())
        channel = NS(send=AsyncMock())
        cog = events.EventsCog(NS(get_channel=lambda _: channel))
        with db.cursor() as c:
            c.execute("INSERT INTO game_config(key,value) VALUES(?,?)", ("announce_channel_id", "10"))
        gm = self.gm()
        gm.guild.get_member = lambda _: owner
        with patch.object(events, "_generate_event", AsyncMock(return_value=("Polskie wydarzenie.", '{"stability":2}'))):
            await cog.event_generate.callback(cog, gm, "B")
        self.assertIn("Szkic wydarzenia #1", gm.followup.send.call_args.kwargs["embed"].title)
        with patch.object(event_adventure, "scene", AsyncMock(return_value=("Polskie wydarzenie.", ["A", "B", "C"]))):
            await cog.event_post.callback(cog, gm, 1)
        channel.send.assert_not_awaited()
        embed = owner.send.call_args.kwargs["embed"]
        self.assertIn("Wydarzenie", embed.title)
        self.assertIn("stabilności", embed.fields[0].value)
        self.assertIn("Wydarzenie", owner.send.call_args.kwargs["embed"].title)
        self.assertEqual(self.nation()["stability"], 50)
        self.assertEqual(len(owner.send.call_args.kwargs["view"].children), 4)
        original = db._UnifiedCursor.fetchall
        def pg_dates(cursor):
            rows = original(cursor)
            for row in rows:
                if "posted_at" in row:
                    row["posted_at"] = datetime(2026, 9, 6, tzinfo=timezone.utc)
            return rows
        player = interaction(2)
        with patch.object(db._UnifiedCursor, "fetchall", pg_dates):
            await cog.event_list.callback(cog, player)
        listed = player.response.send_message.call_args.kwargs["embed"]
        self.assertEqual(listed.title, "📜 Wydarzenia")
        self.assertIn("2026-09-06", listed.fields[0].name)
        self.assertEqual(listed.fields[0].value, "Polskie wydarzenie.")


if __name__ == "__main__":
    unittest.main()
