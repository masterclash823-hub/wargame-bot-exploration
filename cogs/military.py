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
from copy import deepcopy
import discord
from discord import app_commands
from discord.ext import commands

import config, db, i18n
from utils import gm_only

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
    "militia":        {"name":"Militia",        "attack":5, "defense":4, "hp":50,"speed":2,"cost":{"gold":10},                          "requires_tech":0.0,"peace_upkeep":1.0,"desc":"Cheap defence only."},
    "pikemen":        {"name":"Pikemen",        "attack":10,"defense":8, "hp":70,"speed":2,"cost":{"gold":20,"iron":3},                 "requires_tech":1.0,"peace_upkeep":2.0,"desc":"Anti-cavalry, reliable."},
    "musketeers":     {"name":"Musketeers",     "attack":18,"defense":6, "hp":65,"speed":2,"cost":{"gold":30,"iron":5,"gunpowder":3},   "requires_tech":2.0,"peace_upkeep":3.0,"desc":"Core ranged infantry."},
    "dragoons":       {"name":"Dragoons",       "attack":20,"defense":10,"hp":80,"speed":4,"cost":{"gold":40,"horses":3},               "requires_tech":3.0,"peace_upkeep":4.0,"desc":"Mobile cavalry."},
    "field_cannon":   {"name":"Field Cannon",   "attack":35,"defense":3, "hp":60,"speed":1,"cost":{"gold":60,"iron":15,"gunpowder":8},  "requires_tech":3.0,"peace_upkeep":5.0,"desc":"Heavy artillery."},
    "cuirassiers":    {"name":"Cuirassiers",    "attack":28,"defense":16,"hp":100,"speed":4,"cost":{"gold":70,"horses":5,"iron":10},    "requires_tech":4.0,"peace_upkeep":6.0,"desc":"Elite heavy cavalry."},
    "siege_artillery":{"name":"Siege Artillery","attack":50,"defense":2, "hp":50,"speed":1,"cost":{"gold":100,"iron":25,"gunpowder":12},"requires_tech":5.0,"peace_upkeep":8.0,"desc":"Fortress breaker."},
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
    return gm_only(i)

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
class ShipDesignerView(i18n.LocalizedView):
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
                label=f"{i18n.text(mdata['name'])} ({i18n.text(mdata['desc'])})",
                style=discord.ButtonStyle.secondary,
                disabled=disabled,
                row=i // 3,
            )
            btn.callback = self._add_cb(mkey)
            self.add_item(btn)
        # Clear last
        clear = discord.ui.Button(
            label=i18n.text('↩ Remove last'),
            style=discord.ButtonStyle.danger,
            disabled=len(self.selected) == 0,
            row=2,
        )
        clear.callback = self._remove_last
        self.add_item(clear)
        # Save
        save = discord.ui.Button(
            label=i18n.text('💾 Save Blueprint ({p0}/{p1} slots)', p0=used, p1=self.slots),
            style=discord.ButtonStyle.success,
            row=3,
        )
        save.callback = self._save
        self.add_item(save)

    def _add_cb(self, mkey):
        @i18n.localized
        async def callback(interaction: discord.Interaction):
            self.selected.append(mkey)
            self._rebuild()
            await interaction.response.edit_message(embed=self._embed(), view=self)
        return callback

    @i18n.localized
    async def _remove_last(self, interaction: discord.Interaction):
        if self.selected:
            self.selected.pop()
        self._rebuild()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @i18n.localized
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
             i18n.text("Designed ship blueprint '{p0}' (#{p1}): {p2} + {p3} module(s). ATK:{p4} HP:{p5} SPD:{p6} CARGO:{p7}.", p0=self.bp_name, p1=bp_id, p2=i18n.text(HULLS[self.hull_key]['name']), p3=len(self.selected), p4=stats['attack'], p5=stats['hp'], p6=stats['speed'], p7=stats['cargo']))
        for item in self.children:
            item.disabled = True
        final = self._embed()
        final.title = i18n.text('✅ Blueprint Saved — {p0} (#{p1})', p0=self.bp_name, p1=bp_id)
        final.color = discord.Color.green()
        await interaction.response.edit_message(embed=final, view=self)

    def _embed(self):
        stats    = _ship_stats(self.hull_key, self.selected)
        hull     = HULLS[self.hull_key]
        mods_str = (", ".join(i18n.text(MODULES[m]["name"]) for m in self.selected)
                    if self.selected else i18n.term("none"))
        cost_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in hull["cost"].items())
        mod_cost: dict[str, float] = {}
        for m in self.selected:
            for r, a in MODULES.get(m, {}).get("cost", {}).items():
                mod_cost[r] = mod_cost.get(r, 0) + a
        if mod_cost:
            cost_str += " + " + ", ".join(f"{v} {i18n.term(k)}" for k, v in mod_cost.items())
        embed = discord.Embed(
            title=i18n.text('⚓ Designing: {p0}', p0=self.bp_name),
            description=i18n.text('Hull: **{p0}** | {p1}/{p2} slots used', p0=i18n.text(hull['name']), p1=len(self.selected), p2=self.slots),
            color=discord.Color.dark_blue(),
        )
        embed.add_field(name=i18n.text('Modules'),       value=mods_str,              inline=False)
        embed.add_field(name=i18n.text('Total Cost'),    value=cost_str,              inline=False)
        embed.add_field(name=i18n.text('ATK'),           value=str(stats["attack"]),  inline=True)
        embed.add_field(name=i18n.text('HP'),            value=str(stats["hp"]),      inline=True)
        embed.add_field(name=i18n.text('Speed'),         value=str(stats["speed"]),   inline=True)
        embed.add_field(name=i18n.text('Cargo'),         value=str(stats["cargo"]),   inline=True)
        embed.add_field(name=i18n.text('Peace upkeep'),  value=i18n.text('{p0:.0f}g/unit', p0=hull['peace_upkeep']), inline=True)
        embed.add_field(name=i18n.text('War upkeep'),    value=i18n.text('{p0:.0f}g/unit', p0=hull['peace_upkeep'] * WAR_MULT), inline=True)
        embed.set_footer(text=i18n.text('Click modules to add them. Each click uses one slot.'))
        return embed

# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class ForcesView(i18n.LocalizedView):
    def __init__(self, pages, owner_id):
        super().__init__(timeout=180)
        self.pages, self.owner_id, self.index = pages, owner_id, 0
        for i, page in enumerate(pages, 1):
            page.set_footer(text=f"{i} / {len(pages)}")
        self._refresh()

    def _refresh(self):
        self.previous.disabled = self.index == 0
        self.next_page.disabled = self.index == len(self.pages) - 1

    @i18n.localized
    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(i18n.text('This is not your military list.'), ephemeral=True)
        return False

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    @i18n.localized
    async def previous(self, interaction, button):
        self.index = max(0, self.index - 1)
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    @i18n.localized
    async def next_page(self, interaction, button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


class MilitaryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    bp_grp  = app_commands.Group(name="blueprint", description="Blueprint commands")
    mil_grp = app_commands.Group(name="military",  description="Military commands")

    # ============================================================ BLUEPRINTS

    @bp_grp.command(name="list", description="View your blueprints / Twoje projekty")
    @i18n.localized
    async def bp_list(self, interaction: discord.Interaction):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT * FROM blueprints WHERE nation_id=? ORDER BY type,name", (nat["id"],))
            rows = c.fetchall()
        if not rows:
            await interaction.response.send_message(i18n.text('No blueprints yet.'), ephemeral=True); return
        embed = discord.Embed(
            title=i18n.text('📐 Blueprints — {p0} {p1}', p0=nat['flag'] or '', p1=nat['name']).strip(),
            color=discord.Color.dark_blue(),
        )
        for r in rows:
            stats = json.loads(r["stats_json"])
            if r["type"] == "ship":
                comps     = json.loads(r["components_json"])
                mods      = ", ".join(i18n.text(MODULES[m]["name"]) for m in comps if m in MODULES) or i18n.text('no modules')
                hull      = HULLS.get(r["hull"], {})
                base_cost = dict(hull.get("cost", {}))
                for m in comps:
                    for res, amt in MODULES.get(m, {}).get("cost", {}).items():
                        base_cost[res] = base_cost.get(res, 0) + amt
                cost_str  = ", ".join(f"{v} {i18n.term(k)}" for k, v in base_cost.items())
                val = (
                    i18n.text('Hull: {p0} | ATK:{p1} HP:{p2} SPD:{p3} CARGO:{p4}\nModules: {p5}\nCost per unit: {p6}\nUpkeep: {p7:.0f}g/unit (peace) / {p8:.0f}g (war)', p0=i18n.text(hull.get('name', r['hull'])), p1=stats.get('attack', 0), p2=stats.get('hp', 0), p3=stats.get('speed', 0), p4=stats.get('cargo', 0), p5=mods, p6=cost_str, p7=hull.get('peace_upkeep', 5), p8=hull.get('peace_upkeep', 5) * WAR_MULT)
                )
            else:
                udata    = LAND_UNITS.get(r["hull"], {})
                cost_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in udata.get("cost", {}).items())
                val = (
                    i18n.text('ATK:{p0} DEF:{p1} HP:{p2} SPD:{p3}\nCost per unit: {p4}\nUpkeep: {p5:.0f}g/unit (peace) / {p6:.0f}g (war)', p0=stats.get('attack', 0), p1=stats.get('defense', 0), p2=stats.get('hp', 0), p3=stats.get('speed', 0), p4=cost_str, p5=udata.get('peace_upkeep', 2), p6=udata.get('peace_upkeep', 2) * WAR_MULT)
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
    @i18n.localized
    async def design_ship(self, interaction: discord.Interaction,
                          name: str, hull: app_commands.Choice[str]):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        hull_data = HULLS[hull.value]
        if _tech_naval(nat) < hull_data["requires_tech"]:
            await interaction.response.send_message(
                i18n.text('**{p0}** requires Naval tech ≥ {p1:.0f}.', p0=i18n.text(hull_data['name']), p1=hull_data['requires_tech']),
                ephemeral=True); return
        view = ShipDesignerView(
            nation_id=nat["id"], hull_key=hull.value, blueprint_name=name,
            slots=hull_data["slots"], base_stats=hull_data,
        )
        await interaction.response.send_message(embed=view._embed(), view=view, ephemeral=True)

    @bp_grp.command(name="create_unit", description="Create a land unit blueprint")
    @app_commands.describe(name="Blueprint name", unit_type="Unit type")
    @app_commands.choices(unit_type=[
        app_commands.Choice(name="Militia / Milicja (tech 0) — ATK:5 DEF:4 HP:50 | 10g",          value="militia"),
        app_commands.Choice(name="Pikemen / Pikinierzy (tech 1) — ATK:10 DEF:8 HP:70 | 20g+żelazo", value="pikemen"),
        app_commands.Choice(name="Musketeers / Muszkieterzy (tech 2) — ATK:18 DEF:6 | 30g+proch",  value="musketeers"),
        app_commands.Choice(name="Dragoons / Dragoni (tech 3) — ATK:20 DEF:10 HP:80 | 40g+konie",  value="dragoons"),
        app_commands.Choice(name="Field Cannon / Armata (tech 3) — ATK:35 DEF:3 HP:60 | 60g+proch",value="field_cannon"),
        app_commands.Choice(name="Cuirassiers / Kirasjerzy (tech 4) — ATK:28 DEF:16 | 70g+konie",  value="cuirassiers"),
        app_commands.Choice(name="Siege Artillery / Artyleria (tech 5) — ATK:50 DEF:2 | 100g",     value="siege_artillery"),
    ])
    @i18n.localized
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
                i18n.text('**{p0}** requires Land tech ≥ {p1:.0f}.', p0=i18n.text(udata['name']), p1=udata['requires_tech']),
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
             i18n.text("Created unit blueprint '{p0}' (#{p1}): {p2} ATK:{p3} DEF:{p4} HP:{p5}.", p0=name, p1=bp_id, p2=i18n.text(udata['name']), p3=stats['attack'], p4=stats['defense'], p5=stats['hp']))
        embed = discord.Embed(title=i18n.text('⚔️ Blueprint Created — {p0}', p0=name), color=discord.Color.dark_red())
        pl = hasattr(interaction, 'locale') and interaction.locale and 'pl' in str(interaction.locale)
        embed.add_field(
            name="Typ" if pl else i18n.text('Type'),
            value=i18n.t("pl", f"unit_{ukey}") if pl else udata["name"],
            inline=True)
        embed.add_field(
            name=i18n.text('ATK/DEF/HP'),
            value=f"{stats['attack']}/{stats['defense']}/{stats['hp']}",
            inline=True)
        embed.add_field(
            name="Utrzymanie (pokój)" if pl else i18n.text('Peace upkeep'),
            value=f"{udata['peace_upkeep']:.0f}g/jedn." if pl else i18n.text('{p0:.0f}g/unit', p0=udata['peace_upkeep']),
            inline=True)
        cost_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in udata["cost"].items())
        embed.add_field(
            name="Koszt" if pl else i18n.text('Cost'),
            value=cost_str,
            inline=True)
        embed.add_field(
            name="Utrzymanie (wojna)" if pl else i18n.text('War upkeep'),
            value=f"{udata['peace_upkeep']*WAR_MULT:.0f}g/jedn." if pl else i18n.text('{p0:.0f}g/unit', p0=udata['peace_upkeep'] * WAR_MULT),
            inline=True)
        embed.set_footer(
            text=f"ID projektu: {bp_id} — użyj /military build {bp_id} <ilość>" if pl
            else i18n.text('Blueprint ID: {p0} — use /military build {p1} <qty> to train', p0=bp_id, p1=bp_id)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bp_grp.command(name="delete", description="Delete a blueprint / Usun projekt")
    @app_commands.describe(blueprint_id="Blueprint ID")
    @i18n.localized
    async def bp_delete(self, interaction: discord.Interaction, blueprint_id: int):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang,"no_nation"), ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT * FROM blueprints WHERE id=? AND nation_id=?", (blueprint_id,nat["id"]))
            bp = c.fetchone()
        if not bp:
            await interaction.response.send_message(i18n.text('Blueprint #{p0} not found.', p0=blueprint_id), ephemeral=True); return
        with db.cursor() as c:
            c.execute("DELETE FROM blueprints WHERE id=?", (blueprint_id,))
        await interaction.response.send_message(i18n.text('🗑️ Blueprint **{p0}** deleted.', p0=bp['name']), ephemeral=True)

    # ============================================================ MILITARY

    @mil_grp.command(name="build", description="Build units from a blueprint / Zbuduj jednostki")
    @app_commands.describe(
        blueprint_id="Blueprint ID",
        quantity="How many to build",
        cell_id="Province cell ID to station them (optional — leave 0 for floating)",
    )
    @i18n.localized
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
            await interaction.response.send_message(i18n.text('Blueprint #{p0} not found.', p0=blueprint_id), ephemeral=True); return

        prov = None
        if cell_id and cell_id != 0:
            with db.cursor() as c:
                c.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,))
                prov = c.fetchone()
            if not prov:
                await interaction.response.send_message(i18n.text('Province {p0} not found.', p0=cell_id), ephemeral=True); return

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
            # Cloth consumption: 1 cloth per 5 land units (rounded up)
            cloth_needed = max(1, (quantity + 4) // 5)
            base_cost["cloth"] = base_cost.get("cloth", 0) + cloth_needed

        total_cost = {r: a * quantity for r, a in base_cost.items()}
        gold_cost  = total_cost.pop("gold", 0)

        if nat["treasury"] < gold_cost:
            await interaction.response.send_message(
                i18n.text('Not enough gold. Need **{p0:,}g**, have **{p1:,.0f}g**.', p0=gold_cost, p1=nat['treasury']),
                ephemeral=True); return

        res = json.loads(nat["resources_json"])
        for r, a in total_cost.items():
            if res.get(r, 0) < a:
                await interaction.response.send_message(
                    i18n.text('Not enough **{p0}**. Need {p1}, have {p2:.0f}.', p0=r, p1=a, p2=res.get(r, 0)), ephemeral=True); return
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

        loc_str = (prov["name"] or i18n.text('Cell #{p0}', p0=cell_id)) if prov else i18n.text('floating (unassigned)')
        _log(nat["id"],"system",
             i18n.text('Built {p0}x {p1} (group #{p2}) — {p3}. Upkeep: {p4:.0f}g/tick peace / {p5:.0f}g war.', p0=quantity, p1=bp['name'], p2=unit_id, p3=loc_str, p4=peace_upkeep * quantity, p5=peace_upkeep * quantity * WAR_MULT))

        cost_str = f"{gold_cost:,}g"
        if total_cost:
            cost_str += ", " + ", ".join(f"{a} {i18n.term(r)}" for r,a in total_cost.items())
        embed = discord.Embed(
            title=i18n.text('{p0} Units Built', p0='⚓' if bp['type'] == 'ship' else '⚔️'),
            description=i18n.text('**{p0}×** {p1} — stationed: **{p2}**', p0=quantity, p1=bp['name'], p2=loc_str),
            color=discord.Color.dark_green(),
        )
        embed.add_field(name=i18n.text('Cost'),         value=cost_str,                              inline=True)
        embed.add_field(name=i18n.text('Peace upkeep'), value=i18n.text('{p0:.0f}g/tick', p0=peace_upkeep * quantity),  inline=True)
        embed.add_field(name=i18n.text('War upkeep'),   value=i18n.text('{p0:.0f}g/tick', p0=peace_upkeep * quantity * WAR_MULT), inline=True)
        embed.set_footer(text=i18n.text('Unit group ID: {p0} — use /military move {p1} <cell> to assign', p0=unit_id, p1=unit_id))
        await interaction.response.send_message(embed=embed)

    @mil_grp.command(name="list", description="View your forces (private) / Twoje wojsko")
    @app_commands.describe(nation="Nation name — GM only")
    @i18n.localized
    async def mil_list(self, interaction: discord.Interaction, nation: str = ""):
        lang  = _lang(interaction)
        is_gm = _gm(interaction)
        if nation and not is_gm:
            await interaction.response.send_message(
                i18n.text('Military is private. You can only view your own.'), ephemeral=True); return
        nat = _nat_name(nation) if nation else _nat_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(
                i18n.t(lang,"nation_not_found" if nation else "no_nation"), ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        with db.cursor() as c:
            c.execute(
                "SELECT u.*,b.name as bname,b.type as btype,b.hull,b.stats_json,"
                "p.name as pname,p.azgaar_cell_id"
                " FROM military_units u"
                " LEFT JOIN blueprints b ON u.blueprint_id=b.id"
                " LEFT JOIN provinces p ON u.province_id=p.id"
                " WHERE u.nation_id=? ORDER BY b.type,u.id",
                (nat["id"],)
            )
            rows = c.fetchall()
        if not rows:
            await interaction.followup.send(i18n.text('**{p0}** has no units.', p0=nat['name']), ephemeral=True); return
        at_war       = _at_war(nat["id"])
        total_upkeep = _upkeep(nat["id"])
        ships = [r for r in rows if r["btype"]=="ship"]
        units = [r for r in rows if r["btype"]!="ship"]
        embed = discord.Embed(
            title=i18n.text('⚔️ Forces — {p0} {p1}', p0=nat['flag'] or '', p1=nat['name']).strip(),
            color=discord.Color.dark_red(),
        )
        # Calculate total fleet cargo
        total_cargo = sum(
            json.loads(r["stats_json"] or "{}").get("cargo", 0) * r["quantity"]
            for r in rows if r["btype"] == "ship"
        )
        embed.add_field(
            name=i18n.text('Status'),
            value=(
                i18n.text('{p0} | Upkeep: {p1:.0f}g/tick', p0=i18n.text('🔴 At War') if at_war else i18n.text('🟢 Peace'), p1=total_upkeep)
                + (i18n.text(' | ⚓ Fleet cargo: {p0:.0f}', p0=total_cargo) if total_cargo > 0 else "")
            ),
            inline=False,
        )
        def loc(r):
            if r["pname"]:          return r["pname"]
            if r["azgaar_cell_id"] is not None: return i18n.text('Cell #{p0}', p0=r['azgaar_cell_id'])
            return i18n.text('🌊 Floating')

        def upkeep_str(r):
            if r["btype"] == "ship":
                base = HULLS.get(r["hull"], {}).get("peace_upkeep", 5.0)
            else:
                base = LAND_UNITS.get(r["hull"], {}).get("peace_upkeep", 2.0)
            peace = base * r["quantity"]
            war   = peace * WAR_MULT
            return i18n.text('{p0:.0f}g peace / {p1:.0f}g war', p0=peace, p1=war)

        # Keep every group visible without exceeding Discord's field/embed limits.
        pages = []
        template = deepcopy(embed)
        for heading, groups in ((i18n.text('⚓ Navy'), ships), (i18n.text('⚔️ Army'), units)):
            for r in groups:
                name = r["bname"] or r["unit_type"] or i18n.text('Unknown blueprint')
                line = f"`[{r['id']}]` **{r['quantity']}× {name[:200]}** @ {loc(r)[:200]} — {upkeep_str(r)}"
                if len(embed.fields) >= 20 or len(embed) + len(heading) + len(line) > 5800:
                    pages.append(embed)
                    embed = deepcopy(template)
                embed.add_field(name=heading, value=line, inline=False)
        pages.append(embed)
        view = ForcesView(pages, interaction.user.id)
        await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)

    @mil_grp.command(name="move", description="Assign/move units to a province / Przemiesz wojsko")
    @app_commands.describe(unit_id="Unit group ID", cell_id="Destination cell ID")
    @i18n.localized
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
            await interaction.response.send_message(i18n.text('Unit group #{p0} not found.', p0=unit_id), ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,))
            prov = c.fetchone()
        if not prov:
            await interaction.response.send_message(i18n.text('Province {p0} not found.', p0=cell_id), ephemeral=True); return
        with db.cursor() as c:
            c.execute("UPDATE military_units SET province_id=? WHERE id=?", (prov["id"],unit_id))
        _log(nat["id"],"player",
             i18n.text('Moved group #{p0} ({p1}× {p2}) to {p3}.', p0=unit_id, p1=unit['quantity'], p2=unit['bname'], p3=prov['name'] or i18n.text('Cell #{p0}', p0=cell_id)))
        await interaction.response.send_message(
            f"✅ **{unit['quantity']}× {unit['bname']}** → **{prov['name'] or i18n.text('Cell #{p0}', p0=cell_id)}**.",
            ephemeral=True)

    @mil_grp.command(name="unassign", description="Return units to floating pool / Cofnij przydzia")
    @app_commands.describe(unit_id="Unit group ID")
    @i18n.localized
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
            await interaction.response.send_message(i18n.text('Unit group #{p0} not found.', p0=unit_id), ephemeral=True); return
        with db.cursor() as c:
            c.execute("UPDATE military_units SET province_id=NULL WHERE id=?", (unit_id,))
        _log(nat["id"],"player",i18n.text('Unassigned group #{p0} ({p1}) — now floating.', p0=unit_id, p1=unit['bname']))
        await interaction.response.send_message(
            i18n.text('✅ **{p0}× {p1}** returned to floating pool.', p0=unit['quantity'], p1=unit['bname']), ephemeral=True)

    @mil_grp.command(name="disband", description="Disband a unit group / Rozwiaz jednostki")
    @app_commands.describe(unit_id="Unit group ID")
    @i18n.localized
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
            await interaction.response.send_message(i18n.text('Unit group #{p0} not found.', p0=unit_id), ephemeral=True); return

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
                i18n.text('❌ Unit group #{p0} is committed to battle plan #{p1} which is still pending resolution. Wait for the battle to resolve first.', p0=unit_id, p1=committed['id']),
                ephemeral=True)
            return

        with db.cursor() as c:
            c.execute("DELETE FROM military_units WHERE id=?", (unit_id,))
        _log(nat["id"],"player",
             i18n.text('Disbanded group #{p0}: {p1}× {p2}.', p0=unit_id, p1=unit['quantity'], p2=unit['bname'] or i18n.term('unknown')))
        await interaction.response.send_message(
            i18n.text('🗑️ Disbanded **{p0}× {p1}**.', p0=unit['quantity'], p1=unit['bname'] or i18n.term('unknown')), ephemeral=True)


async def setup(bot):
    await bot.add_cog(MilitaryCog(bot))
