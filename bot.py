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
from utils import gm_only
from keep_alive import keep_alive
from command_locale import PolishTranslator
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
    "cogs.panel",
]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
print("[BOOT] bot object created", flush=True)

@i18n.localized
async def global_guild_check(interaction: discord.Interaction) -> bool:
    if interaction.command and interaction.command.name in ("help", "language", "translate", "activate", "panel"):
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
            i18n.text('⛔ Bot not activated on this server. The GM must run `/activate <auth_key>` first.'),
            ephemeral=True)
        return False
    return True

tree.interaction_check = global_guild_check

def _guild_active():
    @i18n.localized
    async def predicate(interaction: discord.Interaction) -> bool:
        # Always allow help and activate
        if interaction.command and interaction.command.name in ("help", "language", "translate", "activate"):
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
                i18n.text('⛔ This bot has not been activated on this server.\nThe Game Master must run `/activate <auth_key>` first.'),
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
                if cog not in bot.extensions:
                    await bot.load_extension(cog)
                print(f"[COG] Loaded: {cog}", flush=True)
            except Exception:
                print(f"[COG] FAILED to load {cog}:", flush=True)
                traceback.print_exc()

        await tree.set_translator(PolishTranslator())
        for guild in bot.guilds:
            try:
                tree.copy_global_to(guild=guild)
                guild_synced = await tree.sync(guild=guild)
                print(f"[SYNC] Guild '{guild.name}': {len(guild_synced)} command(s): {[c.name for c in guild_synced]}", flush=True)
            except Exception as e:
                print(f"[SYNC] Guild sync failed for {guild.name}: {e}", flush=True)

        # Delete remote global duplicates without removing the local source of
        # commands needed by later guilds and reconnects.
        if bot.guilds:
            await bot.http.bulk_upsert_global_commands(bot.application_id, payload=[])
        print(f"[READY] Done. Connected to {len(bot.guilds)} guild(s).", flush=True)

    except Exception:
        print("[READY] FATAL ERROR in on_ready:", flush=True)
        traceback.print_exc()


@tree.error
@i18n.localized
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
@i18n.localized
async def activate_cmd(interaction: discord.Interaction, auth_key: str):
    import os
    expected = os.getenv("AUTH_KEY", "")
    if not expected:
        await interaction.response.send_message(
            i18n.text('AUTH_KEY not set in server environment.'), ephemeral=True)
        return
    if auth_key != expected:
        await interaction.response.send_message(
            i18n.text('❌ Incorrect key.'), ephemeral=True)
        return
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO game_config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"guild_active_{interaction.guild_id}", "1")
        )
    await interaction.response.send_message(
        i18n.text('✅ Bot activated on this server! All commands are now available.'),
        ephemeral=True)


@tree.command(name="language", description="Set your preferred language / Ustaw jezyk")
@app_commands.describe(lang="en or pl")
@app_commands.choices(lang=[
    app_commands.Choice(name="English", value="en"),
    app_commands.Choice(name="Polski",  value="pl"),
])
@i18n.localized
async def language_cmd(interaction: discord.Interaction, lang: app_commands.Choice[str] = None):
    lang = lang or app_commands.Choice(name="Polski", value="pl")
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


# Compatibility alias: one callback, parameter definition and set of choices.
translate_cmd = app_commands.Command(
    name="translate",
    description=language_cmd.description,
    callback=language_cmd.callback,
)
tree.add_command(translate_cmd)


