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
                guild_synced = await tree.sync(guild=guild)
                print(f"[SYNC] Guild '{guild.name}': {len(guild_synced)} command(s): {[c.name for c in guild_synced]}", flush=True)
                # Clear global after guild sync to avoid duplicates
                tree.clear_commands(guild=None)
                await tree.sync()
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
        lang  = i18n.get_user_language(interaction.user.id)
        view  = HelpView(is_gm=is_gm, current="general", lang=lang)
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

@tree.command(name="tutorial", description="Quick start guide / Krótki przewodnik dla nowych graczy")
async def tutorial_cmd(interaction: discord.Interaction):
    lang = _lang(interaction) if "_lang" in globals() else "en"

    class TutorialView(discord.ui.View):
        def __init__(self, author_id: int):
            super().__init__(timeout=180)
            self.author_id = author_id
            self.section = "buildings"
            self.message: discord.InteractionMessage | None = None
            self._refresh()

        async def interaction_check(self, inter: discord.Interaction) -> bool:
            if inter.user.id != self.author_id:
                msg = "To nie jest Twój przewodnik." if lang == "pl" else "This is not your tutorial."
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

        def _embed(self) -> discord.Embed:
            footer_text = "Użyj przycisków poniżej, aby zmienić sekcję" if lang == "pl" else "Use the buttons below to switch sections"

            if self.section == "buildings":
                if lang == "pl":
                    title = "🏗️ Przewodnik: Budynki"
                    desc = (
                        "**Rozbudowa Twojego Narodu:**\n"
                        "• Użyj komendy `/build`, aby wznosić nowe budynki.\n"
                        "• Każdy budynek generuje zasoby (złoto, żywność, produkcję) podczas każdego miesiąca kalendarzowego.\n"
                        "• Wznoszenie budynków wymaga wolnych pól oraz odpowiednich zasobów początkowych.\n"
                        "• Pamiętaj, aby dbać o balans między budynkami gospodarczymi a wojskowymi!"
                    )
                else:
                    title = "🏗️ Guide: Buildings"
                    desc = (
                        "**Developing Your Nation:**\n"
                        "• Use `/build` to construct new facilities.\n"
                        "• Buildings produce resources (gold, food, production) every calendar month.\n"
                        "• Construction requires free land plots and initial resource investment.\n"
                        "• Keep a healthy balance between economy and military infrastructure!"
                    )
                color = discord.Color.green()

            elif self.section == "food":
                if lang == "pl":
                    title = "🌾 Przewodnik: Żywność i Gospodarka"
                    desc = (
                        "**Zarządzanie Żywnością:**\n"
                        "• Żywność jest pobierana co miesiąc, aby utrzymać populację oraz wojsko.\n"
                        "• Niedobór żywności wywołuje głód, obniżając poparcie i osłabiając jednostki.\n"
                        "• Buduj farmy i nadzoruj biomy rolnicze, by utrzymać nadwyżkę produkcyjną.\n"
                        "• Nadwyżki żywności możesz handlować lub gromadzić w magazynach."
                    )
                else:
                    title = "🌾 Guide: Food & Economy"
                    desc = (
                        "**Managing Food Supplies:**\n"
                        "• Food is consumed automatically each month by population and military units.\n"
                        "• Deficits lead to starvation, reducing approval ratings and weakening units.\n"
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
        async def btn_buildings(self, inter: discord.Interaction, btn: discord.ui.Button):
            self.section = "buildings"
            self._refresh()
            await inter.response.edit_message(embed=self._embed(), view=self)

        @discord.ui.button(
            label="🌾 Food / Żywność",
            style=discord.ButtonStyle.secondary,
            custom_id="food"
        )
        async def btn_food(self, inter: discord.Interaction, btn: discord.ui.Button):
            self.section = "food"
            self._refresh()
            await inter.response.edit_message(embed=self._embed(), view=self)

        @discord.ui.button(
            label="🏛️ Megaprojects / Megaprojekty",
            style=discord.ButtonStyle.secondary,
            custom_id="mega"
        )
        async def btn_mega(self, inter: discord.Interaction, btn: discord.ui.Button):
            self.section = "mega"
            self._refresh()
            await inter.response.edit_message(embed=self._embed(), view=self)

    view = TutorialView(author_id=interaction.user.id)
    await interaction.response.send_message(
        embed=view._embed(), view=view, ephemeral=True
    )
    view.message = await interaction.original_response()

if __name__ == "__main__":
    print("[BOOT] Starting keep_alive...", flush=True)
    keep_alive()
    print("[BOOT] Calling bot.run()...", flush=True)
    bot.run(config.DISCORD_TOKEN)
