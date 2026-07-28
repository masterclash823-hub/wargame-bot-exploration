"""
Combat commands:
  /battle plan          - submit a battle plan (forces, location text, orders)
  /battle plans_pending - GM: list all unmatched plans
  /battle match         - GM: match two plans into a battle
  /battle view          - view a battle (full details for GM/parties, summary for others)
  /battle resolve       - GM: get AI modifier, then post battle report
  /battle override      - GM: manually set modifier before resolving
  /diplomacy war        - declare war on a nation
  /diplomacy peace      - propose/accept peace
  /diplomacy alliance   - propose/accept alliance
  /diplomacy status     - view your diplomatic relations

Combat formula:
  effective_attack  = Σ(unit.attack × qty) × (1 + land_tech/20) × ai_modifier × roll(0.85-1.15)
  effective_defense = Σ(unit.defense × qty) × (1 + land_tech/20) × fort_bonus
  naval uses ship ATK/HP instead of unit ATK/DEF
  winner: higher effective value. Casualties proportional to ratio.
"""
import json
import random
import asyncio
import aiohttp
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import i18n


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _lang(i):
    return i18n.get_user_language(i.user.id, i.locale.value if i.locale else None)

def _gm(i):
    return bool(i.guild) and any(r.name == config.GM_ROLE_NAME for r in i.user.roles)

def _nat_owner(uid):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE owner_id=?", (str(uid),))
        return c.fetchone()

def _nat_name(name):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (name,))
        return c.fetchone()

def _nat_id(nid):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE id=?", (nid,))
        return c.fetchone()

def _log(nid, src, txt):
    with db.cursor() as c:
        c.execute(
            "INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)",
            (nid, src, txt)
        )

def _cfg(key, default=""):
    with db.cursor() as c:
        c.execute("SELECT value FROM game_config WHERE key=?", (key,))
        row = c.fetchone()
    return row["value"] if row else default

def _get_relation(a_id, b_id):
    lo, hi = min(a_id, b_id), max(a_id, b_id)
    with db.cursor() as c:
        c.execute(
            "SELECT status FROM relations WHERE nation_a_id=? AND nation_b_id=?",
            (lo, hi)
        )
        row = c.fetchone()
    return row["status"] if row else "peace"

def _set_relation(a_id, b_id, status):
    lo, hi = min(a_id, b_id), max(a_id, b_id)
    with db.cursor() as c:
        c.execute(
            "INSERT INTO relations(nation_a_id,nation_b_id,status) VALUES(?,?,?) "
            "ON CONFLICT(nation_a_id,nation_b_id) DO UPDATE SET status=excluded.status",
            (lo, hi, status)
        )

# ---------------------------------------------------------------------------
# Combat resolution
# ---------------------------------------------------------------------------
def _forces_power(forces_json: str, nation_id: int) -> tuple[float, float, int]:
    """
    Parse a forces string and compute attack, defense, and unit count.
    Forces is a list of {unit_id, qty} dicts OR a plain text description
    (in which case we return 0s and let the GM/AI handle it).
    Returns (attack, defense, total_units).
    """
    try:
        forces = json.loads(forces_json)
        if not isinstance(forces, list):
            return 0.0, 0.0, 0
    except (json.JSONDecodeError, TypeError):
        return 0.0, 0.0, 0

    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE id=?", (nation_id,))
        nat = c.fetchone()
    tech     = json.loads(nat["tech_json"]) if nat else {}
    land_tech = tech.get("land", 3.0)
    tech_mod  = 1 + land_tech / 20.0

    total_atk = total_def = total_qty = 0

    for entry in forces:
        uid = entry.get("unit_id")
        qty = entry.get("qty", 1)
        with db.cursor() as c:
            c.execute(
                "SELECT u.*,b.stats_json,b.type as btype FROM military_units u "
                "LEFT JOIN blueprints b ON u.blueprint_id=b.id "
                "WHERE u.id=? AND u.nation_id=?",
                (uid, nation_id)
            )
            unit = c.fetchone()
        if not unit:
            continue
        stats    = json.loads(unit["stats_json"] or "{}")
        actual_q = min(qty, unit["quantity"])
        total_qty += actual_q
        if unit["btype"] == "ship":
            total_atk += stats.get("attack", 0) * actual_q
            total_def += stats.get("hp", 100) * actual_q * 0.1
        else:
            total_atk += stats.get("attack", 0) * actual_q
            total_def += stats.get("defense", 0) * actual_q

    return total_atk * tech_mod, total_def * tech_mod, total_qty


