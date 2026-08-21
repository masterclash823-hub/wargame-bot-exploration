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

import config, db, i18n
from keep_alive import keep_alive
print("[BOOT] local modules imported", flush=True)

COGS = [
    "cogs.nations",
    "cogs.provinces",
    "cogs.economy",
    "cogs.tech",
    "cogs.military",
    "cogs.combat",
    "cogs.events",
    "cogs.colonialism",
]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
print("[BOOT] bot object created", flush=True)

async def global_guild_check(interaction: discord.Interaction) -> bool:
    if interaction.command and interaction.command.name in ("help", "language", "activate"):
        return True
    if not interaction.guild:
        return False
    with db.cursor() as cur:
        cur.execute(
            "SELECT value FROM game_config WHERE key=?",
            (f"guild_active_{interaction.guild_id}",)
        )
        row = cur.fetchone()
    if not row or row["value"] != "1":
        await interaction.response.send_message(
            "⛔ Bot not activated on this server. The GM must run `/activate <auth_key>` first.",
            ephemeral=True)
        return False
    return True

tree.interaction_check = global_guild_check

def _guild_active():
    async def predicate(interaction: discord.Interaction) -> bool:
        # Always allow help and activate
        if interaction.command and interaction.command.name in ("help", "language", "activate"):
            return True
        if not interaction.guild:
            return False
        with db.cursor() as cur:
            cur.execute(
                "SELECT value FROM game_config WHERE key=?",
                (f"guild_active_{interaction.guild_id}",)
            )
            row = cur.fetchone()
        if not row or row["value"] != "1":
            await interaction.response.send_message(
                "⛔ This bot has not been activated on this server.\n"
                "The Game Master must run `/activate <auth_key>` first.",
                ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

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

        for guild in bot.guilds:
            try:
                tree.copy_global_to(guild=guild)
                # guild_synced = await tree.sync(guild=guild)
                print(f"[SYNC] Guild '{guild.name}': {len(guild_synced)} command(s): {[c.name for c in guild_synced]}", flush=True)
                # Clear global after guild sync to avoid duplicates
                tree.clear_commands(guild=None)
                # await tree.sync()
                print("[SYNC] Global commands cleared to prevent duplicates.", flush=True)
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


@tree.command(name="activate",
              description="Activate the bot on this server (GM only)")
@app_commands.describe(auth_key="Secret key from the bot owner")
async def activate_cmd(interaction: discord.Interaction, auth_key: str):
    import os
    expected = os.getenv("AUTH_KEY", "")
    if not expected:
        await interaction.response.send_message(
            "AUTH_KEY not set in server environment.", ephemeral=True)
        return
    if auth_key != expected:
        await interaction.response.send_message(
            "❌ Incorrect key.", ephemeral=True)
        return
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO game_config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"guild_active_{interaction.guild_id}", "1")
        )
    await interaction.response.send_message(
        "✅ Bot activated on this server! All commands are now available.",
        ephemeral=True)


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


@tree.command(name="help", description="Show available commands / Pokaz dostepne komendy")
async def help_cmd(interaction: discord.Interaction):
    try:
        from cogs.economy import HelpView
        is_gm = bool(interaction.guild) and any(
            r.name == config.GM_ROLE_NAME for r in interaction.user.roles
        )
        view  = HelpView(is_gm=is_gm, current="general")
        embed = view._embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        print(f"[HELP ERROR] {e}", flush=True)
        embed = discord.Embed(
            title="Commands",
            description="Economy cog failed to load — check Render logs.\n\nWorking: `/language`, `/nation`, `/province`",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    print("[BOOT] Starting keep_alive...", flush=True)
    keep_alive()
    print("[BOOT] Calling bot.run()...", flush=True)
    bot.run(config.DISCORD_TOKEN)
