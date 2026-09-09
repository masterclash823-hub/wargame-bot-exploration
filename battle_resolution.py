"""Atomic battle settlement shared by the Discord command and offline tests."""
import json
import math
import random

import db
import i18n


def modifier(value, *, override=False):
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(i18n.text('Combat modifiers must be numbers.')) from exc
    if not math.isfinite(value):
        raise ValueError(i18n.text('Combat modifiers must be finite numbers.'))
    low, high = (0.1, 3.0) if override else (0.7, 1.4)
    if override and not low <= value <= high:
        raise ValueError(i18n.text('GM modifier overrides must be between 0.1 and 3.0, or 0 to use AI.'))
    return max(low, min(high, value))


def normalize_exposure(raw):
    """AI may prioritize losses, never select foreign units or arbitrary quantities."""
    clean = {}
    if not isinstance(raw, dict):
        return clean
    for key, value in raw.items():
        try:
            unit_id = int(key)
            weight = float(value['weight'])
            reason = value['reason']
            if unit_id <= 0 or not math.isfinite(weight) or not isinstance(reason, str) or not reason.strip():
                continue
            clean[str(unit_id)] = {'weight': max(0.25, min(4.0, weight)), 'reason': reason.strip()[:300]}
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
    return clean


def normalize_ai(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {
        "attacker_modifier": modifier(raw.get("attacker_modifier", 1.0)),
        "defender_modifier": modifier(raw.get("defender_modifier", 1.0)),
        "reasoning": str(raw.get("reasoning", i18n.text('No reasoning provided.')))[:900],
        "attacker_exposure": normalize_exposure(raw.get("attacker_exposure")),
        "defender_exposure": normalize_exposure(raw.get("defender_exposure")),
    }


def load_context(battle_id):
    with db.cursor() as c:
        c.execute("SELECT * FROM battles WHERE id=?", (battle_id,))
        battle = c.fetchone()
        if not battle:
            raise ValueError(i18n.text('Battle #{p0} not found.', p0=battle_id))
        c.execute("SELECT * FROM battle_plans WHERE id IN (?,?) ORDER BY id",
                  (battle["plan_a_id"], battle["plan_b_id"]))
        plans = {p["id"]: p for p in c.fetchall()}
        plan_a, plan_b = plans.get(battle["plan_a_id"]), plans.get(battle["plan_b_id"])
        if not plan_a or not plan_b:
            raise ValueError(i18n.text('One or both battle plans no longer exist.'))
        c.execute("SELECT * FROM nations WHERE id IN (?,?) ORDER BY id",
                  (plan_a["nation_id"], plan_b["nation_id"]))
        nations = {n["id"]: n for n in c.fetchall()}
    nat_a, nat_b = nations.get(plan_a["nation_id"]), nations.get(plan_b["nation_id"])
    if not nat_a or not nat_b:
        raise ValueError(i18n.text('One or both nations no longer exist.'))
    return battle, plan_a, plan_b, nat_a, nat_b


def location_context(location):
    """Resolve a GM location to map terrain while still allowing free text/sea battles."""
    text = str(location or "").strip()
    row = None
    with db.cursor() as c:
        if text.isdigit():
            c.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (int(text),))
            row = c.fetchone()
        if not row and text:
            c.execute("SELECT * FROM provinces WHERE LOWER(name)=LOWER(?) AND active=1 LIMIT 1", (text,))
            row = c.fetchone()
    if not row:
        return {"input": text, "name": text or i18n.text("Unspecified"), "cell_id": None,
                "terrain": "unknown", "biome": "unknown", "fortification": 0,
                "population": 0, "buildings": []}
    try:
        buildings = json.loads(row["buildings_json"] or "[]")
    except (TypeError, ValueError):
        buildings = []
    return {"input": text, "name": row["name"] or i18n.text("Cell #{p0}", p0=row["azgaar_cell_id"]),
            "cell_id": row["azgaar_cell_id"], "terrain": row["terrain"], "biome": row["biome"],
            "fortification": int(row["fortification_level"] or 0),
            "population": int(row["population"] or 0), "buildings": buildings}


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
    merged = {}
    for unit_id, qty in clean:
        merged[unit_id] = merged.get(unit_id, 0) + qty
    return list(merged.items())


