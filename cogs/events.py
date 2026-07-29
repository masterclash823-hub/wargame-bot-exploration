"""
Event commands:
  /event generate <nation>        - GM: generate an AI event based on nation history+stats
  /event edit <id> <text>         - GM: edit the draft text before posting
  /event effects <id> <json>      - GM: set stat effects for the event
  /event post <id>                - GM: post the event publicly and apply effects
  /event list [nation]            - GM sees all drafts; players see only posted events
"""
import json
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import i18n


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


def _build_nation_context(nat) -> str:
    """Build a rich context string for Gemini from a nation's stats and history."""
    tech      = json.loads(nat["tech_json"])
    resources = json.loads(nat["resources_json"])
    tech_str  = ", ".join(f"{k.capitalize()} {v:.1f}" for k, v in tech.items())
    res_str   = ", ".join(f"{k}: {v:.0f}" for k, v in resources.items() if v > 0)[:400]

    # Recent history (last 15 entries, public and gm sources only)
    with db.cursor() as c:
        c.execute(
            "SELECT timestamp, source, entry_text FROM nation_history "
            "WHERE nation_id=? AND source IN ('system','gm','player','ai') "
            "ORDER BY timestamp DESC LIMIT 15",
            (nat["id"],)
        )
        history = c.fetchall()
    history_str = "\n".join(
        f"[{r['timestamp'][:10]} {r['source'].upper()}] {r['entry_text']}"
        for r in reversed(history)
    ) or "No recorded history yet."

    # Relations
    with db.cursor() as c:
        c.execute(
            "SELECT r.status, n.name as other "
            "FROM relations r "
            "JOIN nations n ON (CASE WHEN r.nation_a_id=? THEN r.nation_b_id ELSE r.nation_a_id END)=n.id "
            "WHERE r.nation_a_id=? OR r.nation_b_id=?",
            (nat["id"], nat["id"], nat["id"])
        )
        relations = c.fetchall()
    rel_str = ", ".join(f"{r['other']} ({r['status']})" for r in relations) or "None on record."

    # Current in-game date
    month = _cfg("current_month", "?")
    year  = _cfg("current_year",  "?")
    MONTH_NAMES = ["January","February","March","April","May","June",
                   "July","August","September","October","November","December"]
    try:
        mname = MONTH_NAMES[int(month) - 1]
    except (ValueError, IndexError):
        mname = f"Month {month}"

    return f"""Nation: {nat['name']}
Government: {nat['government_type']}
Stability: {nat['stability']:.0f}/100
Treasury: {nat['treasury']:.0f} gold
Population: {nat['population']:,}
Tech levels: {tech_str}
Resources (non-zero): {res_str or 'none'}
Diplomatic relations: {rel_str}
Current in-game date: {mname}, Year {year}

Recent history:
{history_str}"""


async def _generate_event(nat) -> tuple[str, str]:
    """
    Call Gemini to generate a narrative event and suggested effects.
    Returns (event_text, effects_json_str).
    """
    context = _build_nation_context(nat)

    prompt = f"""You are a narrative game master for a fantasy wargame set in the Age of Exploration.
Based on the nation's history, stats, and current situation below, generate a compelling
in-game event that feels organic and grounded in their specific circumstances.

{context}

Write a SHORT narrative event (2-4 sentences) that:
- References specific details from their history or current situation
- Fits the Age of Exploration fantasy setting
- Has a clear consequence or opportunity for the nation
- Feels like something that would actually happen given their stats and relations

Then on a new line write EFFECTS: followed by a JSON object with any of these optional keys:
  stability: (integer, positive or negative, max ±20)
  treasury: (integer gold, positive or negative)
  resources: (object with resource_name: amount pairs)
  special_note: (string, for effects that can't be numbers)

Example format:
A drought has struck the eastern farmlands, threatening grain supplies for the coming winter. The government scrambles to import food from allied nations, but reserves are running low. Local nobles grow restless as the people suffer.
EFFECTS: {{"stability": -8, "resources": {{"food": -50}}, "special_note": "Risk of unrest in eastern provinces"}}

Write the event now:"""

    try:
        from google import genai
        client   = genai.Client(api_key=config.GEMINI_API_KEY)
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
        )
        raw = response.text.strip()

        # Split on EFFECTS:
        if "EFFECTS:" in raw:
            parts       = raw.split("EFFECTS:", 1)
            event_text  = parts[0].strip()
            effects_raw = parts[1].strip()
            # Clean markdown fences
            if effects_raw.startswith("```"):
                effects_raw = effects_raw.split("```")[1]
                if effects_raw.startswith("json"):
                    effects_raw = effects_raw[4:]
            effects_raw = effects_raw.strip()
            try:
                json.loads(effects_raw)  # validate
                return event_text, effects_raw
            except json.JSONDecodeError:
                return event_text, "{}"
        else:
            return raw, "{}"

    except Exception as e:
        print(f"[EVENTS AI] Gemini failed: {type(e).__name__}: {e}", flush=True)
        return (
            f"[AI unavailable: {type(e).__name__}] A significant event occurred in {nat['name']}.",
            "{}"
        )


