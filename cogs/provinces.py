"""
Province commands:
  /admin map_import  - GM: import Azgaar JSON export (first time or re-run)
  /admin map_resync  - GM: re-import updated Azgaar export, merges with existing data
  /province claim    - GM: claim provinces for a nation (comma list + ranges)
  /province unclaim  - GM: remove ownership from provinces
  /province info     - anyone: view a single province
  /province list     - anyone: list all provinces owned by a nation

Azgaar JSON structure used:
  pack.cells.i       -> cell IDs (array)
  pack.cells.pop     -> population per cell
  pack.cells.biome   -> biome index per cell
  pack.cells.height  -> height per cell (used to infer terrain)
  pack.cells.r       -> river index (0 = no river)
  pack.cells.haven   -> haven (coastal indicator)
  pack.biomes.name   -> biome name lookup by index
  pack.cells.state   -> state index (0 = unclaimed in Azgaar)
  Features like burgs (cities) inside cells are noted in name field if present.
"""
import json
import io
import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import i18n

BIOME_RESOURCES = {
    "Tropical Rainforest": {"wood": 8, "spices": 4, "food": 3},
    "Tropical Seasonal Forest": {"wood": 6, "spices": 3, "food": 4},
    "Temperate Rainforest": {"wood": 7, "food": 3},
    "Temperate Deciduous Forest": {"wood": 6, "food": 3},
    "Tropical Grassland": {"food": 5, "horses": 3},
    "Temperate Grassland": {"food": 6, "horses": 4, "cloth": 2},
    "Desert": {"stone": 2, "iron": 1},
    "Tundra": {"food": 1, "fur": 3},
    "Glacier": {},
    "Wetland": {"food": 4, "clay": 4, "tar": 2},
    "Taiga": {"wood": 7, "tar": 3},
    "Savanna": {"food": 4, "horses": 3, "ivory": 1},
    "Marine": {"food": 6},
    "Hot Desert": {"stone": 2, "copper": 1},
    "Cold Desert": {"stone": 3, "iron": 1},
}

def _biome_resources(biome_name: str, height: int, has_river: bool, coastal: bool) -> dict:
    """Derive base resource yields from biome + terrain hints."""
    base = dict(BIOME_RESOURCES.get(biome_name, {"food": 2}))
    base["food"] = base.get("food", 0)
    if height > 60:
        base["stone"] = base.get("stone", 0) + 3
        base["iron"]  = base.get("iron",  0) + 2
        base["coal"]  = base.get("coal",  0) + 2
    elif height > 35:
        base["iron"]   = base.get("iron",   0) + 1
        base["copper"] = base.get("copper", 0) + 1
    if has_river:
        base["clay"] = base.get("clay", 0) + 2
        base["food"] = base.get("food", 0) + 1
    if coastal:
        base["food"] = base.get("food", 0) + 2  # fishing
    return {k: v for k, v in base.items() if v > 0}

def _terrain_label(height: int, biome: str) -> str:
    if height > 70:  return "mountains"
    if height > 50:  return "hills"
    if "forest" in biome.lower(): return "forest"
    if "desert" in biome.lower(): return "desert"
    if "wetland" in biome.lower(): return "wetland"
    if "tundra"  in biome.lower(): return "tundra"
    if "taiga"   in biome.lower(): return "taiga"
    return "plains"

def _parse_ids(id_string: str) -> list[int]:
    """Parse '1,2,5-10,15' into [1,2,5,6,7,8,9,10,15]. Raises ValueError on bad input."""
    ids = []
    for part in id_string.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            ids.extend(range(int(a), int(b) + 1))
        else:
            ids.append(int(part))
    return ids

def _lang(interaction: discord.Interaction) -> str:
    locale = interaction.locale.value if interaction.locale else None
    return i18n.get_user_language(interaction.user.id, locale)

