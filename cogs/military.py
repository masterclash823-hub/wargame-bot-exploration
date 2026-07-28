"""
Military commands:
  /blueprint list               - view your blueprints (including defaults)
  /blueprint design_ship        - interactive button UI to design a ship
  /blueprint create_unit        - create a land unit blueprint
  /blueprint delete <id>        - delete a custom blueprint
  /military build <id> <qty>    - build units (floating, no province required)
  /military list [nation]       - view your forces (private)
  /military move <id> <cell>    - assign/move a unit group to a province (optional)
  /military unassign <id>       - return unit group to floating pool
  /military disband <id>        - disband a unit group

Default blueprints are seeded into each new nation automatically.
Units are floating by default - province assignment is optional.
Upkeep: peace rate per unit, 3x in wartime.
"""
import json
import discord
from discord import app_commands
from discord.ext import commands

import config, db, i18n

# ---------------------------------------------------------------------------
# Data tables
# ---------------------------------------------------------------------------
HULLS = {
    "sloop":            {"name":"Sloop",            "slots":2,"hp":100,"speed":5,"cargo":2, "cost":{"gold":150,"wood":80,"tar":10},              "requires_tech":0.0,"peace_upkeep":3.0,  "desc":"Fast scout/raider. 2 slots."},
    "frigate":          {"name":"Frigate",          "slots":4,"hp":250,"speed":4,"cargo":4, "cost":{"gold":300,"wood":150,"tar":20,"iron":20},   "requires_tech":2.0,"peace_upkeep":6.0,  "desc":"Versatile warship. 4 slots."},
    "galleon":          {"name":"Galleon",          "slots":6,"hp":400,"speed":3,"cargo":10,"cost":{"gold":500,"wood":250,"tar":40,"iron":30},   "requires_tech":3.0,"peace_upkeep":10.0, "desc":"Trade+war vessel. 6 slots."},
    "ship_of_the_line": {"name":"Ship of the Line","slots":8,"hp":600,"speed":2,"cargo":3, "cost":{"gold":800,"wood":350,"tar":60,"iron":60},   "requires_tech":5.0,"peace_upkeep":16.0, "desc":"Max firepower. 8 slots."},
}

MODULES = {
    "light_cannon":        {"name":"Light Cannon",       "attack":15,"hp_bonus":0,  "speed_mod":0, "cargo_mod":0,  "cost":{"gold":80,"iron":20,"gunpowder":5},   "desc":"+15 ATK"},
    "heavy_cannon":        {"name":"Heavy Cannon",       "attack":30,"hp_bonus":0,  "speed_mod":-1,"cargo_mod":0,  "cost":{"gold":150,"iron":40,"gunpowder":10}, "desc":"+30 ATK, -1 SPD"},
    "reinforced_plating":  {"name":"Reinforced Plating", "attack":0, "hp_bonus":80, "speed_mod":-1,"cargo_mod":0,  "cost":{"gold":100,"iron":50},                "desc":"+80 HP, -1 SPD"},
    "extra_sails":         {"name":"Extra Sails",        "attack":0, "hp_bonus":0,  "speed_mod":2, "cargo_mod":0,  "cost":{"gold":60,"wood":30,"cloth":20},      "desc":"+2 SPD"},
    "cargo_hold":          {"name":"Cargo Hold",         "attack":0, "hp_bonus":0,  "speed_mod":-1,"cargo_mod":6,  "cost":{"gold":50,"wood":40},                 "desc":"+6 CARGO, -1 SPD"},
    "enchanted_figurehead":{"name":"Enchanted Figurehead","attack":5,"hp_bonus":20, "speed_mod":1, "cargo_mod":0,  "cost":{"gold":200,"algae":2},                "desc":"+5 ATK, +20 HP, +1 SPD (costs Algae)"},
}

