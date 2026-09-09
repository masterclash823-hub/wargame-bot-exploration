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
from flags import flag_text, flagged_embed
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
        i18n.text('Scholars in **{p0}** report significant advances in knowledge.', p0=nation_name),
        i18n.text('Word spreads of breakthroughs emerging from **{p0}**.', p0=nation_name),
        i18n.text('**{p0}** appears to have made a notable technological leap.', p0=nation_name),
        i18n.text('Rumours from **{p0}** speak of new discoveries and mastery.', p0=nation_name),
        i18n.text('Observers note that **{p0}** has grown more capable in some regard.', p0=nation_name),
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
    with db.atomic() as c:
        c.execute('SELECT id FROM nations ORDER BY id'+(' FOR UPDATE' if db.USE_POSTGRES else ''))
        c.fetchall()
        return _run_drift_locked()


def _run_drift_locked():
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
                     i18n.text('Tech milestone: {p0} reached tier {p1} (was {p2:.1f}, now {p3:.1f}).', p0=i18n.term(cat), p1=tier, p2=old, p3=new))
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
    @i18n.localized
    async def tech_status(self, interaction: discord.Interaction, nation: str = ""):
        lang  = _lang(interaction)
        is_gm = _gm(interaction)

        if nation and not is_gm:
            await interaction.response.send_message(
                i18n.text('Tech levels are private. You can only view your own.'), ephemeral=True)
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

        embed = flagged_embed(discord.Embed(
            title=i18n.text('🔬 Tech Levels — {p0} {p1}', p0=flag_text(nat['flag']), p1=nat['name']).strip(),
            color=discord.Color.teal(),
        ), (nat['flag'], nat['name']))
        for cat in CATEGORIES:
            level = tech.get(cat, 3.0)
            embed.add_field(
                name=i18n.term(cat),
                value=bar(level),
                inline=False,
            )
        embed.add_field(
            name=i18n.text('📚 Universal Knowledge'),
            value=i18n.text('{p0:.0f} points', p0=uk),
            inline=False,
        )
        embed.set_footer(
            text=i18n.text('Passive drift: +{p0}/day per category. Research costs {p1}g + {p2} UK per +0.1.', p0=DRIFT_PER_DAY, p1=RESEARCH_GOLD, p2=RESEARCH_UK)
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
    @i18n.localized
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
                i18n.text('{p0} is already at max (Tier {p1:.0f}).', p0=i18n.term(cat), p1=TECH_MAX), ephemeral=True)
            return
        steps = min(steps, max_steps)

        gold_cost = steps * RESEARCH_GOLD
        uk_cost   = steps * RESEARCH_UK

        if nat["treasury"] < gold_cost:
            await interaction.response.send_message(
                i18n.text('Not enough gold. Need **{p0:,}g**, have **{p1:,.0f}g**.', p0=gold_cost, p1=nat['treasury']),
                ephemeral=True)
            return

        uk_have = res.get("universal_knowledge", 0)
        if uk_have < uk_cost:
            await interaction.response.send_message(
                i18n.text('Not enough Universal Knowledge. Need **{p0}**, have **{p1:.0f}**.\nBuild Universities to generate Universal Knowledge each tick.', p0=uk_cost, p1=uk_have),
                ephemeral=True)
            return

        old     = current
        new     = min(TECH_MAX, round(current + steps * 0.1, 2))

        # Confirmation embed before spending
        confirm_embed = discord.Embed(
            title=i18n.text('🔬 Confirm Research'),
            description=(
                i18n.text('**{p0}**: {p1:.1f} → **{p2:.1f}**\nCost: **{p3:,}g** + **{p4} Universal Knowledge**\n\nClick Confirm to proceed.', p0=i18n.term(cat), p1=old, p2=new, p3=gold_cost, p4=uk_cost)
            ),
            color=discord.Color.teal(),
        )

        class ConfirmView(i18n.LocalizedView):
            def __init__(self):
                super().__init__(timeout=60)
                self.confirmed = False

            @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
            @i18n.localized
            async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if str(btn_interaction.user.id)!=str(nat['owner_id']) or self.confirmed:
                    await btn_interaction.response.send_message(i18n.text('This is not your menu.'),ephemeral=True)
                    return
                self.confirmed = True
                self.stop()
                # Deduct and apply
                from economy_engine import lock_nation,read_json
                from economy_services import spend
                try:
                    with db.atomic() as c:
                        current=lock_nation(c,nat['id'])
                        live_tech=read_json(current['tech_json'])
                        if live_tech.get(cat,3)!=old:raise ValueError(i18n.text('Technology changed. Open research again.'))
                        spend(c,current,{'gold':gold_cost,'universal_knowledge':uk_cost})
                        live_tech[cat]=new
                        c.execute('UPDATE nations SET tech_json=? WHERE id=?',(json.dumps(live_tech),nat['id']))
                except ValueError as exc:
                    await btn_interaction.response.send_message(str(exc),ephemeral=True)
                    return
                _log(nat["id"], "player",
                     i18n.text('Researched {p0}: {p1:.1f} → {p2:.1f} (cost: {p3}g + {p4} UK).', p0=i18n.term(cat), p1=old, p2=new, p3=gold_cost, p4=uk_cost))
                for tier in _tier_crossed(old, new):
                    _log(nat["id"], "system", i18n.text('Tech milestone: {p0} reached tier {p1}.', p0=i18n.term(cat), p1=tier))
                    await _maybe_announce(self.view_bot, nat["name"], tier)
                result_embed = discord.Embed(
                    title=i18n.text('🔬 Research Complete'),
                    description=(
                        i18n.text('**{p0}**: {p1:.1f} → **{p2:.1f}**\nCost: {p3:,}g + {p4} Universal Knowledge', p0=i18n.term(cat), p1=old, p2=new, p3=gold_cost, p4=uk_cost)
                    ),
                    color=discord.Color.teal(),
                )
                await btn_interaction.response.edit_message(embed=result_embed, view=None)

            @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
            @i18n.localized
            async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                self.stop()
                await btn_interaction.response.edit_message(
                    content=i18n.text('Research cancelled.'), embed=None, view=None)

        view = ConfirmView()
        view.view_bot = self.bot
        await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TechCog(bot))
