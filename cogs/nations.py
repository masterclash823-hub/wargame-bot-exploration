"""
Nation commands:
  /nation found <player> <name> <history>    - GM creates and assigns a nation
  /nation transfer <nation> <player>         - GM transfers nation ownership
  /nation stats  [name]                      - view a nation's stats
  /nation history <name> [page]              - public history log (paginated)
  /nation history_add <name> <text>          - GM only: add a history entry
"""
from flags import flag_text, flagged_embed
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


from utils import gm_only as _gm


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

    @nation_group.command(name="found", description="GM: create and assign a nation / GM: utwórz i nadaj państwo")
    @app_commands.describe(player="Player who will own the nation / Gracz otrzymujący państwo",
                           name="Nation name / Nazwa państwa", history="Nation lore / Historia państwa",
                           flag="Flag emoji or URL / Flaga lub URL", government="Government type / Ustrój")
    @i18n.localized
    async def found(self, interaction: discord.Interaction, player: discord.Member,
                    name: str, history: str, flag: str = "", government: str = ""):
        from world_service import create_nation, tr
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), 'gm_only'), ephemeral=True); return
        if player.bot:
            await interaction.response.send_message(tr('Wybierz gracza, nie bota.', 'Choose a player account.'), ephemeral=True); return
        try: create_nation(player.id, name, history, flag, government, interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True); return
        embed = flagged_embed(discord.Embed(title=tr('Państwo utworzone i nadane', 'Nation created and assigned'),
            description=f"**{name.strip()}** → <@{player.id}>",
            color=discord.Color.green()), (flag, name))
        lore=history.strip()
        for start in range(0,len(lore),1000):
            embed.add_field(name=tr('Historia państwa', 'Nation lore'), value=lore[start:start+1000], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @nation_group.command(name='transfer', description='GM: transfer a nation to a player / GM: przekaż państwo graczowi')
    @i18n.localized
    async def transfer(self, interaction: discord.Interaction, nation: str, player: discord.Member):
        from world_service import tr
        from nation_admin import TransferView
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), 'gm_only'), ephemeral=True); return
        n = _get_by_name(nation)
        if not n:
            await interaction.response.send_message(i18n.t(_lang(interaction), 'nation_not_found'), ephemeral=True); return
        if player.bot or _get_by_owner(str(player.id)):
            await interaction.response.send_message(tr('Wybierz gracza, który nie ma państwa.', 'Choose a player without a nation.'), ephemeral=True); return
        text = f"**{n['name']}**: <@{n['owner_id']}> → <@{player.id}>\n\n" + tr(
            'Zasoby, wojsko, historia, cele i pamięć decyzji pozostaną przy państwie. Nowy właściciel przejmie aktywne traktaty i umowy, w tym reparacje. Oczekujące propozycje traktatów i wymian zostaną anulowane.',
            'Resources, forces, history, goals and decision memory remain with the nation. The new owner inherits active treaties and contracts, including reparations. Pending treaty and trade proposals will be cancelled.')
        embed = flagged_embed(discord.Embed(title=tr('Potwierdź przekazanie państwa', 'Confirm nation transfer'), description=text), (n['flag'],n['name']))
        await interaction.response.send_message(embed=embed, view=TransferView(interaction.user.id,n,player.id), ephemeral=True)

    # ------------------------------------------------------------------ /nation stats
    @nation_group.command(name="stats", description="View nation stats / Statystyki narodu")
    @app_commands.describe(name="Nation name, blank = your own / Nazwa, puste = twoj narod")
    @i18n.localized
    async def stats(self, interaction: discord.Interaction, name: str = ""):
        lang = _lang(interaction)
        nation = _get_by_name(name) if name else _get_by_owner(str(interaction.user.id))

        if not nation:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        # Calculate dynamic population from owned provinces
        with db.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(population), 0) AS total_pop FROM provinces WHERE owner_nation_id = ?",
                (nation["id"],)
            )
            row = cur.fetchone()
            total_population = row["total_pop"] if isinstance(row, dict) else row[0]

        tech = json.loads(nation["tech_json"])
        resources = json.loads(nation["resources_json"])
        display_title = f"{flag_text(nation['flag'])} {nation['name']}".strip()

        embed = discord.Embed(
            title=display_title,
            color=discord.Color.blue(),
        )
        flagged_embed(embed, (nation['flag'], nation['name']))

        stab = nation["stability"]
        if stab >= 80:   stab_str = i18n.text('✅ {p0:.0f}/100 (Stable)', p0=stab)
        elif stab >= 60: stab_str = i18n.text('🟡 {p0:.0f}/100 (Tense)', p0=stab)
        elif stab >= 40: stab_str = i18n.text('🟠 {p0:.0f}/100 (Unstable)', p0=stab)
        else:            stab_str = i18n.text('🔴 {p0:.0f}/100 (Crisis)', p0=stab)

        embed.add_field(name=i18n.text('Government'),  value=nation["government_type"],         inline=True)
        embed.add_field(name=i18n.text('Treasury'),    value=i18n.text('{p0:.0f} gold', p0=nation['treasury']),  inline=True)
        embed.add_field(name=i18n.text('Stability'),   value=stab_str,                          inline=True)
        embed.add_field(name=i18n.text('Population'),  value=f"{total_population:,}",           inline=True)
        
        from world_service import profile, tr
        from technology import historical_year, discoveries
        with db.cursor() as cur:year,_=historical_year(nation,discoveries(cur,nation['id']))
        embed.add_field(name=tr('Orientacyjny rok technologiczny (IRL)', 'Approximate technology year (IRL)'),
                        value=tr(f'Około {year} r. — umowne porównanie z historią Ziemi, niezależne od daty gry.',
                                 f'Circa {year} — a rough Earth-history analogy, independent of the game date.'),inline=False)
        with db.cursor() as cur: identity = profile(cur, nation['id'])
        embed.add_field(name=tr('Prestiż', 'Prestige'), value=str(identity['prestige']))
        embed.add_field(name=tr('Reputacja dyplomatyczna', 'Diplomatic reputation'), value=f"{identity['reputation']}/100")
        stab_mod = 0.75 + (stab / 100.0) * 0.25
        embed.add_field(name=i18n.text('Production'),  value=i18n.text('×{p0:.2f} (stability)', p0=stab_mod),   inline=True)
        
        embed.add_field(
            name=i18n.text('Tech Levels'),
            value="\n".join(f"{i18n.term(k)}: {v:.1f}" for k, v in tech.items()),
            inline=True,
        )
        if resources:
            embed.add_field(
                name=i18n.text('Resources'),
                value="\n".join(f"{i18n.term(k)}: {v}" for k, v in resources.items()),
                inline=True,
            )
        created_at = nation["created_at"]
        if hasattr(created_at, "strftime"):
            created_str = created_at.strftime("%Y-%m-%d")
        else:
            created_str = str(created_at)[:10]
        
        embed.set_footer(text=i18n.text('Founded: {p0}', p0=created_str))
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ /nation history
    @nation_group.command(name="history", description="View nation history / Historia narodu")
    @app_commands.describe(
        name="Nation name / Nazwa narodu",
        page="Page number / Numer strony",
    )
    @i18n.localized
    async def history(self, interaction: discord.Interaction, name: str, page: int = 1):
        lang   = _lang(interaction)
        nation = _get_by_name(name)
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        is_owner = nation["owner_id"] == str(interaction.user.id)
        is_gm = _gm(interaction)
        # Private trade and event entries are visible only to nation owner and GM.
        if is_owner or is_gm:
            source_filter = ""
            filter_params: tuple = (nation["id"],)
        else:
            source_filter = "AND source NOT IN ('trade_private','event_private','research_private')"
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
            f"`{r['timestamp'].strftime('%Y-%m-%d') if hasattr(r['timestamp'], 'strftime') else str(r['timestamp'])[:10]}` [{r['source'].upper()}] {r['entry_text']}"
            for r in rows
        ]

        embed = discord.Embed(
            title=i18n.t(lang, "history_title", nation=name),
            description="\n\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=i18n.text('Page {p0}/{p1}', p0=page, p1=total_pages))
        await interaction.response.send_message(embed=embed, ephemeral=is_owner or is_gm)

    # ------------------------------------------------------------------ /nation list
    @nation_group.command(name="list", description="List all nations / Lista wszystkich narodow")
    @i18n.localized
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

        from utils import EmbedPager
        pages = []
        for r in rows:
            flag = flag_text(r["flag"])
            owner = f"<@{r['owner_id']}>"
            pages.append(flagged_embed(discord.Embed(
                title=i18n.t(lang, "nation_list_title"),
                description=f"{flag} **{r['name']}** — {r['government_type']} ({owner})",
                color=discord.Color.blurple(),
            ), (r['flag'], r['name'])))
        await interaction.response.send_message(embed=pages[0], view=EmbedPager(pages, interaction.user.id))
    @nation_group.command(name="history_add", description="[GM] Add history entry / [GM] Dodaj wpis historii")
    @app_commands.describe(
        name="Nation name / Nazwa narodu",
        text="Entry text / Tresc wpisu",
    )
    @i18n.localized
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
    @i18n.localized
    async def delete(self, interaction: discord.Interaction, name: str):
        lang = _lang(interaction)
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(lang, "gm_only"), ephemeral=True)
            return
        nation = _get_by_name(name)
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        class ConfirmDelete(i18n.LocalizedView):
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
                    content=i18n.text('🗑️ Nation **{p0}** deleted. Provinces unclaimed.', p0=nation['name']),
                    embed=None, view=None)

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, btn: discord.Interaction, button: discord.ui.Button):
                self.stop()
                await btn.response.edit_message(content=i18n.text('Cancelled.'), embed=None, view=None)

        embed = discord.Embed(
            title=i18n.text('⚠️ Confirm Nation Deletion'),
            description=(
                i18n.text('This will permanently delete **{p0}** and release all their provinces.\nMilitary units, blueprints, history, and projects will also be deleted.\n\n**This cannot be undone.**', p0=nation['name'])
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, view=ConfirmDelete(), ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(NationCog(bot))
