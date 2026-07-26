"""
Military commands:
  /blueprint create ship <name> <hull> <modules...>  - design a ship blueprint
  /blueprint create unit <name> <type>               - create a land unit blueprint
  /blueprint list                                    - view your blueprints
  /blueprint delete <id>                             - delete a blueprint
  /military build <blueprint_id> <qty> <cell_id>    - build units from a blueprint
  /military list [nation]                            - view your forces
  /military move <unit_id> <cell_id>                - move a unit group
  /military disband <unit_id>                        - disband a unit group

Upkeep is deducted each resource tick:
  Peace: base rate per unit
  War:   3x base rate (when any war relation exists)
"""
import json

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import i18n

# ---------------------------------------------------------------------------
# Ship hulls
# ---------------------------------------------------------------------------
HULLS = {
    "sloop": {
        "name": "Sloop",
        "slots": 2, "hp": 100, "speed": 5, "cargo": 2,
        "cost": {"gold": 150, "wood": 80, "tar": 10},
        "requires_tech": 0.0,
        "peace_upkeep": 3.0,
        "desc": "Fast, lightly armed. Good for scouting and raiding.",
    },
    "frigate": {
        "name": "Frigate",
        "slots": 4, "hp": 250, "speed": 4, "cargo": 4,
        "cost": {"gold": 300, "wood": 150, "tar": 20, "iron": 20},
        "requires_tech": 2.0,
        "peace_upkeep": 6.0,
        "desc": "Versatile warship. Good balance of speed and firepower.",
    },
    "galleon": {
        "name": "Galleon",
        "slots": 6, "hp": 400, "speed": 3, "cargo": 10,
        "cost": {"gold": 500, "wood": 250, "tar": 40, "iron": 30},
        "requires_tech": 3.0,
        "peace_upkeep": 10.0,
        "desc": "Heavy trade and war vessel. High cargo capacity.",
    },
    "ship_of_the_line": {
        "name": "Ship of the Line",
        "slots": 8, "hp": 600, "speed": 2, "cargo": 3,
        "cost": {"gold": 800, "wood": 350, "tar": 60, "iron": 60},
        "requires_tech": 5.0,
        "peace_upkeep": 16.0,
        "desc": "Most powerful warship. Slow but devastating firepower.",
    },
}

# ---------------------------------------------------------------------------
# Weapon / utility modules (each fills 1 slot)
# ---------------------------------------------------------------------------
MODULES = {
    "light_cannon": {
        "name": "Light Cannon",
        "attack": 15, "hp_bonus": 0, "speed_mod": 0, "cargo_mod": 0,
        "cost": {"gold": 80, "iron": 20, "gunpowder": 5},
        "desc": "Standard ship armament.",
    },
    "heavy_cannon": {
        "name": "Heavy Cannon",
        "attack": 30, "hp_bonus": 0, "speed_mod": -1, "cargo_mod": 0,
        "cost": {"gold": 150, "iron": 40, "gunpowder": 10},
        "desc": "Powerful but slows the ship.",
    },
    "reinforced_plating": {
        "name": "Reinforced Plating",
        "attack": 0, "hp_bonus": 80, "speed_mod": -1, "cargo_mod": 0,
        "cost": {"gold": 100, "iron": 50},
        "desc": "Extra armour. Reduces speed.",
    },
    "extra_sails": {
        "name": "Extra Sails",
        "attack": 0, "hp_bonus": 0, "speed_mod": 2, "cargo_mod": 0,
        "cost": {"gold": 60, "wood": 30, "cloth": 20},
        "desc": "Increases speed significantly.",
    },
    "cargo_hold": {
        "name": "Cargo Hold",
        "attack": 0, "hp_bonus": 0, "speed_mod": -1, "cargo_mod": 6,
        "cost": {"gold": 50, "wood": 40},
        "desc": "Extra trade capacity. Reduces speed.",
    },
    "enchanted_figurehead": {
        "name": "Enchanted Figurehead",
        "attack": 5, "hp_bonus": 20, "speed_mod": 1, "cargo_mod": 0,
        "cost": {"gold": 200, "algae": 2},
        "desc": "Rare magical enhancement. Costs Algae.",
    },
}

