"""
Nation commands:
  /nation found  <name> [flag] [government]  - create your nation
  /nation stats  [name]                      - view a nation's stats
  /nation history <name> [page]              - public history log (paginated)
  /nation history_add <name> <text>          - GM only: add a history entry
"""
import json
import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import i18n
import os


def _lang(interaction: discord.Interaction) -> str:
    locale = interaction.locale.value if interaction.locale else None
    return i18n.get_user_language(interaction.user.id, locale)


def _gm(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    return any(r.name == config.GM_ROLE_NAME for r in interaction.user.roles)


def _get_by_name(name: str):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations WHERE LOWER(name) = LOWER(?)", (name,))
        return cur.fetchone()


def _get_by_owner(owner_id: str):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations WHERE owner_id = ?", (owner_id,))
        return cur.fetchone()


def _log(nation_id: int, source: str, text: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO nation_history (nation_id, source, entry_text) VALUES (?, ?, ?)",
            (nation_id, source, text),
        )


class NationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    nation_group = app_commands.Group(
        name="nation",
        description="Nation commands / Komendy narodów",
    )

    # ------------------------------------------------------------------ /nation found
    @nation_group.command(name="found", description="Found your nation / Zaloz swoj narod")
    @app_commands.describe(
        name="Nation name / Nazwa narodu",
        founding_story="Brief history or origin of your nation (required) / Krotka historia narodu",
        flag="Flag emoji or URL / Emoji flagi lub URL",
        government="Government type / Typ rzadu",
    )
    async def found(
        self,
        interaction: discord.Interaction,
        name: str,
        founding_story: str,
        flag: str = "",
        government: str = "Monarchy",
    ):
        lang = _lang(interaction)

        if _get_by_owner(str(interaction.user.id)):
            existing = _get_by_owner(str(interaction.user.id))
            await interaction.response.send_message(
                i18n.t(lang, "nation_already_exists", nation=existing["name"]),
                ephemeral=True,
            )
            return

        if _get_by_name(name):
            await interaction.response.send_message(
                i18n.t(lang, "nation_name_taken", name=name),
                ephemeral=True,
            )
            return

        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO nations (owner_id, name, flag, government_type) VALUES (?, ?, ?, ?)",
                (str(interaction.user.id), name, flag, government),
            )
            nation_id = cur.lastrowid

        # Seed default blueprints into the new nation
        try:
            from cogs.military import seed_nation_blueprints
            seed_nation_blueprints(nation_id)
        except Exception as e:
            print(f"[MILITARY] Blueprint seed failed: {e}", flush=True)

        _log(nation_id, "system", i18n.t("en", "history_founded", nation=name))

        embed = discord.Embed(
            title=i18n.t(lang, "nation_founded_title"),
            description=i18n.t(lang, "nation_founded_desc", name=name, flag=flag, government=government),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ /nation stats
    @nation_group.command(name="stats", description="View nation stats / Statystyki narodu")
    @app_commands.describe(name="Nation name, blank = your own / Nazwa, puste = twoj narod")
    async def stats(self, interaction: discord.Interaction, name: str = ""):
        lang = _lang(interaction)
        nation = _get_by_name(name) if name else _get_by_owner(str(interaction.user.id))

        if not nation:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        tech = json.loads(nation["tech_json"])
        resources = json.loads(nation["resources_json"])
        flag = nation["flag"] or ""

        stab = nation["stability"]
        if stab >= 80:   stab_str = f"✅ {stab:.0f}/100 (Stable)"
        elif stab >= 60: stab_str = f"🟡 {stab:.0f}/100 (Tense)"
        elif stab >= 40: stab_str = f"🟠 {stab:.0f}/100 (Unstable)"
        else:            stab_str = f"🔴 {stab:.0f}/100 (Crisis)"

        embed = discord.Embed(
            title=f"{flag}  {nation['name']}".strip(),
            color=discord.Color.blue(),
        )
        embed.add_field(name="Government",  value=nation["government_type"],         inline=True)
        embed.add_field(name="Treasury",    value=f"{nation['treasury']:.0f} gold",  inline=True)
        embed.add_field(name="Stability",   value=stab_str,                          inline=True)
        embed.add_field(name="Population",  value=f"{nation['population']:,}",       inline=True)
        # Stability production modifier
        stab_mod = 0.75 + (stab / 100.0) * 0.25
        embed.add_field(name="Production",  value=f"×{stab_mod:.2f} (stability)",   inline=True)
        embed.add_field(
            name="Tech Levels",
            value="\n".join(f"{k.capitalize()}: {v:.1f}" for k, v in tech.items()),
            inline=True,
        )
        if resources:
            embed.add_field(
                name="Resources",
                value="\n".join(f"{k.capitalize()}: {v}" for k, v in resources.items()),
                inline=True,
            )
        embed.set_footer(text=f"Founded: {nation['created_at'][:10]}")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ /nation history
    @nation_group.command(name="history", description="View nation history / Historia narodu")
    @app_commands.describe(
        name="Nation name / Nazwa narodu",
        page="Page number / Numer strony",
    )
    async def history(self, interaction: discord.Interaction, name: str, page: int = 1):
        lang   = _lang(interaction)
        nation = _get_by_name(name)
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        is_owner = nation["owner_id"] == str(interaction.user.id)
        is_gm    = bool(interaction.guild) and any(
            r.name == config.GM_ROLE_NAME for r in interaction.user.roles
        )
        # trade_private entries visible only to nation owner and GM
        if is_owner or is_gm:
            source_filter = ""
            filter_params: tuple = (nation["id"],)
        else:
            source_filter = "AND source != 'trade_private'"
            filter_params = (nation["id"],)

        page     = max(1, page)
        per_page = 10
        offset   = (page - 1) * per_page

        with db.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) as cnt FROM nation_history "
                f"WHERE nation_id=? {source_filter}",
                filter_params,
            )
            total = cur.fetchone()["cnt"]
            cur.execute(
                f"SELECT timestamp, source, entry_text FROM nation_history "
                f"WHERE nation_id=? {source_filter} "
                f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (*filter_params, per_page, offset),
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                i18n.t(lang, "history_empty", nation=name), ephemeral=True
            )
            return

        total_pages = max(1, (total + per_page - 1) // per_page)
        lines = [
            f"`{r['timestamp'][:10]}` [{r['source'].upper()}] {r['entry_text']}"
            for r in rows
        ]

        embed = discord.Embed(
            title=i18n.t(lang, "history_title", nation=name),
            description="\n\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Page {page}/{total_pages}")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ /nation list
    @nation_group.command(name="list", description="List all nations / Lista wszystkich narodow")
    async def nation_list(self, interaction: discord.Interaction):
        lang = _lang(interaction)
        with db.cursor() as cur:
            cur.execute("SELECT name, flag, government_type, owner_id FROM nations ORDER BY name")
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                i18n.t(lang, "nation_list_empty"), ephemeral=True
            )
            return

        lines = []
        for r in rows:
            flag = r["flag"] or ""
            owner = f"<@{r['owner_id']}>"
            lines.append(f"{flag} **{r['name']}** — {r['government_type']} ({owner})")

        embed = discord.Embed(
            title=i18n.t(lang, "nation_list_title"),
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=i18n.t(lang, "nation_list_footer", count=len(rows)))
        await interaction.response.send_message(embed=embed)
    @nation_group.command(name="history_add", description="[GM] Add history entry / [GM] Dodaj wpis historii")
    @app_commands.describe(
        name="Nation name / Nazwa narodu",
        text="Entry text / Tresc wpisu",
    )
    async def history_add(self, interaction: discord.Interaction, name: str, text: str):
        lang = _lang(interaction)
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(lang, "gm_only"), ephemeral=True)
            return
        nation = _get_by_name(name)
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return
        _log(nation["id"], "gm", text)
        await interaction.response.send_message(
            i18n.t(lang, "history_added", nation=name), ephemeral=True
        )


    # ------------------------------------------------------------------ /nation delete
    @nation_group.command(name="delete", description="[GM] Delete a nation / [GM] Usun narod")
    @app_commands.describe(name="Nation name to delete / Nazwa narodu do usuniecia")
    async def delete(self, interaction: discord.Interaction, name: str):
        lang = _lang(interaction)
        if not (bool(interaction.guild) and any(
            r.name == config.GM_ROLE_NAME for r in interaction.user.roles
        )):
            await interaction.response.send_message(i18n.t(lang, "gm_only"), ephemeral=True)
            return
        nation = _get_by_name(name)
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        class ConfirmDelete(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)

            @discord.ui.button(label="⚠️ Confirm Delete", style=discord.ButtonStyle.danger)
            async def confirm(self, btn: discord.Interaction, button: discord.ui.Button):
                with db.cursor() as cur:
                    cur.execute("UPDATE provinces SET owner_nation_id=NULL WHERE owner_nation_id=?",
                                (nation["id"],))
                    cur.execute("DELETE FROM nations WHERE id=?", (nation["id"],))
                self.stop()
                await btn.response.edit_message(
                    content=f"🗑️ Nation **{nation['name']}** deleted. Provinces unclaimed.",
                    embed=None, view=None)

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, btn: discord.Interaction, button: discord.ui.Button):
                self.stop()
                await btn.response.edit_message(content="Cancelled.", embed=None, view=None)

        embed = discord.Embed(
            title="⚠️ Confirm Nation Deletion",
            description=(
                f"This will permanently delete **{nation['name']}** and release all their provinces.\n"
                "Military units, blueprints, history, and megaprojects will also be deleted.\n\n"
                "**This cannot be undone.**"
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, view=ConfirmDelete(), ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(NationCog(bot))
