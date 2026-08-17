"""
Province commands:
  /admin map_import         - GM: import Azgaar JSON (file attachment or URL)
  /admin map_resync         - GM: re-import updated map, merges with existing data
  /admin map_export_markers - GM: generate JS snippet to place resource markers in Azgaar
  /province claim           - GM: claim provinces for a nation (comma list + ranges)
  /province unclaim         - GM: remove ownership from provinces
  /province info            - anyone: view a single province by cell ID
  /province list            - anyone: list all provinces owned by a nation
"""
import json
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import i18n

BIOME_RESOURCES = {
    "Tropical Rainforest":        {"wood": 8, "spices": 4},
    "Tropical Seasonal Forest":   {"wood": 6, "spices": 3},
    "Temperate Rainforest":       {"wood": 7},
    "Temperate Deciduous Forest": {"wood": 6},
    "Tropical Grassland":         {"horses": 3},
    "Temperate Grassland":        {"horses": 4, "cloth": 2},
    "Desert":                     {"stone": 2, "iron": 1},
    "Hot Desert":                 {"stone": 2, "copper": 1},
    "Cold Desert":                {"stone": 3, "iron": 1},
    "Tundra":                     {},
    "Glacier":                    {},
    "Wetland":                    {"clay": 4, "tar": 2},
    "Taiga":                      {"wood": 7, "tar": 3},
    "Savanna":                    {"horses": 3},
    "Marine":                     {},
}

def _biome_resources(biome_name: str, height: int, has_river: bool, coastal: bool) -> dict:
    base = dict(BIOME_RESOURCES.get(biome_name, {}))
    if height > 60:
        base["stone"] = base.get("stone", 0) + 3
        base["iron"]  = base.get("iron",  0) + 2
        base["coal"]  = base.get("coal",  0) + 2
    elif height > 35:
        base["iron"]   = base.get("iron",   0) + 1
        base["copper"] = base.get("copper", 0) + 1
    if has_river:
        base["clay"] = base.get("clay", 0) + 2
    # coastal adds nothing without a Fishing Wharf building — food is building-derived
    return {k: v for k, v in base.items() if v > 0}

def _terrain_label(height: int, biome: str) -> str:
    if height > 70:               return "mountains"
    if height > 50:               return "hills"
    if "forest"  in biome.lower(): return "forest"
    if "desert"  in biome.lower(): return "desert"
    if "wetland" in biome.lower(): return "wetland"
    if "tundra"  in biome.lower(): return "tundra"
    if "taiga"   in biome.lower(): return "taiga"
    return "plains"

def _parse_ids(id_string: str) -> list[int]:
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

def _get_nation(name: str):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (name,))
        return cur.fetchone()

async def _fetch_json(source: str, attachment: discord.Attachment | None) -> dict:
    """Load JSON from attachment bytes or a URL string. Handles gzipped Azgaar .map files."""
    import gzip

    def _parse_bytes(raw: bytes) -> dict:
        # Try gzip first (newer Azgaar .map files are gzipped)
        try:
            raw = gzip.decompress(raw)
        except (gzip.BadGzipFile, OSError):
            pass  # Not gzipped, use as-is
        return json.loads(raw.decode("utf-8"))

    if attachment:
        raw = await attachment.read()
        return _parse_bytes(raw)

    async with aiohttp.ClientSession() as session:
        async with session.get(source, headers={"User-Agent": "WargameBot/1.0"}) as resp:
            resp.raise_for_status()
            raw = await resp.read()
            return _parse_bytes(raw)