def _gm(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    return any(r.name == config.GM_ROLE_NAME for r in interaction.user.roles)

def _get_nation_by_name(name: str):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations WHERE LOWER(name) = LOWER(?)", (name,))
        return cur.fetchone()


class ProvincesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    admin_group    = app_commands.Group(name="admin",    description="Admin commands / Komendy admina")
    province_group = app_commands.Group(name="province", description="Province commands / Komendy prowincji")

    # --------------------------------------------------------- /admin map_import
    @admin_group.command(name="map_import", description="[GM] Import Azgaar JSON export / [GM] Importuj mape Azgaar")
    @app_commands.describe(file="Azgaar .json export file / Plik eksportu .json z Azgaar")
    async def map_import(self, interaction: discord.Interaction, file: discord.Attachment):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._do_import(interaction, file, resync=False)

    # --------------------------------------------------------- /admin map_resync
    @admin_group.command(name="map_resync", description="[GM] Re-import updated Azgaar map / [GM] Zaktualizuj mape Azgaar")
    @app_commands.describe(file="Updated Azgaar .json export / Zaktualizowany plik .json")
    async def map_resync(self, interaction: discord.Interaction, file: discord.Attachment):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._do_import(interaction, file, resync=True)

    async def _do_import(self, interaction: discord.Interaction, file: discord.Attachment, resync: bool):
        lang = _lang(interaction)
        try:
            raw = await file.read()
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            await interaction.followup.send(f"Failed to read file: {e}", ephemeral=True)
            return

        try:
            pack   = data.get("pack", data)   # Azgaar wraps everything in "pack"
            cells  = pack["cells"]
            biomes = pack.get("biomes", {})
            biome_names = biomes.get("name", [])

            cell_ids  = cells["i"]
            pops      = cells.get("pop",   [0]*len(cell_ids))
            biome_idx = cells.get("biome", [0]*len(cell_ids))
            heights   = cells.get("h",     [0]*len(cell_ids))
            rivers    = cells.get("r",     [0]*len(cell_ids))
            havens    = cells.get("haven", [0]*len(cell_ids))

            # Optional: burg names mapped to cells
            burg_cell: dict[int, str] = {}
            for burg in pack.get("burgs", []):
                if isinstance(burg, dict) and burg.get("cell") is not None:
                    burg_cell[burg["cell"]] = burg.get("name", "")

        except KeyError as e:
            await interaction.followup.send(
                f"Unexpected Azgaar format — missing key: {e}\n"
                "Make sure you exported with **Full data** (not just SVG).", ephemeral=True
            )
            return

        inserted = updated = deactivated = 0
        incoming_ids = set(cell_ids)

        with db.cursor() as cur:
            # Soft-delete cells no longer in the export (resync only)
            if resync:
                cur.execute("SELECT azgaar_cell_id FROM provinces WHERE active=1")
                existing_ids = {r["azgaar_cell_id"] for r in cur.fetchall()}
                gone = existing_ids - incoming_ids
                if gone:
                    placeholders = ",".join("?" * len(gone))
                    cur.execute(
                        f"UPDATE provinces SET active=0, owner_nation_id=NULL WHERE azgaar_cell_id IN ({placeholders})",
                        list(gone),
                    )
                    deactivated = len(gone)

            for idx, cid in enumerate(cell_ids):
                biome_i   = biome_idx[idx] if idx < len(biome_idx) else 0
                biome_name = biome_names[biome_i] if biome_i < len(biome_names) else "unknown"
                height    = heights[idx] if idx < len(heights) else 0
                has_river = bool(rivers[idx]) if idx < len(rivers) else False
                coastal   = bool(havens[idx]) if idx < len(havens) else False
                pop       = int(pops[idx]) if idx < len(pops) else 0
                terrain   = _terrain_label(height, biome_name)
                resources = _biome_resources(biome_name, height, has_river, coastal)
                name      = burg_cell.get(cid, "")

                cur.execute("SELECT id FROM provinces WHERE azgaar_cell_id=?", (cid,))
                existing = cur.fetchone()
                if existing:
                    # Resync: update terrain/biome data but preserve ownership and buildings
                    cur.execute(
                        """UPDATE provinces SET biome=?, terrain=?, base_resources_json=?,
                           population=?, name=?, active=1 WHERE azgaar_cell_id=?""",
                        (biome_name, terrain, json.dumps(resources), pop, name, cid),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """INSERT INTO provinces
                           (azgaar_cell_id, name, biome, terrain, base_resources_json, population)
                           VALUES (?,?,?,?,?,?)""",
                        (cid, name, biome_name, terrain, json.dumps(resources), pop),
                    )
                    inserted += 1

        action = "Resync" if resync else "Import"
        summary = (
            f"**Map {action} complete.**\n"
            f"• Inserted: {inserted} new provinces\n"
            f"• Updated:  {updated} existing provinces\n"
        )
        if resync:
            summary += f"• Deactivated: {deactivated} removed provinces\n"
        await interaction.followup.send(summary, ephemeral=True)

    # --------------------------------------------------------- /province claim
    @province_group.command(name="claim", description="[GM] Claim provinces for a nation / [GM] Przyznaj prowincje narodowi")
    @app_commands.describe(
        nation="Nation name / Nazwa narodu",
        ids="Cell IDs: comma list and/or ranges e.g. 1,2,5-10 / ID komorek np. 1,2,5-10",
    )
    async def claim(self, interaction: discord.Interaction, nation: str, ids: str):
        lang = _lang(interaction)
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(lang, "gm_only"), ephemeral=True)
            return

        nation_row = _get_nation_by_name(nation)
        if not nation_row:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        try:
            cell_ids = _parse_ids(ids)
        except ValueError:
            await interaction.response.send_message(
                i18n.t(lang, "province_bad_ids"), ephemeral=True
            )
            return

        claimed = not_found = 0
        with db.cursor() as cur:
            for cid in cell_ids:
                cur.execute(
                    "UPDATE provinces SET owner_nation_id=? WHERE azgaar_cell_id=? AND active=1",
                    (nation_row["id"], cid),
                )
                if cur.rowcount:
                    claimed += 1
                else:
                    not_found += 1

        # Log to nation history
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO nation_history (nation_id, source, entry_text) VALUES (?,?,?)",
                (nation_row["id"], "system",
                 f"Claimed {claimed} province(s) (cell IDs: {ids})."),
            )

        msg = i18n.t(lang, "province_claimed", nation=nation_row["name"],
                     claimed=claimed, not_found=not_found)
        await interaction.response.send_message(msg, ephemeral=True)

    # --------------------------------------------------------- /province unclaim
    @province_group.command(name="unclaim", description="[GM] Remove province ownership / [GM] Odbierz prowincje")
    @app_commands.describe(ids="Cell IDs to unclaim / ID komorek do odebrania")
    async def unclaim(self, interaction: discord.Interaction, ids: str):
        lang = _lang(interaction)
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(lang, "gm_only"), ephemeral=True)
            return
        try:
            cell_ids = _parse_ids(ids)
        except ValueError:
            await interaction.response.send_message(i18n.t(lang, "province_bad_ids"), ephemeral=True)
            return

        cleared = 0
        with db.cursor() as cur:
            for cid in cell_ids:
                cur.execute(
                    "UPDATE provinces SET owner_nation_id=NULL WHERE azgaar_cell_id=? AND active=1", (cid,)
                )
                if cur.rowcount:
                    cleared += 1

        await interaction.response.send_message(
            i18n.t(lang, "province_unclaimed", count=cleared), ephemeral=True
        )

    # --------------------------------------------------------- /province info
    @province_group.command(name="info", description="View province details / Szczegoły prowincji")
    @app_commands.describe(cell_id="Azgaar cell ID / ID komorki Azgaar")
    async def info(self, interaction: discord.Interaction, cell_id: int):
        lang = _lang(interaction)
        with db.cursor() as cur:
            cur.execute(
                """SELECT p.*, n.name as nation_name, n.flag as nation_flag
                   FROM provinces p
                   LEFT JOIN nations n ON p.owner_nation_id = n.id
                   WHERE p.azgaar_cell_id=? AND p.active=1""",
                (cell_id,),
            )
            row = cur.fetchone()

        if not row:
            await interaction.response.send_message(
                i18n.t(lang, "province_not_found", cell_id=cell_id), ephemeral=True
            )
            return

        resources = json.loads(row["base_resources_json"])
        owner_str = (
            f"{row['nation_flag'] or ''} {row['nation_name']}".strip()
            if row["nation_name"] else "*Unclaimed*"
        )
        res_str = ", ".join(f"{k}: {v}" for k, v in resources.items()) or "—"
        display_name = row["name"] or f"Cell #{cell_id}"

        embed = discord.Embed(
            title=f"Province — {display_name}",
            color=discord.Color.green() if row["nation_name"] else discord.Color.greyple(),
        )
        embed.add_field(name="Cell ID",       value=str(cell_id),            inline=True)
        embed.add_field(name="Owner",         value=owner_str,               inline=True)
        embed.add_field(name="Terrain",       value=row["terrain"],          inline=True)
        embed.add_field(name="Biome",         value=row["biome"],            inline=True)
        embed.add_field(name="Population",    value=f"{row['population']:,}",inline=True)
        embed.add_field(name="Fortification", value=str(row["fortification_level"]), inline=True)
        embed.add_field(name="Base Resources",value=res_str, inline=False)
        await interaction.response.send_message(embed=embed)

    # --------------------------------------------------------- /province list
    @province_group.command(name="list", description="List provinces of a nation / Prowincje narodu")
    @app_commands.describe(
        nation="Nation name / Nazwa narodu",
        page="Page / Strona",
    )
    async def province_list(self, interaction: discord.Interaction, nation: str, page: int = 1):
        lang = _lang(interaction)
        nation_row = _get_nation_by_name(nation)
        if not nation_row:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return

        per_page = 20
        offset   = (max(1, page) - 1) * per_page
        with db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as cnt FROM provinces WHERE owner_nation_id=? AND active=1",
                (nation_row["id"],),
            )
            total = cur.fetchone()["cnt"]
            cur.execute(
                """SELECT azgaar_cell_id, name, terrain, biome, population
                   FROM provinces WHERE owner_nation_id=? AND active=1
                   ORDER BY azgaar_cell_id LIMIT ? OFFSET ?""",
                (nation_row["id"], per_page, offset),
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                i18n.t(lang, "province_list_empty", nation=nation), ephemeral=True
            )
            return

        total_pages = max(1, (total + per_page - 1) // per_page)
        lines = []
        for r in rows:
            name = r["name"] or f"Cell #{r['azgaar_cell_id']}"
            lines.append(f"`{r['azgaar_cell_id']:>6}` **{name}** — {r['terrain']} | pop: {r['population']:,}")

        flag = nation_row["flag"] or ""
        embed = discord.Embed(
            title=f"{flag} {nation_row['name']} — Provinces".strip(),
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Page {page}/{total_pages} · {total} province(s) total")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ProvincesCog(bot))
