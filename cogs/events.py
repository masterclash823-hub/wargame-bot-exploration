"""
Event commands:
  /event generate <nation>        - GM: generate an AI event based on nation history+stats
  /event edit <id> <text>         - GM: edit the draft text before posting
  /event effects <id> <json>      - GM: set stat effects for the event
  /event post <id>                - GM: open a three-decision interactive event
  /event play <id>                - owner/GM: resume or inspect the saved event
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
from utils import short_date
import event_adventure as adventure
from event_ui import EventView, render_event


def _event_language(nat):
    """Narrative language belongs to the nation owner, not the invoking GM."""
    return i18n.get_user_language(nat["owner_id"])


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
        f"[{short_date(r['timestamp'])} {r['source'].upper()}] {r['entry_text']}"
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
    lang = _event_language(nat)
    language = "Polish" if lang == "pl" else "English"

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

Write the narrative and special_note in {language}, the nation owner's preferred language.
Keep the literal EFFECTS: separator and all JSON keys/resource identifiers in English.
The example above illustrates the structure only, not the required output language.
Write the event now:"""

    try:
        from google import genai
        client = genai.Client(api_key=config.GEMINI_API_KEY)

        def _call():
            return client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
            )

        # Try once, retry after 2s if rate-limited
        import time
        try:
            response = await asyncio.get_event_loop().run_in_executor(None, _call)
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print(f"[EVENTS AI] 429 rate limit, retrying in 2s...", flush=True)
                await asyncio.sleep(2)
                response = await asyncio.get_event_loop().run_in_executor(None, _call)
            else:
                raise

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
            (f"[AI niedostępne: {type(e).__name__}] W państwie {nat['name']} miało miejsce ważne wydarzenie."
             if lang == "pl" else
             f"[AI unavailable: {type(e).__name__}] A significant event occurred in {nat['name']}."),
            "{}"
        )