def _resolve_combat(
    atk_power: float,
    def_power: float,
    atk_modifier: float,
    def_modifier: float,
    fort_bonus: float = 1.0,
) -> dict:
    """
    Run the deterministic formula and return a result dict.
    """
    roll          = random.uniform(0.85, 1.15)
    eff_attack    = atk_power * atk_modifier * roll
    eff_defense   = def_power * def_modifier * fort_bonus

    if eff_attack == 0 and eff_defense == 0:
        return {
            "winner": "draw",
            "eff_attack": 0, "eff_defense": 0,
            "atk_casualties_pct": 0, "def_casualties_pct": 0,
            "roll": round(roll, 3),
        }

    if eff_attack >= eff_defense:
        winner = "attacker"
        margin = eff_attack / max(eff_defense, 1)
        atk_cas = max(5,  int(30 / margin))
        def_cas = min(80, int(30 * margin))
    else:
        winner = "defender"
        margin = eff_defense / max(eff_attack, 1)
        def_cas = max(5,  int(30 / margin))
        atk_cas = min(80, int(30 * margin))

    return {
        "winner":             winner,
        "eff_attack":         round(eff_attack,  1),
        "eff_defense":        round(eff_defense, 1),
        "atk_casualties_pct": atk_cas,
        "def_casualties_pct": def_cas,
        "roll":               round(roll, 3),
    }


async def _get_ai_modifier(plan_a: dict, plan_b: dict, nat_a: dict, nat_b: dict) -> dict:
    """
    Call Gemini to review battle plans and return structured modifiers.
    Returns {"attacker_modifier": float, "defender_modifier": float, "reasoning": str}
    on success, or a default dict on failure.
    """
    tech_a = json.loads(nat_a["tech_json"])
    tech_b = json.loads(nat_b["tech_json"])

    prompt = f"""You are a military analyst for a fantasy wargame set in the Age of Exploration.
Review these two battle plans and return ONLY a JSON object with exactly these keys:
  "attacker_modifier": (float between 0.7 and 1.4)
  "defender_modifier": (float between 0.7 and 1.4)
  "reasoning": (one sentence explaining the modifiers)

Attacker: {nat_a['name']}
Land tech: {tech_a.get('land', 3):.1f} | Naval tech: {tech_a.get('naval', 3):.1f}
Location/direction: {plan_a['location_text']}
Orders: {plan_a['orders_text']}
Forces note: {plan_a.get('forces_note', 'not specified')}

Defender: {nat_b['name']}
Land tech: {tech_b.get('land', 3):.1f} | Naval tech: {tech_b.get('naval', 3):.1f}
Location/direction: {plan_b['location_text']}
Orders: {plan_b['orders_text']}
Forces note: {plan_b.get('forces_note', 'not specified')}

Consider: terrain (from location text), tactical creativity, supply lines, flanking,
weather if mentioned, and anything else tactically relevant.
Respond ONLY with the JSON object. No markdown, no explanation outside the JSON."""

    try:
        url     = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}
        body    = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": {"temperature": 0.3, "maxOutputTokens": 300}}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        return {
            "attacker_modifier": float(result.get("attacker_modifier", 1.0)),
            "defender_modifier": float(result.get("defender_modifier", 1.0)),
            "reasoning":         str(result.get("reasoning", "No reasoning provided.")),
        }
    except Exception as e:
        print(f"[COMBAT AI] Gemini call failed: {e}", flush=True)
        return {
            "attacker_modifier": 1.0,
            "defender_modifier": 1.0,
            "reasoning": f"AI unavailable ({type(e).__name__}) — modifiers defaulted to 1.0.",
        }


