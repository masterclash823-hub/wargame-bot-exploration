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
        flag="Flag emoji or URL / Emoji flagi lub URL",
        government="Government type / Typ rzadu",
    )
    async def found(
        self,
        interaction: discord.Interaction,
        name: str,
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

        embed = discord.Embed(
            title=f"{flag}  {nation['name']}".strip(),
            color=discord.Color.blue(),
        )
        embed.add_field(name="Government",  value=nation["government_type"],      inline=True)
        embed.add_field(name="Treasury",    value=f"{nation['treasury']:.0f} gold", inline=True)
        embed.add_field(name="Stability",   value=f"{nation['stability']:.0f}/100", inline=True)
        embed.add_field(name="Population",  value=f"{nation['population']:,}",    inline=True)
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
        lang = _lang(interaction)
        nation = _get_by_name(name)
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        page = max(1, page)
        per_page = 10
        offset = (page - 1) * per_page

        with db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as cnt FROM nation_history WHERE nation_id = ?",
                (nation["id"],),
            )
            total = cur.fetchone()["cnt"]
            cur.execute(
                """SELECT timestamp, source, entry_text FROM nation_history
                   WHERE nation_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
                (nation["id"], per_page, offset),
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


async def setup(bot: commands.Bot):
    await bot.add_cog(NationCog(bot))
