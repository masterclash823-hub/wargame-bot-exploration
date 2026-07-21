"""
Nation commands:
  /nation found  <name> [flag] [government]  - create your nation
  /nation stats  [name]                      - view a nation's stats
  /nation history <name>                     - public history log (paginated)
  /nation history_add <name> <text>          - GM only: add a history entry
"""
import json
import discord
from discord import app_commands
from discord.ext import commands

import db
import i18n
from utils import gm_only, get_nation_by_name, get_nation_by_owner, log_history, t_interaction


class NationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    nation_group = app_commands.Group(name="nation", description="Nation commands / Komendy narodów")

    # ------------------------------------------------------------------ /nation found
    @nation_group.command(name="found", description="Found your nation / Załóż swój naród")
    @app_commands.describe(
        name="Nation name / Nazwa narodu",
        flag="Flag emoji or URL / Emoji flagi lub URL",
        government="Government type / Typ rządu (e.g. Monarchy, Republic ...)",
    )
    async def found(
        self,
        interaction: discord.Interaction,
        name: str,
        flag: str = "",
        government: str = "Monarchy",
    ):
        lang = t_interaction(interaction)

        # One nation per player
        existing = get_nation_by_owner(str(interaction.user.id))
        if existing:
            await interaction.response.send_message(
                i18n.t(lang, "nation_already_exists", nation=existing["name"]),
                ephemeral=True,
            )
            return

        # Name must be unique
        if get_nation_by_name(name):
            await interaction.response.send_message(
                i18n.t(lang, "nation_name_taken", name=name),
                ephemeral=True,
            )
            return

        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO nations (owner_id, name, flag, government_type)
                   VALUES (?, ?, ?, ?)""",
                (str(interaction.user.id), name, flag, government),
            )
            nation_id = cur.lastrowid

        log_history(nation_id, "system", i18n.t("en", "history_founded", nation=name))

        embed = discord.Embed(
            title=i18n.t(lang, "nation_founded_title"),
            description=i18n.t(lang, "nation_founded_desc", name=name, flag=flag, government=government),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ /nation stats
    @nation_group.command(name="stats", description="View a nation's stats / Statystyki narodu")
    @app_commands.describe(name="Nation name (leave blank for your own) / Nazwa (puste = twój naród)")
    async def stats(self, interaction: discord.Interaction, name: str = ""):
        lang = t_interaction(interaction)

        if name:
            nation = get_nation_by_name(name)
        else:
            nation = get_nation_by_owner(str(interaction.user.id))

        if not nation:
            await interaction.response.send_message(
                i18n.t(lang, "nation_not_found"),
                ephemeral=True,
            )
            return

        tech = json.loads(nation["tech_json"])
        resources = json.loads(nation["resources_json"])

        embed = discord.Embed(
            title=f"{nation['flag']}  {nation['name']}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Government", value=nation["government_type"], inline=True)
        embed.add_field(name="Treasury", value=f"{nation['treasury']:.0f} gold", inline=True)
        embed.add_field(name="Stability", value=f"{nation['stability']:.0f}/100", inline=True)
        embed.add_field(name="Population", value=f"{nation['population']:,}", inline=True)
        embed.add_field(
            name="Tech Levels",
            value="\n".join(f"{k.capitalize()}: {v:.1f}" for k, v in tech.items()),
            inline=True,
        )
        if resources:
            embed.add_field(
                name="Resources",
                value="\n".join(f"{k.capitalize()}: {v}" for k, v in resources.items()) or "—",
                inline=True,
            )
        embed.set_footer(text=f"Founded: {nation['created_at'][:10]}")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ /nation history
    @nation_group.command(name="history", description="View a nation's history / Historia narodu")
    @app_commands.describe(
        name="Nation name / Nazwa narodu",
        page="Page number / Numer strony",
    )
    async def history(self, interaction: discord.Interaction, name: str, page: int = 1):
        lang = t_interaction(interaction)
        nation = get_nation_by_name(name)
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
                   WHERE nation_id = ?
                   ORDER BY timestamp DESC
                   LIMIT ? OFFSET ?""",
                (nation["id"], per_page, offset),
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                i18n.t(lang, "history_empty", nation=name),
                ephemeral=True,
            )
            return

        total_pages = max(1, (total + per_page - 1) // per_page)
        lines = []
        for row in rows:
            date = row["timestamp"][:10]
            source_tag = f"[{row['source'].upper()}]"
            lines.append(f"`{date}` {source_tag} {row['entry_text']}")

        embed = discord.Embed(
            title=i18n.t(lang, "history_title", nation=name),
            description="\n\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Page {page}/{total_pages}")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ /nation history_add
    @nation_group.command(name="history_add", description="[GM] Add a history entry / [GM] Dodaj wpis do historii")
    @app_commands.describe(
        name="Nation name / Nazwa narodu",
        text="Entry text / Treść wpisu",
    )
    async def history_add(self, interaction: discord.Interaction, name: str, text: str):
        lang = t_interaction(interaction)

        if not gm_only(interaction):
            await interaction.response.send_message(i18n.t(lang, "gm_only"), ephemeral=True)
            return

        nation = get_nation_by_name(name)
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        log_history(nation["id"], "gm", text)
        await interaction.response.send_message(
            i18n.t(lang, "history_added", nation=name),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(NationCog(bot))