def _process_azgaar(data: dict) -> tuple[list[dict], str | None]:
    """
    Parse Azgaar JSON into a list of province dicts.
    Handles both export formats:
      - New style: pack.cells is a list of cell objects [{i, pop, biome, h, ...}, ...]
      - Old style: pack.cells is a dict of parallel arrays {i:[...], pop:[...], ...}
    Returns (province_list, error_message).
    """
    if isinstance(data, dict):
        pack = data.get("pack", data)
    else:
        pack = {}

    cells_raw = pack.get("cells")
    if not cells_raw:
        return [], "Could not find 'cells' in the export. Make sure you exported Full Data (not SVG only)."

    # Extract biomes and burgs data
    biomes_data = pack.get("biomesData", {}) or data.get("biomesData", {})
    biome_names = biomes_data.get("name", []) if isinstance(biomes_data, dict) else []

    burgs = pack.get("burgs", []) or data.get("burgs", [])
    burg_cell = {}
    if isinstance(burgs, list):
        for b in burgs:
            if isinstance(b, dict) and b.get("cell") and b.get("name"):
                burg_cell[int(b["cell"])] = b["name"]

    provinces = []

    # --- Format A: cells is a LIST of cell objects ---
    if isinstance(cells_raw, list):
        for cell in cells_raw:
            if not isinstance(cell, dict):
                continue
            cid = cell.get("i")
            if cid is None:
                continue
            cid = int(cid)
            bi = int(cell.get("biome", 0))
            bname = biome_names[bi] if bi < len(biome_names) else "unknown"
            height = int(cell.get("h", 0))
            has_river = bool(cell.get("r", 0))
            
            # --- COASTAL CHECK FOR FORMAT A ---
            # Land height is h >= 20; haven > 0 means it borders an ocean/sea water body
            haven_val = int(cell.get("haven", 0))
            coastal = (height >= 20) and (haven_val > 0)
            
            # Multiply population by 10,000
            pop = int(cell.get("pop", 0)) * 10000
            
            terrain = _terrain_label(height, bname)
            resources = _biome_resources(bname, height, has_river, coastal)
            name = burg_cell.get(cid, "")
            provinces.append({
                "cell_id": cid, 
                "name": name, 
                "biome": bname,
                "terrain": terrain, 
                "resources": resources, 
                "pop": pop,
                "coastal": coastal
            })

    # --- Format B: cells is a DICT of parallel arrays ---
    elif isinstance(cells_raw, dict):
        cell_ids = cells_raw.get("i", [])
        if not cell_ids:
            return [], "Cell ID array 'i' is empty or missing."
            
        pops = cells_raw.get("pop", [0] * len(cell_ids))
        biome_idx = cells_raw.get("biome", [0] * len(cell_ids))
        heights = cells_raw.get("h", [0] * len(cell_ids))
        rivers = cells_raw.get("r", [0] * len(cell_ids))
        havens = cells_raw.get("haven", [0] * len(cell_ids))

        for idx, cid in enumerate(cell_ids):
            cid = int(cid)
            bi = int(biome_idx[idx]) if idx < len(biome_idx) else 0
            bname = biome_names[bi] if bi < len(biome_names) else "unknown"
            height = int(heights[idx]) if idx < len(heights) else 0
            has_river = bool(rivers[idx]) if idx < len(rivers) else False
            
            # --- COASTAL CHECK FOR FORMAT B ---
            # Land height is h >= 20; haven > 0 means it borders an ocean/sea water body
            haven_val = int(havens[idx]) if idx < len(havens) else 0
            coastal = (height >= 20) and (haven_val > 0)
            
            # Multiply population by 10,000
            pop = (int(pops[idx]) if idx < len(pops) else 0) * 10000
            
            terrain = _terrain_label(height, bname)
            resources = _biome_resources(bname, height, has_river, coastal)
            name = burg_cell.get(cid, "")
            provinces.append({
                "cell_id": cid, 
                "name": name, 
                "biome": bname,
                "terrain": terrain, 
                "resources": resources, 
                "pop": pop,
                "coastal": coastal
            })
    else:
        return [], f"Unexpected 'cells' type: {type(cells_raw).__name__}. Please share a snippet of your JSON so the parser can be adjusted."

    if not provinces:
        return [], "No provinces found after parsing. The file may be empty or in an unsupported format."

    return provinces, None

