"""
Economy commands:
  /resources              - view your nation's resource stockpile
  /build                  - construct a building in a province
  /buildings list         - list all available building types
  /buildings province     - list buildings in a specific province
  /megaproject propose    - propose a megaproject
  /megaproject approve    - GM: approve a megaproject
  /megaproject list       - list all megaprojects
  /admin tick             - GM: manually trigger a resource tick
  /admin building_set     - GM: edit a building definition value
  /admin building_new     - GM: create a new building definition

Resource tick runs automatically once every 24h via a background task.
Each owned province contributes its base_resources * building multipliers to
the nation's resource pool. Upkeep is deducted from treasury each tick.
"""
import json
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db
import i18n

# ---------------------------------------------------------------------------
# Default building definitions — seeded into building_defs on first run
# if the table is empty. GMs can edit these live via /admin building_set.
# effect_json keys: resource name -> amount added per tick per province level
# cost_json: resource -> amount required to build
# upkeep_json: resource -> amount deducted per tick (usually gold)
# requires_terrain: comma-separated list of allowed terrains, empty = any
# ---------------------------------------------------------------------------
DEFAULT_BUILDINGS = [
    {
        "key": "farm",
        "name": "Farm",
        "tier": 1,
        "cost_json":    {"gold": 100, "wood": 50},
        "effect_json":  {"food": 10},
        "upkeep_json":  {"gold": 2},
        "requires_terrain": "plains,grassland",
        "requires_tech": 0.0,
        "description": "Produces food on plains and grassland provinces.",
    },
    {
        "key": "fishing_wharf",
        "name": "Fishing Wharf",
        "tier": 1,
        "cost_json":    {"gold": 80, "wood": 60},
        "effect_json":  {"food": 8},
        "upkeep_json":  {"gold": 2},
        "requires_terrain": "coastal",
        "requires_tech": 0.0,
        "description": "Produces food on coastal provinces.",
    },
    {
        "key": "plantation",
        "name": "Plantation",
        "tier": 2,
        "cost_json":    {"gold": 150, "wood": 40},
        "effect_json":  {"food": 6, "spices": 2},
        "upkeep_json":  {"gold": 3},
        "requires_terrain": "forest,jungle",
        "requires_tech": 3.0,
        "description": "Produces food and spices in tropical/forest provinces.",
    },
    {
        "key": "pasture",
        "name": "Pasture",
        "tier": 1,
        "cost_json":    {"gold": 60, "wood": 20},
        "effect_json":  {"food": 5, "horses": 1},
        "upkeep_json":  {"gold": 1},
        "requires_terrain": "plains,grassland,hills",
        "requires_tech": 0.0,
        "description": "Produces food and horses on open terrain.",
    },
    {
        "key": "lumber_camp",
        "name": "Lumber Camp",
        "tier": 1,
        "cost_json":    {"gold": 80},
        "effect_json":  {"wood": 8},
        "upkeep_json":  {"gold": 1},
        "requires_terrain": "forest,taiga",
        "requires_tech": 0.0,
        "description": "Produces wood in forested provinces.",
    },
    {
        "key": "mine",
        "name": "Mine",
        "tier": 1,
        "cost_json":    {"gold": 120, "wood": 30},
        "effect_json":  {"iron": 6, "stone": 4, "coal": 3},
        "upkeep_json":  {"gold": 2},
        "requires_terrain": "hills,mountains",
        "requires_tech": 0.0,
        "description": "Extracts iron, stone and coal from hills and mountains.",
    },
    {
        "key": "copper_mine",
        "name": "Copper Mine",
        "tier": 1,
        "cost_json":    {"gold": 100, "wood": 20},
        "effect_json":  {"copper": 5},
        "upkeep_json":  {"gold": 2},
        "requires_terrain": "hills,mountains",
        "requires_tech": 0.0,
        "description": "Extracts copper from hills and mountains.",
    },
    {
        "key": "clay_pit",
        "name": "Clay Pit",
        "tier": 1,
        "cost_json":    {"gold": 60},
        "effect_json":  {"clay": 6},
        "upkeep_json":  {"gold": 1},
        "requires_terrain": "wetland,plains",
        "requires_tech": 0.0,
        "description": "Extracts clay from wetland and plains provinces.",
    },
    {
        "key": "tar_works",
        "name": "Tar Works",
        "tier": 1,
        "cost_json":    {"gold": 80, "wood": 20},
        "effect_json":  {"tar": 5},
        "upkeep_json":  {"gold": 1},
        "requires_terrain": "forest,wetland,taiga",
        "requires_tech": 0.0,
        "description": "Produces tar from forests and wetlands.",
    },
    {
        "key": "powder_mill",
        "name": "Powder Mill",
        "tier": 2,
        "cost_json":    {"gold": 200, "stone": 50, "iron": 20},
        "effect_json":  {"gunpowder": 4},
        "upkeep_json":  {"gold": 5},
        "requires_terrain": "",
        "requires_tech": 4.0,
        "description": "Produces gunpowder. Requires tech level 4.",
    },
    {
        "key": "textile_mill",
        "name": "Textile Mill",
        "tier": 2,
        "cost_json":    {"gold": 150, "wood": 40},
        "effect_json":  {"cloth": 6},
        "upkeep_json":  {"gold": 3},
        "requires_terrain": "",
        "requires_tech": 3.0,
        "description": "Produces cloth. Requires tech level 3.",
    },
    {
        "key": "market",
        "name": "Market",
        "tier": 1,
        "cost_json":    {"gold": 100, "wood": 30},
        "effect_json":  {"gold": 15},
        "upkeep_json":  {},
        "requires_terrain": "",
        "requires_tech": 0.0,
        "description": "Generates gold income each tick.",
    },
    {
        "key": "port",
        "name": "Port",
        "tier": 1,
        "cost_json":    {"gold": 150, "wood": 80},
        "effect_json":  {"gold": 10},
        "upkeep_json":  {"gold": 2},
        "requires_terrain": "coastal",
        "requires_tech": 0.0,
        "description": "Generates trade gold on coastal provinces. Enables ship construction.",
    },
    {
        "key": "fort",
        "name": "Fort",
        "tier": 1,
        "cost_json":    {"gold": 200, "stone": 80},
        "effect_json":  {},
        "upkeep_json":  {"gold": 5},
        "requires_terrain": "",
        "requires_tech": 0.0,
        "description": "Raises province fortification level by 1.",
    },
    {
        "key": "university",
        "name": "University",
        "tier": 3,
        "cost_json":    {"gold": 500, "stone": 100, "wood": 50},
        "effect_json":  {"tech_points": 1},
        "upkeep_json":  {"gold": 10},
        "requires_terrain": "",
        "requires_tech": 5.0,
        "description": "Generates tech points each tick. Requires tech level 5.",
    },
    {
        "key": "algae_farm",
        "name": "Algae Farm",
        "tier": 3,
        "cost_json":    {"gold": 400, "wood": 60},
        "effect_json":  {"algae": 1},
        "upkeep_json":  {"gold": 8},
        "requires_terrain": "coastal,wetland",
        "requires_tech": 6.0,
        "description": "Slowly produces rare Algae. Requires tech level 6.",
    },
]