def _apply_event_effects(nation_id: int, effects_json: str, lang: str = "en") -> list[str]:
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
        applied.append(f"{'+'if stab>0 else ''}{stab} " + ("stabilności" if lang == "pl" else "stability"))

    gold = effects.get("treasury", 0)
    if gold:
        treasury = max(0.0, treasury + gold)
        applied.append(f"{'+'if gold>0 else ''}{gold} " + ("złota" if lang == "pl" else "gold"))

    res_effects = effects.get("resources", {})
    for res, amt in res_effects.items():
        resources[res] = max(0.0, resources.get(res, 0) + amt)
        applied.append(f"{'+'if amt>0 else ''}{amt} {res}")

    special = effects.get("special_note", "")
    if special:
        applied.append(("Uwaga: " if lang == "pl" else "Note: ") + special)

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
        lang = _event_language(nat)

        event_id = db.insert_returning_id(
            "INSERT INTO events(nation_id,ai_draft_text,gm_final_text,effects_json,status)"
            " VALUES(?,?,?,?,?)",
            (nat["id"], event_text, event_text, effects_json, "draft"))

        effects = {}
        try:
            effects = json.loads(effects_json)
        except Exception:
            pass

        embed = discord.Embed(
            title=("📜 Szkic wydarzenia" if lang == "pl" else "📜 Event Draft")
                  + f" #{event_id} — {nat['flag'] or ''} {nat['name']}",
            description=event_text,
            color=discord.Color.purple(),
        )
        if effects:
            eff_lines = []
            if effects.get("stability"):
                eff_lines.append(("Stabilność: " if lang == "pl" else "Stability: ")
                                 + f"{'+' if effects['stability']>0 else ''}{effects['stability']}")
            if effects.get("treasury"):
                eff_lines.append(("Skarbiec: " if lang == "pl" else "Treasury: ")
                                 + f"{'+' if effects['treasury']>0 else ''}{effects['treasury']}g")
            for res, amt in effects.get("resources", {}).items():
                eff_lines.append(f"{res.capitalize()}: {'+' if amt>0 else ''}{amt}")
            if effects.get("special_note"):
                eff_lines.append(("Uwaga: " if lang == "pl" else "Note: ") + effects['special_note'])
            if eff_lines:
                embed.add_field(name="Proponowane efekty" if lang == "pl" else "Suggested Effects", value="\n".join(eff_lines), inline=False)
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
            parsed = adventure.validate_effects(effects_json)
        except ValueError as e:
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
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        with db.cursor() as c:
            c.execute("SELECT * FROM events WHERE id=?", (event_id,))
            ev = c.fetchone()
        if not ev or ev["status"] != "draft":
            await interaction.followup.send("Event not found or already published. / Event nie istnieje lub jest już opublikowany.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM nations WHERE id=?", (ev["nation_id"],))
            nat = c.fetchone()
        if not nat:
            await interaction.followup.send("Nation not found.", ephemeral=True)
            return
        try:
            state = adventure.start_run(await adventure.prepare_run(ev, nat))
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        # Publishing opens the first decision. No nation balances change here.
        embed = render_event(state)
        failures = []
        ch_id = _cfg("announce_channel_id")
        ch = self.bot.get_channel(int(ch_id)) if ch_id else None
        if ch:
            try:
                await ch.send(embed=embed, view=EventView(state), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                failures.append("channel")
        try:
            owner = interaction.guild.get_member(int(nat["owner_id"])) if interaction.guild else None
            if owner is None:
                owner = await self.bot.fetch_user(int(nat["owner_id"]))
            await owner.send(embed=embed, view=EventView(state), allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, ValueError):
            failures.append("DM")
        notice = adventure.tr(state["lang"],
            f"✅ Event #{event_id} rozpoczęty. Gracz wybiera przez /event play {event_id}. Efekty dopiero po trzeciej decyzji.",
            f"✅ Event #{event_id} started. The player can use /event play {event_id}. Effects apply after decision three.")
        if failures:
            notice += "\n" + adventure.tr(state["lang"], "Nie udało się wysłać: ", "Delivery failed: ") + ", ".join(failures)
        await interaction.followup.send(notice, embed=embed, view=EventView(state), ephemeral=True)

    @event_grp.command(name="play", description="Continue your event / Kontynuuj wydarzenie")
    async def event_play(self, interaction: discord.Interaction, event_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            state = adventure.load_run(event_id)
            if str(interaction.user.id) != state["owner_id"] and not _gm(interaction):
                raise ValueError("Only the nation owner and GM can view this event. / Dostęp tylko dla właściciela i GM.")
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(embed=render_event(state), view=EventView(state),
                                        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

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
                        " WHERE e.nation_id=? AND e.status IN ('posted','active','resolved')"
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
                        " WHERE e.nation_id=? AND e.status IN ('posted','active','resolved')"
                        " ORDER BY e.id DESC LIMIT 10",
                        (nat["id"],)
                    )
                else:
                    await interaction.response.send_message(
                        i18n.t(lang, "no_nation"), ephemeral=True)
                    return
            rows = c.fetchall()

        if not rows:
            await interaction.response.send_message("Nie znaleziono wydarzeń." if lang == "pl" else "No events found.", ephemeral=True)
            return

        STATUS_EMOJI = {"draft": "📝", "posted": "📜", "active": "🎲", "resolved": "✅"}
        embed = discord.Embed(
            title=("📜 Wydarzenia" if lang == "pl" else "📜 Events")
                  + ((" — Widok GM" if lang == "pl" else " — GM View") if is_gm else ""),
            color=discord.Color.purple(),
        )
        for r in rows:
            text   = r["gm_final_text"] if is_gm else r["gm_final_text"]
            status = STATUS_EMOJI.get(r["status"], "❓")
            date   = short_date(r["posted_at"] or r["created_at"])
            embed.add_field(
                name=f"{status} #{r['id']} — {r['nflag'] or ''} {r['nname']} ({date})",
                value=text[:200] + ("..." if len(text) > 200 else ""),
                inline=False,
            )
        embed.set_footer(text="/event play <id> — " + adventure.tr(lang, "kontynuuj lub zobacz finał", "continue or view the outcome"))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(EventsCog(bot))