def _apply_casualties(nation_id: int, casualty_pct: int):
    """Reduce all military unit quantities by casualty_pct%. Remove groups that hit 0."""
    with db.cursor() as c:
        c.execute("SELECT * FROM military_units WHERE nation_id=?", (nation_id,))
        units = c.fetchall()
    for u in units:
        new_qty = max(0, int(u["quantity"] * (1 - casualty_pct / 100)))
        with db.cursor() as c:
            if new_qty == 0:
                c.execute("DELETE FROM military_units WHERE id=?", (u["id"],))
            else:
                c.execute("UPDATE military_units SET quantity=? WHERE id=?", (new_qty, u["id"]))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class CombatCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    battle_grp   = app_commands.Group(name="battle",    description="Battle commands / Bitwy")
    diplomacy_grp = app_commands.Group(name="diplomacy", description="Diplomacy commands / Dyplomacja")

    # ======================================================== BATTLE PLANS

    @battle_grp.command(name="plan", description="Submit a battle plan / Wyslij plan bitwy")
    @app_commands.describe(
        location="Where your forces are heading (free text — be descriptive) / Dokad ida twoje sily",
        orders="Tactical orders and intent / Rozkazy taktyczne",
        forces_note="Brief description of forces committed (optional) / Krotki opis sil",
        unit_ids="Comma-separated unit group IDs to commit (optional) / ID grup jednostek",
    )
    async def battle_plan(self, interaction: discord.Interaction,
                          location: str, orders: str,
                          forces_note: str = "", unit_ids: str = ""):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return

        # Parse unit IDs if provided
        forces = []
        if unit_ids.strip():
            try:
                ids = [int(x.strip()) for x in unit_ids.split(",") if x.strip()]
            except ValueError:
                await interaction.response.send_message(
                    "Invalid unit IDs — use comma-separated numbers e.g. 1,2,5", ephemeral=True)
                return
            for uid in ids:
                with db.cursor() as c:
                    c.execute("SELECT * FROM military_units WHERE id=? AND nation_id=?",
                              (uid, nat["id"]))
                    u = c.fetchone()
                if not u:
                    await interaction.response.send_message(
                        f"Unit group #{uid} not found or not yours.", ephemeral=True)
                    return
                forces.append({"unit_id": uid, "qty": u["quantity"]})

        plan_data = {
            "location_text": location,
            "orders_text":   orders,
            "forces_note":   forces_note,
        }

        with db.cursor() as c:
            c.execute(
                "INSERT INTO battle_plans(nation_id,forces_json,provinces_json,orders_text,status)"
                " VALUES(?,?,?,?,?)",
                (nat["id"], json.dumps(forces), json.dumps([location]),
                 f"{orders} | Location: {location} | Forces: {forces_note or 'see unit_ids'}",
                 "unmatched")
            )
            plan_id = c.lastrowid

        _log(nat["id"], "player",
             f"Submitted battle plan #{plan_id}. Location: {location}.")

        embed = discord.Embed(
            title=f"⚔️ Battle Plan #{plan_id} Submitted",
            description="Your plan has been received. The Game Master will match it when opposing plans arrive.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Location/Direction", value=location,              inline=False)
        embed.add_field(name="Orders",             value=orders,                inline=False)
        if forces_note:
            embed.add_field(name="Forces note",   value=forces_note,           inline=False)
        if forces:
            embed.add_field(name="Committed units",
                            value=", ".join(f"Group #{f['unit_id']}" for f in forces),
                            inline=False)
        embed.set_footer(text="Only you and the GM can see this plan.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /battle plans_pending
    @battle_grp.command(name="plans_pending",
                        description="[GM] List all unmatched battle plans / [GM] Lista oczekujacych planow")
    async def plans_pending(self, interaction: discord.Interaction):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute(
                "SELECT p.*,n.name as nname,n.flag as nflag"
                " FROM battle_plans p JOIN nations n ON p.nation_id=n.id"
                " WHERE p.status='unmatched' ORDER BY p.submitted_at",
            )
            rows = c.fetchall()
        if not rows:
            await interaction.response.send_message("No unmatched battle plans.", ephemeral=True)
            return
        embed = discord.Embed(title="⚔️ Pending Battle Plans", color=discord.Color.red())
        for r in rows:
            forces   = json.loads(r["forces_json"])
            loc      = json.loads(r["provinces_json"])
            loc_str  = loc[0] if loc else "not specified"
            # Extract orders from the combined field
            orders_full = r["orders_text"]
            orders_disp = orders_full.split(" | Location:")[0][:200]
            embed.add_field(
                name=f"[#{r['id']}] {r['nflag'] or ''} {r['nname']} — {r['submitted_at'][:10]}",
                value=(
                    f"**Location:** {loc_str[:100]}\n"
                    f"**Orders:** {orders_disp}\n"
                    f"**Units:** {len(forces)} group(s) committed"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /battle match
    @battle_grp.command(name="match",
                        description="[GM] Match two plans into a battle / [GM] Polacz dwa plany")
    @app_commands.describe(
        plan_a_id="First plan ID (attacker) / ID pierwszego planu",
        plan_b_id="Second plan ID (defender) / ID drugiego planu",
        gm_note="Context note — terrain, ambush, conditions (optional)",
    )
    async def battle_match(self, interaction: discord.Interaction,
                           plan_a_id: int, plan_b_id: int, gm_note: str = ""):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return

        with db.cursor() as c:
            c.execute("SELECT * FROM battle_plans WHERE id=?", (plan_a_id,))
            plan_a = c.fetchone()
            c.execute("SELECT * FROM battle_plans WHERE id=?", (plan_b_id,))
            plan_b = c.fetchone()

        if not plan_a:
            await interaction.response.send_message(f"Plan #{plan_a_id} not found.", ephemeral=True)
            return
        if not plan_b:
            await interaction.response.send_message(f"Plan #{plan_b_id} not found.", ephemeral=True)
            return
        if plan_a["nation_id"] == plan_b["nation_id"]:
            await interaction.response.send_message(
                "Both plans belong to the same nation.", ephemeral=True)
            return

        with db.cursor() as c:
            c.execute(
                "INSERT INTO battles(plan_a_id,plan_b_id,gm_note,status)"
                " VALUES(?,?,?,?)",
                (plan_a_id, plan_b_id, gm_note, "pending")
            )
            battle_id = c.lastrowid
            c.execute("UPDATE battle_plans SET status='matched' WHERE id=? OR id=?",
                      (plan_a_id, plan_b_id))

        nat_a = _nat_id(plan_a["nation_id"])
        nat_b = _nat_id(plan_b["nation_id"])

        embed = discord.Embed(
            title=f"⚔️ Battle #{battle_id} Created",
            color=discord.Color.red(),
        )
        embed.add_field(name="Attacker", value=f"{nat_a['flag'] or ''} {nat_a['name']}", inline=True)
        embed.add_field(name="Defender", value=f"{nat_b['flag'] or ''} {nat_b['name']}", inline=True)
        if gm_note:
            embed.add_field(name="GM Context", value=gm_note, inline=False)
        embed.set_footer(
            text=f"Run /battle resolve {battle_id} to get AI modifier and resolve.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /battle view
    @battle_grp.command(name="view",
                        description="View a battle / Szczegoly bitwy")
    @app_commands.describe(battle_id="Battle ID")
    async def battle_view(self, interaction: discord.Interaction, battle_id: int):
        lang  = _lang(interaction)
        nat   = _nat_owner(str(interaction.user.id))
        is_gm = _gm(interaction)

        with db.cursor() as c:
            c.execute("SELECT * FROM battles WHERE id=?", (battle_id,))
            battle = c.fetchone()
        if not battle:
            await interaction.response.send_message(f"Battle #{battle_id} not found.", ephemeral=True)
            return

        with db.cursor() as c:
            c.execute("SELECT * FROM battle_plans WHERE id=?", (battle["plan_a_id"],))
            plan_a = c.fetchone()
            c.execute("SELECT * FROM battle_plans WHERE id=?", (battle["plan_b_id"],))
            plan_b = c.fetchone()

        nat_a = _nat_id(plan_a["nation_id"])
        nat_b = _nat_id(plan_b["nation_id"])

        is_party = nat and (nat["id"] in [plan_a["nation_id"], plan_b["nation_id"]])

        STATUS_EMOJI = {"pending":"🟡","resolved":"✅","cancelled":"❌"}
        embed = discord.Embed(
            title=f"{STATUS_EMOJI.get(battle['status'],'❓')} Battle #{battle_id}",
            description=(
                f"**{nat_a['flag'] or ''} {nat_a['name']}** ⚔️ "
                f"**{nat_b['flag'] or ''} {nat_b['name']}**"
            ),
            color=discord.Color.red(),
        )
        embed.add_field(name="Status", value=battle["status"].capitalize(), inline=True)
        if battle["gm_note"]:
            embed.add_field(name="GM Context", value=battle["gm_note"], inline=False)

        if battle["status"] == "resolved":
            report = json.loads(battle["report_json"])
            embed.add_field(name="Winner",         value=report.get("winner","?").capitalize(), inline=True)
            embed.add_field(name="Roll",           value=str(report.get("roll","?")),           inline=True)
            embed.add_field(name="Atk casualties", value=f"{report.get('atk_casualties_pct',0)}%", inline=True)
            embed.add_field(name="Def casualties", value=f"{report.get('def_casualties_pct',0)}%", inline=True)

        if is_party or is_gm:
            def plan_field(p, nat_p, label):
                loc  = json.loads(p["provinces_json"])
                loc_str = loc[0] if loc else "?"
                orders_full = p["orders_text"]
                orders_disp = orders_full.split(" | Location:")[0]
                embed.add_field(
                    name=f"🔒 {label}: {nat_p['flag'] or ''} {nat_p['name']}",
                    value=f"**Location:** {loc_str}\n**Orders:** {orders_disp[:300]}",
                    inline=False,
                )
            plan_field(plan_a, nat_a, "Attacker's Plan")
            plan_field(plan_b, nat_b, "Defender's Plan")
            if battle["ai_modifier_json"] and battle["ai_modifier_json"] != "{}":
                ai = json.loads(battle["ai_modifier_json"])
                embed.add_field(
                    name="🤖 AI Modifier",
                    value=(
                        f"Attacker: ×{ai.get('attacker_modifier',1.0):.2f} | "
                        f"Defender: ×{ai.get('defender_modifier',1.0):.2f}\n"
                        f"Reasoning: {ai.get('reasoning','—')}"
                    ),
                    inline=False,
                )
        else:
            embed.add_field(
                name="🔒 Battle Plans",
                value="*Plans are private — visible to involved parties and GM only.*",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /battle resolve
    @battle_grp.command(name="resolve",
                        description="[GM] Resolve a battle / [GM] Rozstrzygnij bitwe")
    @app_commands.describe(
        battle_id="Battle ID",
        atk_modifier_override="Override AI attacker modifier (leave blank to use AI)",
        def_modifier_override="Override AI defender modifier (leave blank to use AI)",
        apply_casualties="Apply casualties to units automatically (default True)",
    )
    async def battle_resolve(self, interaction: discord.Interaction,
                             battle_id: int,
                             atk_modifier_override: float = 0.0,
                             def_modifier_override: float = 0.0,
                             apply_casualties: bool = True):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        with db.cursor() as c:
            c.execute("SELECT * FROM battles WHERE id=?", (battle_id,))
            battle = c.fetchone()
        if not battle or battle["status"] != "pending":
            await interaction.followup.send(
                f"Battle #{battle_id} not found or already resolved.", ephemeral=True)
            return

        with db.cursor() as c:
            c.execute("SELECT * FROM battle_plans WHERE id=?", (battle["plan_a_id"],))
            plan_a = c.fetchone()
            c.execute("SELECT * FROM battle_plans WHERE id=?", (battle["plan_b_id"],))
            plan_b = c.fetchone()

        nat_a = _nat_id(plan_a["nation_id"])
        nat_b = _nat_id(plan_b["nation_id"])

        # Build plan dicts for AI
        def plan_dict(p):
            loc = json.loads(p["provinces_json"])
            orders_full = p["orders_text"]
            orders_disp = orders_full.split(" | Location:")[0]
            forces_note = ""
            if " | Forces:" in orders_full:
                forces_note = orders_full.split(" | Forces:")[-1]
            return {
                "location_text": loc[0] if loc else "unknown",
                "orders_text":   orders_disp,
                "forces_note":   forces_note,
            }

        pd_a = plan_dict(plan_a)
        pd_b = plan_dict(plan_b)

        # Get AI modifier
        await interaction.followup.send(
            "⏳ Consulting AI for combat modifier...", ephemeral=True)
        ai_mod = await _get_ai_modifier(pd_a, pd_b, nat_a, nat_b)

        with db.cursor() as c:
            c.execute("UPDATE battles SET ai_modifier_json=? WHERE id=?",
                      (json.dumps(ai_mod), battle_id))

        # Use overrides if provided
        final_atk_mod = atk_modifier_override if atk_modifier_override > 0 else ai_mod["attacker_modifier"]
        final_def_mod = def_modifier_override if def_modifier_override > 0 else ai_mod["defender_modifier"]

        # Calculate forces power
        atk_power, atk_def_unused, atk_units = _forces_power(
            plan_a["forces_json"], plan_a["nation_id"])
        def_unused, def_power, def_units = _forces_power(
            plan_b["forces_json"], plan_b["nation_id"])

        # Fort bonus from any assigned province
        fort_bonus = 1.0
        locs_b = json.loads(plan_b["provinces_json"])
        if locs_b:
            with db.cursor() as c:
                c.execute(
                    "SELECT fortification_level FROM provinces "
                    "WHERE name LIKE ? AND active=1 LIMIT 1",
                    (f"%{locs_b[0][:20]}%",)
                )
                fort_row = c.fetchone()
            if fort_row and fort_row["fortification_level"]:
                fort_bonus = 1.0 + fort_row["fortification_level"] * 0.1

        result = _resolve_combat(atk_power, def_power, final_atk_mod, final_def_mod, fort_bonus)

        final_mod_json = json.dumps({
            "attacker_modifier": final_atk_mod,
            "defender_modifier": final_def_mod,
            "overridden": atk_modifier_override > 0 or def_modifier_override > 0,
        })

        with db.cursor() as c:
            c.execute(
                "UPDATE battles SET status='resolved',gm_final_modifier_json=?,"
                "report_json=?,resolved_at=datetime('now') WHERE id=?",
                (final_mod_json, json.dumps(result), battle_id)
            )

        # Apply casualties
        if apply_casualties:
            _apply_casualties(plan_a["nation_id"], result["atk_casualties_pct"])
            _apply_casualties(plan_b["nation_id"], result["def_casualties_pct"])

        # Log to both nations (private — includes modifiers)
        winner_name = nat_a["name"] if result["winner"]=="attacker" else (
                      nat_b["name"] if result["winner"]=="defender" else "Draw")
        for nid, role in [(plan_a["nation_id"],"attacker"),(plan_b["nation_id"],"defender")]:
            cas = result["atk_casualties_pct"] if role=="attacker" else result["def_casualties_pct"]
            _log(nid, "system",
                 f"Battle #{battle_id}: {result['winner'].upper()} wins ({winner_name}). "
                 f"Your casualties: {cas}%. "
                 f"Modifiers — ATK:×{final_atk_mod:.2f} DEF:×{final_def_mod:.2f}. "
                 f"AI reasoning: {ai_mod['reasoning']}")

        # Public battle report
        ch_id = _cfg("announce_channel_id")
        ch    = self.bot.get_channel(int(ch_id)) if ch_id else None

        WINNER_COLOR = {
            "attacker": discord.Color.red(),
            "defender": discord.Color.blue(),
            "draw":     discord.Color.greyple(),
        }
        report_embed = discord.Embed(
            title=f"⚔️ Battle Report — Battle #{battle_id}",
            color=WINNER_COLOR.get(result["winner"], discord.Color.greyple()),
        )
        report_embed.add_field(
            name="Combatants",
            value=(
                f"**{nat_a['flag'] or ''} {nat_a['name']}** (Attacker)\n"
                f"vs\n"
                f"**{nat_b['flag'] or ''} {nat_b['name']}** (Defender)"
            ),
            inline=False,
        )
        if battle["gm_note"]:
            report_embed.add_field(name="Conditions", value=battle["gm_note"], inline=False)
        report_embed.add_field(
            name="📍 Locations",
            value=(
                f"**{nat_a['name']}:** {pd_a['location_text']}\n"
                f"**{nat_b['name']}:** {pd_b['location_text']}"
            ),
            inline=False,
        )
        report_embed.add_field(
            name="🏆 Outcome",
            value=f"**{'DRAW' if result['winner']=='draw' else (nat_a['name'] if result['winner']=='attacker' else nat_b['name']) + ' WINS'}**",
            inline=False,
        )
        report_embed.add_field(
            name=f"Casualties — {nat_a['name']}",
            value=f"~{result['atk_casualties_pct']}% of committed forces",
            inline=True,
        )
        report_embed.add_field(
            name=f"Casualties — {nat_b['name']}",
            value=f"~{result['def_casualties_pct']}% of committed forces",
            inline=True,
        )
        report_embed.set_footer(text=f"Full details including orders are private to the parties involved.")

        if ch:
            try:
                await ch.send(embed=report_embed)
            except discord.Forbidden:
                pass

        # Also confirm to GM with full details
        gm_embed = discord.Embed(
            title=f"✅ Battle #{battle_id} Resolved — GM View",
            color=discord.Color.green(),
        )
        gm_embed.add_field(name="ATK power",    value=f"{atk_power:.1f}",      inline=True)
        gm_embed.add_field(name="DEF power",    value=f"{def_power:.1f}",       inline=True)
        gm_embed.add_field(name="Fort bonus",   value=f"×{fort_bonus:.1f}",     inline=True)
        gm_embed.add_field(name="ATK modifier", value=f"×{final_atk_mod:.2f}",  inline=True)
        gm_embed.add_field(name="DEF modifier", value=f"×{final_def_mod:.2f}",  inline=True)
        gm_embed.add_field(name="Roll",         value=str(result["roll"]),       inline=True)
        gm_embed.add_field(name="Eff ATK",      value=f"{result['eff_attack']}", inline=True)
        gm_embed.add_field(name="Eff DEF",      value=f"{result['eff_defense']}",inline=True)
        gm_embed.add_field(name="Winner",       value=result["winner"].upper(),  inline=True)
        gm_embed.add_field(
            name="AI Reasoning",
            value=ai_mod["reasoning"],
            inline=False,
        )
        if apply_casualties:
            gm_embed.add_field(
                name="Casualties applied",
                value=f"ATK: -{result['atk_casualties_pct']}% | DEF: -{result['def_casualties_pct']}%",
                inline=False,
            )
        await interaction.followup.send(embed=gm_embed, ephemeral=True)

    # ======================================================== DIPLOMACY

    @diplomacy_grp.command(name="war",
                           description="Declare war on a nation / Wypowiedz wojne")
    @app_commands.describe(nation="Nation to declare war on / Narod do wypowiedzenia wojny")
    async def declare_war(self, interaction: discord.Interaction, nation: str):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        target = _nat_name(nation)
        if not target:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return
        if target["id"] == nat["id"]:
            await interaction.response.send_message("You cannot declare war on yourself.", ephemeral=True)
            return
        current = _get_relation(nat["id"], target["id"])
        if current == "war":
            await interaction.response.send_message(
                f"You are already at war with **{target['name']}**.", ephemeral=True)
            return
        _set_relation(nat["id"], target["id"], "war")
        _log(nat["id"],   "system", f"Declared war on {target['name']}.")
        _log(target["id"],"system", f"{nat['name']} declared war on us.")

        ch_id = _cfg("announce_channel_id")
        ch    = self.bot.get_channel(int(ch_id)) if ch_id else None
        if ch:
            embed = discord.Embed(
                title="⚔️ War Declared!",
                description=(
                    f"**{nat['flag'] or ''} {nat['name']}** has declared war on "
                    f"**{target['flag'] or ''} {target['name']}**!"
                ),
                color=discord.Color.red(),
            )
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                pass

        await interaction.response.send_message(
            f"⚔️ War declared on **{target['name']}**. "
            f"Your upkeep is now at war rate (×3). Submit battle plans with `/battle plan`.",
        )

    @diplomacy_grp.command(name="peace",
                           description="Propose or accept peace / Zaproponuj lub zaakceptuj pokoj")
    @app_commands.describe(nation="Nation to make peace with / Narod do zawarcia pokoju")
    async def make_peace(self, interaction: discord.Interaction, nation: str):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        target = _nat_name(nation)
        if not target:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return
        current = _get_relation(nat["id"], target["id"])
        if current != "war":
            await interaction.response.send_message(
                f"You are not at war with **{target['name']}** (status: {current}).",
                ephemeral=True)
            return
        _set_relation(nat["id"], target["id"], "peace")
        _log(nat["id"],   "system", f"Peace agreed with {target['name']}.")
        _log(target["id"],"system", f"Peace agreed with {nat['name']}.")

        ch_id = _cfg("announce_channel_id")
        ch    = self.bot.get_channel(int(ch_id)) if ch_id else None
        if ch:
            embed = discord.Embed(
                title="🕊️ Peace Declared",
                description=(
                    f"**{nat['flag'] or ''} {nat['name']}** and "
                    f"**{target['flag'] or ''} {target['name']}** have made peace."
                ),
                color=discord.Color.green(),
            )
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                pass

        await interaction.response.send_message(
            f"🕊️ Peace agreed with **{target['name']}**. Upkeep returns to peace rate.")

    @diplomacy_grp.command(name="alliance",
                           description="Propose an alliance / Zaproponuj sojusz")
    @app_commands.describe(nation="Nation to ally with / Narod do sojuszu")
    async def alliance(self, interaction: discord.Interaction, nation: str):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        target = _nat_name(nation)
        if not target:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return
        _set_relation(nat["id"], target["id"], "alliance")
        _log(nat["id"],   "system", f"Alliance formed with {target['name']}.")
        _log(target["id"],"system", f"Alliance formed with {nat['name']}.")
        await interaction.response.send_message(
            f"🤝 Alliance formed with **{target['name']}**.")

    @diplomacy_grp.command(name="status",
                           description="View your diplomatic relations / Status dyplomatyczny")
    async def diplo_status(self, interaction: discord.Interaction):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute(
                "SELECT r.*,"
                "na.name as na_name, na.flag as na_flag,"
                "nb.name as nb_name, nb.flag as nb_flag"
                " FROM relations r"
                " JOIN nations na ON r.nation_a_id=na.id"
                " JOIN nations nb ON r.nation_b_id=nb.id"
                " WHERE r.nation_a_id=? OR r.nation_b_id=?",
                (nat["id"], nat["id"])
            )
            rows = c.fetchall()
        if not rows:
            await interaction.response.send_message(
                "No diplomatic relations on record — all nations default to peace.",
                ephemeral=True)
            return
        STATUS_EMOJI = {"war":"⚔️","peace":"🕊️","alliance":"🤝","truce":"🏳️"}
        lines = []
        for r in rows:
            other_name = r["nb_name"] if r["nation_a_id"]==nat["id"] else r["na_name"]
            other_flag = r["nb_flag"] if r["nation_a_id"]==nat["id"] else r["na_flag"]
            emoji      = STATUS_EMOJI.get(r["status"],"❓")
            lines.append(f"{emoji} {other_flag or ''} **{other_name}** — {r['status'].capitalize()}")
        embed = discord.Embed(
            title=f"🌍 Diplomatic Relations — {nat['flag'] or ''} {nat['name']}",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(CombatCog(bot))
