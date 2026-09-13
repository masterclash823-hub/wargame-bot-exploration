import asyncio
import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from test_regressions import DatabaseFixture, interaction
import battle_resolution as resolution
import config
import db
import i18n
from cogs import combat
from cogs.combat import CombatCog


class BattleResolveTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        with db.cursor() as c:
            for nation_id in (1, 2):
                c.execute("INSERT INTO blueprints(nation_id,type,name,hull,stats_json) VALUES(?,?,?,?,?)",
                          (nation_id, "unit", f"Infantry {nation_id}", "musketeers",
                           '{"attack":100,"defense":100,"hp":100}'))
                blueprint = c.lastrowid
                c.execute("INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(?,?,?)",
                          (nation_id, blueprint, 10))
                committed = c.lastrowid
                c.execute("INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(?,?,?)",
                          (nation_id, blueprint, 7))
                if nation_id == 1:
                    self.atk_unit = committed
                else:
                    self.def_unit = committed
            c.execute("INSERT INTO provinces(azgaar_cell_id,name,terrain,biome,fortification_level) VALUES(?,?,?,?,?)",
                      (55, "Harbor", "hills", "temperate", 2))

    def plan(self, nation_id, unit_id, status="matched"):
        return db.insert_returning_id(
            "INSERT INTO battle_plans(nation_id,forces_json,provinces_json,orders_text,status) VALUES(?,?,?,?,?)",
            (nation_id, json.dumps([{"unit_id": unit_id, "qty": 4}]), '["Harbor"]',
             "Advance | Location: Harbor | Forces: test", status))

    def battle(self):
        pa, pb = self.plan(1, self.atk_unit), self.plan(2, self.def_unit)
        bid = db.insert_returning_id(
            "INSERT INTO battles(plan_a_id,plan_b_id,gm_note,status) VALUES(?,?,?,?)",
            (pa, pb, "Fog", "pending"))
        return bid, pa, pb

    def quantities(self, nation_id):
        with db.cursor() as c:
            c.execute("SELECT quantity FROM military_units WHERE nation_id=? ORDER BY id", (nation_id,))
            return [row["quantity"] for row in c.fetchall()]

    async def test_match_returns_real_id(self):
        pa = self.plan(1, self.atk_unit, "unmatched")
        pb = self.plan(2, self.def_unit, "unmatched")
        inter = interaction(999, [NS(id=12, name=config.GM_ROLE_NAME)])
        await CombatCog.battle_match.callback(None, inter, pa, pb)
        embed = inter.response.send_message.call_args.kwargs["embed"]
        self.assertRegex(embed.title, r"Battle #\d+ Created")
        self.assertNotIn("None", embed.title)
        with db.cursor() as c:
            c.execute("SELECT id FROM battles ORDER BY id DESC LIMIT 1")
            self.assertIn(f"#{c.fetchone()['id']}", embed.title)

    async def test_command_resolves_and_only_hurts_committed_units(self):
        bid, pa, pb = self.battle()
        bot = NS(get_channel=lambda _: None)
        inter = interaction(999, [NS(id=12, name=config.GM_ROLE_NAME)])
        i18n.set_user_language(999, 'pl')
        i18n.set_user_language(1, 'en')
        i18n.set_user_language(2, 'en')
        with patch.object(combat, "_get_ai_modifier", AsyncMock(return_value={
                "attacker_modifier": 1, "defender_modifier": 1, "reasoning": "Even"})) as modifier, \
             patch.object(combat, "_get_ai_battle_report", AsyncMock(return_value={
                "opening":"The lines met on the hills.", "turning_point":"A flanking attack broke the line.",
                "outcome":"The defender withdrew."})) as narrate, \
             patch.object(resolution.random, "uniform", return_value=1):
            await CombatCog.battle_resolve.callback(CombatCog(bot), inter, bid, "55")
        self.assertEqual(modifier.call_args.kwargs['lang'], 'pl')
        self.assertEqual(narrate.call_args.kwargs['lang'], 'pl')
        self.assertEqual(self.quantities(1), [8, 7])
        self.assertEqual(self.quantities(2), [9, 7])
        with db.cursor() as c:
            c.execute("SELECT * FROM battles WHERE id=?", (bid,))
            battle = c.fetchone()
            c.execute("SELECT status FROM battle_plans WHERE id IN (?,?) ORDER BY id", (pa, pb))
            statuses = [row["status"] for row in c.fetchall()]
        self.assertEqual(battle["status"], "resolved")
        self.assertIsNotNone(battle["resolved_at"])
        self.assertEqual(statuses, ["resolved", "resolved"])
        report = json.loads(battle["report_json"])
        self.assertEqual(narrate.call_args.args[7]['attacker_losses'], report['attacker_losses'])
        self.assertEqual(narrate.call_args.args[7]['defender_losses'], report['defender_losses'])
        self.assertEqual(report["attacker_losses"][0]["committed"], 4)
        self.assertEqual(report["attacker_losses"][0]["lost"], 2)
        self.assertEqual(report["defender_losses"][0]["lost"], 1)
        self.assertEqual(report["battlefield"]["terrain"], "hills")
        self.assertEqual(report["battlefield"]["cell_id"], 55)
        self.assertIn("flanking", report["narrative"]["turning_point"])
        self.assertEqual(report["attacker_forces"][0]["name"], "Infantry 1")
        self.assertIn("rozstrzygnięta", inter.followup.send.call_args.kwargs["embed"].title)

    async def test_missing_final_location_opens_modal_without_resolving(self):
        bid, _, _ = self.battle()
        inter = interaction(999, [NS(id=12, name=config.GM_ROLE_NAME)])
        inter.response.send_modal = AsyncMock()
        await CombatCog.battle_resolve.callback(
            CombatCog(NS(get_channel=lambda _: None)), inter, bid)
        inter.response.send_modal.assert_awaited_once()
        self.assertIsInstance(inter.response.send_modal.call_args.args[0], combat.BattleLocationModal)
        with db.cursor() as c:
            c.execute("SELECT status FROM battles WHERE id=?", (bid,))
            self.assertEqual(c.fetchone()["status"], "pending")

    async def test_resolve_without_id_lists_legacy_pending_battle(self):
        bid, _, _ = self.battle()
        inter = interaction(999, [NS(id=12, name=config.GM_ROLE_NAME)])
        await CombatCog.battle_resolve.callback(CombatCog(NS(get_channel=lambda _: None)), inter)
        embed = inter.followup.send.call_args.kwargs["embed"]
        self.assertIn(f"#{bid}", embed.description)
        self.assertIn("A → B", embed.description)

    async def test_no_casualties_mode(self):
        bid, _, _ = self.battle()
        with patch.object(resolution.random, "uniform", return_value=1):
            settled = resolution.resolve(bid, {}, apply_casualties=False, final_location="Harbor")
        self.assertEqual(self.quantities(1), [10, 7])
        self.assertFalse(settled["result"]["casualties_applied"])
        self.assertEqual(settled["fort_bonus"], 1.2)
        self.assertTrue(settled['result']['attacker_losses'])

    async def test_tactical_exposure_changes_real_unit_losses(self):
        bid, pa, _ = self.battle()
        with db.cursor() as c:
            c.execute('SELECT id FROM military_units WHERE nation_id=1 ORDER BY id')
            front, reserve = [r['id'] for r in c.fetchall()]
            c.execute('UPDATE battle_plans SET forces_json=? WHERE id=?',
                      (json.dumps([{'unit_id':front,'qty':4}, {'unit_id':reserve,'qty':4}]), pa))
        ai = {'attacker_exposure': {str(front): {'weight':4, 'reason':'Assault'},
                                    str(reserve): {'weight':.25, 'reason':'Rear reserve'}}}
        with patch.object(resolution.random, 'uniform', return_value=1):
            settled = resolution.resolve(bid, ai, final_location='55')
        losses = settled['result']['attacker_losses']
        self.assertGreater(losses[0]['lost'], losses[1]['lost'])
        self.assertEqual(self.quantities(1), [10-losses[0]['lost'], 7-losses[1]['lost']])
        with db.cursor() as c:
            c.execute('SELECT report_json FROM battles WHERE id=?', (bid,))
            self.assertEqual(json.loads(c.fetchone()['report_json'])['attacker_losses'], losses)

    async def test_duplicate_and_concurrent_resolution_pay_once(self):
        bid, _, _ = self.battle()
        def run():
            try:
                return resolution.resolve(bid, {})
            except ValueError:
                return None
        results = await asyncio.gather(asyncio.to_thread(run), asyncio.to_thread(run))
        self.assertEqual(sum(item is not None for item in results), 1)
        after = self.quantities(1)
        with self.assertRaises(ValueError):
            resolution.resolve(bid, {})
        self.assertEqual(after, self.quantities(1))

    async def test_failure_rolls_back_status_and_losses(self):
        bid, _, _ = self.battle()
        original = db._UnifiedCursor.execute
        def fail(cur, sql, params=()):
            if sql.startswith("INSERT INTO nation_history"):
                raise RuntimeError("simulated write failure")
            return original(cur, sql, params)
        before = self.quantities(1)
        with patch.object(db._UnifiedCursor, "execute", fail), self.assertRaises(RuntimeError):
            resolution.resolve(bid, {})
        self.assertEqual(before, self.quantities(1))
        with db.cursor() as c:
            c.execute("SELECT status FROM battles WHERE id=?", (bid,))
            self.assertEqual(c.fetchone()["status"], "pending")

    async def test_modifier_validation(self):
        normalized = resolution.normalize_ai({"attacker_modifier":99,
            "defender_modifier":-5, "reasoning":"x" * 2000})
        self.assertEqual((normalized["attacker_modifier"], normalized["defender_modifier"]), (1.4, 0.7))
        self.assertEqual(len(normalized["reasoning"]), 900)
        for value in (float("nan"), float("inf"), "bad", -1, 4):
            with self.assertRaises(ValueError):
                resolution.modifier(value, override=True)

    async def test_ai_uses_config_model_and_falls_back(self):
        generate = Mock(return_value=NS(text='{"attacker_modifier":2,"defender_modifier":0,"reasoning":"ok"}'))
        google = NS(genai=NS(Client=Mock(return_value=NS(models=NS(generate_content=generate)))))
        plan = {"location_text":"Harbor","orders_text":"Advance","forces_note":""}
        with db.cursor() as c:
            c.execute("SELECT * FROM nations ORDER BY id")
            nations = c.fetchall()
        battlefield = resolution.location_context("55")
        forces = [{"name":"Infantry 1","committed":4}]
        with patch.dict(sys.modules, {"google": google}):
            result = await combat._get_ai_modifier(
                plan, plan, nations[0], nations[1], battlefield, forces, forces)
        self.assertEqual(generate.call_args.kwargs["model"], config.GEMINI_MODEL)
        prompt = generate.call_args.kwargs["contents"]
        self.assertIn('"terrain": "hills"', prompt)
        self.assertIn("Infantry 1", prompt)
        self.assertIn("Advance", prompt)
        self.assertEqual((result["attacker_modifier"], result["defender_modifier"]), (1.4, 0.7))
        with patch.dict(sys.modules, {"google": NS(genai=NS(Client=Mock(side_effect=RuntimeError("offline"))))}):
            result = await combat._get_ai_modifier(plan, plan, nations[0], nations[1])
        self.assertEqual(result["attacker_modifier"], 1)

    async def test_detailed_report_fallback_names_units_terrain_and_plans(self):
        plan_a = {"location_text":"Harbor", "orders_text":"Flank the ridge", "forces_note":""}
        plan_b = {"location_text":"Harbor", "orders_text":"Hold the walls", "forces_note":""}
        with db.cursor() as c:
            c.execute("SELECT * FROM nations ORDER BY id")
            nations = c.fetchall()
        result = {"winner":"attacker", "eff_attack":120, "eff_defense":90,
                  "atk_casualties_pct":20, "def_casualties_pct":40}
        with patch.dict(sys.modules, {"google": NS(genai=NS(Client=Mock(side_effect=RuntimeError("offline"))))}):
            story = await combat._get_ai_battle_report(
                plan_a, plan_b, nations[0], nations[1], resolution.location_context("55"),
                [{"name":"Infantry 1", "committed":4}],
                [{"name":"Infantry 2", "committed":3}], result, "The ridge favored A.")
        self.assertIn("Infantry 1", story["opening"])
        self.assertIn("Flank the ridge", story["opening"])
        self.assertIn("hills", story["opening"])
        self.assertIn("120", story["turning_point"])
        self.assertIn("20%", story["outcome"])

    async def test_report_explicit_language_overrides_ambient_context(self):
        generate = Mock(return_value=NS(text=json.dumps(dict(opening='A', turning_point='B', outcome='C'))))
        google = NS(genai=NS(Client=Mock(return_value=NS(models=NS(generate_content=generate)))))
        plan = dict(orders_text='Defend', location_text='Harbor')
        result = dict(winner='attacker', eff_attack=120, eff_defense=90,
                      atk_casualties_pct=20, def_casualties_pct=40)
        args = (plan, plan, {'name':'A'}, {'name':'B'}, {'terrain':'hills'}, [], [], result, '')
        for lang, other, expected in [('pl', 'en', 'Polish'), ('en', 'pl', 'English')]:
            with i18n.using_language(other), patch.dict(sys.modules, {'google': google}):
                await combat._get_ai_battle_report(*args, lang=lang)
                self.assertIn(f'written in {expected}', generate.call_args.kwargs['contents'])
                self.assertEqual(i18n.current_language(), other)
            with i18n.using_language(other), patch.dict(sys.modules, {'google': NS(
                    genai=NS(Client=Mock(side_effect=RuntimeError('offline'))))}):
                story = await combat._get_ai_battle_report(*args, lang=lang)
                self.assertIn('Straty' if lang == 'pl' else 'casualties', story['outcome'])

    async def test_postgres_specific_sql_is_portable(self):
        source = Path("battle_resolution.py").read_text(encoding="utf-8")
        combat_source = Path("cogs/combat.py").read_text(encoding="utf-8")
        self.assertNotIn("datetime('now')", source + combat_source)
        self.assertIn("CURRENT_TIMESTAMP", source)
        self.assertIn("FOR UPDATE OF u", source)


if __name__ == "__main__":
    unittest.main()
