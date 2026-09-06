"""Atomic battle settlement shared by the Discord command and offline tests."""
import json
import math
import random

import db


def modifier(value, *, override=False):
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Combat modifiers must be numbers.") from exc
    if not math.isfinite(value):
        raise ValueError("Combat modifiers must be finite numbers.")
    low, high = (0.1, 3.0) if override else (0.7, 1.4)
    if override and not low <= value <= high:
        raise ValueError("GM modifier overrides must be between 0.1 and 3.0, or 0 to use AI.")
    return max(low, min(high, value))


def normalize_ai(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {
        "attacker_modifier": modifier(raw.get("attacker_modifier", 1.0)),
        "defender_modifier": modifier(raw.get("defender_modifier", 1.0)),
        "reasoning": str(raw.get("reasoning", "No reasoning provided."))[:900],
    }


def load_context(battle_id):
    with db.cursor() as c:
        c.execute("SELECT * FROM battles WHERE id=?", (battle_id,))
        battle = c.fetchone()
        if not battle:
            raise ValueError(f"Battle #{battle_id} not found.")
        c.execute("SELECT * FROM battle_plans WHERE id IN (?,?) ORDER BY id",
                  (battle["plan_a_id"], battle["plan_b_id"]))
        plans = {p["id"]: p for p in c.fetchall()}
        plan_a, plan_b = plans.get(battle["plan_a_id"]), plans.get(battle["plan_b_id"])
        if not plan_a or not plan_b:
            raise ValueError("One or both battle plans no longer exist.")
        c.execute("SELECT * FROM nations WHERE id IN (?,?) ORDER BY id",
                  (plan_a["nation_id"], plan_b["nation_id"]))
        nations = {n["id"]: n for n in c.fetchall()}
    nat_a, nat_b = nations.get(plan_a["nation_id"]), nations.get(plan_b["nation_id"])
    if not nat_a or not nat_b:
        raise ValueError("One or both nations no longer exist.")
    return battle, plan_a, plan_b, nat_a, nat_b


def _entries(raw):
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    clean = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        try:
            unit_id, qty = int(item.get("unit_id")), int(item.get("qty", 1))
        except (TypeError, ValueError):
            continue
        if unit_id > 0 and qty > 0:
            clean.append((unit_id, qty))
    return clean


def _power(c, plan, nation):
    tech = json.loads(nation["tech_json"] or "{}")
    tech_mod = 1 + float(tech.get("land", 3.0)) / 20.0
    attack = defense = 0.0
    committed = []
    lock = " FOR UPDATE OF u" if db.USE_POSTGRES else ""
    for unit_id, requested in _entries(plan["forces_json"]):
        c.execute("SELECT u.*,b.stats_json,b.type AS btype FROM military_units u "
                  "LEFT JOIN blueprints b ON u.blueprint_id=b.id "
                  "WHERE u.id=? AND u.nation_id=?" + lock, (unit_id, nation["id"]))
        unit = c.fetchone()
        if not unit:
            continue
        qty = min(requested, max(0, int(unit["quantity"])))
        if qty <= 0:
            continue
        try:
            stats = json.loads(unit["stats_json"] or "{}")
        except (TypeError, ValueError):
            stats = {}
        attack += float(stats.get("attack", 0)) * qty
        defense += float(stats.get("hp", 100) if unit["btype"] == "ship" else stats.get("defense", 0)) * qty * (0.1 if unit["btype"] == "ship" else 1)
        committed.append((unit["id"], qty, int(unit["quantity"])))
    return attack * tech_mod, defense * tech_mod, committed


def _fort_bonus(c, plan):
    try:
        locations = json.loads(plan["provinces_json"])
        location = str(locations[0]).strip() if locations else ""
    except (TypeError, ValueError):
        location = ""
    if not location:
        return 1.0
    c.execute("SELECT fortification_level FROM provinces WHERE name LIKE ? AND active=1 LIMIT 1",
              (f"%{location[:20]}%",))
    row = c.fetchone()
    return 1.0 + float(row["fortification_level"] or 0) * 0.1 if row else 1.0


def _combat(atk, defense, atk_mod, def_mod, fort):
    roll = random.uniform(0.85, 1.15)
    effective_atk, effective_def = atk * atk_mod * roll, defense * def_mod * fort
    if effective_atk == effective_def == 0:
        winner, atk_loss, def_loss = "draw", 0, 0
    elif effective_atk >= effective_def:
        winner, margin = "attacker", effective_atk / max(effective_def, 1)
        atk_loss, def_loss = max(5, int(30 / margin)), min(80, int(30 * margin))
    else:
        winner, margin = "defender", effective_def / max(effective_atk, 1)
        def_loss, atk_loss = max(5, int(30 / margin)), min(80, int(30 * margin))
    return {"winner": winner, "eff_attack": round(effective_atk, 1),
            "eff_defense": round(effective_def, 1), "atk_casualties_pct": atk_loss,
            "def_casualties_pct": def_loss, "roll": round(roll, 3)}


def _casualties(c, units, percent):
    applied = []
    for unit_id, committed, owned in units:
        lost = min(committed, math.ceil(committed * percent / 100)) if percent else 0
        remaining = owned - lost
        if remaining <= 0:
            c.execute("DELETE FROM military_units WHERE id=?", (unit_id,))
        elif lost:
            c.execute("UPDATE military_units SET quantity=? WHERE id=?", (remaining, unit_id))
        applied.append({"unit_id": unit_id, "committed": committed, "lost": lost})
    return applied


def resolve(battle_id, ai_raw, atk_override=0.0, def_override=0.0, apply_casualties=True):
    ai = normalize_ai(ai_raw)
    atk_mod = modifier(atk_override, override=True) if atk_override else ai["attacker_modifier"]
    def_mod = modifier(def_override, override=True) if def_override else ai["defender_modifier"]
    with db.cursor() as c:
        if not db.USE_POSTGRES:
            c.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.USE_POSTGRES else ""
        c.execute("SELECT * FROM battles WHERE id=?" + lock, (battle_id,))
        battle = c.fetchone()
        if not battle:
            raise ValueError(f"Battle #{battle_id} not found.")
        if battle["status"] != "pending":
            raise ValueError(f"Battle #{battle_id} is already {battle['status']}.")
        c.execute("SELECT * FROM battle_plans WHERE id IN (?,?)" + lock,
                  (battle["plan_a_id"], battle["plan_b_id"]))
        plans = {p["id"]: p for p in c.fetchall()}
        plan_a, plan_b = plans.get(battle["plan_a_id"]), plans.get(battle["plan_b_id"])
        if not plan_a or not plan_b:
            raise ValueError("One or both battle plans no longer exist.")
        c.execute("SELECT * FROM nations WHERE id IN (?,?)" + lock,
                  (plan_a["nation_id"], plan_b["nation_id"]))
        nations = {n["id"]: n for n in c.fetchall()}
        nat_a, nat_b = nations.get(plan_a["nation_id"]), nations.get(plan_b["nation_id"])
        if not nat_a or not nat_b:
            raise ValueError("One or both nations no longer exist.")
        atk_power, _, atk_units = _power(c, plan_a, nat_a)
        _, def_power, def_units = _power(c, plan_b, nat_b)
        fort = _fort_bonus(c, plan_b)
        result = _combat(atk_power, def_power, atk_mod, def_mod, fort)
        result["casualties_applied"] = bool(apply_casualties)
        if apply_casualties:
            result["attacker_losses"] = _casualties(c, atk_units, result["atk_casualties_pct"])
            result["defender_losses"] = _casualties(c, def_units, result["def_casualties_pct"])
        final = {"attacker_modifier": atk_mod, "defender_modifier": def_mod,
                 "overridden": bool(atk_override or def_override)}
        c.execute("UPDATE battles SET status='resolved',ai_modifier_json=?,gm_final_modifier_json=?,"
                  "report_json=?,resolved_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                  (json.dumps(ai), json.dumps(final), json.dumps(result), battle_id))
        if c.rowcount != 1:
            raise ValueError("Battle was resolved by another request.")
        c.execute("UPDATE battle_plans SET status='resolved' WHERE id IN (?,?)",
                  (plan_a["id"], plan_b["id"]))
        winner_name = nat_a["name"] if result["winner"] == "attacker" else nat_b["name"] if result["winner"] == "defender" else "Draw"
        for nation, role in ((nat_a, "attacker"), (nat_b, "defender")):
            pct = result["atk_casualties_pct"] if role == "attacker" else result["def_casualties_pct"]
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)",
                      (nation["id"], "system", f"Battle #{battle_id}: {winner_name}. Your casualties: {pct}%."))
    return {"battle": battle, "plan_a": plan_a, "plan_b": plan_b,
            "nat_a": nat_a, "nat_b": nat_b, "ai": ai, "final": final,
            "result": result, "atk_power": atk_power, "def_power": def_power, "fort_bonus": fort}