LAND_UNITS = {
    "militia":        {"name":"Militia",        "attack":5, "defense":4, "hp":50,"speed":2,"cost":{"gold":30},                          "requires_tech":0.0,"peace_upkeep":1.0,"desc":"Cheap defence only."},
    "pikemen":        {"name":"Pikemen",        "attack":10,"defense":8, "hp":70,"speed":2,"cost":{"gold":60,"iron":5},                 "requires_tech":1.0,"peace_upkeep":2.0,"desc":"Anti-cavalry, reliable."},
    "musketeers":     {"name":"Musketeers",     "attack":18,"defense":6, "hp":65,"speed":2,"cost":{"gold":100,"iron":10,"gunpowder":5}, "requires_tech":2.0,"peace_upkeep":3.0,"desc":"Core ranged infantry."},
    "dragoons":       {"name":"Dragoons",       "attack":20,"defense":10,"hp":80,"speed":4,"cost":{"gold":150,"horses":5},              "requires_tech":3.0,"peace_upkeep":4.0,"desc":"Mobile cavalry."},
    "field_cannon":   {"name":"Field Cannon",   "attack":35,"defense":3, "hp":60,"speed":1,"cost":{"gold":200,"iron":30,"gunpowder":15},"requires_tech":3.0,"peace_upkeep":5.0,"desc":"Heavy artillery."},
    "cuirassiers":    {"name":"Cuirassiers",    "attack":28,"defense":16,"hp":100,"speed":4,"cost":{"gold":250,"horses":8,"iron":20},   "requires_tech":4.0,"peace_upkeep":6.0,"desc":"Elite heavy cavalry."},
    "siege_artillery":{"name":"Siege Artillery","attack":50,"defense":2, "hp":50,"speed":1,"cost":{"gold":300,"iron":50,"gunpowder":25},"requires_tech":5.0,"peace_upkeep":8.0,"desc":"Fortress breaker."},
}

WAR_MULT = 3.0

# Default blueprints copied to every new nation (tech <= 2 land, basic sloop)
DEFAULT_BLUEPRINTS = [
    {"type":"unit","name":"Militia",    "hull":"militia",    "components":[]},
    {"type":"unit","name":"Pikemen",    "hull":"pikemen",    "components":[]},
    {"type":"unit","name":"Musketeers", "hull":"musketeers", "components":[]},
    {"type":"ship","name":"Scout Sloop","hull":"sloop",      "components":["light_cannon"]},
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _lang(i): 
    return i18n.get_user_language(i.user.id, i.locale.value if i.locale else None)

def _gm(i):
    return bool(i.guild) and any(r.name == config.GM_ROLE_NAME for r in i.user.roles)

def _nat_owner(uid):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE owner_id=?", (str(uid),))
        return c.fetchone()

def _nat_name(name):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (name,))
        return c.fetchone()

def _log(nid, src, txt):
    with db.cursor() as c:
        c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)", (nid,src,txt))

def _at_war(nid):
    with db.cursor() as c:
        c.execute("SELECT nation_a_id FROM relations WHERE status='war' AND (nation_a_id=? OR nation_b_id=?)", (nid,nid))
        return c.fetchone() is not None

def _tech_naval(nat): return json.loads(nat["tech_json"]).get("naval", 3.0)
def _tech_land(nat):  return json.loads(nat["tech_json"]).get("land",  3.0)

def _ship_stats(hull_key, modules):
    h = HULLS[hull_key]
    atk, hp, spd, cargo = 0, h["hp"], h["speed"], h["cargo"]
    for m in modules:
        md   = MODULES[m]
        atk += md["attack"]; hp += md["hp_bonus"]
        spd += md["speed_mod"]; cargo += md["cargo_mod"]
    return {"attack":atk,"hp":max(10,hp),"speed":max(1,spd),"cargo":max(0,cargo)}

def _unit_stats(ukey):
    u = LAND_UNITS[ukey]
    return {"attack":u["attack"],"defense":u["defense"],"hp":u["hp"],"speed":u["speed"]}

