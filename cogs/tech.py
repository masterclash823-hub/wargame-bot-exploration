"""
Tech commands:
  /tech status [nation]         - view your own tech levels (or GM views any nation)
  /tech research <category>     - spend gold + universal knowledge to boost a category
  /admineco tech_set            - GM: set a nation's tech level directly

Passive drift: +0.1 per category per real day, via a daily background task.
Crossing a full tier has a tier*10% chance of a public announcement in the
calendar channel (no details revealed publicly — full details in private history).

Categories: naval, land, economy, colonial
Tech points resource key: "universal_knowledge"
"""
import json
import random
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db
import i18n

CATEGORIES = ["naval", "land", "economy", "colonial"]
TECH_MAX   = 10.0
DRIFT_PER_DAY = 0.1

# Research cost per 0.1 tech gained
RESEARCH_GOLD = 50
RESEARCH_UK   = 1   # universal_knowledge points


def _lang(interaction):
    locale = interaction.locale.value if interaction.locale else None
    return i18n.get_user_language(interaction.user.id, locale)

from utils import gm_only as _gm

def _nation_owner(uid):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE owner_id=?", (str(uid),))
        return c.fetchone()

def _nation_name(name):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (name,))
        return c.fetchone()

def _cfg(key, default=""):
    with db.cursor() as c:
        c.execute("SELECT value FROM game_config WHERE key=?", (key,))
        row = c.fetchone()
    return row["value"] if row else default

def _log(nid, source, text):
    with db.cursor() as c:
        c.execute(
            "INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)",
            (nid, source, text)
        )

def _tier_crossed(old: float, new: float) -> list[int]:
    """Return list of integer tiers crossed going from old to new."""
    return [t for t in range(int(old) + 1, int(new) + 1) if t <= int(TECH_MAX)]

async def _maybe_announce(bot, nation_name: str, tier: int):
    """
    tier*10% chance of posting a vague public announcement.
    Never reveals which category or exact level.
    """
    chance = tier * 0.10
    if random.random() > chance:
        return
    ch_id = _cfg("announce_channel_id")
    if not ch_id:
        return
    ch = bot.get_channel(int(ch_id))
    if not ch:
        return
    flavours = [
        f"Scholars in **{nation_name}** report significant advances in knowledge.",
        f"Word spreads of breakthroughs emerging from **{nation_name}**.",
        f"**{nation_name}** appears to have made a notable technological leap.",
        f"Rumours from **{nation_name}** speak of new discoveries and mastery.",
        f"Observers note that **{nation_name}** has grown more capable in some regard.",
    ]
    embed = discord.Embed(
        description=random.choice(flavours),
        color=discord.Color.teal(),
    )
    try:
        await ch.send(embed=embed)
    except discord.Forbidden:
        pass

def _run_drift():
    """
    Apply +DRIFT_PER_DAY to each tech category for every nation.
    Guaranteed to run at most once per 24 hours.
    """
    # 1. Check when drift last ran
    last_run_str = _cfg("last_tech_drift", "0")
    now_ts = datetime.now(timezone.utc).timestamp()
    
    # If 24 hours (86400 seconds) have not passed since last drift, skip
    if now_ts - float(last_run_str) < 86400:
        return []

    with db.cursor() as c:
        c.execute("SELECT * FROM nations")
        nations = c.fetchall()

    crossings = []
    for nat in nations:
        tech = json.loads(nat["tech_json"])
        changed = False
        for cat in CATEGORIES:
            old = tech.get(cat, 3.0)
            if old >= TECH_MAX:
                continue
            new = min(TECH_MAX, round(old + DRIFT_PER_DAY, 2))
            tech[cat] = new
            changed = True
            for tier in _tier_crossed(old, new):
                crossings.append((nat["id"], nat["name"], cat, old, new, tier))
        
        if changed:
            with db.cursor() as c:
                c.execute(
                    "UPDATE nations SET tech_json=? WHERE id=?",
                    (json.dumps(tech), nat["id"])
                )

    # 2. Record successful drift timestamp in game_config
    with db.cursor() as c:
        c.execute(
            "INSERT INTO game_config (key, value) VALUES ('last_tech_drift', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(now_ts),)
        )

    return crossings


class TechCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drift_loop.start()

    def cog_unload(self):
        self.drift_loop.cancel()

    @tasks.loop(hours=1)
    async def drift_loop(self):
        try:
            crossings = await self.bot.loop.run_in_executor(None, _run_drift)
            if crossings:
                print(f"[TECH] Daily drift executed. {len(crossings)} tier crossing(s).", flush=True)
            for nid, nname, cat, old, new, tier in crossings:
                _log(nid, "system",
                     f"Tech milestone: {cat.capitalize()} reached tier {tier} "
                     f"(was {old:.1f}, now {new:.1f}).")
                await _maybe_announce(self.bot, nname, tier)
        except Exception as e:
            import traceback
            print(f"[TECH ERROR] {e}", flush=True)
            traceback.print_exc()

    @drift_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ---- Groups ---------------------------------------------------------
    tech_grp = app_commands.Group(name="tech", description="Technology commands / Technologia")

    # -------------------------------------------------- /tech status
    @tech_grp.command(name="status",
                      description="View your tech levels (private) / Poziomy technologii (prywatne)")
    @app_commands.describe(nation="Nation name — GM only / Nazwa narodu — tylko GM")
    async def tech_status(self, interaction: discord.Interaction, nation: str = ""):
        lang  = _lang(interaction)
        is_gm = _gm(interaction)

        if nation and not is_gm:
            await interaction.response.send_message(
                "Tech levels are private. You can only view your own.", ephemeral=True)
            return

        if nation:
            nat = _nation_name(nation)
        else:
            nat = _nation_owner(str(interaction.user.id))

        if not nat:
            await interaction.response.send_message(
                i18n.t(lang, "nation_not_found" if nation else "no_nation"),
                ephemeral=True)
            return

        tech = json.loads(nat["tech_json"])
        res  = json.loads(nat["resources_json"])
        uk   = res.get("universal_knowledge", 0)

        def bar(level: float) -> str:
            filled = int(level)
            frac   = level - filled
            bar_str = "█" * filled
            if frac >= 0.5:
                bar_str += "▌"
            bar_str = bar_str.ljust(10, "░")
            return f"`{bar_str}` {level:.1f}/10"

        embed = discord.Embed(
            title=f"🔬 Tech Levels — {nat['flag'] or ''} {nat['name']}".strip(),
            color=discord.Color.teal(),
        )
        for cat in CATEGORIES:
            level = tech.get(cat, 3.0)
            embed.add_field(
                name=cat.capitalize(),
                value=bar(level),
                inline=False,
            )
        embed.add_field(
            name="📚 Universal Knowledge",
            value=f"{uk:.0f} points",
            inline=False,
        )
        embed.set_footer(
            text=f"Passive drift: +{DRIFT_PER_DAY}/day per category. "
                 f"Research costs {RESEARCH_GOLD}g + {RESEARCH_UK} UK per +0.1."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /tech research
    @tech_grp.command(name="research",
                      description="Spend gold + knowledge to advance tech / Wydaj zasoby na technologie")
    @app_commands.describe(
        category="Category to advance: naval / land / economy / colonial",
        steps="How many +0.1 steps to buy (default 1)",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="Naval",    value="naval"),
        app_commands.Choice(name="Land",     value="land"),
        app_commands.Choice(name="Economy",  value="economy"),
        app_commands.Choice(name="Colonial", value="colonial"),
    ])
    async def tech_research(self, interaction: discord.Interaction,
                            category: app_commands.Choice[str], steps: int = 1):
        lang = _lang(interaction)
        nat  = _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return

        steps = max(1, min(steps, 50))  # cap at 50 steps per command
        cat   = category.value
        tech  = json.loads(nat["tech_json"])
        res   = json.loads(nat["resources_json"])

        current = tech.get(cat, 3.0)
        max_steps = int((TECH_MAX - current) / 0.1)
        if max_steps <= 0:
            await interaction.response.send_message(
                f"{cat.capitalize()} is already at max (Tier {TECH_MAX:.0f}).", ephemeral=True)
            return
        steps = min(steps, max_steps)

        gold_cost = steps * RESEARCH_GOLD
        uk_cost   = steps * RESEARCH_UK

        if nat["treasury"] < gold_cost:
            await interaction.response.send_message(
                f"Not enough gold. Need **{gold_cost:,}g**, have **{nat['treasury']:,.0f}g**.",
                ephemeral=True)
            return

        uk_have = res.get("universal_knowledge", 0)
        if uk_have < uk_cost:
            await interaction.response.send_message(
                f"Not enough Universal Knowledge. Need **{uk_cost}**, have **{uk_have:.0f}**.\n"
                "Build Universities to generate Universal Knowledge each tick.",
                ephemeral=True)
            return

        old     = current
        new     = min(TECH_MAX, round(current + steps * 0.1, 2))

        # Confirmation embed before spending
        confirm_embed = discord.Embed(
            title="🔬 Confirm Research",
            description=(
                f"**{cat.capitalize()}**: {old:.1f} → **{new:.1f}**\n"
                f"Cost: **{gold_cost:,}g** + **{uk_cost} Universal Knowledge**\n\n"
                "Click Confirm to proceed."
            ),
            color=discord.Color.teal(),
        )

        class ConfirmView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.confirmed = False

            @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
            async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                self.confirmed = True
                self.stop()
                # Deduct and apply
                tech[cat] = new
                res["universal_knowledge"] = uk_have - uk_cost
                with db.cursor() as c:
                    c.execute(
                        "UPDATE nations SET tech_json=?,resources_json=?,treasury=? WHERE id=?",
                        (json.dumps(tech), json.dumps(res), nat["treasury"] - gold_cost, nat["id"])
                    )
                _log(nat["id"], "player",
                     f"Researched {cat.capitalize()}: {old:.1f} → {new:.1f} "
                     f"(cost: {gold_cost}g + {uk_cost} UK).")
                for tier in _tier_crossed(old, new):
                    _log(nat["id"], "system", f"Tech milestone: {cat.capitalize()} reached tier {tier}.")
                    await _maybe_announce(self.view_bot, nat["name"], tier)
                result_embed = discord.Embed(
                    title="🔬 Research Complete",
                    description=(
                        f"**{cat.capitalize()}**: {old:.1f} → **{new:.1f}**\n"
                        f"Cost: {gold_cost:,}g + {uk_cost} Universal Knowledge"
                    ),
                    color=discord.Color.teal(),
                )
                await btn_interaction.response.edit_message(embed=result_embed, view=None)

            @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                self.stop()
                await btn_interaction.response.edit_message(
                    content="Research cancelled.", embed=None, view=None)

        view = ConfirmView()
        view.view_bot = self.bot
        await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TechCog(bot))