# ---------------------------------------------------------------------------
# Land unit roster (fixed, gated by Land tech level)
# ---------------------------------------------------------------------------
LAND_UNITS = {
    "militia": {
        "name": "Militia",
        "attack": 5, "defense": 4, "hp": 50, "speed": 2,
        "cost": {"gold": 30},
        "requires_tech": 0.0,
        "peace_upkeep": 1.0,
        "desc": "Cheap, weak. Defence only.",
    },
    "pikemen": {
        "name": "Pikemen",
        "attack": 10, "defense": 8, "hp": 70, "speed": 2,
        "cost": {"gold": 60, "iron": 5},
        "requires_tech": 1.0,
        "peace_upkeep": 2.0,
        "desc": "Anti-cavalry. Cheap and reliable.",
    },
    "musketeers": {
        "name": "Musketeers",
        "attack": 18, "defense": 6, "hp": 65, "speed": 2,
        "cost": {"gold": 100, "iron": 10, "gunpowder": 5},
        "requires_tech": 2.0,
        "peace_upkeep": 3.0,
        "desc": "Core ranged infantry. Needs Gunpowder.",
    },
    "dragoons": {
        "name": "Dragoons",
        "attack": 20, "defense": 10, "hp": 80, "speed": 4,
        "cost": {"gold": 150, "horses": 5},
        "requires_tech": 3.0,
        "peace_upkeep": 4.0,
        "desc": "Mobile cavalry. Needs Horses.",
    },
    "field_cannon": {
        "name": "Field Cannon",
        "attack": 35, "defense": 3, "hp": 60, "speed": 1,
        "cost": {"gold": 200, "iron": 30, "gunpowder": 15},
        "requires_tech": 3.0,
        "peace_upkeep": 5.0,
        "desc": "Heavy artillery. Slow but devastating.",
    },
    "cuirassiers": {
        "name": "Cuirassiers",
        "attack": 28, "defense": 16, "hp": 100, "speed": 4,
        "cost": {"gold": 250, "horses": 8, "iron": 20},
        "requires_tech": 4.0,
        "peace_upkeep": 6.0,
        "desc": "Elite heavy cavalry.",
    },
    "siege_artillery": {
        "name": "Siege Artillery",
        "attack": 50, "defense": 2, "hp": 50, "speed": 1,
        "cost": {"gold": 300, "iron": 50, "gunpowder": 25},
        "requires_tech": 5.0,
        "peace_upkeep": 8.0,
        "desc": "Fortress breaker. Very slow.",
    },
}

WAR_UPKEEP_MULTIPLIER = 3.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _lang(interaction):
    locale = interaction.locale.value if interaction.locale else None
    return i18n.get_user_language(interaction.user.id, locale)

def _gm(interaction):
    return bool(interaction.guild) and any(
        r.name == config.GM_ROLE_NAME for r in interaction.user.roles
    )

def _nation_owner(uid):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE owner_id=?", (str(uid),))
        return c.fetchone()

def _nation_name(name):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (name,))
        return c.fetchone()

def _log(nid, source, text):
    with db.cursor() as c:
        c.execute(
            "INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)",
            (nid, source, text)
        )

def _is_at_war(nation_id: int) -> bool:
    with db.cursor() as c:
        c.execute(
            "SELECT id FROM relations WHERE status='war' "
            "AND (nation_a_id=? OR nation_b_id=?)",
            (nation_id, nation_id)
        )
        return c.fetchone() is not None

def _tech_land(nation) -> float:
    tech = json.loads(nation["tech_json"])
    return tech.get("land", 3.0)

def _tech_naval(nation) -> float:
    tech = json.loads(nation["tech_json"])
    return tech.get("naval", 3.0)

def _compute_ship_stats(hull_key: str, modules: list[str]) -> dict:
    hull   = HULLS[hull_key]
    attack = 0
    hp     = hull["hp"]
    speed  = hull["speed"]
    cargo  = hull["cargo"]
    for m in modules:
        mod     = MODULES[m]
        attack += mod["attack"]
        hp     += mod["hp_bonus"]
        speed  += mod["speed_mod"]
        cargo  += mod["cargo_mod"]
    return {
        "attack": attack,
        "hp":     max(10, hp),
        "speed":  max(1, speed),
        "cargo":  max(0, cargo),
        "slots_used": len(modules),
        "slots_total": hull["slots"],
    }


