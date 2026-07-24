"""
Bot entry point.
"""
import sys
print("[BOOT] bot.py started, Python", sys.version, flush=True)

import traceback
import discord
from discord import app_commands
from discord.ext import commands
print("[BOOT] discord.py imported", flush=True)

import config
import db
import i18n
from keep_alive import keep_alive
print("[BOOT] local modules imported", flush=True)

COGS = [
    "cogs.nations",
    "cogs.provinces",
    "cogs.economy",
]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
print("[BOOT] bot object created", flush=True)


@bot.event
async def on_ready():
    print("[READY] on_ready fired", flush=True)
    try:
        db.init_db()
        print("[DB] init_db complete", flush=True)

        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f"[COG] Loaded: {cog}", flush=True)
            except Exception:
                print(f"[COG] FAILED to load {cog}:", flush=True)
                traceback.print_exc()

        global_synced = await tree.sync()
        print(f"[SYNC] Global: {len(global_synced)} command(s): {[c.name for c in global_synced]}", flush=True)

        for guild in bot.guilds:
            try:
                guild_synced = await tree.sync(guild=guild)
                print(f"[SYNC] Guild '{guild.name}': {len(guild_synced)} command(s): {[c.name for c in guild_synced]}", flush=True)
            except Exception as e:
                print(f"[SYNC] Guild sync failed for {guild.name}: {e}", flush=True)

        print(f"[READY] Done. Connected to {len(bot.guilds)} guild(s).", flush=True)

    except Exception:
        print("[READY] FATAL ERROR in on_ready:", flush=True)
        traceback.print_exc()


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"[CMD ERROR] {interaction.command.name if interaction.command else '?'}: {type(error).__name__}: {error}", flush=True)
    traceback.print_exc()
    lang = i18n.get_user_language(interaction.user.id)
    try:
        await interaction.response.send_message(i18n.t(lang, "generic_error"), ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(i18n.t(lang, "generic_error"), ephemeral=True)


@tree.command(name="language", description="Set your preferred language / Ustaw jezyk")
@app_commands.describe(lang="en or pl")
@app_commands.choices(lang=[
    app_commands.Choice(name="English", value="en"),
    app_commands.Choice(name="Polski",  value="pl"),
])
async def language_cmd(interaction: discord.Interaction, lang: app_commands.Choice[str]):
    if lang.value not in config.SUPPORTED_LANGUAGES:
        current = i18n.get_user_language(interaction.user.id)
        await interaction.response.send_message(
            i18n.t(current, "language_invalid", languages=", ".join(config.SUPPORTED_LANGUAGES)),
            ephemeral=True,
        )
        return
    i18n.set_user_language(interaction.user.id, lang.value)
    await interaction.response.send_message(
        i18n.t(lang.value, "language_set_confirm"), ephemeral=True
    )


def _is_gm(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    return any(r.name == config.GM_ROLE_NAME for r in interaction.user.roles)


@tree.command(name="help", description="Show available commands / Pokaz dostepne komendy")
@app_commands.describe(section="Section: general / nation / province / economy / trade / gm")
@app_commands.choices(section=[
    app_commands.Choice(name="General",  value="general"),
    app_commands.Choice(name="Nation",   value="nation"),
    app_commands.Choice(name="Province", value="province"),
    app_commands.Choice(name="Economy",  value="economy"),
    app_commands.Choice(name="Trade",    value="trade"),
    app_commands.Choice(name="GM only",  value="gm"),
])
async def help_cmd(interaction: discord.Interaction,
                   section: app_commands.Choice[str] = None):
    lang = i18n.get_user_language(interaction.user.id)
    sec  = section.value if section else "general"

    # GM section is restricted
    if sec == "gm" and not _is_gm(interaction):
        await interaction.response.send_message(
            i18n.t(lang, "gm_only"), ephemeral=True)
        return

    SECTIONS = {
        "general": {
            "title": "General Commands",
            "color": discord.Color.blurple(),
            "fields": [
                ("/help [section]",   "Show this help. Sections: general, nation, province, economy, trade, gm"),
                ("/language",         "Set your preferred language (en / pl)."),
                ("/calendar status",  "View the current in-game date and calendar settings."),
            ],
        },
        "nation": {
            "title": "Nation Commands",
            "color": discord.Color.blue(),
            "fields": [
                ("/nation found",       "Found your nation."),
                ("/nation stats",       "View a nation's stats (blank = your own)."),
                ("/nation list",        "List all nations."),
                ("/nation history",     "View a nation's public history log."),
            ],
        },
        "province": {
            "title": "Province Commands",
            "color": discord.Color.green(),
            "fields": [
                ("/province info",    "View a province by Azgaar cell ID."),
                ("/province list",    "List all provinces owned by a nation."),
            ],
        },
        "economy": {
            "title": "Economy Commands",
            "color": discord.Color.gold(),
            "fields": [
                ("/resources",           "View your nation's resource stockpile and treasury."),
                ("/build",               "Construct a building in one of your provinces."),
                ("/buildings list",      "List all available building types."),
                ("/buildings province",  "List buildings in a specific province."),
                ("/megaproject propose", "Propose a megaproject for GM approval."),
                ("/megaproject list",    "List your megaprojects."),
            ],
        },
        "trade": {
            "title": "Trade Commands",
            "color": discord.Color.orange(),
            "fields": [
                ("/trade offer",   "Propose a trade to another nation (public note + private terms)."),
                ("/trade accept",  "Accept a pending trade offer."),
                ("/trade cancel",  "Cancel or decline a trade offer."),
                ("/trade list",    "List your pending trades."),
                ("/trade view",    "View full details of a trade (private terms visible to parties + GM only)."),
            ],
        },
        "gm": {
            "title": "GM Commands",
            "color": discord.Color.red(),
            "fields": [
                ("/nation history_add",      "Add a manual history entry to a nation."),
                ("/province claim",          "Claim provinces for a nation by cell ID(s)."),
                ("/province unclaim",        "Remove ownership from provinces."),
                ("/admin map_import",        "Import an Azgaar JSON export."),
                ("/admin map_resync",        "Re-import an updated Azgaar map."),
                ("/admin map_export_markers","Generate JS snippet for Azgaar resource markers."),
                ("/admineco tick",           "Manually trigger a resource tick (specify months)."),
                ("/admineco grant",          "Give resources or gold to a nation (logged)."),
                ("/admineco mp_approve",     "Approve a megaproject, set its effect, cost, and duration."),
                ("/admineco mp_advance",     "Advance a megaproject's construction by N months."),
                ("/admineco building_set",   "Edit a building definition field live."),
                ("/admineco building_new",   "Add a new custom building type."),
                ("/calendar set",            "Configure the in-game calendar (speed, channel, start date)."),
                ("/calendar start",          "Start the calendar loop."),
                ("/calendar stop",           "Pause the calendar loop."),
            ],
        },
    }

    data = SECTIONS.get(sec, SECTIONS["general"])
    embed = discord.Embed(
        title=f"📖 {data['title']}",
        color=data["color"],
        description="Use `/help <section>` to browse: general · nation · province · economy · trade · gm",
    )
    for name, value in data["fields"]:
        embed.add_field(name=name, value=value, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    print("[BOOT] Starting keep_alive...", flush=True)
    keep_alive()
    print("[BOOT] Calling bot.run()...", flush=True)
    bot.run(config.DISCORD_TOKEN)