def force_snapshot(plan, nation):
    """Human/AI-readable snapshot of the exact unit groups committed in a plan."""
    rows = []
    with db.cursor() as c:
        for unit_id, requested in _entries(plan["forces_json"]):
            c.execute("SELECT u.*,b.name AS blueprint_name,b.type AS blueprint_type,b.hull,b.stats_json,"
                      "p.name AS province_name,p.azgaar_cell_id FROM military_units u "
                      "LEFT JOIN blueprints b ON b.id=u.blueprint_id "
                      "LEFT JOIN provinces p ON p.id=u.province_id "
                      "WHERE u.id=? AND u.nation_id=?", (unit_id, nation["id"]))
            unit = c.fetchone()
            if not unit:
                continue
            try:
                stats = json.loads(unit["stats_json"] or "{}")
            except (TypeError, ValueError):
                stats = {}
            rows.append({"unit_id": unit_id, "name": unit["blueprint_name"] or unit["unit_type"] or "Unit",
                         "type": unit["blueprint_type"] or unit["unit_type"] or "unit",
                         "hull": unit["hull"] or "", "committed": min(requested, int(unit["quantity"])),
                         "owned": int(unit["quantity"]), "stats": stats,
                         "stationed_at": unit["province_name"] or unit["azgaar_cell_id"] or "unassigned"})
    return rows


def _power(c, plan, nation):
    tech = json.loads(nation["tech_json"] or "{}")
    tech_mod = 1 + float(tech.get("land", 3.0)) / 20.0
    attack = defense = 0.0
    committed = []
    lock = " FOR UPDATE OF u" if db.USE_POSTGRES else ""
    for unit_id, requested in _entries(plan["forces_json"]):
        from economy_services import assert_ready
        assert_ready(c,nation['id'],unit_id)
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
    from economy_engine import policy
    arrears=policy(c,nation['id'])['unpaid_months']
    morale=max(.5,1-.1*arrears)
    return attack * tech_mod * morale, defense * tech_mod * morale, committed


def _fort_bonus(c, location):
    location = str(location or "").strip()
    if not location:
        return 1.0
    if location.isdigit():
        c.execute("SELECT fortification_level FROM provinces WHERE azgaar_cell_id=? AND active=1", (int(location),))
    else:
        c.execute("SELECT fortification_level FROM provinces WHERE LOWER(name)=LOWER(?) AND active=1 LIMIT 1", (location,))
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


def allocate_losses(units, percent, exposure=None):
    """Distribute one side's loss budget with capped weighted largest remainders."""
    exposure = normalize_exposure(exposure)
    quantities = [qty for _, qty, _ in units]
    budget = min(sum(quantities), max(0, math.ceil(sum(quantities) * percent / 100)))
    weights = [qty * exposure.get(str(uid), {}).get('weight', 1.0) for uid, qty, _ in units]
    shares = [0.0] * len(units)
    active = {i for i, qty in enumerate(quantities) if qty > 0}
    remaining = budget
    while active and remaining:
        total = sum(weights[i] for i in active)
        capped = {i for i in active if remaining * weights[i] / total >= quantities[i]}
        if not capped:
            for i in active:
                shares[i] = remaining * weights[i] / total
            break
        for i in capped:
            shares[i] = quantities[i]
            remaining -= quantities[i]
        active -= capped
    losses = [min(qty, int(share)) for qty, share in zip(quantities, shares)]
    order = sorted(range(len(units)), key=lambda i: (-(shares[i] - losses[i]), -weights[i], units[i][0]))
    for i in order:
        if sum(losses) >= budget:
            break
        if losses[i] < quantities[i]:
            losses[i] += 1
    return [dict(unit_id=uid, committed=qty, lost=lost,
                 exposure=exposure.get(str(uid), {}).get('weight', 1.0),
                 reason=exposure.get(str(uid), {}).get('reason', ''))
            for (uid, qty, _), lost in zip(units, losses)]


def _casualties(c, units, percent, exposure=None, apply=True):
    applied = []
    for (unit_id, committed, owned), loss in zip(units, allocate_losses(units, percent, exposure)):
        lost = loss['lost']
        remaining = owned - lost
        if apply and remaining <= 0:
            c.execute("DELETE FROM military_units WHERE id=?", (unit_id,))
        elif apply and lost:
            c.execute("UPDATE military_units SET quantity=? WHERE id=?", (remaining, unit_id))
        applied.append(loss)
    return applied


