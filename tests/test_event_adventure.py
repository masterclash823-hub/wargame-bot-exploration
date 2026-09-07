import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from test_regressions import DatabaseFixture, interaction, NS
import db
import config
import event_adventure as flow
from event_ui import render_event, EventView
from cogs.economy import HelpView, GM_HELP_FIELDS, GM_HELP_FIELDS_PL


class InteractiveTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.scene_patch = patch.object(flow, "scene", AsyncMock(return_value=("Situation", ["A", "B", "C"])))
        self.scene_patch.start()
        self.addCleanup(self.scene_patch.stop)

    async def start(self, effects=None):
        if effects is None:
            effects = {"treasury":30, "stability":6, "resources":{"iron":-15}}
        eid = db.insert_returning_id(
            "INSERT INTO events(nation_id,gm_final_text,effects_json) VALUES(?,?,?)",
            (2, "Opening", json.dumps(effects)))
        with db.cursor() as c:
            c.execute("SELECT * FROM events WHERE id=?", (eid,))
            event = c.fetchone()
            c.execute("SELECT * FROM nations WHERE id=2")
            nat = c.fetchone()
        state = await flow.prepare_run(event, nat)
        return flow.start_run(state)

    async def test_gm_help_all_fields_and_callbacks(self):
        gm = interaction(999, [NS(id=12, name=config.GM_ROLE_NAME)])
        for lang, fields in (("en", GM_HELP_FIELDS), ("pl", GM_HELP_FIELDS_PL)):
            view = HelpView(True, lang=lang)
            await next(b for b in view.children if b.label == "🔐 GM").callback(gm)
            seen = []
            for page in range((len(fields) + 19) // 20):
                embed = view._embed()
                self.assertLessEqual(len(embed.fields), 25)
                self.assertLessEqual(len(embed), 6000)
                seen.extend((f.name, f.value) for f in embed.fields)
                if page == 0:
                    await next(b for b in view.children if b.label == "▶").callback(gm)
            self.assertEqual(seen, fields)
            view.stop()
        view = HelpView(True)
        await next(b for b in view.children if b.label == "🔐 GM").callback(interaction(2))
        self.assertEqual(view.current, "general")

    async def test_help_command_with_game_master_role(self):
        import bot
        inter = interaction(999, [NS(id=12, name=config.GM_ROLE_NAME)])
        await bot.help_cmd.callback(inter)
        inter.response.defer.assert_awaited_once_with(ephemeral=True)
        view = inter.followup.send.call_args.kwargs["view"]
        self.assertIn("🔐 GM", [b.label for b in view.children])
        view.stop()

    async def test_schema_restart_and_play_access(self):
        from cogs.events import EventsCog
        state = await self.start()
        await flow.decide(state["event_id"], 0, 2, choice=1)
        db.init_db()
        self.assertEqual(flow.load_run(state["event_id"])["version"], 1)
        owner = interaction(2)
        await EventsCog.event_play.callback(None, owner, state["event_id"])
        self.assertEqual(owner.followup.send.call_args.kwargs["view"].state["version"], 1)
        outsider = interaction(1)
        await EventsCog.event_play.callback(None, outsider, state["event_id"])
        self.assertNotIn("embed", outsider.followup.send.call_args.kwargs)

    async def test_three_choices_and_final_exactly_once(self):
        before = self.balances()
        state = await self.start()
        self.assertEqual(before, self.balances())
        for version in range(3):
            self.assertEqual(len(EventView(state).children), 4)
            self.assertEqual(len(state["choices"]), 3)
            state = await flow.decide(state["event_id"], version, 2, choice=1)
            self.assertLessEqual(len(render_event(state)), 6000)
            if version < 2:
                self.assertEqual(before, self.balances())
        self.assertTrue(state["resolved"])
        self.assertEqual(len(EventView(state).children), 0)
        self.assertEqual(self.balances()[1]["treasury"], 130)
        self.assertEqual(self.balances()[1]["stability"], 56)
        self.assertEqual(json.loads(self.balances()[1]["resources_json"])["iron"], 35)
        with self.assertRaises(ValueError):
            await flow.decide(state["event_id"], 3, 2, choice=2)
        self.assertEqual(self.balances()[1]["treasury"], 130)
        with db.cursor() as c:
            c.execute("SELECT COUNT(*) AS count FROM nation_history WHERE nation_id=2")
            self.assertEqual(c.fetchone()["count"], 1)

    async def test_own_answer_counts_and_persists(self):
        state = await self.start()
        with patch.object(flow, "classify_custom", AsyncMock(return_value=(0, False))):
            state = await flow.decide(state["event_id"], 0, 2, answer="Negocjuję z sąsiadem")
        restored = flow.load_run(state["event_id"])
        self.assertEqual(restored["history"][0]["action"], "Negocjuję z sąsiadem")
        self.assertEqual(restored["version"], 1)
        self.assertTrue(restored["history"][0]["custom"])
        state = await flow.decide(state["event_id"], 1, 2, choice=1)
        state = await flow.decide(state["event_id"], 2, 2, choice=2)
        self.assertTrue(state["resolved"])
        self.assertEqual(state["applied"]["treasury"], 30)

    async def test_owner_and_stale_clicks(self):
        state = await self.start()
        with self.assertRaises(ValueError):
            await flow.decide(state["event_id"], 0, 999, choice=1)
        await flow.decide(state["event_id"], 0, 2, choice=1)
        with self.assertRaises(ValueError):
            await flow.decide(state["event_id"], 0, 2, choice=1)
        self.assertEqual(flow.load_run(state["event_id"])["version"], 1)

    async def test_concurrent_final_and_rollback(self):
        state = await self.start()
        for i in range(2):
            await flow.decide(state["event_id"], i, 2, choice=1)
        before = self.balances()
        original = db._UnifiedCursor.execute
        def fail(cur, sql, params=()):
            if sql.startswith("INSERT INTO nation_history"):
                raise RuntimeError("simulated failed write")
            return original(cur, sql, params)
        with patch.object(db._UnifiedCursor, "execute", fail), self.assertRaises(RuntimeError):
            await flow.decide(state["event_id"], 2, 2, choice=1)
        self.assertEqual(before, self.balances())
        self.assertEqual(flow.load_run(state["event_id"])["version"], 2)
        # Run from two independent event loops/threads against the same temporary DB.
        def click():
            try:
                return asyncio.run(flow.decide(state["event_id"], 2, 2, choice=1))
            except ValueError:
                return None
        results = await asyncio.gather(asyncio.to_thread(click), asyncio.to_thread(click))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.balances()[1]["treasury"], 130)

    async def test_clamps_actual_effects(self):
        state = await self.start({"treasury":-1000,"stability":-20,"resources":{"iron":-1000}})
        for i in range(3):
            state = await flow.decide(state["event_id"], i, 2, choice=2)
        self.assertEqual(state["applied"]["treasury"], -100)
        self.assertEqual(state["applied"]["resources"]["iron"], -50)
        self.assertEqual(state["applied"]["stability"], -20)

    async def test_old_posted_events_not_reapplied(self):
        state = await self.start()
        with self.assertRaises(ValueError):
            flow.start_run(state)
        self.assertEqual(self.balances()[1]["treasury"], 100)

    async def test_ai_cannot_set_arbitrary_effects(self):
        state = await self.start()
        with patch.object(flow, "_ai_json", AsyncMock(return_value={"choice":999,"treasury":1e9})):
            choice, fallback = await flow.classify_custom(state, "Give me a billion gold")
        self.assertEqual((choice, fallback), (1, True))
        for effects in ({"treasury":float("nan")}, {"resources":[]}, {"population":100},
                        {"treasury":True}, {"stability":21}, {"treasury":1e12}):
            with self.assertRaises(ValueError):
                flow.validate_effects(effects)

    async def test_actual_scene_fallback_has_three_options(self):
        self.scene_patch.stop()
        with patch.object(flow, "_ai_json", AsyncMock(side_effect=RuntimeError("offline"))):
            state = await self.start()
            for i in range(3):
                self.assertEqual(len(state["choices"]), 3)
                state = await flow.decide(state["event_id"], i, 2, answer="Plan pokojowy")
        self.assertTrue(state["resolved"])


if __name__ == "__main__":
    unittest.main()