def _lang(interaction: discord.Interaction) -> str:
    locale = interaction.locale.value if interaction.locale else None
    return i18n.get_user_language(interaction.user.id, locale)

def _gm(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    return any(r.name == config.GM_ROLE_NAME for r in interaction.user.roles)

def _get_nation_by_owner(owner_id: str):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations WHERE owner_id=?", (owner_id,))
        return cur.fetchone()

def _get_nation_by_name(name: str):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (name,))
        return cur.fetchone()

def _get_building_def(key: str):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM building_defs WHERE key=?", (key,))
        return cur.fetchone()

def _seed_buildings():
    """Insert default building definitions if table is empty."""
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) as cnt FROM building_defs")
        if cur.fetchone()["cnt"] > 0:
            return
        for b in DEFAULT_BUILDINGS:
            cur.execute(
                """INSERT OR IGNORE INTO building_defs
                   (key,name,tier,cost_json,effect_json,upkeep_json,
                    requires_terrain,requires_tech,description)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (b["key"], b["name"], b["tier"],
                 json.dumps(b["cost_json"]), json.dumps(b["effect_json"]),
                 json.dumps(b["upkeep_json"]), b["requires_terrain"],
                 b["requires_tech"], b["description"]),
            )

def _terrain_matches(province_terrain: str, requires_terrain: str) -> bool:
    """Check if a province's terrain is in the building's allowed terrain list."""
    if not requires_terrain:
        return True
    allowed = [t.strip().lower() for t in requires_terrain.split(",")]
    return province_terrain.lower() in allowed

def _tech_ok(nation, requires_tech: float) -> bool:
    """Check if a nation meets the tech requirement (uses average of all tech levels)."""
    if requires_tech <= 0:
        return True
    tech = json.loads(nation["tech_json"])
    avg = sum(tech.values()) / len(tech)
    return avg >= requires_tech

def _deduct_cost(nation_resources: dict, cost: dict) -> tuple[bool, str]:
    """
    Check and deduct build cost from nation resources.
    Returns (success, missing_resource_message).
    Gold is stored separately on the nation row, handled by caller.
    """
    for resource, amount in cost.items():
        if resource == "gold":
            continue
        if nation_resources.get(resource, 0) < amount:
            return False, resource
    for resource, amount in cost.items():
        if resource == "gold":
            continue
        nation_resources[resource] = nation_resources.get(resource, 0) - amount
    return True, ""

def _run_tick():
    """
    Core tick logic — runs synchronously, called from async task and /admin tick.
    For each nation, sums resource yields from all owned provinces + buildings,
    adds to stockpile, deducts upkeep from treasury, logs summary to history.
    Returns a summary string.
    """
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations")
        nations = cur.fetchall()

    summaries = []
    for nation in nations:
        nation_id = nation["id"]
        resources = json.loads(nation["resources_json"])
        treasury  = nation["treasury"]
        total_upkeep = 0.0

        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM provinces WHERE owner_nation_id=? AND active=1",
                (nation_id,),
            )
            provinces = cur.fetchall()

        for province in provinces:
            base  = json.loads(province["base_resources_json"])
            bldgs = json.loads(province["buildings_json"])

            # Add base province resources
            for res, amt in base.items():
                resources[res] = resources.get(res, 0) + amt

            # Add building effects + collect upkeep
            for bkey in bldgs:
                bdef = _get_building_def(bkey)
                if not bdef:
                    continue
                effects = json.loads(bdef["effect_json"])
                upkeep  = json.loads(bdef["upkeep_json"])

                for res, amt in effects.items():
                    if res == "gold":
                        treasury += amt
                    elif res == "tech_points":
                        pass  # tech points handled separately (step 6)
                    else:
                        resources[res] = resources.get(res, 0) + amt

                total_upkeep += upkeep.get("gold", 0)

        treasury -= total_upkeep
        treasury  = max(0.0, treasury)

        with db.cursor() as cur:
            cur.execute(
                "UPDATE nations SET resources_json=?, treasury=? WHERE id=?",
                (json.dumps(resources), treasury, nation_id),
            )
            cur.execute(
                "INSERT INTO nation_history (nation_id,source,entry_text) VALUES (?,?,?)",
                (nation_id, "system",
                 f"Daily tick: treasury {treasury:.0f} gold, upkeep {total_upkeep:.0f} gold."),
            )

        summaries.append(f"{nation['name']}: upkeep -{total_upkeep:.0f}g, treasury {treasury:.0f}g")

    return summaries


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class EconomyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _seed_buildings()
        self.daily_tick.start()

    def cog_unload(self):
        self.daily_tick.cancel()

    @tasks.loop(hours=24)
    async def daily_tick(self):
        print("[TICK] Running daily resource tick...", flush=True)
        try:
            summaries = await asyncio.get_event_loop().run_in_executor(None, _run_tick)
            for s in summaries:
                print(f"[TICK] {s}", flush=True)
            print("[TICK] Done.", flush=True)
        except Exception as e:
            print(f"[TICK] ERROR: {e}", flush=True)

    @daily_tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    # Groups
    buildings_grp    = app_commands.Group(name="buildings",    description="Building commands")
    megaproject_grp  = app_commands.Group(name="megaproject",  description="Megaproject commands")
    admin_eco_grp    = app_commands.Group(name="admineco",     description="GM economy admin")

    # -------------------------------------------------- /resources
    @app_commands.command(name="resources",
                          description="View your nation's resources / Zasoby twojego narodu")
    async def resources(self, interaction: discord.Interaction):
        lang = _lang(interaction)
        nation = _get_nation_by_owner(str(interaction.user.id))
        if not nation:
            await interaction.response.send_message(
                i18n.t(lang, "no_nation"), ephemeral=True)
            return

        res = json.loads(nation["resources_json"])
        if not res:
            desc = "*No resources yet. Build some buildings to start producing.*"
        else:
            desc = "\n".join(
                f"**{k.capitalize()}**: {v:,.1f}" for k, v in sorted(res.items()) if v > 0
            )

        flag = nation["flag"] or ""
        embed = discord.Embed(
            title=f"{flag} {nation['name']} — Resources".strip(),
            description=desc,
            color=discord.Color.green(),
        )
        embed.add_field(name="Treasury", value=f"{nation['treasury']:,.0f} gold", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /build
    @app_commands.command(name="build",
                          description="Construct a building in a province / Buduj w prowincji")
    @app_commands.describe(
        cell_id="Province cell ID / ID komorki prowincji",
        building="Building key e.g. farm, mine, port / Klucz budynku np. farm, mine",
    )
    async def build(self, interaction: discord.Interaction, cell_id: int, building: str):
        lang = _lang(interaction)
        nation = _get_nation_by_owner(str(interaction.user.id))
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return

        # Check province ownership
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,)
            )
            province = cur.fetchone()

        if not province:
            await interaction.response.send_message(
                i18n.t(lang, "province_not_found", cell_id=cell_id), ephemeral=True)
            return

        if province["owner_nation_id"] != nation["id"]:
            await interaction.response.send_message(
                i18n.t(lang, "build_not_owner"), ephemeral=True)
            return

        bdef = _get_building_def(building.lower())
        if not bdef:
            await interaction.response.send_message(
                i18n.t(lang, "build_unknown", key=building), ephemeral=True)
            return

        # Check terrain
        if not _terrain_matches(province["terrain"], bdef["requires_terrain"]):
            await interaction.response.send_message(
                i18n.t(lang, "build_wrong_terrain",
                        building=bdef["name"], terrain=province["terrain"]),
                ephemeral=True)
            return

        # Check tech
        if not _tech_ok(nation, bdef["requires_tech"]):
            await interaction.response.send_message(
                i18n.t(lang, "build_need_tech",
                        building=bdef["name"], level=bdef["requires_tech"]),
                ephemeral=True)
            return

        # Check already built
        buildings = json.loads(province["buildings_json"])
        if building.lower() in buildings:
            await interaction.response.send_message(
                i18n.t(lang, "build_already_exists", building=bdef["name"]), ephemeral=True)
            return

        # Check and deduct costs
        cost = json.loads(bdef["cost_json"])
        gold_cost = cost.get("gold", 0)
        if nation["treasury"] < gold_cost:
            await interaction.response.send_message(
                i18n.t(lang, "build_no_gold",
                        need=gold_cost, have=nation["treasury"]),
                ephemeral=True)
            return

        resources = json.loads(nation["resources_json"])
        ok, missing = _deduct_cost(resources, cost)
        if not ok:
            await interaction.response.send_message(
                i18n.t(lang, "build_no_resource", resource=missing), ephemeral=True)
            return

        # Apply build
        buildings.append(building.lower())
        new_treasury = nation["treasury"] - gold_cost
        fort_bonus = 1 if building.lower() == "fort" else 0

        with db.cursor() as cur:
            cur.execute(
                "UPDATE provinces SET buildings_json=?, fortification_level=fortification_level+? WHERE azgaar_cell_id=?",
                (json.dumps(buildings), fort_bonus, cell_id),
            )
            cur.execute(
                "UPDATE nations SET resources_json=?, treasury=? WHERE id=?",
                (json.dumps(resources), new_treasury, nation["id"]),
            )
            cur.execute(
                "INSERT INTO nation_history (nation_id,source,entry_text) VALUES (?,?,?)",
                (nation["id"], "system",
                 f"Built {bdef['name']} in province {province['name'] or cell_id}."),
            )

        effects = json.loads(bdef["effect_json"])
        effects_str = ", ".join(f"+{v} {k}/tick" for k, v in effects.items()) or "special effect"
        embed = discord.Embed(
            title=i18n.t(lang, "build_success_title"),
            description=i18n.t(lang, "build_success_desc",
                                building=bdef["name"],
                                province=province["name"] or f"Cell #{cell_id}",
                                effects=effects_str),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------- /buildings list
    @buildings_grp.command(name="list",
                           description="List all building types / Lista typow budynkow")
    async def buildings_list(self, interaction: discord.Interaction):
        lang = _lang(interaction)
        with db.cursor() as cur:
            cur.execute("SELECT * FROM building_defs ORDER BY tier, name")
            rows = cur.fetchall()

        embeds = []
        current = discord.Embed(title="Available Buildings", color=discord.Color.blue())
        count = 0
        for b in rows:
            cost    = json.loads(b["cost_json"])
            effects = json.loads(b["effect_json"])
            cost_str    = ", ".join(f"{v} {k}" for k, v in cost.items())
            effects_str = ", ".join(f"+{v} {k}/tick" for k, v in effects.items()) or "special"
            terrain_str = b["requires_terrain"] or "any"
            tech_str    = f" | Tech ≥{b['requires_tech']}" if b["requires_tech"] > 0 else ""
            value = (
                f"{b['description']}\n"
                f"Cost: {cost_str} | Terrain: {terrain_str}{tech_str}\n"
                f"Produces: {effects_str}"
            )
            current.add_field(name=f"Tier {b['tier']} — `{b['key']}` {b['name']}", value=value, inline=False)
            count += 1
            if count % 5 == 0:
                embeds.append(current)
                current = discord.Embed(title="Available Buildings (cont.)", color=discord.Color.blue())
        if current.fields:
            embeds.append(current)

        await interaction.response.send_message(embed=embeds[0], ephemeral=True)
        for e in embeds[1:]:
            await interaction.followup.send(embed=e, ephemeral=True)

    # -------------------------------------------------- /buildings province
    @buildings_grp.command(name="province",
                           description="List buildings in a province / Budynki w prowincji")
    @app_commands.describe(cell_id="Province cell ID / ID komorki")
    async def buildings_province(self, interaction: discord.Interaction, cell_id: int):
        lang = _lang(interaction)
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,)
            )
            province = cur.fetchone()

        if not province:
            await interaction.response.send_message(
                i18n.t(lang, "province_not_found", cell_id=cell_id), ephemeral=True)
            return

        buildings = json.loads(province["buildings_json"])
        if not buildings:
            await interaction.response.send_message(
                f"No buildings in **{province['name'] or f'Cell #{cell_id}'}** yet.",
                ephemeral=True)
            return

        lines = []
        for bkey in buildings:
            bdef = _get_building_def(bkey)
            name = bdef["name"] if bdef else bkey
            effects = json.loads(bdef["effect_json"]) if bdef else {}
            eff_str = ", ".join(f"+{v} {k}/tick" for k, v in effects.items()) or "special"
            lines.append(f"**{name}** (`{bkey}`) — {eff_str}")

        embed = discord.Embed(
            title=f"Buildings in {province['name'] or f'Cell #{cell_id}'}",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.add_field(name="Fortification", value=str(province["fortification_level"]), inline=True)
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------- /megaproject propose
    @megaproject_grp.command(name="propose",
                             description="Propose a megaproject / Zaproponuj megaprojekt")
    @app_commands.describe(
        name="Project name / Nazwa projektu",
        effect="Desired effect / Oczekiwany efekt",
        gold_budget="Gold you are willing to spend / Budzet w zlocie",
    )
    async def megaproject_propose(self, interaction: discord.Interaction,
                                  name: str, effect: str, gold_budget: int):
        lang = _lang(interaction)
        nation = _get_nation_by_owner(str(interaction.user.id))
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return

        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO megaprojects
                   (nation_id, name, proposed_effect, cost_json, status)
                   VALUES (?,?,?,?,?)""",
                (nation["id"], name, effect,
                 json.dumps({"gold": gold_budget}), "proposed"),
            )
            mp_id = cur.lastrowid
            cur.execute(
                "INSERT INTO nation_history (nation_id,source,entry_text) VALUES (?,?,?)",
                (nation["id"], "player",
                 f"Proposed megaproject '{name}': {effect} (budget: {gold_budget} gold)."),
            )

        embed = discord.Embed(
            title="Megaproject Proposed",
            description=f"**{name}** (ID: {mp_id})\n{effect}\nBudget: {gold_budget:,} gold\n\nAwaiting GM approval.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------- /megaproject approve
    @megaproject_grp.command(name="approve",
                             description="[GM] Approve a megaproject / [GM] Zatwierdz megaprojekt")
    @app_commands.describe(
        mp_id="Megaproject ID / ID megaprojektu",
        final_effect="Final approved effect / Ostateczny zatwierdzony efekt",
        gm_notes="Optional GM notes / Opcjonalne notatki GM",
    )
    async def megaproject_approve(self, interaction: discord.Interaction,
                                  mp_id: int, final_effect: str, gm_notes: str = ""):
        lang = _lang(interaction)
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(lang, "gm_only"), ephemeral=True)
            return

        with db.cursor() as cur:
            cur.execute("SELECT * FROM megaprojects WHERE id=?", (mp_id,))
            mp = cur.fetchone()

        if not mp:
            await interaction.response.send_message(
                f"Megaproject ID {mp_id} not found.", ephemeral=True)
            return

        with db.cursor() as cur:
            cur.execute(
                """UPDATE megaprojects
                   SET status='approved', proposed_effect=?, gm_notes=?
                   WHERE id=?""",
                (final_effect, gm_notes, mp_id),
            )
            cur.execute(
                "INSERT INTO nation_history (nation_id,source,entry_text) VALUES (?,?,?)",
                (mp["nation_id"], "gm",
                 f"Megaproject '{mp['name']}' approved. Effect: {final_effect}. {gm_notes}"),
            )

        await interaction.response.send_message(
            f"✅ Megaproject **{mp['name']}** (ID {mp_id}) approved.\nEffect: {final_effect}",
            ephemeral=True,
        )

    # -------------------------------------------------- /megaproject list
    @megaproject_grp.command(name="list",
                             description="List megaprojects / Lista megaprojektow")
    @app_commands.describe(nation="Nation name (blank = all) / Nazwa narodu (puste = wszystkie)")
    async def megaproject_list(self, interaction: discord.Interaction, nation: str = ""):
        lang = _lang(interaction)
        with db.cursor() as cur:
            if nation:
                nat = _get_nation_by_name(nation)
                if not nat:
                    await interaction.response.send_message(
                        i18n.t(lang, "nation_not_found"), ephemeral=True)
                    return
                cur.execute(
                    """SELECT m.*, n.name as nation_name FROM megaprojects m
                       JOIN nations n ON m.nation_id=n.id
                       WHERE m.nation_id=? ORDER BY m.id DESC""",
                    (nat["id"],),
                )
            else:
                cur.execute(
                    """SELECT m.*, n.name as nation_name FROM megaprojects m
                       JOIN nations n ON m.nation_id=n.id
                       ORDER BY m.id DESC"""
                )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                "No megaprojects found.", ephemeral=True)
            return

        STATUS_EMOJI = {
            "proposed": "🟡", "approved": "🟢",
            "building": "🔨", "complete": "✅",
        }
        lines = []
        for r in rows:
            emoji = STATUS_EMOJI.get(r["status"], "❓")
            cost  = json.loads(r["cost_json"])
            cost_str = ", ".join(f"{v} {k}" for k, v in cost.items())
            lines.append(
                f"{emoji} **[{r['id']}] {r['name']}** ({r['nation_name']})\n"
                f"  {r['proposed_effect']}\n"
                f"  Budget: {cost_str} | Status: {r['status']}"
            )

        embed = discord.Embed(
            title="Megaprojects",
            description="\n\n".join(lines),
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------- /admineco tick
    @admin_eco_grp.command(name="tick",
                           description="[GM] Manually run resource tick / [GM] Uruchom tick zasobow")
    async def admin_tick(self, interaction: discord.Interaction):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            summaries = await asyncio.get_event_loop().run_in_executor(None, _run_tick)
            report = "\n".join(summaries) if summaries else "No nations found."
            await interaction.followup.send(
                f"✅ **Tick complete.**\n```\n{report}\n```", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Tick failed: {e}", ephemeral=True)

    # -------------------------------------------------- /admineco building_set
    @admin_eco_grp.command(name="building_set",
                           description="[GM] Edit a building definition / [GM] Edytuj definicje budynku")
    @app_commands.describe(
        key="Building key / Klucz budynku",
        field="Field to change: cost_json / effect_json / upkeep_json / description / requires_terrain / requires_tech",
        value="New value / Nowa wartosc",
    )
    async def building_set(self, interaction: discord.Interaction,
                           key: str, field: str, value: str):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        allowed_fields = {"cost_json","effect_json","upkeep_json",
                          "description","requires_terrain","requires_tech","name","tier"}
        if field not in allowed_fields:
            await interaction.response.send_message(
                f"Unknown field `{field}`. Allowed: {', '.join(sorted(allowed_fields))}",
                ephemeral=True)
            return
        if not _get_building_def(key):
            await interaction.response.send_message(
                f"Building `{key}` not found.", ephemeral=True)
            return
        with db.cursor() as cur:
            cur.execute(
                f"UPDATE building_defs SET {field}=? WHERE key=?", (value, key)
            )
        await interaction.response.send_message(
            f"✅ `{key}.{field}` updated to `{value}`.", ephemeral=True)

    # -------------------------------------------------- /admineco building_new
    @admin_eco_grp.command(name="building_new",
                           description="[GM] Add a new building type / [GM] Dodaj nowy typ budynku")
    @app_commands.describe(
        key="Unique key / Unikalny klucz",
        name="Display name / Nazwa",
        tier="Tier 1-3",
        cost_json='Cost JSON e.g. {"gold":100,"wood":50}',
        effect_json='Effect JSON e.g. {"food":10}',
        requires_terrain="Comma-separated terrains or blank for any",
        description="Short description",
    )
    async def building_new(self, interaction: discord.Interaction,
                           key: str, name: str, tier: int,
                           cost_json: str, effect_json: str,
                           requires_terrain: str = "", description: str = ""):
        if not _gm(interaction):
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        try:
            json.loads(cost_json)
            json.loads(effect_json)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(
                f"Invalid JSON: {e}", ephemeral=True)
            return
        if _get_building_def(key.lower()):
            await interaction.response.send_message(
                f"Building `{key}` already exists. Use `/admineco building_set` to edit it.",
                ephemeral=True)
            return
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO building_defs
                   (key,name,tier,cost_json,effect_json,upkeep_json,
                    requires_terrain,requires_tech,description)
                   VALUES (?,?,?,?,?,'{}',?,0.0,?)""",
                (key.lower(), name, tier, cost_json, effect_json,
                 requires_terrain, description),
            )
        await interaction.response.send_message(
            f"✅ Building `{key}` created.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