@tree.command(name="help", description="Show available commands / Pokaz dostepne komendy")
@i18n.localized
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        from cogs.economy import HelpView
        is_gm = gm_only(interaction)
        lang  = i18n.get_user_language(interaction.user.id)
        view  = HelpView(is_gm=is_gm, current="general", lang=lang)
        embed = view._embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        print(f"[HELP ERROR] {e}", flush=True)
        embed = discord.Embed(
            title=i18n.text('Commands'),
            description=i18n.text('Economy cog failed to load — check Render logs.\n\nWorking: `/language`, `/nation`, `/province`'),
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="tutorial", description="Quick start guide / Krótki przewodnik dla nowych graczy")
@i18n.localized
async def tutorial_cmd(interaction: discord.Interaction):
    class TutorialView(i18n.LocalizedView):
        def __init__(self, author_id: int):
            super().__init__(timeout=180)
            self.author_id = author_id
            self.section = "buildings"
            self.message: discord.InteractionMessage | None = None
            self._refresh()

        @i18n.localized
        async def interaction_check(self, inter: discord.Interaction) -> bool:
            if inter.user.id != self.author_id:
                # Fetch language dynamically for the user trying to click
                clicker_lang = i18n.get_user_language(inter.user.id) if hasattr(i18n, "get_user_language") else "en"
                msg = "To nie jest Twój przewodnik." if clicker_lang == "pl" else i18n.text('This is not your tutorial.')
                await inter.response.send_message(msg, ephemeral=True)
                return False
            return True

        async def on_timeout(self):
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

        def _refresh(self):
            for item in self.children:
                if hasattr(item, "custom_id"):
                    item.style = (
                        discord.ButtonStyle.primary
                        if item.custom_id == self.section
                        else discord.ButtonStyle.secondary
                    )

        def _embed(self, user_id: int) -> discord.Embed:
            # DYNAMICALLY fetch the language for the target user every time the embed is generated
            lang = i18n.get_user_language(user_id) if hasattr(i18n, "get_user_language") else "en"

            footer_text = "Użyj przycisków poniżej, aby zmienić sekcję" if lang == "pl" else i18n.text('Use the buttons below to switch sections')

            if self.section == "buildings":
                if lang == "pl":
                    title = "🏗️ Przewodnik: Budynki"
                    desc = (
                        "**Rozbudowa Twojego Narodu:**\n"
                        "• Otwórz Panel → Gospodarka → Buduj; wybierz prowincję i budynek.\n"
                        "• W prowincji mieści się po jednym budynku każdego typu, z ulepszeniami do poziomu 3.\n"
                        "• Bot automatycznie przydziela pracowników; żywność ma pierwszeństwo.\n"
                        "• Zasoby w panelu pokazują prognozę bilansu i podpowiadają, co poprawić."
                    )
                else:
                    title = "🏗️ Guide: Buildings"
                    desc = (
                        "**Developing Your Nation:**\n"
                        "• Open Panel → Economy → Build; choose a province and building.\n"
                        "• Each province supports one of each building type, upgraded to level 3.\n"
                        "• Workers are assigned automatically, with food first.\n"
                        "• Resources in the panel shows the projected balance and practical tips."
                    )
                color = discord.Color.green()

            elif self.section == "food":
                if lang == "pl":
                    title = "🌾 Przewodnik: Żywność i Gospodarka"
                    desc = (
                        "**Zarządzanie Żywnością:**\n"
                        "• Żywność jest pobierana co miesiąc, aby utrzymać populację oraz wojsko.\n"
                        "• Pierwszy miesiąc niedoboru ostrzega; od drugiego spada stabilność, a długi ciężki głód zmniejsza populację.\n"
                        "• Buduj farmy i nadzoruj biomy rolnicze, by utrzymać nadwyżkę produkcyjną.\n"
                        "• Nadwyżki żywności możesz handlować lub gromadzić w magazynach."
                    )
                else:
                    title = "🌾 Guide: Food & Economy"
                    desc = (
                        "**Managing Food Supplies:**\n"
                        "• Food is consumed automatically each month by population and military units.\n"
                        "• The first shortage warns you; the second reduces stability. Prolonged severe hunger reduces population.\n"
                        "• Expand farms and utilize fertile biomes to maintain a surplus.\n"
                        "• Excess food can be stored in stockpiles or traded to other nations."
                    )
                color = discord.Color.gold()

            else:  # megaprojects
                if lang == "pl":
                    title = "🏛️ Przewodnik: Megaprojekty"
                    desc = (
                        "**Wielkie Inwestycje Państwowe:**\n"
                        "• Megaprojekty to unikalne, wielkoskalowe struktury dające potężne bonusy.\n"
                        "• Przełomowe budowy wymagają akceptacji Gamemastera (GM).\n"
                        "• Wymagają znacznych nakładów surowców i wielu miesięcy budowy.\n"
                        "• Sprawdzaj postęp swoich projektów za pomocą komendy `/mp list`."
                    )
                else:
                    title = "🏛️ Guide: Megaprojects"
                    desc = (
                        "**Large-Scale National Works:**\n"
                        "• Megaprojects are massive, unique structures providing faction-wide buffs.\n"
                        "• New proposals require Gamemaster (GM) approval before construction starts.\n"
                        "• They require heavy investment and take multiple months to complete.\n"
                        "• Monitor construction status anytime using `/mp list`."
                    )
                color = discord.Color.purple()

            return discord.Embed(
                title=title,
                description=desc,
                color=color,
            ).set_footer(text=footer_text)

        @discord.ui.button(
            label="🏗️ Buildings / Budynki",
            style=discord.ButtonStyle.primary,
            custom_id="buildings"
        )
        @i18n.localized
        async def btn_buildings(self, inter: discord.Interaction, btn: discord.ui.Button):
            self.section = "buildings"
            self._refresh()
            await inter.response.edit_message(embed=self._embed(inter.user.id), view=self)

        @discord.ui.button(
            label="🌾 Food / Żywność",
            style=discord.ButtonStyle.secondary,
            custom_id="food"
        )
        @i18n.localized
        async def btn_food(self, inter: discord.Interaction, btn: discord.ui.Button):
            self.section = "food"
            self._refresh()
            await inter.response.edit_message(embed=self._embed(inter.user.id), view=self)

        @discord.ui.button(
            label="🏛️ Megaprojects / Megaprojekty",
            style=discord.ButtonStyle.secondary,
            custom_id="mega"
        )
        @i18n.localized
        async def btn_mega(self, inter: discord.Interaction, btn: discord.ui.Button):
            self.section = "mega"
            self._refresh()
            await inter.response.edit_message(embed=self._embed(inter.user.id), view=self)

    view = TutorialView(author_id=interaction.user.id)
    await interaction.response.send_message(
        embed=view._embed(interaction.user.id), view=view, ephemeral=True
    )
    view.message = await interaction.original_response()

if __name__ == "__main__":
    print("[BOOT] Starting keep_alive...", flush=True)
    keep_alive()
    print("[BOOT] Calling bot.run()...", flush=True)
    bot.run(config.DISCORD_TOKEN)
