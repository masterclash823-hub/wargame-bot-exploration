"""
Bot entry point. Uses commands.Bot so cogs can be loaded.
Each feature lives in its own cog under cogs/ - bot.py stays minimal.
"""
import traceback
import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import i18n
from keep_alive import keep_alive

COGS = [
    "cogs.nations",
]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


@bot.event
async def on_ready():
    try:
        print(f"[READY] on_ready fired. User: {bot.user}")
        db.init_db()
        print("[DB] init_db complete")

        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f"[COG] Loaded: {cog}")
            except Exception:
                print(f"[COG] FAILED to load {cog}:")
                traceback.print_exc()

        synced = await tree.sync()
        print(f"[SYNC] Synced {len(synced)} command(s): {[c.name for c in synced]}")
        print(f"[READY] Connected to {len(bot.guilds)} guild(s).")

    except Exception:
        print("[READY] FATAL ERROR in on_ready:")
        traceback.print_exc()


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"[CMD ERROR] {interaction.command.name if interaction.command else '?'}: {type(error).__name__}: {error}")
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


@tree.command(name="help", description="Show available commands / Pokaz dostepne komendy")
async def help_cmd(interaction: discord.Interaction):
    lang = i18n.get_user_language(interaction.user.id)
    embed = discord.Embed(
        title=i18n.t(lang, "help_title"),
        description=i18n.t(lang, "help_intro"),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="/help",               value=i18n.t(lang, "help_help"),                inline=False)
    embed.add_field(name="/language",           value=i18n.t(lang, "help_language"),            inline=False)
    embed.add_field(name="/nation found",       value=i18n.t(lang, "help_nation_found"),        inline=False)
    embed.add_field(name="/nation stats",       value=i18n.t(lang, "help_nation_stats"),        inline=False)
    embed.add_field(name="/nation history",     value=i18n.t(lang, "help_nation_history"),      inline=False)
    embed.add_field(name="/nation history_add", value=i18n.t(lang, "help_nation_history_add"),  inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    keep_alive()
    bot.run(config.DISCORD_TOKEN)
