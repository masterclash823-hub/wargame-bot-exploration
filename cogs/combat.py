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
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import i18n
import battle_resolution
from utils import short_date, EmbedPager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _lang(i):
    return i18n.get_user_language(i.user.id, i.locale.value if i.locale else None)

from utils import gm_only as _gm

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
async def _get_ai_modifier(plan_a: dict, plan_b: dict, nat_a: dict, nat_b: dict) -> dict:
    """
    Call Gemini to review battle plans and return structured modifiers.
    Uses the google-genai SDK which is already installed.
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
    language = "Polish" if i18n.current_language() == "pl" else "English"
    prompt += f"\nWrite the reasoning in {language}. Keep all JSON keys in English."

    try:
        from google import genai
        client   = genai.Client(api_key=config.GEMINI_API_KEY)
        response = await asyncio.wait_for(
            asyncio.to_thread(lambda: client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
            )), timeout=25)
        raw = response.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return battle_resolution.normalize_ai(json.loads(raw.strip()))
    except Exception as e:
        print(f"[COMBAT AI] Gemini call failed: {type(e).__name__}: {e}", flush=True)
        return {
            "attacker_modifier": 1.0,
            "defender_modifier": 1.0,
            "reasoning": i18n.text('AI unavailable ({p0}) — modifiers defaulted to 1.0.', p0=type(e).__name__),
        }


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
    @i18n.localized
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
                    i18n.text('Invalid unit IDs — use comma-separated numbers e.g. 1,2,5'), ephemeral=True)
                return
            for uid in ids:
                with db.cursor() as c:
                    c.execute("SELECT * FROM military_units WHERE id=? AND nation_id=?",
                              (uid, nat["id"]))
                    u = c.fetchone()
                if not u:
                    await interaction.response.send_message(
                        i18n.text('Unit group #{p0} not found or not yours.', p0=uid), ephemeral=True)
                    return
                forces.append({"unit_id": uid, "qty": u["quantity"]})

        # Check no unit is already committed to another pending plan
        if forces:
            with db.cursor() as c:
                c.execute(
                    "SELECT id, forces_json FROM battle_plans "
                    "WHERE nation_id=? AND status IN ('unmatched','matched')",
                    (nat["id"],)
                )
                existing_plans = c.fetchall()
            already_committed = {}
            for ep in existing_plans:
                try:
                    ep_forces = json.loads(ep["forces_json"])
                    for f in ep_forces:
                        already_committed[f["unit_id"]] = ep["id"]
                except Exception:
                    pass
            conflicts = [
                i18n.text('Group #{p0} (already in plan #{p1})', p0=f['unit_id'], p1=already_committed[f['unit_id']])
                for f in forces if f["unit_id"] in already_committed
            ]
            if conflicts:
                await interaction.response.send_message(
                    i18n.text('❌ Some units are already committed to a pending battle plan:\n')
                    + "\n".join(conflicts)
                    + i18n.text('\nWait for those battles to resolve before committing them again.'),
                    ephemeral=True)
                return

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
             i18n.text('Submitted battle plan #{p0}. Location: {p1}.', p0=plan_id, p1=location))

        embed = discord.Embed(
            title=i18n.text('⚔️ Battle Plan #{p0} Submitted', p0=plan_id),
            description=i18n.text('Your plan has been received. The Game Master will match it when opposing plans arrive.'),
            color=discord.Color.orange(),
        )
        embed.add_field(name=i18n.text('Location/Direction'), value=location,              inline=False)
        embed.add_field(name=i18n.text('Orders'),             value=orders,                inline=False)
        if forces_note:
            embed.add_field(name=i18n.text('Forces note'),   value=forces_note,           inline=False)
        if forces:
            embed.add_field(name=i18n.text('Committed units'),
                            value=", ".join(i18n.text('Group #{p0}', p0=f['unit_id']) for f in forces),
                            inline=False)
        embed.set_footer(text=i18n.text('Only you and the GM can see this plan.'))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /battle plans_pending
    @battle_grp.command(name="plans_pending",
                        description="[GM] List all unmatched battle plans / [GM] Lista oczekujacych planow")
    @i18n.localized
    async def plans_pending(self, interaction: discord.Interaction):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        with db.cursor() as c:
            c.execute(
                "SELECT p.*,n.name as nname,n.flag as nflag"
                " FROM battle_plans p JOIN nations n ON p.nation_id=n.id"
                " WHERE p.status='unmatched' ORDER BY p.submitted_at,p.id",
            )
            rows = c.fetchall()
        if not rows:
            await interaction.followup.send(i18n.text('No unmatched battle plans.'), ephemeral=True)
            return
        embed = discord.Embed(title=i18n.text('⚔️ Pending Battle Plans'), color=discord.Color.red())
        pages = []
        for r in rows:
            forces   = json.loads(r["forces_json"])
            loc      = json.loads(r["provinces_json"])
            loc_str  = str(loc[0])[:150] if loc else i18n.text('not specified')
            orders_full = r["orders_text"]
            orders_disp = orders_full.split(" | Location:")[0][:200]
            name = f"Plan #{r['id']} — {short_date(r['submitted_at'])}"
            value = (
                    i18n.text('**Nation:** {p0} {p1}\n**Location:** {p2}\n**Orders:** {p3}\n**Units:** {p4} group(s) committed', p0=(r['nflag'] or '')[:80], p1=r['nname'][:200], p2=loc_str, p3=orders_disp, p4=len(forces))
                )
            if len(embed.fields) >= 20 or len(embed) + len(name) + len(value) > 5800:
                pages.append(embed)
                embed = discord.Embed(title=i18n.text('⚔️ Pending Battle Plans'), color=discord.Color.red())
            embed.add_field(name=name, value=value, inline=False)
        pages.append(embed)
        await interaction.followup.send(embed=pages[0],
            view=EmbedPager(pages, interaction.user.id), ephemeral=True)

    # -------------------------------------------------- /battle match
    @battle_grp.command(name="match",
                        description="[GM] Match two plans into a battle / [GM] Polacz dwa plany")
    @app_commands.describe(
        attacker_plan_id="Plan ID of the ATTACKER / ID planu atakujacego",
        defender_plan_id="Plan ID of the DEFENDER / ID planu broniącego",
        gm_note="Context note — terrain, ambush, conditions (optional)",
    )
    @i18n.localized
    async def battle_match(self, interaction: discord.Interaction,
                           attacker_plan_id: int, defender_plan_id: int, gm_note: str = ""):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return

        with db.cursor() as c:
            c.execute("SELECT * FROM battle_plans WHERE id=?", (attacker_plan_id,))
            plan_a = c.fetchone()
            c.execute("SELECT * FROM battle_plans WHERE id=?", (defender_plan_id,))
            plan_b = c.fetchone()

        if not plan_a:
            await interaction.response.send_message(i18n.text('Plan #{p0} not found.', p0=attacker_plan_id), ephemeral=True)
            return
        if not plan_b:
            await interaction.response.send_message(i18n.text('Plan #{p0} not found.', p0=defender_plan_id), ephemeral=True)
            return
        if plan_a["nation_id"] == plan_b["nation_id"]:
            await interaction.response.send_message(
                i18n.text('Both plans belong to the same nation.'), ephemeral=True)
            return
        if plan_a["status"] != "unmatched" or plan_b["status"] != "unmatched":
            await interaction.response.send_message(
                i18n.text('Both plans must still be unmatched.'), ephemeral=True)
            return

        battle_id = db.insert_returning_id(
            "INSERT INTO battles(plan_a_id,plan_b_id,gm_note,status) VALUES(?,?,?,?)",
            (attacker_plan_id, defender_plan_id, gm_note, "pending"))
        with db.cursor() as c:
            c.execute("UPDATE battle_plans SET status='matched' WHERE id=? OR id=?",
                      (attacker_plan_id, defender_plan_id))

        nat_a = _nat_id(plan_a["nation_id"])
        nat_b = _nat_id(plan_b["nation_id"])

        embed = discord.Embed(
            title=i18n.text('⚔️ Battle #{p0} Created', p0=battle_id),
            color=discord.Color.red(),
        )
        embed.add_field(name=i18n.text('⚔️ Attacker'), value=f"{nat_a['flag'] or ''} {nat_a['name']} (Plan #{attacker_plan_id})", inline=True)
        embed.add_field(name=i18n.text('🛡️ Defender'), value=f"{nat_b['flag'] or ''} {nat_b['name']} (Plan #{defender_plan_id})", inline=True)
        if gm_note:
            embed.add_field(name=i18n.text('GM Context'), value=gm_note, inline=False)
        embed.set_footer(text=i18n.text('Run /battle resolve {p0} to get AI modifier and resolve.', p0=battle_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /battle view
    @battle_grp.command(name="view",
                        description="View a battle / Szczegoly bitwy")
    @app_commands.describe(battle_id="Battle ID")
    @i18n.localized
    async def battle_view(self, interaction: discord.Interaction, battle_id: int):
        lang  = _lang(interaction)
        nat   = _nat_owner(str(interaction.user.id))
        is_gm = _gm(interaction)

        with db.cursor() as c:
            c.execute("SELECT * FROM battles WHERE id=?", (battle_id,))
            battle = c.fetchone()
        if not battle:
            await interaction.response.send_message(i18n.text('Battle #{p0} not found.', p0=battle_id), ephemeral=True)
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
            title=i18n.text('{p0} Battle #{p1}', p0=STATUS_EMOJI.get(battle['status'], '❓'), p1=battle_id),
            description=(
                f"**{nat_a['flag'] or ''} {nat_a['name']}** ⚔️ "
                f"**{nat_b['flag'] or ''} {nat_b['name']}**"
            ),
            color=discord.Color.red(),
        )
        embed.add_field(name=i18n.text('Status'), value=i18n.term(battle["status"]), inline=True)
        if battle["gm_note"]:
            embed.add_field(name=i18n.text('GM Context'), value=battle["gm_note"], inline=False)

        if battle["status"] == "resolved":
            report = json.loads(battle["report_json"])
            embed.add_field(name=i18n.text('Winner'),         value=i18n.term(report.get("winner","?")), inline=True)
            embed.add_field(name=i18n.text('Roll'),           value=str(report.get("roll","?")),           inline=True)
            embed.add_field(name=i18n.text('Atk casualties'), value=f"{report.get('atk_casualties_pct',0)}%", inline=True)
            embed.add_field(name=i18n.text('Def casualties'), value=f"{report.get('def_casualties_pct',0)}%", inline=True)

        if is_party or is_gm:
            def plan_field(p, nat_p, label):
                loc  = json.loads(p["provinces_json"])
                loc_str = loc[0][:200] if loc else "?"
                orders_full = p["orders_text"]
                orders_disp = orders_full.split(" | Location:")[0][:300]
                embed.add_field(
                    name=f"🔒 {label}",
                    value=(
                        i18n.text('**{p0} {p1}**\n**Location:** {p2}\n**Orders:** {p3}', p0=nat_p['flag'] or '', p1=nat_p['name'], p2=loc_str, p3=orders_disp)
                    ),
                    inline=False,
                )
            plan_field(plan_a, nat_a, i18n.text("Attacker's Plan"))
            plan_field(plan_b, nat_b, i18n.text("Defender's Plan"))
            if battle["ai_modifier_json"] and battle["ai_modifier_json"] != "{}":
                ai = json.loads(battle["ai_modifier_json"])
                embed.add_field(
                    name=i18n.text('🤖 AI Modifier'),
                    value=(
                        i18n.text('ATK ×{p0:.2f} | DEF ×{p1:.2f}\n{p2}', p0=ai.get('attacker_modifier', 1.0), p1=ai.get('defender_modifier', 1.0), p2=ai.get('reasoning', '—')[:300])
                    ),
                    inline=False,
                )
        else:
            embed.add_field(
                name=i18n.text('🔒 Battle Plans'),
                value=i18n.text('*Plans are private — visible to involved parties and GM only.*'),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /battle resolve
    @battle_grp.command(name="resolve",
                        description="[GM] Resolve a battle / [GM] Rozstrzygnij bitwe")
    @app_commands.describe(
        battle_id="Battle ID (leave blank to list pending battles)",
        atk_modifier_override="Override AI attacker modifier (leave blank to use AI)",
        def_modifier_override="Override AI defender modifier (leave blank to use AI)",
        apply_casualties="Apply casualties to units automatically (default True)",
    )
    @i18n.localized
    async def battle_resolve(self, interaction: discord.Interaction,
                             battle_id: int = 0,
                             atk_modifier_override: float = 0.0,
                             def_modifier_override: float = 0.0,
                             apply_casualties: bool = True):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if battle_id <= 0:
            with db.cursor() as c:
                c.execute(
                    "SELECT b.id,na.name AS attacker,nd.name AS defender "
                    "FROM battles b JOIN battle_plans pa ON pa.id=b.plan_a_id "
                    "JOIN battle_plans pd ON pd.id=b.plan_b_id "
                    "JOIN nations na ON na.id=pa.nation_id JOIN nations nd ON nd.id=pd.nation_id "
                    "WHERE b.status='pending' ORDER BY b.id LIMIT 25")
                pending = c.fetchall()
            if not pending:
                await interaction.followup.send(i18n.text('No pending battles. / Brak oczekujących bitew.'), ephemeral=True)
                return
            embed = discord.Embed(title=i18n.text('⚔️ Pending Battles / Oczekujące bitwy'), color=discord.Color.red())
            embed.description = "\n".join(
                f"`#{row['id']}` {row['attacker']} → {row['defender']}" for row in pending)
            embed.set_footer(text=i18n.text('Run /battle resolve <id> / Użyj /battle resolve <id>'))
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        try:
            battle, plan_a, plan_b, nat_a, nat_b = battle_resolution.load_context(battle_id)
            if battle["status"] != "pending":
                raise ValueError(i18n.text('Battle #{p0} is already {p1}.', p0=battle_id, p1=i18n.term(battle['status'])))
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        def plan_dict(plan):
            try:
                locations = json.loads(plan["provinces_json"])
            except (TypeError, ValueError):
                locations = []
            orders = str(plan["orders_text"] or "")
            return {
                "location_text": str(locations[0]) if locations else "unknown",
                "orders_text": orders.split(" | Location:", 1)[0],
                "forces_note": orders.split(" | Forces:", 1)[1] if " | Forces:" in orders else "",
            }

        pd_a, pd_b = plan_dict(plan_a), plan_dict(plan_b)
        await interaction.followup.send(i18n.text('⏳ Consulting AI for combat modifier...'), ephemeral=True)
        ai_mod = await _get_ai_modifier(pd_a, pd_b, nat_a, nat_b)
        try:
            settled = battle_resolution.resolve(
                battle_id, ai_mod, atk_modifier_override, def_modifier_override,
                apply_casualties)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        battle, plan_a, plan_b = settled["battle"], settled["plan_a"], settled["plan_b"]
        nat_a, nat_b, ai_mod = settled["nat_a"], settled["nat_b"], settled["ai"]
        result = settled["result"]
        final_atk_mod = settled["final"]["attacker_modifier"]
        final_def_mod = settled["final"]["defender_modifier"]
        atk_power, def_power, fort_bonus = settled["atk_power"], settled["def_power"], settled["fort_bonus"]
        # Public battle report
        ch_id = _cfg("announce_channel_id")
        try:
            ch = self.bot.get_channel(int(ch_id)) if ch_id else None
        except (TypeError, ValueError):
            ch = None

        WINNER_COLOR = {
            "attacker": discord.Color.red(),
            "defender": discord.Color.blue(),
            "draw":     discord.Color.greyple(),
        }
        report_embed = discord.Embed(
            title=i18n.text('⚔️ Battle Report — Battle #{p0}', p0=battle_id),
            color=WINNER_COLOR.get(result["winner"], discord.Color.greyple()),
        )
        report_embed.add_field(
            name=i18n.text('Combatants'),
            value=(
                i18n.text('**{p0} {p1}** (Attacker)\nvs\n**{p2} {p3}** (Defender)', p0=nat_a['flag'] or '', p1=nat_a['name'], p2=nat_b['flag'] or '', p3=nat_b['name'])
            ),
            inline=False,
        )
        if battle["gm_note"]:
            report_embed.add_field(name=i18n.text('Conditions'), value=battle["gm_note"], inline=False)
        report_embed.add_field(
            name=i18n.text('📍 Locations'),
            value=(
                f"**{nat_a['name']}:** {pd_a['location_text']}\n"
                f"**{nat_b['name']}:** {pd_b['location_text']}"
            ),
            inline=False,
        )
        report_embed.add_field(
            name=i18n.text('🏆 Outcome'),
            value=f"**{i18n.text('DRAW') if result['winner']=='draw' else (nat_a['name'] if result['winner']=='attacker' else nat_b['name']) + i18n.text(' WINS')}**",
            inline=False,
        )
        report_embed.add_field(
            name=i18n.text('Casualties — {p0}', p0=nat_a['name']),
            value=i18n.text('~{p0}% of committed forces', p0=result['atk_casualties_pct']),
            inline=True,
        )
        report_embed.add_field(
            name=i18n.text('Casualties — {p0}', p0=nat_b['name']),
            value=i18n.text('~{p0}% of committed forces', p0=result['def_casualties_pct']),
            inline=True,
        )
        report_embed.set_footer(text=i18n.text('Full details including orders are private to the parties involved.'))

        if ch:
            try:
                await ch.send(embed=report_embed)
            except discord.HTTPException as exc:
                print(f"[COMBAT] Battle #{battle_id} resolved but announcement failed: {exc}", flush=True)
                pass

        # Also confirm to GM with full details
        gm_embed = discord.Embed(
            title=i18n.text('✅ Battle #{p0} Resolved — GM View', p0=battle_id),
            color=discord.Color.green(),
        )
        gm_embed.add_field(name=i18n.text('ATK power'),    value=f"{atk_power:.1f}",      inline=True)
        gm_embed.add_field(name=i18n.text('DEF power'),    value=f"{def_power:.1f}",       inline=True)
        gm_embed.add_field(name=i18n.text('Fort bonus'),   value=f"×{fort_bonus:.1f}",     inline=True)
        gm_embed.add_field(name=i18n.text('ATK modifier'), value=f"×{final_atk_mod:.2f}",  inline=True)
        gm_embed.add_field(name=i18n.text('DEF modifier'), value=f"×{final_def_mod:.2f}",  inline=True)
        gm_embed.add_field(name=i18n.text('Roll'),         value=str(result["roll"]),       inline=True)
        gm_embed.add_field(name=i18n.text('Eff ATK'),      value=f"{result['eff_attack']}", inline=True)
        gm_embed.add_field(name=i18n.text('Eff DEF'),      value=f"{result['eff_defense']}",inline=True)
        gm_embed.add_field(name=i18n.text('Winner'),       value=result["winner"].upper(),  inline=True)
        gm_embed.add_field(
            name=i18n.text('AI Reasoning'),
            value=ai_mod["reasoning"],
            inline=False,
        )
        if apply_casualties:
            gm_embed.add_field(
                name=i18n.text('Casualties applied'),
                value=i18n.text('ATK: -{p0}% | DEF: -{p1}%', p0=result['atk_casualties_pct'], p1=result['def_casualties_pct']),
                inline=False,
            )
        await interaction.followup.send(embed=gm_embed, ephemeral=True)

    # ======================================================== DIPLOMACY

    @diplomacy_grp.command(name="war",
                           description="Declare war on a nation / Wypowiedz wojne")
    @app_commands.describe(nation="Nation to declare war on / Narod do wypowiedzenia wojny")
    @i18n.localized
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
            await interaction.response.send_message(i18n.text('You cannot declare war on yourself.'), ephemeral=True)
            return
        current = _get_relation(nat["id"], target["id"])
        if current == "war":
            await interaction.response.send_message(
                i18n.text('You are already at war with **{p0}**.', p0=target['name']), ephemeral=True)
            return
        _set_relation(nat["id"], target["id"], "war")
        _log(nat["id"],   "system", i18n.text('Declared war on {p0}.', p0=target['name']))
        _log(target["id"],"system", i18n.text('{p0} declared war on us.', p0=nat['name']))

        ch_id = _cfg("announce_channel_id")
        ch    = self.bot.get_channel(int(ch_id)) if ch_id else None
        if ch:
            embed = discord.Embed(
                title=i18n.text('⚔️ War Declared!'),
                description=(
                    i18n.text('**{p0} {p1}** has declared war on **{p2} {p3}**!', p0=nat['flag'] or '', p1=nat['name'], p2=target['flag'] or '', p3=target['name'])
                ),
                color=discord.Color.red(),
            )
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                pass

        await interaction.response.send_message(
            i18n.text('⚔️ War declared on **{p0}**. Your upkeep is now at war rate (×3). Submit battle plans with `/battle plan`.', p0=target['name']),
        )

    @diplomacy_grp.command(name="peace",
                           description="Propose or accept peace / Zaproponuj lub zaakceptuj pokoj")
    @app_commands.describe(nation="Nation to make peace with / Narod do zawarcia pokoju")
    @i18n.localized
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
                i18n.text('You are not at war with **{p0}** (status: {p1}).', p0=target['name'], p1=i18n.term(current)),
                ephemeral=True)
            return
        _set_relation(nat["id"], target["id"], "peace")
        _log(nat["id"],   "system", i18n.text('Peace agreed with {p0}.', p0=target['name']))
        _log(target["id"],"system", i18n.text('Peace agreed with {p0}.', p0=nat['name']))

        ch_id = _cfg("announce_channel_id")
        ch    = self.bot.get_channel(int(ch_id)) if ch_id else None
        if ch:
            embed = discord.Embed(
                title=i18n.text('🕊️ Peace Declared'),
                description=(
                    i18n.text('**{p0} {p1}** and **{p2} {p3}** have made peace.', p0=nat['flag'] or '', p1=nat['name'], p2=target['flag'] or '', p3=target['name'])
                ),
                color=discord.Color.green(),
            )
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                pass

        await interaction.response.send_message(
            i18n.text('🕊️ Peace agreed with **{p0}**. Upkeep returns to peace rate.', p0=target['name']))

    @diplomacy_grp.command(name="alliance",
                           description="Propose an alliance / Zaproponuj sojusz")
    @app_commands.describe(nation="Nation to ally with / Narod do sojuszu")
    @i18n.localized
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
        _log(nat["id"],   "system", i18n.text('Alliance formed with {p0}.', p0=target['name']))
        _log(target["id"],"system", i18n.text('Alliance formed with {p0}.', p0=nat['name']))
        await interaction.response.send_message(
            i18n.text('🤝 Alliance formed with **{p0}**.', p0=target['name']))

    @diplomacy_grp.command(name="status",
                           description="View your diplomatic relations / Status dyplomatyczny")
    @i18n.localized
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
                i18n.text('No diplomatic relations on record — all nations default to peace.'),
                ephemeral=True)
            return
        STATUS_EMOJI = {"war":"⚔️","peace":"🕊️","alliance":"🤝","truce":"🏳️"}
        lines = []
        for r in rows:
            other_name = r["nb_name"] if r["nation_a_id"]==nat["id"] else r["na_name"]
            other_flag = r["nb_flag"] if r["nation_a_id"]==nat["id"] else r["na_flag"]
            emoji      = STATUS_EMOJI.get(r["status"],"❓")
            lines.append(f"{emoji} {other_flag or ''} **{other_name}** — {i18n.term(r['status'])}")
        embed = discord.Embed(
            title=i18n.text('🌍 Diplomatic Relations — {p0} {p1}', p0=nat['flag'] or '', p1=nat['name']),
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(CombatCog(bot))