def _military_upkeep(nation_id: int) -> float:
    """Calculate total upkeep for all units of a nation for one tick."""
    at_war = _is_at_war(nation_id)
    mult   = WAR_UPKEEP_MULTIPLIER if at_war else 1.0
    with db.cursor() as c:
        c.execute(
            "SELECT u.*, b.type as btype, b.hull, b.components_json "
            "FROM military_units u "
            "LEFT JOIN blueprints b ON u.blueprint_id=b.id "
            "WHERE u.nation_id=?",
            (nation_id,)
        )
        units = c.fetchall()
    total = 0.0
    for u in units:
        if u["blueprint_id"]:
            btype = u["btype"]
            if btype == "ship":
                hull_data = HULLS.get(u["hull"], {})
                base = hull_data.get("peace_upkeep", 5.0)
            else:
                base = LAND_UNITS.get(u["unit_type"], {}).get("peace_upkeep", 2.0)
        else:
            base = LAND_UNITS.get(u["unit_type"], {}).get("peace_upkeep", 2.0)
        total += base * u["quantity"] * mult
    return total


# Called from economy tick
def compute_military_upkeep(nation_id: int) -> float:
    return _military_upkeep(nation_id)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class MilitaryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    blueprint_grp = app_commands.Group(
        name="blueprint", description="Blueprint commands / Projekty"
    )
    military_grp = app_commands.Group(
        name="military", description="Military commands / Wojsko"
    )

    # ================================================== BLUEPRINTS

    # -------------------------------------------------- /blueprint list
    @blueprint_grp.command(name="list",
                           description="View your blueprints / Twoje projekty")
    async def blueprint_list(self, interaction: discord.Interaction):
        lang = _lang(interaction)
        nat  = _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute(
                "SELECT * FROM blueprints WHERE nation_id=? ORDER BY type, name",
                (nat["id"],)
            )
            rows = c.fetchall()
        if not rows:
            await interaction.response.send_message(
                "No blueprints yet. Use `/blueprint create ship` or `/blueprint create unit`.",
                ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📐 Blueprints — {nat['flag'] or ''} {nat['name']}".strip(),
            color=discord.Color.dark_blue(),
        )
        for r in rows:
            stats = json.loads(r["stats_json"])
            if r["type"] == "ship":
                comps = json.loads(r["components_json"])
                mods  = ", ".join(MODULES[m]["name"] for m in comps if m in MODULES) or "none"
                hull  = HULLS.get(r["hull"], {})
                value = (
                    f"Hull: {hull.get('name', r['hull'])} | "
                    f"ATK: {stats.get('attack',0)} HP: {stats.get('hp',0)} "
                    f"SPD: {stats.get('speed',0)} CARGO: {stats.get('cargo',0)}\n"
                    f"Modules: {mods}"
                )
            else:
                lu    = LAND_UNITS.get(r["hull"], {})
                value = (
                    f"ATK: {stats.get('attack',0)} DEF: {stats.get('defense',0)} "
                    f"HP: {stats.get('hp',0)} SPD: {stats.get('speed',0)}"
                )
            embed.add_field(
                name=f"[{r['id']}] {'⚓' if r['type']=='ship' else '⚔️'} {r['name']}",
                value=value,
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /blueprint create ship
    @blueprint_grp.command(name="create_ship",
                           description="Design a ship blueprint / Zaprojektuj okret")
    @app_commands.describe(
        name="Blueprint name / Nazwa projektu",
        hull="Hull type: sloop / frigate / galleon / ship_of_the_line",
        modules="Space-separated module keys e.g. light_cannon extra_sails",
    )
    @app_commands.choices(hull=[
        app_commands.Choice(name="Sloop (2 slots)",            value="sloop"),
        app_commands.Choice(name="Frigate (4 slots)",          value="frigate"),
        app_commands.Choice(name="Galleon (6 slots)",          value="galleon"),
        app_commands.Choice(name="Ship of the Line (8 slots)", value="ship_of_the_line"),
    ])
    async def create_ship(self, interaction: discord.Interaction,
                          name: str, hull: app_commands.Choice[str],
                          modules: str = ""):
        lang = _lang(interaction)
        nat  = _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return

        hull_key  = hull.value
        hull_data = HULLS[hull_key]

        # Tech check
        if _tech_naval(nat) < hull_data["requires_tech"]:
            await interaction.response.send_message(
                f"**{hull_data['name']}** requires Naval tech ≥ {hull_data['requires_tech']:.0f}.",
                ephemeral=True)
            return

        # Parse modules
        mod_keys = [m.strip().lower() for m in modules.split() if m.strip()]
        unknown  = [m for m in mod_keys if m not in MODULES]
        if unknown:
            await interaction.response.send_message(
                f"Unknown module(s): {', '.join(unknown)}\n"
                f"Available: {', '.join(MODULES.keys())}",
                ephemeral=True)
            return

        if len(mod_keys) > hull_data["slots"]:
            await interaction.response.send_message(
                f"Too many modules. **{hull_data['name']}** has {hull_data['slots']} slot(s), "
                f"you specified {len(mod_keys)}.",
                ephemeral=True)
            return

        stats = _compute_ship_stats(hull_key, mod_keys)

        with db.cursor() as c:
            c.execute(
                "INSERT INTO blueprints(nation_id,type,name,hull,components_json,stats_json)"
                " VALUES(?,?,?,?,?,?)",
                (nat["id"], "ship", name, hull_key,
                 json.dumps(mod_keys), json.dumps(stats))
            )
            bp_id = c.lastrowid

        _log(nat["id"], "player",
             f"Designed ship blueprint '{name}' (#{bp_id}): {hull_data['name']} "
             f"with {len(mod_keys)} module(s). ATK:{stats['attack']} HP:{stats['hp']} "
             f"SPD:{stats['speed']} CARGO:{stats['cargo']}.")

        mods_str = ", ".join(MODULES[m]["name"] for m in mod_keys) or "no modules"
        embed = discord.Embed(
            title=f"⚓ Blueprint Created — {name}",
            color=discord.Color.dark_blue(),
        )
        embed.add_field(name="Hull",    value=hull_data["name"],      inline=True)
        embed.add_field(name="Modules", value=mods_str,               inline=True)
        embed.add_field(name="ATK",     value=str(stats["attack"]),   inline=True)
        embed.add_field(name="HP",      value=str(stats["hp"]),       inline=True)
        embed.add_field(name="Speed",   value=str(stats["speed"]),    inline=True)
        embed.add_field(name="Cargo",   value=str(stats["cargo"]),    inline=True)
        embed.set_footer(text=f"Blueprint ID: {bp_id} — use /military build {bp_id} <qty> <cell_id>")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /blueprint create unit
    @blueprint_grp.command(name="create_unit",
                           description="Create a land unit blueprint / Projekt jednostki ladowej")
    @app_commands.describe(
        name="Blueprint name / Nazwa projektu",
        unit_type="Unit type",
    )
    @app_commands.choices(unit_type=[
        app_commands.Choice(name="Militia",          value="militia"),
        app_commands.Choice(name="Pikemen",          value="pikemen"),
        app_commands.Choice(name="Musketeers",       value="musketeers"),
        app_commands.Choice(name="Dragoons",         value="dragoons"),
        app_commands.Choice(name="Field Cannon",     value="field_cannon"),
        app_commands.Choice(name="Cuirassiers",      value="cuirassiers"),
        app_commands.Choice(name="Siege Artillery",  value="siege_artillery"),
    ])
    async def create_unit(self, interaction: discord.Interaction,
                          name: str, unit_type: app_commands.Choice[str]):
        lang = _lang(interaction)
        nat  = _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return

        ukey  = unit_type.value
        udata = LAND_UNITS[ukey]

        if _tech_land(nat) < udata["requires_tech"]:
            await interaction.response.send_message(
                f"**{udata['name']}** requires Land tech ≥ {udata['requires_tech']:.0f}.",
                ephemeral=True)
            return

        stats = {
            "attack":  udata["attack"],
            "defense": udata["defense"],
            "hp":      udata["hp"],
            "speed":   udata["speed"],
        }

        with db.cursor() as c:
            c.execute(
                "INSERT INTO blueprints(nation_id,type,name,hull,components_json,stats_json)"
                " VALUES(?,?,?,?,?,?)",
                (nat["id"], "unit", name, ukey, "[]", json.dumps(stats))
            )
            bp_id = c.lastrowid

        _log(nat["id"], "player",
             f"Designed unit blueprint '{name}' (#{bp_id}): {udata['name']}. "
             f"ATK:{stats['attack']} DEF:{stats['defense']} HP:{stats['hp']}.")

        embed = discord.Embed(
            title=f"⚔️ Blueprint Created — {name}",
            color=discord.Color.dark_red(),
        )
        embed.add_field(name="Type",    value=udata["name"],           inline=True)
        embed.add_field(name="ATK",     value=str(stats["attack"]),    inline=True)
        embed.add_field(name="DEF",     value=str(stats["defense"]),   inline=True)
        embed.add_field(name="HP",      value=str(stats["hp"]),        inline=True)
        embed.add_field(name="Speed",   value=str(stats["speed"]),     inline=True)
        embed.add_field(name="Peace upkeep", value=f"{udata['peace_upkeep']}g/unit/tick", inline=True)
        embed.set_footer(text=f"Blueprint ID: {bp_id} — use /military build {bp_id} <qty> <cell_id>")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /blueprint delete
    @blueprint_grp.command(name="delete",
                           description="Delete a blueprint / Usun projekt")
    @app_commands.describe(blueprint_id="Blueprint ID / ID projektu")
    async def blueprint_delete(self, interaction: discord.Interaction, blueprint_id: int):
        lang = _lang(interaction)
        nat  = _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM blueprints WHERE id=? AND nation_id=?",
                      (blueprint_id, nat["id"]))
            bp = c.fetchone()
        if not bp:
            await interaction.response.send_message(
                f"Blueprint #{blueprint_id} not found or not yours.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("DELETE FROM blueprints WHERE id=?", (blueprint_id,))
        await interaction.response.send_message(
            f"🗑️ Blueprint **{bp['name']}** (#{blueprint_id}) deleted.", ephemeral=True)

    # ================================================== MILITARY

    # -------------------------------------------------- /military build
    @military_grp.command(name="build",
                          description="Build units from a blueprint / Zbuduj jednostki")
    @app_commands.describe(
        blueprint_id="Blueprint ID / ID projektu",
        quantity="How many to build / Ile sztuk",
        cell_id="Province cell ID where they're stationed / ID prowincji",
    )
    async def military_build(self, interaction: discord.Interaction,
                             blueprint_id: int, quantity: int, cell_id: int):
        lang = _lang(interaction)
        nat  = _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return

        with db.cursor() as c:
            c.execute("SELECT * FROM blueprints WHERE id=? AND nation_id=?",
                      (blueprint_id, nat["id"]))
            bp = c.fetchone()
        if not bp:
            await interaction.response.send_message(
                f"Blueprint #{blueprint_id} not found or not yours.", ephemeral=True)
            return

        with db.cursor() as c:
            c.execute(
                "SELECT * FROM provinces WHERE azgaar_cell_id=? AND owner_nation_id=? AND active=1",
                (cell_id, nat["id"])
            )
            prov = c.fetchone()
        if not prov:
            await interaction.response.send_message(
                f"Province {cell_id} not found or not owned by you.", ephemeral=True)
            return

        quantity = max(1, quantity)

        # Calculate total cost
        if bp["type"] == "ship":
            hull_data = HULLS.get(bp["hull"], {})
            base_cost = dict(hull_data.get("cost", {}))
            mods      = json.loads(bp["components_json"])
            for m in mods:
                for r, a in MODULES.get(m, {}).get("cost", {}).items():
                    base_cost[r] = base_cost.get(r, 0) + a
            peace_upkeep = hull_data.get("peace_upkeep", 5.0)
        else:
            udata        = LAND_UNITS.get(bp["hull"], {})
            base_cost    = dict(udata.get("cost", {}))
            peace_upkeep = udata.get("peace_upkeep", 2.0)

        total_cost = {r: a * quantity for r, a in base_cost.items()}
        gold_cost  = total_cost.pop("gold", 0)

        if nat["treasury"] < gold_cost:
            await interaction.response.send_message(
                f"Not enough gold. Need **{gold_cost:,}g**, have **{nat['treasury']:,.0f}g**.",
                ephemeral=True)
            return

        res = json.loads(nat["resources_json"])
        for r, a in total_cost.items():
            if res.get(r, 0) < a:
                await interaction.response.send_message(
                    f"Not enough **{r}**. Need {a}, have {res.get(r,0):.0f}.",
                    ephemeral=True)
                return

        for r, a in total_cost.items():
            res[r] = res.get(r, 0) - a

        with db.cursor() as c:
            c.execute(
                "INSERT INTO military_units(nation_id,blueprint_id,unit_type,quantity,province_id)"
                " VALUES(?,?,?,?,?)",
                (nat["id"], blueprint_id, bp["hull"], quantity, prov["id"])
            )
            unit_id = c.lastrowid
            c.execute(
                "UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                (json.dumps(res), nat["treasury"] - gold_cost, nat["id"])
            )

        war_upkeep = peace_upkeep * WAR_UPKEEP_MULTIPLIER
        _log(nat["id"], "system",
             f"Built {quantity}x {bp['name']} (Unit group #{unit_id}) "
             f"in province {prov['name'] or cell_id}. "
             f"Upkeep: {peace_upkeep*quantity:.0f}g/tick (peace) / "
             f"{war_upkeep*quantity:.0f}g/tick (war).")

        cost_str = f"{gold_cost:,}g" + (
            ", " + ", ".join(f"{a} {r}" for r, a in total_cost.items())
            if total_cost else ""
        )
        embed = discord.Embed(
            title=f"{'⚓' if bp['type']=='ship' else '⚔️'} Units Built",
            description=(
                f"**{quantity}×** {bp['name']} stationed in "
                f"**{prov['name'] or f'Cell #{cell_id}'}**"
            ),
            color=discord.Color.dark_green(),
        )
        embed.add_field(name="Cost",          value=cost_str, inline=True)
        embed.add_field(name="Peace upkeep",  value=f"{peace_upkeep*quantity:.0f}g/tick", inline=True)
        embed.add_field(name="War upkeep",    value=f"{war_upkeep*quantity:.0f}g/tick",   inline=True)
        embed.set_footer(text=f"Unit group ID: {unit_id}")
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------- /military list
    @military_grp.command(name="list",
                          description="View your forces / Twoje sily zbrojne")
    @app_commands.describe(nation="Nation name — GM only / Nazwa narodu — tylko GM")
    async def military_list(self, interaction: discord.Interaction, nation: str = ""):
        lang  = _lang(interaction)
        is_gm = _gm(interaction)

        if nation and not is_gm:
            await interaction.response.send_message(
                "Military composition is private. You can only view your own.", ephemeral=True)
            return

        nat = _nation_name(nation) if nation else _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(
                i18n.t(lang, "nation_not_found" if nation else "no_nation"), ephemeral=True)
            return

        with db.cursor() as c:
            c.execute(
                "SELECT u.*, b.name as bname, b.type as btype, b.hull,"
                " p.name as pname, p.azgaar_cell_id"
                " FROM military_units u"
                " LEFT JOIN blueprints b ON u.blueprint_id=b.id"
                " LEFT JOIN provinces p ON u.province_id=p.id"
                " WHERE u.nation_id=?"
                " ORDER BY b.type, u.id",
                (nat["id"],)
            )
            rows = c.fetchall()

        if not rows:
            await interaction.response.send_message(
                f"**{nat['name']}** has no military units.", ephemeral=True)
            return

        at_war       = _is_at_war(nat["id"])
        total_upkeep = _military_upkeep(nat["id"])

        ships = [r for r in rows if r["btype"] == "ship"]
        units = [r for r in rows if r["btype"] != "ship"]

        embed = discord.Embed(
            title=f"{'⚓⚔️'} Forces — {nat['flag'] or ''} {nat['name']}".strip(),
            color=discord.Color.dark_red(),
        )
        embed.add_field(
            name="Status",
            value=f"{'🔴 At War' if at_war else '🟢 Peace'} | Total upkeep: {total_upkeep:.0f}g/tick",
            inline=False,
        )

        if ships:
            lines = []
            for r in ships:
                loc = r["pname"] or (f"Cell #{r['azgaar_cell_id']}" if r["azgaar_cell_id"] else "unknown")
                lines.append(f"`[{r['id']}]` **{r['quantity']}× {r['bname']}** @ {loc}")
            embed.add_field(name="⚓ Navy", value="\n".join(lines), inline=False)

        if units:
            lines = []
            for r in units:
                loc = r["pname"] or (f"Cell #{r['azgaar_cell_id']}" if r["azgaar_cell_id"] else "unknown")
                lines.append(f"`[{r['id']}]` **{r['quantity']}× {r['bname']}** @ {loc}")
            embed.add_field(name="⚔️ Army", value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------- /military move
    @military_grp.command(name="move",
                          description="Move a unit group / Przemiesz jednostki")
    @app_commands.describe(
        unit_id="Unit group ID / ID grupy jednostek",
        cell_id="Destination province cell ID / ID prowincji docelowej",
    )
    async def military_move(self, interaction: discord.Interaction,
                            unit_id: int, cell_id: int):
        lang = _lang(interaction)
        nat  = _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return

        with db.cursor() as c:
            c.execute("SELECT * FROM military_units WHERE id=? AND nation_id=?",
                      (unit_id, nat["id"]))
            unit = c.fetchone()
        if not unit:
            await interaction.response.send_message(
                f"Unit group #{unit_id} not found or not yours.", ephemeral=True)
            return

        with db.cursor() as c:
            c.execute(
                "SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,)
            )
            prov = c.fetchone()
        if not prov:
            await interaction.response.send_message(
                f"Province {cell_id} not found.", ephemeral=True)
            return

        with db.cursor() as c:
            c.execute(
                "UPDATE military_units SET province_id=? WHERE id=?",
                (prov["id"], unit_id)
            )

        with db.cursor() as c:
            c.execute("SELECT b.name FROM blueprints b WHERE b.id=?", (unit["blueprint_id"],))
            bp = c.fetchone()
        bp_name = bp["name"] if bp else "Unknown"

        _log(nat["id"], "player",
             f"Moved unit group #{unit_id} ({unit['quantity']}× {bp_name}) "
             f"to {prov['name'] or f'Cell #{cell_id}'}.")

        await interaction.response.send_message(
            f"✅ **{unit['quantity']}× {bp_name}** moved to "
            f"**{prov['name'] or f'Cell #{cell_id}'}**.",
            ephemeral=True,
        )

    # -------------------------------------------------- /military disband
    @military_grp.command(name="disband",
                          description="Disband a unit group / Rozwiaz jednostki")
    @app_commands.describe(unit_id="Unit group ID / ID grupy")
    async def military_disband(self, interaction: discord.Interaction, unit_id: int):
        lang = _lang(interaction)
        nat  = _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return

        with db.cursor() as c:
            c.execute(
                "SELECT u.*,b.name as bname FROM military_units u "
                "LEFT JOIN blueprints b ON u.blueprint_id=b.id "
                "WHERE u.id=? AND u.nation_id=?",
                (unit_id, nat["id"])
            )
            unit = c.fetchone()
        if not unit:
            await interaction.response.send_message(
                f"Unit group #{unit_id} not found or not yours.", ephemeral=True)
            return

        with db.cursor() as c:
            c.execute("DELETE FROM military_units WHERE id=?", (unit_id,))

        _log(nat["id"], "player",
             f"Disbanded unit group #{unit_id}: {unit['quantity']}× {unit['bname'] or 'unknown'}.")

        await interaction.response.send_message(
            f"🗑️ Disbanded **{unit['quantity']}× {unit['bname'] or 'unknown'}** (group #{unit_id}).",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MilitaryCog(bot))