def _apply_event_effects(nation_id: int, effects_json: str) -> list[str]:
    """Apply event effects to a nation. Returns list of applied effect strings."""
    try:
        effects = json.loads(effects_json)
    except (json.JSONDecodeError, TypeError):
        return []

    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE id=?", (nation_id,))
        nat = c.fetchone()
    if not nat:
        return []

    applied   = []
    stability = nat["stability"]
    treasury  = nat["treasury"]
    resources = json.loads(nat["resources_json"])

    stab = effects.get("stability", 0)
    if stab:
        stability = max(0.0, min(100.0, stability + stab))
        applied.append(f"{'+'if stab>0 else ''}{stab} stability")

    gold = effects.get("treasury", 0)
    if gold:
        treasury = max(0.0, treasury + gold)
        applied.append(f"{'+'if gold>0 else ''}{gold} gold")

    res_effects = effects.get("resources", {})
    for res, amt in res_effects.items():
        resources[res] = max(0.0, resources.get(res, 0) + amt)
        applied.append(f"{'+'if amt>0 else ''}{amt} {res}")

    special = effects.get("special_note", "")
    if special:
        applied.append(f"Note: {special}")

    with db.cursor() as c:
        c.execute(
            "UPDATE nations SET stability=?,treasury=?,resources_json=? WHERE id=?",
            (stability, treasury, json.dumps(resources), nation_id)
        )

    return applied


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    event_grp = app_commands.Group(name="event", description="Event commands / Eventy")

    # -------------------------------------------------- /event generate
    @event_grp.command(name="generate",
                       description="[GM] Generate an AI event for a nation / [GM] Generuj event AI")
    @app_commands.describe(nation="Nation name / Nazwa narodu")
    async def event_generate(self, interaction: discord.Interaction, nation: str):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return

        nat = _nat_name(nation)
        if not nat:
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "nation_not_found"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        event_text, effects_json = await _generate_event(nat)

        with db.cursor() as c:
            c.execute(
                "INSERT INTO events(nation_id,ai_draft_text,gm_final_text,effects_json,status)"
                " VALUES(?,?,?,?,?)",
                (nat["id"], event_text, event_text, effects_json, "draft")
            )
            event_id = c.lastrowid

        effects = {}
        try:
            effects = json.loads(effects_json)
        except Exception:
            pass

        embed = discord.Embed(
            title=f"📜 Event Draft #{event_id} — {nat['flag'] or ''} {nat['name']}",
            description=event_text,
            color=discord.Color.purple(),
        )
        if effects:
            eff_lines = []
            if effects.get("stability"):
                eff_lines.append(f"Stability: {'+' if effects['stability']>0 else ''}{effects['stability']}")
            if effects.get("treasury"):
                eff_lines.append(f"Treasury: {'+' if effects['treasury']>0 else ''}{effects['treasury']}g")
            for res, amt in effects.get("resources", {}).items():
                eff_lines.append(f"{res.capitalize()}: {'+' if amt>0 else ''}{amt}")
            if effects.get("special_note"):
                eff_lines.append(f"Note: {effects['special_note']}")
            if eff_lines:
                embed.add_field(name="Suggested Effects", value="\n".join(eff_lines), inline=False)
        embed.set_footer(
            text=f"Event ID: {event_id} | "
                 f"Edit: /event edit {event_id} <text> | "
                 f"Set effects: /event effects {event_id} <json> | "
                 f"Post: /event post {event_id}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # -------------------------------------------------- /event edit
    @event_grp.command(name="edit",
                       description="[GM] Edit an event draft / [GM] Edytuj szkic eventu")
    @app_commands.describe(
        event_id="Event ID / ID eventu",
        text="New event text / Nowy tekst eventu",
    )
    async def event_edit(self, interaction: discord.Interaction, event_id: int, text: str):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM events WHERE id=?", (event_id,))
            ev = c.fetchone()
        if not ev or ev["status"] != "draft":
            await interaction.response.send_message(
                f"Event #{event_id} not found or already posted.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("UPDATE events SET gm_final_text=? WHERE id=?", (text, event_id))
        await interaction.response.send_message(
            f"✅ Event #{event_id} text updated.", ephemeral=True)

    # -------------------------------------------------- /event effects
    @event_grp.command(name="effects",
                       description="[GM] Set effects for an event / [GM] Ustaw efekty eventu")
    @app_commands.describe(
        event_id="Event ID / ID eventu",
        effects_json='Effects JSON e.g. {"stability":-5,"treasury":100,"resources":{"food":50}}',
    )
    async def event_effects(self, interaction: discord.Interaction,
                            event_id: int, effects_json: str):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        try:
            parsed = json.loads(effects_json)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(f"❌ Invalid JSON: {e}", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM events WHERE id=?", (event_id,))
            ev = c.fetchone()
        if not ev or ev["status"] != "draft":
            await interaction.response.send_message(
                f"Event #{event_id} not found or already posted.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("UPDATE events SET effects_json=? WHERE id=?",
                      (json.dumps(parsed), event_id))
        eff_str = ", ".join(f"{k}: {v}" for k, v in parsed.items() if k != "resources")
        await interaction.response.send_message(
            f"✅ Event #{event_id} effects updated: {eff_str or 'none'}.", ephemeral=True)

    # -------------------------------------------------- /event post
    @event_grp.command(name="post",
                       description="[GM] Post an event publicly / [GM] Opublikuj event")
    @app_commands.describe(event_id="Event ID / ID eventu")
    async def event_post(self, interaction: discord.Interaction, event_id: int):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM events WHERE id=?", (event_id,))
            ev = c.fetchone()
        if not ev or ev["status"] != "draft":
            await interaction.response.send_message(
                f"Event #{event_id} not found or already posted.", ephemeral=True)
            return

        nat = None
        with db.cursor() as c:
            c.execute("SELECT * FROM nations WHERE id=?", (ev["nation_id"],))
            nat = c.fetchone()
        if not nat:
            await interaction.response.send_message("Nation not found.", ephemeral=True)
            return

        # Apply effects
        applied = _apply_event_effects(ev["nation_id"], ev["effects_json"])

        # Log to nation history
        log_text = f"Event: {ev['gm_final_text'][:200]}"
        if applied:
            log_text += f" Effects: {', '.join(applied)}."
        _log(ev["nation_id"], "ai", log_text)

        # Mark posted
        with db.cursor() as c:
            c.execute(
                "UPDATE events SET status='posted',posted_at=datetime('now') WHERE id=?",
                (event_id,)
            )

        # Build public embed
        embed = discord.Embed(
            title=f"📜 Event — {nat['flag'] or ''} {nat['name']}",
            description=ev["gm_final_text"],
            color=discord.Color.purple(),
        )
        if applied:
            embed.add_field(
                name="Effects",
                value="\n".join(applied),
                inline=False,
            )
        month = _cfg("current_month", "?")
        year  = _cfg("current_year",  "?")
        embed.set_footer(text=f"Month {month}, Year {year}")

        # Post to announce channel if set
        ch_id = _cfg("announce_channel_id")
        ch    = self.bot.get_channel(int(ch_id)) if ch_id else None
        if ch:
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                pass

        # Also respond to GM
        await interaction.response.send_message(
            f"✅ Event #{event_id} posted for **{nat['name']}**."
            + (f"\nEffects applied: {', '.join(applied)}" if applied else ""),
            ephemeral=True,
        )

    # -------------------------------------------------- /event list
    @event_grp.command(name="list",
                       description="List events / Lista eventow")
    @app_commands.describe(
        nation="Nation name (blank = all) / Nazwa narodu (puste = wszystkie)",
    )
    async def event_list(self, interaction: discord.Interaction, nation: str = ""):
        lang  = _lang(interaction)
        is_gm = _gm(interaction)
        nat   = _nat_owner(str(interaction.user.id))

        with db.cursor() as c:
            if nation:
                target = _nat_name(nation)
                if not target:
                    await interaction.response.send_message(
                        i18n.t(lang, "nation_not_found"), ephemeral=True)
                    return
                if is_gm:
                    c.execute(
                        "SELECT e.*,n.name as nname,n.flag as nflag"
                        " FROM events e JOIN nations n ON e.nation_id=n.id"
                        " WHERE e.nation_id=? ORDER BY e.id DESC LIMIT 10",
                        (target["id"],)
                    )
                else:
                    c.execute(
                        "SELECT e.*,n.name as nname,n.flag as nflag"
                        " FROM events e JOIN nations n ON e.nation_id=n.id"
                        " WHERE e.nation_id=? AND e.status='posted'"
                        " ORDER BY e.id DESC LIMIT 10",
                        (target["id"],)
                    )
            else:
                if is_gm:
                    c.execute(
                        "SELECT e.*,n.name as nname,n.flag as nflag"
                        " FROM events e JOIN nations n ON e.nation_id=n.id"
                        " ORDER BY e.id DESC LIMIT 15"
                    )
                elif nat:
                    c.execute(
                        "SELECT e.*,n.name as nname,n.flag as nflag"
                        " FROM events e JOIN nations n ON e.nation_id=n.id"
                        " WHERE e.nation_id=? AND e.status='posted'"
                        " ORDER BY e.id DESC LIMIT 10",
                        (nat["id"],)
                    )
                else:
                    await interaction.response.send_message(
                        i18n.t(lang, "no_nation"), ephemeral=True)
                    return
            rows = c.fetchall()

        if not rows:
            await interaction.response.send_message("No events found.", ephemeral=True)
            return

        STATUS_EMOJI = {"draft": "📝", "posted": "📜"}
        embed = discord.Embed(
            title="📜 Events" + (" — GM View" if is_gm else ""),
            color=discord.Color.purple(),
        )
        for r in rows:
            text   = r["gm_final_text"] if is_gm else r["gm_final_text"]
            status = STATUS_EMOJI.get(r["status"], "❓")
            date   = (r["posted_at"] or r["created_at"])[:10]
            embed.add_field(
                name=f"{status} #{r['id']} — {r['nflag'] or ''} {r['nname']} ({date})",
                value=text[:200] + ("..." if len(text) > 200 else ""),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(EventsCog(bot))