def _upsert_provinces(province_list: list[dict], resync: bool) -> dict:
    inserted = updated = deactivated = 0
    incoming = {p["cell_id"] for p in province_list}

    with db.cursor() as cur:
        if resync:
            cur.execute("SELECT azgaar_cell_id FROM provinces WHERE active=1")
            existing = {r["azgaar_cell_id"] for r in cur.fetchall()}
            gone = existing - incoming
            if gone:
                ph = ",".join("?" * len(gone))
                cur.execute(
                    f"UPDATE provinces SET active=0, owner_nation_id=NULL WHERE azgaar_cell_id IN ({ph})",
                    list(gone),
                )
                deactivated = len(gone)

        for p in province_list:
            cur.execute("SELECT id FROM provinces WHERE azgaar_cell_id=?", (p["cell_id"],))
            if cur.fetchone():
                cur.execute(
                    """UPDATE provinces SET biome=?,terrain=?,base_resources_json=?,
                       population=?,name=?,active=1 WHERE azgaar_cell_id=?""",
                    (p["biome"], p["terrain"], json.dumps(p["resources"]),
                     p["pop"], p["name"], p["cell_id"]),
                )
                updated += 1
            else:
                cur.execute(
                    """INSERT INTO provinces
                       (azgaar_cell_id,name,biome,terrain,base_resources_json,population)
                       VALUES (?,?,?,?,?,?)""",
                    (p["cell_id"], p["name"], p["biome"], p["terrain"],
                     json.dumps(p["resources"]), p["pop"]),
                )
                inserted += 1

    return {"inserted": inserted, "updated": updated, "deactivated": deactivated}


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class ProvincesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Declare groups at cog level so discord.py picks them up correctly
    admin_grp    = app_commands.Group(name="admin",    description="GM admin commands")
    province_grp = app_commands.Group(name="province", description="Province commands")

    # -------------------------------------------------- /admin map_import
    @admin_grp.command(name="map_import",
                       description="[GM] Import Azgaar JSON / [GM] Importuj mape Azgaar")
    @app_commands.describe(
        file="Attach the .json file (max ~8 MB) / Dolacz plik .json",
        url="Or paste a direct URL to the JSON file / Lub wklej URL do pliku JSON",
    )
    async def map_import(self, interaction: discord.Interaction,
                         file: discord.Attachment | None = None,
                         url: str | None = None):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        if not file and not url:
            await interaction.response.send_message(
                "Provide either a file attachment or a URL.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = await _fetch_json(url or "", file)
            provinces, err = _process_azgaar(data)
            if err:
                await interaction.followup.send(f"❌ {err}", ephemeral=True)
                return
            stats = _upsert_provinces(provinces, resync=False)
            await interaction.followup.send(
                f"✅ **Import complete.**\n"
                f"• Inserted: {stats['inserted']} provinces\n"
                f"• Updated:  {stats['updated']} provinces",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            raise

    # -------------------------------------------------- /admin map_resync
    @admin_grp.command(name="map_resync",
                       description="[GM] Re-import updated Azgaar map / [GM] Zaktualizuj mape")
    @app_commands.describe(
        file="Attach updated .json / Dolacz zaktualizowany .json",
        url="Or paste a direct URL / Lub wklej URL",
    )
    async def map_resync(self, interaction: discord.Interaction,
                         file: discord.Attachment | None = None,
                         url: str | None = None):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        if not file and not url:
            await interaction.response.send_message(
                "Provide either a file attachment or a URL.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = await _fetch_json(url or "", file)
            provinces, err = _process_azgaar(data)
            if err:
                await interaction.followup.send(f"❌ {err}", ephemeral=True)
                return
            stats = _upsert_provinces(provinces, resync=True)
            await interaction.followup.send(
                f"✅ **Resync complete.**\n"
                f"• Inserted: {stats['inserted']} new provinces\n"
                f"• Updated:  {stats['updated']} existing provinces\n"
                f"• Deactivated: {stats['deactivated']} removed provinces",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            raise

    # -------------------------------------------------- /admin map_export_markers
    # Emoji icons for each resource — chosen to be visually distinct on the map.
    RESOURCE_ICONS = {
        "food":      "🌾",
        "wood":      "🌲",
        "stone":     "🪨",
        "iron":      "⚙️",
        "copper":    "🔶",
        "coal":      "⬛",
        "clay":      "🏺",
        "tar":       "🛢️",
        "gunpowder": "💣",
        "horses":    "🐴",
        "spices":    "🌶️",
        "silk":      "🎀",
        "cloth":     "🧵",
        "algae":     "🧪",
    }

    @admin_grp.command(
        name="map_export_markers",
        description="[GM] Generate JS to place resource markers in Azgaar / [GM] Generuj JS z markerami zasobow",
    )
    async def map_export_markers(self, interaction: discord.Interaction):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Pull all active provinces that have resources
        with db.cursor() as cur:
            cur.execute(
                "SELECT azgaar_cell_id, base_resources_json FROM provinces WHERE active=1"
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.followup.send(
                "No provinces in the database yet. Run `/admin map_import` first.",
                ephemeral=True,
            )
            return

        # Build one marker entry per resource per cell
        # Azgaar marker structure: {i, icon, x, y, cell, type, size}
        # x/y aren't known to the bot (we don't store cell coordinates), so we
        # set them to 0 — Azgaar will reposition them to the cell center when
        # the markers layer is toggled off and on.
        marker_entries = []
        marker_id = 1
        for row in rows:
            cid = row["azgaar_cell_id"]
            resources = json.loads(row["base_resources_json"])
            for resource, amount in resources.items():
                if resource == "food":
                    continue  # food comes from buildings, not shown as map markers
                icon = self.RESOURCE_ICONS.get(resource, "❓")
                marker_entries.append(
                    f'  {{i:{marker_id},icon:"{icon}",x:0,y:0,cell:{cid},'
                    f'type:"{resource}",size:20}}'
                )
                marker_id += 1

        if not marker_entries:
            await interaction.followup.send(
                "No non-food resources found in the database.", ephemeral=True
            )
            return

        markers_js = ",\n".join(marker_entries)

        # The JS snippet:
        # 1. Removes any existing resource markers (by type matching our resource names)
        # 2. Pushes the new markers into pack.markers
        # 3. Toggles the markers layer to force a re-render
        resource_types = json.dumps(list(self.RESOURCE_ICONS.keys()))
        js = f"""// Wargame Bot — Resource Markers
// Paste this into the Azgaar browser console (F12 → Console) and press Enter.
// Then toggle the Markers layer off and on to see them.

(function() {{
  const resourceTypes = {resource_types};

  // Remove old resource markers
  pack.markers = pack.markers.filter(m => !resourceTypes.includes(m.type));

  // Add new resource markers
  const newMarkers = [
{markers_js}
  ];
  pack.markers.push(...newMarkers);

  // Re-render markers layer
  const markersLayer = document.getElementById('markers');
  if (markersLayer) {{
    markersLayer.style.display = 'none';
    setTimeout(() => {{ markersLayer.style.display = ''; }}, 100);
  }}

  console.log(`✅ Added ${{newMarkers.length}} resource markers. Toggle Markers layer to refresh.`);
}})();"""

        # Discord has an 8MB file limit — JS will be well under that
        # but we send as a file so it's easy to copy without line wrapping
        js_bytes = js.encode("utf-8")
        file = discord.File(
            fp=__import__("io").BytesIO(js_bytes),
            filename="azgaar_resource_markers.js",
        )

        await interaction.followup.send(
            f"✅ Generated markers for **{marker_id - 1}** resources across **{len(rows)}** provinces.\n\n"
            "**How to use:**\n"
            "1. Open your map in Azgaar\n"
            "2. Press **F12** → **Console** tab\n"
            "3. Paste the contents of the attached `.js` file and press **Enter**\n"
            "4. Toggle the **Markers** layer off and on to see the icons\n\n"
            "Re-run this command after any `/admin map_resync` to keep markers in sync.",
            file=file,
            ephemeral=True,
        )

    # -------------------------------------------------- /province claim
    @province_grp.command(name="claim",
                          description="[GM] Claim provinces for a nation / [GM] Przyznaj prowincje")
    @app_commands.describe(
        nation="Nation name / Nazwa narodu",
        ids="Cell IDs, e.g. 1,2,5-10 / ID komorek np. 1,2,5-10",
    )
    async def claim(self, interaction: discord.Interaction, nation: str, ids: str):
        lang = _lang(interaction)
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(lang, "gm_only"), ephemeral=True)
            return
        nation_row = _get_nation(nation)
        if not nation_row:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return
        try:
            cell_ids = _parse_ids(ids)
        except ValueError:
            await interaction.response.send_message(i18n.t(lang, "province_bad_ids"), ephemeral=True)
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
            cur.execute(
                "INSERT INTO nation_history (nation_id,source,entry_text) VALUES (?,?,?)",
                (nation_row["id"], "system", f"Claimed {claimed} province(s) (cells: {ids})."),
            )

        await interaction.response.send_message(
            i18n.t(lang, "province_claimed",
                   nation=nation_row["name"], claimed=claimed, not_found=not_found),
            ephemeral=True,
        )

    # -------------------------------------------------- /province unclaim
    @province_grp.command(name="unclaim",
                          description="[GM] Remove province ownership / [GM] Odbierz prowincje")
    @app_commands.describe(ids="Cell IDs to unclaim / ID komorek")
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

    # -------------------------------------------------- /province yield
    @province_grp.command(name="yield",
                          description="Total resource yield of your provinces / Laczna produkcja")
    @app_commands.describe(nation="Nation name (blank = your own, GM only for others)")
    async def province_yield(self, interaction: discord.Interaction, nation: str = ""):
        lang  = _lang(interaction)
        is_gm = _gm(interaction)
        if nation and not is_gm:
            await interaction.response.send_message(
                "Province yields are private. You can only view your own.", ephemeral=True)
            return
        if nation:
            nat = _get_nation(nation)
        else:
            with db.cursor() as c:
                c.execute("SELECT * FROM nations WHERE owner_id=?", (str(interaction.user.id),))
                nat = c.fetchone()
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM provinces WHERE owner_nation_id=? AND active=1", (nat["id"],))
            provs = c.fetchall()
        if not provs:
            await interaction.response.send_message(f"**{nat['name']}** owns no provinces.", ephemeral=True)
            return

        total: dict[str, float] = {}
        gold_per_tick = 0.0
        for prov in provs:
            base = json.loads(prov["base_resources_json"])
            for k, v in base.items():
                total[k] = total.get(k, 0) + v
            for bkey in json.loads(prov["buildings_json"]):
                with db.cursor() as c:
                    c.execute("SELECT effect_json FROM building_defs WHERE key=?", (bkey,))
                    bd = c.fetchone()
                if bd:
                    for k, v in json.loads(bd["effect_json"]).items():
                        if k == "gold":
                            gold_per_tick += v
                        else:
                            total[k] = total.get(k, 0) + v

        stab_mod = 0.75 + (nat["stability"] / 100.0) * 0.25
        lines = []
        if gold_per_tick > 0:
            lines.append(f"**Gold**: {gold_per_tick:.0f}/tick (×{stab_mod:.2f} = {gold_per_tick*stab_mod:.0f} effective)")
        for k, v in sorted(total.items()):
            if v > 0:
                lines.append(
                    f"**{k.replace('_',' ').capitalize()}**: {v:.1f}/tick "
                    f"(×{stab_mod:.2f} = {v*stab_mod:.1f} effective)"
                )

        embed = discord.Embed(
            title=f"📊 Province Yield — {nat['flag'] or ''} {nat['name']}",
            description="\n".join(lines) or "*No production yet.*",
            color=discord.Color.green(),
        )
        embed.set_footer(
            text=f"{len(provs)} province(s) | Stability {nat['stability']:.0f}/100 → ×{stab_mod:.2f} modifier"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /province info
    @province_grp.command(name="info",
                          description="View province details / Szczegoly prowincji")
    @app_commands.describe(cell_id="Azgaar cell ID / ID komorki")
    async def info(self, interaction: discord.Interaction, cell_id: int):
        lang = _lang(interaction)
        with db.cursor() as cur:
            cur.execute(
                """SELECT p.*, n.name as nation_name, n.flag as nation_flag
                   FROM provinces p
                   LEFT JOIN nations n ON p.owner_nation_id=n.id
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
        display  = row["name"] or f"Cell #{cell_id}"
        res_str  = ", ".join(f"{k}: {v}" for k, v in resources.items()) or "—"
        embed = discord.Embed(
            title=f"Province — {display}",
            color=discord.Color.green() if row["nation_name"] else discord.Color.greyple(),
        )
        embed.add_field(name="Cell ID",        value=str(cell_id),              inline=True)
        embed.add_field(name="Owner",          value=owner_str,                 inline=True)
        embed.add_field(name="Terrain",        value=row["terrain"],            inline=True)
        embed.add_field(name="Biome",          value=row["biome"],              inline=True)
        embed.add_field(name="Population",     value=f"{row['population']:,}",  inline=True)
        embed.add_field(name="Fortification",  value=str(row["fortification_level"]), inline=True)
        embed.add_field(name="Base Resources", value=res_str,                   inline=False)
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------- /province list
    @province_grp.command(name="list",
                          description="List provinces of a nation / Prowincje narodu")
    @app_commands.describe(
        nation="Nation name / Nazwa narodu",
        page="Page / Strona",
    )
    async def province_list(self, interaction: discord.Interaction,
                            nation: str, page: int = 1):
        lang = _lang(interaction)
        nation_row = _get_nation(nation)
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
                """SELECT azgaar_cell_id,name,terrain,population
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
            cid  = r["azgaar_cell_id"]
            name = r["name"] or f"Cell #{cid}"
            lines.append(f"`{cid:>6}` **{name}** — {r['terrain']} | pop: {r['population']:,}")
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
