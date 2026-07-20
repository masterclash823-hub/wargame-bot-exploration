"""
Bot entry point. Step 1 of the build: skeleton only - connects, syncs slash commands,
and provides /help + /language so localization is proven out before anything else is
built on top of it.

Run: python bot.py
"""
import discord
from discord import app_commands

import config
import db
import i18n
from keep_alive import keep_alive

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    db.init_db()
    await tree.sync()
    print(f"Logged in as {client.user} (id: {client.user.id})")
    print(f"Connected to {len(client.guilds)} guild(s).")


@tree.command(name="language", description="Set your preferred language / Ustaw jezyk")
@app_commands.describe(lang="en or pl")
@app_commands.choices(lang=[
    app_commands.Choice(name="English", value="en"),
    app_commands.Choice(name="Polski", value="pl"),
])
async def language_cmd(interaction: discord.Interaction, lang: app_commands.Choice[str]):
    if lang.value not in config.SUPPORTED_LANGUAGES:
        current_lang = i18n.get_user_language(interaction.user.id, interaction.locale.value)
        await interaction.response.send_message(
            i18n.t(current_lang, "language_invalid", languages=", ".join(config.SUPPORTED_LANGUAGES)),
            ephemeral=True,
        )
        return

    i18n.set_user_language(interaction.user.id, lang.value)
    await interaction.response.send_message(
        i18n.t(lang.value, "language_set_confirm"),
        ephemeral=True,
    )


@tree.command(name="help", description="Show available commands / Pokaz dostepne komendy")
async def help_cmd(interaction: discord.Interaction):
    lang = i18n.get_user_language(interaction.user.id, interaction.locale.value)

    embed = discord.Embed(title=i18n.t(lang, "help_title"), description=i18n.t(lang, "help_intro"))
    embed.add_field(name="\u200b", value=i18n.t(lang, "help_help"), inline=False)
    embed.add_field(name="\u200b", value=i18n.t(lang, "help_language"), inline=False)
    # More command entries get appended here as each build step adds commands.

    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    keep_alive()  # remove this line if you're not relying on an external uptime pinger
    client.run(config.DISCORD_TOKEN)