def _upkeep(nid):
    mult = WAR_MULT if _at_war(nid) else 1.0
    with db.cursor() as c:
        c.execute(
            "SELECT u.*,b.type as btype,b.hull FROM military_units u "
            "LEFT JOIN blueprints b ON u.blueprint_id=b.id WHERE u.nation_id=?", (nid,)
        )
        units = c.fetchall()
    total = 0.0
    for u in units:
        if u["btype"] == "ship":
            base = HULLS.get(u["hull"],{}).get("peace_upkeep", 5.0)
        else:
            base = LAND_UNITS.get(u["hull"],{}).get("peace_upkeep", 2.0)
        total += base * u["quantity"] * mult
    return total

def seed_nation_blueprints(nation_id: int):
    """Copy default blueprints into a newly founded nation."""
    with db.cursor() as c:
        c.execute("SELECT COUNT(*) as cnt FROM blueprints WHERE nation_id=?", (nation_id,))
        if c.fetchone()["cnt"] > 0:
            return
    for bp in DEFAULT_BLUEPRINTS:
        if bp["type"] == "ship":
            stats = _ship_stats(bp["hull"], bp["components"])
        else:
            stats = _unit_stats(bp["hull"])
        with db.cursor() as c:
            c.execute(
                "INSERT INTO blueprints(nation_id,type,name,hull,components_json,stats_json)"
                " VALUES(?,?,?,?,?,?)",
                (nation_id, bp["type"], bp["name"], bp["hull"],
                 json.dumps(bp["components"]), json.dumps(stats))
            )

def compute_military_upkeep(nid): return _upkeep(nid)

# ---------------------------------------------------------------------------
# Ship designer UI
# ---------------------------------------------------------------------------
class ShipDesignerView(discord.ui.View):
    def __init__(self, nation_id, hull_key, blueprint_name, slots, base_stats):
        super().__init__(timeout=300)
        self.nation_id     = nation_id
        self.hull_key      = hull_key
        self.bp_name       = blueprint_name
        self.slots         = slots
        self.selected      = []   # list of module keys (repeats allowed)
        self.base_stats    = base_stats
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        used = len(self.selected)
        # Module buttons — up to 5 per row, 3 rows = 15 buttons max, then save/clear
        for i, (mkey, mdata) in enumerate(MODULES.items()):
            disabled = used >= self.slots
            btn = discord.ui.Button(
                label=f"{mdata['name']} ({mdata['desc']})",
                style=discord.ButtonStyle.secondary,
                disabled=disabled,
                row=i // 3,
            )
            btn.callback = self._add_cb(mkey)
            self.add_item(btn)
        # Clear last
        clear = discord.ui.Button(
            label="↩ Remove last",
            style=discord.ButtonStyle.danger,
            disabled=len(self.selected) == 0,
            row=2,
        )
        clear.callback = self._remove_last
        self.add_item(clear)
        # Save
        save = discord.ui.Button(
            label=f"💾 Save Blueprint ({used}/{self.slots} slots)",
            style=discord.ButtonStyle.success,
            row=3,
        )
        save.callback = self._save
        self.add_item(save)

    def _add_cb(self, mkey):
        async def callback(interaction: discord.Interaction):
            self.selected.append(mkey)
            self._rebuild()
            await interaction.response.edit_message(embed=self._embed(), view=self)
        return callback

    async def _remove_last(self, interaction: discord.Interaction):
        if self.selected:
            self.selected.pop()
        self._rebuild()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _save(self, interaction: discord.Interaction):
        stats = _ship_stats(self.hull_key, self.selected)
        with db.cursor() as c:
            c.execute(
                "INSERT INTO blueprints(nation_id,type,name,hull,components_json,stats_json)"
                " VALUES(?,?,?,?,?,?)",
                (self.nation_id, "ship", self.bp_name, self.hull_key,
                 json.dumps(self.selected), json.dumps(stats))
            )
            bp_id = c.lastrowid
        _log(self.nation_id, "player",
             f"Designed ship blueprint '{self.bp_name}' (#{bp_id}): "
             f"{HULLS[self.hull_key]['name']} + {len(self.selected)} module(s). "
             f"ATK:{stats['attack']} HP:{stats['hp']} SPD:{stats['speed']} CARGO:{stats['cargo']}.")
        for item in self.children:
            item.disabled = True
        final = self._embed()
        final.title = f"✅ Blueprint Saved — {self.bp_name} (#{bp_id})"
        final.color = discord.Color.green()
        await interaction.response.edit_message(embed=final, view=self)

    def _embed(self):
        stats = _ship_stats(self.hull_key, self.selected)
        hull  = HULLS[self.hull_key]
        mods_str = (", ".join(MODULES[m]["name"] for m in self.selected)
                    if self.selected else "none")
        embed = discord.Embed(
            title=f"⚓ Designing: {self.bp_name}",
            description=f"Hull: **{hull['name']}** | {len(self.selected)}/{self.slots} slots used",
            color=discord.Color.dark_blue(),
        )
        embed.add_field(name="Modules",value=mods_str,        inline=False)
        embed.add_field(name="ATK",    value=str(stats["attack"]),  inline=True)
        embed.add_field(name="HP",     value=str(stats["hp"]),      inline=True)
        embed.add_field(name="Speed",  value=str(stats["speed"]),   inline=True)
        embed.add_field(name="Cargo",  value=str(stats["cargo"]),   inline=True)
        embed.set_footer(text="Click modules to add them. Each click uses one slot.")
        return embed

# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class MilitaryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    bp_grp  = app_commands.Group(name="blueprint", description="Blueprint commands")
    mil_grp = app_commands.Group(name="military",  description="Military commands")

    # ============================================================ BLUEPRINTS

    @bp_grp.command(name="list", description="View your blueprints / Twoje projekty")
    async def bp_list(self, interaction: discord.Interaction):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT * FROM blueprints WHERE nation_id=? ORDER BY type,name", (nat["id"],))
            rows = c.fetchall()
        if not rows:
            await interaction.response.send_message("No blueprints yet.", ephemeral=True); return
        embed = discord.Embed(
            title=f"📐 Blueprints — {nat['flag'] or ''} {nat['name']}".strip(),
            color=discord.Color.dark_blue(),
        )
        for r in rows:
            stats = json.loads(r["stats_json"])
            if r["type"] == "ship":
                comps     = json.loads(r["components_json"])
                mods      = ", ".join(MODULES[m]["name"] for m in comps if m in MODULES) or "no modules"
                hull      = HULLS.get(r["hull"], {})
                base_cost = dict(hull.get("cost", {}))
                for m in comps:
                    for res, amt in MODULES.get(m, {}).get("cost", {}).items():
                        base_cost[res] = base_cost.get(res, 0) + amt
                cost_str  = ", ".join(f"{v} {k}" for k, v in base_cost.items())
                val = (
                    f"Hull: {hull.get('name', r['hull'])} | "
                    f"ATK:{stats.get('attack',0)} HP:{stats.get('hp',0)} "
                    f"SPD:{stats.get('speed',0)} CARGO:{stats.get('cargo',0)}\n"
                    f"Modules: {mods}\n"
                    f"Cost per unit: {cost_str}\n"
                    f"Upkeep: {hull.get('peace_upkeep',5):.0f}g/unit (peace) "
                    f"/ {hull.get('peace_upkeep',5)*WAR_MULT:.0f}g (war)"
                )
            else:
                udata    = LAND_UNITS.get(r["hull"], {})
                cost_str = ", ".join(f"{v} {k}" for k, v in udata.get("cost", {}).items())
                val = (
                    f"ATK:{stats.get('attack',0)} DEF:{stats.get('defense',0)} "
                    f"HP:{stats.get('hp',0)} SPD:{stats.get('speed',0)}\n"
                    f"Cost per unit: {cost_str}\n"
                    f"Upkeep: {udata.get('peace_upkeep',2):.0f}g/unit (peace) "
                    f"/ {udata.get('peace_upkeep',2)*WAR_MULT:.0f}g (war)"
                )
            embed.add_field(
                name=f"[{r['id']}] {'⚓' if r['type']=='ship' else '⚔️'} {r['name']}",
                value=val, inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bp_grp.command(name="design_ship", description="Design a ship blueprint with button UI")
    @app_commands.describe(name="Blueprint name", hull="Hull type")
    @app_commands.choices(hull=[
        app_commands.Choice(name="Sloop (2 slots, fast scout, tech 0)",            value="sloop"),
        app_commands.Choice(name="Frigate (4 slots, versatile, tech 2)",           value="frigate"),
        app_commands.Choice(name="Galleon (6 slots, trade+war, tech 3)",           value="galleon"),
        app_commands.Choice(name="Ship of the Line (8 slots, max power, tech 5)",  value="ship_of_the_line"),
    ])
    async def design_ship(self, interaction: discord.Interaction,
                          name: str, hull: app_commands.Choice[str]):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        hull_data = HULLS[hull.value]
        if _tech_naval(nat) < hull_data["requires_tech"]:
            await interaction.response.send_message(
                f"**{hull_data['name']}** requires Naval tech ≥ {hull_data['requires_tech']:.0f}.",
                ephemeral=True); return
        view = ShipDesignerView(
            nation_id=nat["id"], hull_key=hull.value, blueprint_name=name,
            slots=hull_data["slots"], base_stats=hull_data,
        )
        await interaction.response.send_message(embed=view._embed(), view=view, ephemeral=True)

    @bp_grp.command(name="create_unit", description="Create a land unit blueprint")
    @app_commands.describe(name="Blueprint name", unit_type="Unit type")
    @app_commands.choices(unit_type=[
        app_commands.Choice(name="Militia (tech 0) — ATK:5 DEF:4 HP:50",           value="militia"),
        app_commands.Choice(name="Pikemen (tech 1) — ATK:10 DEF:8 HP:70",          value="pikemen"),
        app_commands.Choice(name="Musketeers (tech 2) — ATK:18 DEF:6 HP:65",       value="musketeers"),
        app_commands.Choice(name="Dragoons (tech 3) — ATK:20 DEF:10 HP:80",        value="dragoons"),
        app_commands.Choice(name="Field Cannon (tech 3) — ATK:35 DEF:3 HP:60",     value="field_cannon"),
        app_commands.Choice(name="Cuirassiers (tech 4) — ATK:28 DEF:16 HP:100",    value="cuirassiers"),
        app_commands.Choice(name="Siege Artillery (tech 5) — ATK:50 DEF:2 HP:50",  value="siege_artillery"),
    ])
    async def create_unit(self, interaction: discord.Interaction,
                          name: str, unit_type: app_commands.Choice[str]):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        ukey  = unit_type.value
        udata = LAND_UNITS[ukey]
        if _tech_land(nat) < udata["requires_tech"]:
            await interaction.response.send_message(
                f"**{udata['name']}** requires Land tech ≥ {udata['requires_tech']:.0f}.",
                ephemeral=True); return
        stats = _unit_stats(ukey)
        with db.cursor() as c:
            c.execute(
                "INSERT INTO blueprints(nation_id,type,name,hull,components_json,stats_json)"
                " VALUES(?,?,?,?,?,?)",
                (nat["id"],"unit",name,ukey,"[]",json.dumps(stats))
            )
            bp_id = c.lastrowid
        _log(nat["id"],"player",
             f"Created unit blueprint '{name}' (#{bp_id}): {udata['name']} "
             f"ATK:{stats['attack']} DEF:{stats['defense']} HP:{stats['hp']}.")
        embed = discord.Embed(title=f"⚔️ Blueprint Created — {name}", color=discord.Color.dark_red())
        embed.add_field(name="Type",         value=udata["name"],                        inline=True)
        embed.add_field(name="ATK/DEF/HP",   value=f"{stats['attack']}/{stats['defense']}/{stats['hp']}", inline=True)
        embed.add_field(name="Peace upkeep", value=f"{udata['peace_upkeep']:.0f}g/unit", inline=True)
        embed.set_footer(text=f"Blueprint ID: {bp_id}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bp_grp.command(name="delete", description="Delete a blueprint / Usun projekt")
    @app_commands.describe(blueprint_id="Blueprint ID")
    async def bp_delete(self, interaction: discord.Interaction, blueprint_id: int):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT * FROM blueprints WHERE id=? AND nation_id=?", (blueprint_id,nat["id"]))
            bp = c.fetchone()
        if not bp:
            await interaction.response.send_message(f"Blueprint #{blueprint_id} not found.", ephemeral=True); return
        with db.cursor() as c:
            c.execute("DELETE FROM blueprints WHERE id=?", (blueprint_id,))
        await interaction.response.send_message(f"🗑️ Blueprint **{bp['name']}** deleted.", ephemeral=True)

    # ============================================================ MILITARY

    @mil_grp.command(name="build", description="Build units from a blueprint / Zbuduj jednostki")
    @app_commands.describe(
        blueprint_id="Blueprint ID",
        quantity="How many to build",
        cell_id="Province cell ID to station them (optional — leave 0 for floating)",
    )
    async def mil_build(self, interaction: discord.Interaction,
                        blueprint_id: int, quantity: int, cell_id: int = 0):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT * FROM blueprints WHERE id=? AND nation_id=?", (blueprint_id,nat["id"]))
            bp = c.fetchone()
        if not bp:
            await interaction.response.send_message(f"Blueprint #{blueprint_id} not found.", ephemeral=True); return

        prov = None
        if cell_id and cell_id != 0:
            with db.cursor() as c:
                c.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,))
                prov = c.fetchone()
            if not prov:
                await interaction.response.send_message(f"Province {cell_id} not found.", ephemeral=True); return

        quantity = max(1, quantity)
        if bp["type"] == "ship":
            hull_data    = HULLS.get(bp["hull"], {})
            base_cost    = dict(hull_data.get("cost", {}))
            for m in json.loads(bp["components_json"]):
                for r,a in MODULES.get(m,{}).get("cost",{}).items():
                    base_cost[r] = base_cost.get(r,0) + a
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
                ephemeral=True); return

        res = json.loads(nat["resources_json"])
        for r, a in total_cost.items():
            if res.get(r, 0) < a:
                await interaction.response.send_message(
                    f"Not enough **{r}**. Need {a}, have {res.get(r,0):.0f}.", ephemeral=True); return
        for r, a in total_cost.items():
            res[r] = res.get(r, 0) - a

        prov_id = prov["id"] if prov else None
        with db.cursor() as c:
            c.execute(
                "INSERT INTO military_units(nation_id,blueprint_id,unit_type,quantity,province_id)"
                " VALUES(?,?,?,?,?)",
                (nat["id"], blueprint_id, bp["hull"], quantity, prov_id)
            )
            unit_id = c.lastrowid
            c.execute("UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                      (json.dumps(res), nat["treasury"]-gold_cost, nat["id"]))

        loc_str = (prov["name"] or f"Cell #{cell_id}") if prov else "floating (unassigned)"
        _log(nat["id"],"system",
             f"Built {quantity}x {bp['name']} (group #{unit_id}) — {loc_str}. "
             f"Upkeep: {peace_upkeep*quantity:.0f}g/tick peace / {peace_upkeep*quantity*WAR_MULT:.0f}g war.")

        cost_str = f"{gold_cost:,}g"
        if total_cost:
            cost_str += ", " + ", ".join(f"{a} {r}" for r,a in total_cost.items())
        embed = discord.Embed(
            title=f"{'⚓' if bp['type']=='ship' else '⚔️'} Units Built",
            description=f"**{quantity}×** {bp['name']} — stationed: **{loc_str}**",
            color=discord.Color.dark_green(),
        )
        embed.add_field(name="Cost",         value=cost_str,                              inline=True)
        embed.add_field(name="Peace upkeep", value=f"{peace_upkeep*quantity:.0f}g/tick",  inline=True)
        embed.add_field(name="War upkeep",   value=f"{peace_upkeep*quantity*WAR_MULT:.0f}g/tick", inline=True)
        embed.set_footer(text=f"Unit group ID: {unit_id} — use /military move {unit_id} <cell> to assign")
        await interaction.response.send_message(embed=embed)

    @mil_grp.command(name="list", description="View your forces (private) / Twoje wojsko")
    @app_commands.describe(nation="Nation name — GM only")
    async def mil_list(self, interaction: discord.Interaction, nation: str = ""):
        lang  = _lang(interaction)
        is_gm = _gm(interaction)
        if nation and not is_gm:
            await interaction.response.send_message(
                "Military is private. You can only view your own.", ephemeral=True); return
        nat = _nat_name(nation) if nation else _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(
                i18n.t(lang,"nation_not_found" if nation else "no_nation"), ephemeral=True); return
        with db.cursor() as c:
            c.execute(
                "SELECT u.*,b.name as bname,b.type as btype,b.hull,"
                "p.name as pname,p.azgaar_cell_id"
                " FROM military_units u"
                " LEFT JOIN blueprints b ON u.blueprint_id=b.id"
                " LEFT JOIN provinces p ON u.province_id=p.id"
                " WHERE u.nation_id=? ORDER BY b.type,u.id",
                (nat["id"],)
            )
            rows = c.fetchall()
        if not rows:
            await interaction.response.send_message(f"**{nat['name']}** has no units.", ephemeral=True); return
        at_war       = _at_war(nat["id"])
        total_upkeep = _upkeep(nat["id"])
        ships = [r for r in rows if r["btype"]=="ship"]
        units = [r for r in rows if r["btype"]!="ship"]
        embed = discord.Embed(
            title=f"⚔️ Forces — {nat['flag'] or ''} {nat['name']}".strip(),
            color=discord.Color.dark_red(),
        )
        embed.add_field(
            name="Status",
            value=f"{'🔴 At War' if at_war else '🟢 Peace'} | Upkeep: {total_upkeep:.0f}g/tick",
            inline=False,
        )
        def loc(r):
            if r["pname"]:      return r["pname"]
            if r["azgaar_cell_id"]: return f"Cell #{r['azgaar_cell_id']}"
            return "🌊 Floating"
        if ships:
            embed.add_field(name="⚓ Navy",
                value="\n".join(f"`[{r['id']}]` **{r['quantity']}× {r['bname']}** @ {loc(r)}" for r in ships),
                inline=False)
        if units:
            embed.add_field(name="⚔️ Army",
                value="\n".join(f"`[{r['id']}]` **{r['quantity']}× {r['bname']}** @ {loc(r)}" for r in units),
                inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @mil_grp.command(name="move", description="Assign/move units to a province / Przemiesz wojsko")
    @app_commands.describe(unit_id="Unit group ID", cell_id="Destination cell ID")
    async def mil_move(self, interaction: discord.Interaction, unit_id: int, cell_id: int):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT u.*,b.name as bname FROM military_units u "
                      "LEFT JOIN blueprints b ON u.blueprint_id=b.id "
                      "WHERE u.id=? AND u.nation_id=?", (unit_id,nat["id"]))
            unit = c.fetchone()
        if not unit:
            await interaction.response.send_message(f"Unit group #{unit_id} not found.", ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,))
            prov = c.fetchone()
        if not prov:
            await interaction.response.send_message(f"Province {cell_id} not found.", ephemeral=True); return
        with db.cursor() as c:
            c.execute("UPDATE military_units SET province_id=? WHERE id=?", (prov["id"],unit_id))
        _log(nat["id"],"player",
             f"Moved group #{unit_id} ({unit['quantity']}× {unit['bname']}) "
             f"to {prov['name'] or f'Cell #{cell_id}'}.")
        await interaction.response.send_message(
            f"✅ **{unit['quantity']}× {unit['bname']}** → **{prov['name'] or f'Cell #{cell_id}'}**.",
            ephemeral=True)

    @mil_grp.command(name="unassign", description="Return units to floating pool / Cofnij przydzia")
    @app_commands.describe(unit_id="Unit group ID")
    async def mil_unassign(self, interaction: discord.Interaction, unit_id: int):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT u.*,b.name as bname FROM military_units u "
                      "LEFT JOIN blueprints b ON u.blueprint_id=b.id "
                      "WHERE u.id=? AND u.nation_id=?", (unit_id,nat["id"]))
            unit = c.fetchone()
        if not unit:
            await interaction.response.send_message(f"Unit group #{unit_id} not found.", ephemeral=True); return
        with db.cursor() as c:
            c.execute("UPDATE military_units SET province_id=NULL WHERE id=?", (unit_id,))
        _log(nat["id"],"player",f"Unassigned group #{unit_id} ({unit['bname']}) — now floating.")
        await interaction.response.send_message(
            f"✅ **{unit['quantity']}× {unit['bname']}** returned to floating pool.", ephemeral=True)

    @mil_grp.command(name="disband", description="Disband a unit group / Rozwiaz jednostki")
    @app_commands.describe(unit_id="Unit group ID")
    async def mil_disband(self, interaction: discord.Interaction, unit_id: int):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        with db.cursor() as c:
            c.execute(
                "SELECT u.*,b.name as bname FROM military_units u "
                "LEFT JOIN blueprints b ON u.blueprint_id=b.id "
                "WHERE u.id=? AND u.nation_id=?",
                (unit_id, nat["id"])
            )
            unit = c.fetchone()
        if not unit:
            await interaction.response.send_message(f"Unit group #{unit_id} not found.", ephemeral=True); return

        # Block if committed to a pending (unmatched or matched) battle plan
        with db.cursor() as c:
            c.execute(
                "SELECT p.id FROM battle_plans p "
                "WHERE p.nation_id=? AND p.status IN ('unmatched','matched') "
                "AND p.forces_json LIKE ?",
                (nat["id"], f'%"unit_id": {unit_id}%')
            )
            committed = c.fetchone()
        if not committed:
            # Also check without space after colon
            with db.cursor() as c:
                c.execute(
                    "SELECT p.id FROM battle_plans p "
                    "WHERE p.nation_id=? AND p.status IN ('unmatched','matched') "
                    "AND p.forces_json LIKE ?",
                    (nat["id"], f'%"unit_id":{unit_id}%')
                )
                committed = c.fetchone()
        if committed:
            await interaction.response.send_message(
                f"❌ Unit group #{unit_id} is committed to battle plan #{committed['id']} "
                f"which is still pending resolution. Wait for the battle to resolve first.",
                ephemeral=True)
            return

        with db.cursor() as c:
            c.execute("DELETE FROM military_units WHERE id=?", (unit_id,))
        _log(nat["id"],"player",
             f"Disbanded group #{unit_id}: {unit['quantity']}× {unit['bname'] or 'unknown'}.")
        await interaction.response.send_message(
            f"🗑️ Disbanded **{unit['quantity']}× {unit['bname'] or 'unknown'}**.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(MilitaryCog(bot))