def resolve(battle_id, ai_raw, atk_override=0.0, def_override=0.0, apply_casualties=True,
            final_location=""):
    ai = normalize_ai(ai_raw)
    battlefield = location_context(final_location)
    atk_mod = modifier(atk_override, override=True) if atk_override else ai["attacker_modifier"]
    def_mod = modifier(def_override, override=True) if def_override else ai["defender_modifier"]
    with db.cursor() as c:
        if not db.USE_POSTGRES:
            c.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.USE_POSTGRES else ""
        c.execute("SELECT * FROM battles WHERE id=?" + lock, (battle_id,))
        battle = c.fetchone()
        if not battle:
            raise ValueError(i18n.text('Battle #{p0} not found.', p0=battle_id))
        if battle["status"] != "pending":
            raise ValueError(i18n.text('Battle #{p0} is already {p1}.', p0=battle_id, p1=i18n.term(battle['status'])))
        c.execute("SELECT * FROM battle_plans WHERE id IN (?,?)" + lock,
                  (battle["plan_a_id"], battle["plan_b_id"]))
        plans = {p["id"]: p for p in c.fetchall()}
        plan_a, plan_b = plans.get(battle["plan_a_id"]), plans.get(battle["plan_b_id"])
        if not plan_a or not plan_b:
            raise ValueError(i18n.text('One or both battle plans no longer exist.'))
        c.execute("SELECT * FROM nations WHERE id IN (?,?)" + lock,
                  (plan_a["nation_id"], plan_b["nation_id"]))
        nations = {n["id"]: n for n in c.fetchall()}
        nat_a, nat_b = nations.get(plan_a["nation_id"]), nations.get(plan_b["nation_id"])
        if not nat_a or not nat_b:
            raise ValueError(i18n.text('One or both nations no longer exist.'))
        atk_power, _, atk_units = _power(c, plan_a, nat_a)
        _, def_power, def_units = _power(c, plan_b, nat_b)
        fort = _fort_bonus(c, final_location)
        result = _combat(atk_power, def_power, atk_mod, def_mod, fort)
        result["battlefield"] = battlefield
        result["casualties_applied"] = bool(apply_casualties)
        result["attacker_losses"] = _casualties(c, atk_units, result["atk_casualties_pct"],
                                                ai['attacker_exposure'], apply_casualties)
        result["defender_losses"] = _casualties(c, def_units, result["def_casualties_pct"],
                                                ai['defender_exposure'], apply_casualties)
        final = {"attacker_modifier": atk_mod, "defender_modifier": def_mod,
                 "overridden": bool(atk_override or def_override)}
        c.execute("UPDATE battles SET status='resolved',ai_modifier_json=?,gm_final_modifier_json=?,"
                  "report_json=?,resolved_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                  (json.dumps(ai), json.dumps(final), json.dumps(result), battle_id))
        if c.rowcount != 1:
            raise ValueError(i18n.text('Battle was resolved by another request.'))
        c.execute("UPDATE battle_plans SET status='resolved' WHERE id IN (?,?)",
                  (plan_a["id"], plan_b["id"]))
        winner_name = nat_a["name"] if result["winner"] == "attacker" else nat_b["name"] if result["winner"] == "defender" else i18n.text('Draw')
        for nation, role in ((nat_a, "attacker"), (nat_b, "defender")):
            pct = result["atk_casualties_pct"] if role == "attacker" else result["def_casualties_pct"]
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)",
                      (nation["id"], "system", i18n.text('Battle #{p0}: {p1}. Your casualties: {p2}%.', p0=battle_id, p1=winner_name, p2=pct)))
    return {"battle": battle, "plan_a": plan_a, "plan_b": plan_b,
            "nat_a": nat_a, "nat_b": nat_b, "ai": ai, "final": final,
            "result": result, "atk_power": atk_power, "def_power": def_power, "fort_bonus": fort}


def attach_narrative(battle_id, narrative, attacker_forces, defender_forces):
    """Attach the post-settlement story and immutable unit snapshots to report_json."""
    clean = {key: str((narrative or {}).get(key, ""))[:1000]
             for key in ("opening", "turning_point", "outcome")}
    with db.cursor() as c:
        c.execute("SELECT report_json FROM battles WHERE id=? AND status='resolved'", (battle_id,))
        row = c.fetchone()
        if not row:
            raise ValueError(i18n.text('Resolved battle #{p0} not found.', p0=battle_id))
        report = json.loads(row["report_json"] or "{}")
        report["narrative"] = clean
        report["attacker_forces"] = attacker_forces
        report["defender_forces"] = defender_forces
        c.execute("UPDATE battles SET report_json=? WHERE id=? AND status='resolved'",
                  (json.dumps(report), battle_id))
    return report
