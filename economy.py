"""
Economy cog: resources, buildings, calendar, megaprojects, trades, admineco.
All slash commands use @app_commands.command or group subcommands — no hybrid.
"""
import json, asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config, db, i18n

MONTH_NAMES = [
    "January","February","March","April","May","June",
    "July","August","September","October","November","December",
]
MONTH_NAMES_PL = [
    "Styczeń","Luty","Marzec","Kwiecień","Maj","Czerwiec",
    "Lipiec","Sierpień","Wrzesień","Październik","Listopad","Grudzień",
]

def _month_name(month: int, lang: str = "en") -> str:
    names = MONTH_NAMES_PL if lang == "pl" else MONTH_NAMES
    try:
        return names[month - 1]
    except (IndexError, TypeError):
        return str(month)

DEFAULT_BUILDINGS = [
    {"key":"farm",            "name":"Farm",            "tier":1,"cost":{"gold":100,"wood":50},                      "effect":{"food":10},                  "upkeep":{"gold":2}, "terrain":"plains,grassland",       "tech":0.0,"desc":"Food on plains/grassland."},
    {"key":"fishing_wharf",   "name":"Fishing Wharf",   "tier":1,"cost":{"gold":80,"wood":60},                       "effect":{"food":8},                   "upkeep":{"gold":2}, "terrain":"coastal",                "tech":0.0,"desc":"Food on coastal provinces."},
    {"key":"plantation",      "name":"Plantation",      "tier":2,"cost":{"gold":150,"wood":40},                      "effect":{"food":6,"spices":2},        "upkeep":{"gold":3}, "terrain":"forest,jungle",          "tech":3.0,"desc":"Food+spices in tropical/forest provinces."},
    {"key":"pasture",         "name":"Pasture",          "tier":1,"cost":{"gold":60,"wood":20},                       "effect":{"food":5,"horses":1},        "upkeep":{"gold":1}, "terrain":"plains,grassland,hills", "tech":0.0,"desc":"Food+horses on open terrain."},
    {"key":"lumber_camp",     "name":"Lumber Camp",      "tier":1,"cost":{"gold":80},                                 "effect":{"wood":8},                   "upkeep":{"gold":1}, "terrain":"forest,taiga",           "tech":0.0,"desc":"Wood from forests."},
    {"key":"mine",            "name":"Mine",             "tier":1,"cost":{"gold":120,"wood":30},                      "effect":{"iron":6,"stone":4,"coal":3},"upkeep":{"gold":2}, "terrain":"hills,mountains",        "tech":0.0,"desc":"Iron/stone/coal from hills/mountains."},
    {"key":"copper_mine",     "name":"Copper Mine",      "tier":1,"cost":{"gold":100,"wood":20},                      "effect":{"copper":5},                 "upkeep":{"gold":2}, "terrain":"hills,mountains",        "tech":0.0,"desc":"Copper from hills/mountains."},
    {"key":"clay_pit",        "name":"Clay Pit",         "tier":1,"cost":{"gold":60},                                 "effect":{"clay":6},                   "upkeep":{"gold":1}, "terrain":"wetland,plains",         "tech":0.0,"desc":"Clay from wetlands/plains."},
    {"key":"tar_works",       "name":"Tar Works",        "tier":1,"cost":{"gold":80,"wood":20},                       "effect":{"tar":5},                    "upkeep":{"gold":1}, "terrain":"forest,wetland,taiga",   "tech":0.0,"desc":"Tar from forests/wetlands."},
    {"key":"powder_mill",     "name":"Powder Mill",      "tier":2,"cost":{"gold":200,"stone":50,"iron":20,"coal":20,"copper":10}, "effect":{"gunpowder":4},  "upkeep":{"gold":5}, "terrain":"",                       "tech":4.0,"desc":"Gunpowder. Requires coal+copper+iron. Tech 4."},
    {"key":"cannon_foundry",  "name":"Cannon Foundry",   "tier":2,"cost":{"gold":250,"iron":40,"coal":30,"copper":20},"effect":{"gunpowder":6,"iron":-2},   "upkeep":{"gold":6}, "terrain":"",                       "tech":4.0,"desc":"More gunpowder output, consumes iron. Requires coal+copper. Tech 4."},
    {"key":"textile_mill",    "name":"Textile Mill",     "tier":2,"cost":{"gold":150,"wood":40},                      "effect":{"cloth":6},                  "upkeep":{"gold":3}, "terrain":"",                       "tech":3.0,"desc":"Cloth. Requires tech 3."},
    {"key":"silk_workshop",   "name":"Silk Workshop",    "tier":2,"cost":{"gold":200,"wood":30,"cloth":20},           "effect":{"silk":3},                   "upkeep":{"gold":4}, "terrain":"plains,grassland",       "tech":3.0,"desc":"Silk production. Requires cloth. Tech 3."},
    {"key":"market",          "name":"Market",           "tier":1,"cost":{"gold":100,"wood":30},                      "effect":{"gold":15},                  "upkeep":{},         "terrain":"",                       "tech":0.0,"desc":"Gold income each tick."},
    {"key":"port",            "name":"Port",             "tier":1,"cost":{"gold":150,"wood":80},                      "effect":{"gold":10},                  "upkeep":{"gold":2}, "terrain":"coastal",                "tech":0.0,"desc":"Trade gold on coastal provinces."},
    {"key":"fort",            "name":"Fort",             "tier":1,"cost":{"gold":200,"stone":80,"clay":40},           "effect":{},                           "upkeep":{"gold":5}, "terrain":"",                       "tech":0.0,"desc":"+1 fortification. Requires clay."},
    {"key":"university",      "name":"University",       "tier":3,"cost":{"gold":500,"stone":100,"wood":50,"clay":60},"effect":{"universal_knowledge":1},   "upkeep":{"gold":10},"terrain":"",                       "tech":5.0,"desc":"Universal Knowledge each tick. Requires clay. Tech 5."},
    {"key":"algae_farm",      "name":"Algae Farm",       "tier":3,"cost":{"gold":400,"wood":60},                      "effect":{"algae":1},                  "upkeep":{"gold":8}, "terrain":"coastal,wetland",        "tech":6.0,"desc":"Rare Algae. Requires tech 6."},
]

# ---------------------------------------------------------------------------
# Pure helper functions (no discord imports needed)
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

def _bdef(key):
    with db.cursor() as c:
        c.execute("SELECT * FROM building_defs WHERE key=?", (key.lower(),))
        return c.fetchone()

def _cfg(key, default=""):
    with db.cursor() as c:
        c.execute("SELECT value FROM game_config WHERE key=?", (key,))
        row = c.fetchone()
    return row["value"] if row else default

def _cfg_set(key, value):
    with db.cursor() as c:
        c.execute(
            "INSERT INTO game_config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )

def _log(nid, source, text):
    with db.cursor() as c:
        c.execute(
            "INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)",
            (nid, source, text)
        )

def _terrain_ok(terrain, req):
    if not req:
        return True
    return terrain.lower() in [t.strip().lower() for t in req.split(",")]

def _tech_ok(nation, req):
    if req <= 0:
        return True
    t = json.loads(nation["tech_json"])
    return sum(t.values()) / len(t) >= req

def _deduct(res, cost):
    for r, a in cost.items():
        if r == "gold":
            continue
        if res.get(r, 0) < a:
            return False, r
    for r, a in cost.items():
        if r != "gold":
            res[r] = res.get(r, 0) - a
    return True, ""

def _apply_mp_effect(nid, effect_str, mp_name):
    effect = json.loads(effect_str) if isinstance(effect_str, str) else effect_str
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE id=?", (nid,))
        nat = c.fetchone()
    if not nat:
        return
    res       = json.loads(nat["resources_json"])
    treasury  = nat["treasury"]
    stability = nat["stability"]
    parts     = []
    for k, v in effect.get("resources_once", {}).items():
        res[k] = res.get(k, 0) + v
        parts.append(f"+{v} {k}")
    gold_once = effect.get("gold_once", 0)
    if gold_once:
        treasury += gold_once
        parts.append(f"+{gold_once} gold")
    stab = effect.get("stability", 0)
    if stab:
        stability = min(100.0, stability + stab)
        parts.append(f"+{stab} stability")
    with db.cursor() as c:
        c.execute(
            "UPDATE nations SET resources_json=?,treasury=?,stability=? WHERE id=?",
            (json.dumps(res), treasury, stability, nid)
        )
    entry = f"Megaproject '{mp_name}' completed."
    if parts:
        entry += f" Granted: {', '.join(parts)}."
    res_tick = effect.get("resources_per_tick", {})
    if res_tick:
        entry += f" Ongoing: {', '.join(f'+{v} {k}/tick' for k, v in res_tick.items())}."
    special = effect.get("special_note", "")
    if special:
        entry += f" {special}"
    _log(nid, "system", entry)

def _seed_buildings():
    with db.cursor() as c:
        c.execute("SELECT COUNT(*) as cnt FROM building_defs")
        if c.fetchone()["cnt"] > 0:
            return
        for b in DEFAULT_BUILDINGS:
            c.execute(
                "INSERT OR IGNORE INTO building_defs"
                "(key,name,tier,cost_json,effect_json,upkeep_json,requires_terrain,requires_tech,description)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (b["key"], b["name"], b["tier"],
                 json.dumps(b["cost"]), json.dumps(b["effect"]), json.dumps(b["upkeep"]),
                 b["terrain"], b["tech"], b["desc"]),
            )

def _run_tick(months=1):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations")
        nations = c.fetchall()
    month = int(_cfg("current_month", "1"))
    year  = int(_cfg("current_year",  "1"))
    for _ in range(months):
        month += 1
        if month > 12:
            month = 1
            year += 1
    _cfg_set("current_month", month)
    _cfg_set("current_year",  year)
    summaries = []
    for nat in nations:
        nid      = nat["id"]
        res      = json.loads(nat["resources_json"])
        treasury = nat["treasury"]
        upkeep   = 0.0
        stability = nat["stability"]

        # Sync nation population from provinces
        with db.cursor() as c:
            c.execute(
                "SELECT COALESCE(SUM(population),0) as total_pop "
                "FROM provinces WHERE owner_nation_id=? AND active=1",
                (nid,)
            )
            total_pop = c.fetchone()["total_pop"] or 0

        # Stability modifier on production (low stability = reduced output)
        # 100 stability = 1.0x, 50 = 0.9x, 0 = 0.75x
        stab_mod = 0.75 + (stability / 100.0) * 0.25

        with db.cursor() as c:
            c.execute("SELECT * FROM provinces WHERE owner_nation_id=? AND active=1", (nid,))
            provs = c.fetchall()
        for prov in provs:
            base = json.loads(prov["base_resources_json"])
            for k, v in base.items():
                res[k] = res.get(k, 0) + v * months * stab_mod
            for bkey in json.loads(prov["buildings_json"]):
                bd = _bdef(bkey)
                if not bd:
                    continue
                for k, v in json.loads(bd["effect_json"]).items():
                    if k == "gold":
                        treasury += v * months * stab_mod
                    else:
                        res[k] = res.get(k, 0) + v * months * stab_mod
                upkeep += json.loads(bd["upkeep_json"]).get("gold", 0) * months
        # Military upkeep (imported here to avoid circular import at module level)
        try:
            from cogs.military import compute_military_upkeep
            upkeep += compute_military_upkeep(nid) * months
        except Exception:
            pass

        # ---- FOOD: feeds population + military ----
        try:
            # Count total population across owned provinces
            with db.cursor() as c:
                c.execute(
                    "SELECT COALESCE(SUM(population),0) as total_pop "
                    "FROM provinces WHERE owner_nation_id=? AND active=1",
                    (nid,)
                )
                total_pop = c.fetchone()["total_pop"] or 0

            # Count military units
            with db.cursor() as c:
                c.execute(
                    "SELECT COALESCE(SUM(quantity),0) as total "
                    "FROM military_units WHERE nation_id=?",
                    (nid,)
                )
                total_units = c.fetchone()["total"] or 0

            # Food needed: 1 per 100 pop + 1 per 10 military units, per month
            food_for_pop     = (total_pop / 100.0) * months
            food_for_military= (total_units / 10.0) * months
            food_needed      = food_for_pop + food_for_military
            food_have        = res.get("food", 0)

            if food_needed <= 0:
                pass  # no consumption needed
            elif food_have >= food_needed:
                # Sufficient food
                res["food"] = food_have - food_needed
                surplus_ratio = food_have / food_needed

                # Population growth if well-fed (surplus > 20%)
                if surplus_ratio >= 1.2 and total_pop > 0:
                    growth_rate = min(0.005, (surplus_ratio - 1.0) * 0.01) * months
                    with db.cursor() as c:
                        c.execute(
                            "SELECT id, population FROM provinces "
                            "WHERE owner_nation_id=? AND active=1 AND population>0",
                            (nid,)
                        )
                        provs_pop = c.fetchall()
                    for pp in provs_pop:
                        new_pop = int(pp["population"] * (1 + growth_rate))
                        if new_pop != pp["population"]:
                            with db.cursor() as c:
                                c.execute(
                                    "UPDATE provinces SET population=? WHERE id=?",
                                    (new_pop, pp["id"])
                                )
            else:
                # Food shortage
                shortage_ratio = food_have / food_needed if food_needed > 0 else 0
                res["food"]    = 0
                stab_penalty   = max(1, int((1.0 - shortage_ratio) * 8 * months))

                with db.cursor() as c:
                    c.execute("SELECT stability FROM nations WHERE id=?", (nid,))
                    cur_stab = c.fetchone()["stability"]
                new_stab = max(0.0, cur_stab - stab_penalty)
                with db.cursor() as c:
                    c.execute("UPDATE nations SET stability=? WHERE id=?", (new_stab, nid))

                # Population decline if severe shortage (< 50% fed)
                if shortage_ratio < 0.5 and total_pop > 0:
                    decline_rate = (0.5 - shortage_ratio) * 0.02 * months
                    with db.cursor() as c:
                        c.execute(
                            "SELECT id, population FROM provinces "
                            "WHERE owner_nation_id=? AND active=1 AND population>0",
                            (nid,)
                        )
                        provs_pop = c.fetchall()
                    for pp in provs_pop:
                        new_pop = max(0, int(pp["population"] * (1 - decline_rate)))
                        if new_pop != pp["population"]:
                            with db.cursor() as c:
                                c.execute(
                                    "UPDATE provinces SET population=? WHERE id=?",
                                    (new_pop, pp["id"])
                                )

                _log(nid, "system",
                     f"Food shortage! Needed {food_needed:.0f} (pop {total_pop:,} + "
                     f"{total_units} units), had {food_have:.0f}. "
                     f"Stability -{stab_penalty}."
                     + (" Population declining." if shortage_ratio < 0.5 else ""))
        except Exception as e:
            print(f"[TICK] Food calc error for nation {nid}: {e}", flush=True)

        # SILK + SPICES: luxury income (1 gold per 5 units held, capped at 50g/tick)
        silk_income   = min(50.0, res.get("silk",   0) / 5) * months
        spices_income = min(50.0, res.get("spices", 0) / 5) * months
        luxury_income = silk_income + spices_income
        if luxury_income > 0:
            treasury += luxury_income

        # CLOTH: consumed when building military land units (handled in /military build)
        # Here we just track — cloth upkeep is negligible and handled at build time
        with db.cursor() as c:
            c.execute(
                "SELECT * FROM megaprojects WHERE nation_id=? AND status IN ('building','complete')",
                (nid,)
            )
            mps = c.fetchall()
        for mp in mps:
            eff = json.loads(mp["effect_json"])
            for k, v in eff.get("resources_per_tick", {}).items():
                res[k] = res.get(k, 0) + v * months
            treasury += eff.get("gold_per_tick", 0) * months
            if mp["status"] == "building" and mp["duration_months"] > 0:
                new_spent = min(mp["months_spent"] + months, mp["duration_months"])
                if new_spent >= mp["duration_months"]:
                    with db.cursor() as c:
                        c.execute(
                            "UPDATE megaprojects SET status='complete',months_spent=?,"
                            "completed_at=datetime('now') WHERE id=?",
                            (new_spent, mp["id"])
                        )
                    _apply_mp_effect(nid, mp["effect_json"], mp["name"])
                else:
                    with db.cursor() as c:
                        c.execute(
                            "UPDATE megaprojects SET months_spent=? WHERE id=?",
                            (new_spent, mp["id"])
                        )
        treasury = max(0.0, treasury - upkeep)
        with db.cursor() as c:
            c.execute(
                "UPDATE nations SET resources_json=?,treasury=?,population=? WHERE id=?",
                (json.dumps(res), treasury, total_pop, nid)
            )
        summary_parts = [f"{nat['name']}: -{upkeep:.0f}g upkeep, {treasury:.0f}g treasury"]
        if luxury_income > 0:
            summary_parts.append(f"+{luxury_income:.0f}g luxury")
        _log(nid, "system",
             f"Month {month}/{year}: upkeep -{upkeep:.0f}g"
             + (f", luxury income +{luxury_income:.0f}g" if luxury_income > 0 else "")
             + f", treasury {treasury:.0f}g.")
        summaries.append(", ".join(summary_parts))
    return month, year, summaries

# ---------------------------------------------------------------------------
# UI: paginated buildings list
# ---------------------------------------------------------------------------
class PageView(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=120)
        self.pages = pages
        self.page  = 0
        self._refresh()

    def _refresh(self):
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page == len(self.pages) - 1)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.page], view=self)

# ---------------------------------------------------------------------------
# UI: help section switcher (used in bot.py /help)
# ---------------------------------------------------------------------------
HELP_SECTIONS = {
    "general": {
        "title": "📖 General",
        "color": discord.Color.blurple(),
        "fields": [
            ("/help", "Browse commands by section using the buttons below."),
            ("/language", "Set your preferred language (en / pl)."),
            ("/calendar status", "View current in-game date."),
        ],
    },
    "nation": {
        "title": "🏳️ Nation",
        "color": discord.Color.blue(),
        "fields": [
            ("/nation found", "Found your nation."),
            ("/nation stats [name]", "View a nation's stats."),
            ("/nation list", "List all nations."),
            ("/nation history <name>", "View a nation's public history log."),
        ],
    },
    "province": {
        "title": "🗺️ Province",
        "color": discord.Color.green(),
        "fields": [
            ("/province info <cell_id>", "View a province by Azgaar cell ID."),
            ("/province list <nation>", "List all provinces owned by a nation."),
            ("/province yield [nation]", "View total resource yield from all your provinces per tick."),
        ],
    },
    "economy": {
        "title": "💰 Economy",
        "color": discord.Color.gold(),
        "fields": [
            ("/resources", "View stockpile, treasury, food status, luxury income, and population."),
            ("/build <cell_id> <key>", "Construct a building. Fort+University now require Clay."),
            ("/buildings list", "Browse all building types with costs and effects."),
            ("/buildings province <cell_id>", "List buildings in a specific province."),
            ("/megaproject propose", "Propose a megaproject for GM approval."),
            ("/megaproject build <id>", "Pay and start an approved megaproject."),
            ("/megaproject list", "View your megaprojects."),
            ("/tech status", "View your nation's tech levels (private)."),
            ("/tech research <category>", "Spend gold + Universal Knowledge to advance tech."),
            ("Resource mechanics",
             "• **Food**: consumed by population (1/100 pop) + military (1/10 units) per tick. "
             "Surplus → pop growth. Shortage → stability loss. Severe shortage → pop decline.\n"
             "• **Silk + Spices**: generate luxury income (1g per 5 held, cap 50g/tick each).\n"
             "• **Cloth**: consumed when building land units (1 per 5 units).\n"
             "• **Coal + Copper**: required for Powder Mill and Cannon Foundry.\n"
             "• **Clay**: required for Fort and University construction."),
        ],
    },
    "trade": {
        "title": "🤝 Trade",
        "color": discord.Color.orange(),
        "fields": [
            ("/trade offer <nation>", "Propose a trade (public note + private terms)."),
            ("/trade accept <id>", "Accept a pending trade offer."),
            ("/trade cancel <id>", "Cancel or decline a trade."),
            ("/trade list", "List your pending trades."),
            ("/trade view <id>", "Full trade details (private terms visible to parties + GM only)."),
        ],
    },
    "military": {
        "title": "⚔️ Military",
        "color": discord.Color.dark_red(),
        "fields": [
            ("/blueprint design_ship <name> <hull>", "Design a ship blueprint with interactive module buttons."),
            ("/blueprint create_unit <name> <type>", "Create a land unit blueprint (stats shown in choices)."),
            ("/blueprint list", "View your saved blueprints with stats and upkeep."),
            ("/blueprint delete <id>", "Delete a custom blueprint."),
            ("/military build <id> <qty> [cell_id]", "Build units. Leave cell_id blank to keep them floating."),
            ("/military list", "View your forces — army, navy, and floating units (private)."),
            ("/military move <unit_id> <cell_id>", "Assign a unit group to a province."),
            ("/military unassign <unit_id>", "Return a unit group to the floating pool."),
            ("/military disband <unit_id>", "Disband a unit group permanently."),
        ],
    },
    "combat": {
        "title": "🗡️ Combat & Diplomacy",
        "color": discord.Color.dark_orange(),
        "fields": [
            ("/battle plan", "Submit a battle plan — location (free text), orders, optional unit IDs and image URL."),
            ("/battle view <id>", "View a battle. Plans are private to parties and GM only."),
            ("/diplomacy war <nation>", "Declare war. Upkeep rises to war rate (×3) immediately."),
            ("/diplomacy peace <nation>", "Make peace with a nation you are at war with."),
            ("/diplomacy alliance <nation>", "Form an alliance with another nation."),
            ("/diplomacy status", "View your own diplomatic relations."),
            ("/diplomacy public", "View the world diplomatic landscape — all active wars and alliances."),
            ("/event list [nation]", "View posted events for a nation or your own."),
        ],
    },
}

GM_HELP_FIELDS = [
    ("/nation history_add", "Add a manual history entry."),
    ("/nation delete <name>", "Delete a nation and release all their provinces (confirmation required)."),
    ("/province claim", "Claim provinces by cell ID(s)."),
    ("/province unclaim", "Remove ownership from provinces."),
    ("/admin map_import", "Import an Azgaar JSON export."),
    ("/admin map_resync", "Re-import an updated Azgaar map."),
    ("/admin map_export_markers", "Generate JS for Azgaar resource markers."),
    ("/admineco tick [months]", "Manually trigger a resource tick."),
    ("/admineco starter_pack [nation|all]", "Give starting resources to one nation or all nations."),
    ("/admineco grant", "Give resources or gold to a nation (logged)."),
    ("/event generate <nation>", "Generate an AI event based on nation history and stats."),
    ("/event edit <id> <text>", "Edit an event draft before posting."),
    ("/event effects <id> <json>", "Set stat effects for an event draft."),
    ("/event post <id>", "Post an event publicly and apply its effects."),
    ("/battle plans_pending", "List all unmatched battle plans."),
    ("/battle match <plan_a> <plan_b>", "Match two plans into a battle, optional context note."),
    ("/battle resolve <id>", "Get AI modifier and resolve battle. GM can override modifiers."),
    ("/admineco tech_set", "Set a nation's tech level directly."),
    ("/admineco mp_approve", "Approve a megaproject with effect, cost, duration."),
    ("/admineco mp_advance", "Advance megaproject construction by N months."),
    ("/admineco building_set", "Edit a building definition live."),
    ("/admineco building_new", "Add a new custom building type."),
    ("/calendar set", "Configure calendar speed, channel, and start date."),
    ("/calendar start/stop", "Start or pause the calendar."),
]

class HelpView(discord.ui.View):
    PLAYER_KEYS = ["general", "nation", "province", "economy", "trade", "military", "combat"]

    def __init__(self, is_gm: bool, current: str = "general"):
        super().__init__(timeout=180)
        self.is_gm   = is_gm
        self.current = current
        self._build()

    def _build(self):
        self.clear_items()
        labels = {"general":"General","nation":"Nation",
                  "province":"Province","economy":"Economy",
                  "trade":"Trade","military":"Military","combat":"Combat"}
        for key, label in labels.items():
            btn = discord.ui.Button(
                label=label,
                style=(discord.ButtonStyle.primary
                       if key == self.current
                       else discord.ButtonStyle.secondary),
            )
            btn.callback = self._cb(key)
            self.add_item(btn)
        if self.is_gm:
            gm = discord.ui.Button(
                label="🔐 GM",
                style=(discord.ButtonStyle.danger
                       if self.current == "gm"
                       else discord.ButtonStyle.secondary),
            )
            gm.callback = self._cb("gm")
            self.add_item(gm)

    def _cb(self, key: str):
        async def callback(interaction: discord.Interaction):
            self.current = key
            self._build()
            embed = self._embed()
            await interaction.response.edit_message(embed=embed, view=self)
        return callback

    def _embed(self) -> discord.Embed:
        if self.current == "gm":
            e = discord.Embed(title="🔐 GM Commands", color=discord.Color.red())
            for name, value in GM_HELP_FIELDS:
                e.add_field(name=name, value=value, inline=False)
            return e
        data = HELP_SECTIONS[self.current]
        e = discord.Embed(title=data["title"], color=data["color"])
        for name, value in data["fields"]:
            e.add_field(name=name, value=value, inline=False)
        return e

# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class EconomyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _seed_buildings()
        self.calendar_loop.start()

    def cog_unload(self):
        self.calendar_loop.cancel()

    @tasks.loop(minutes=1)
    async def calendar_loop(self):
        try:
            if _cfg("calendar_running", "0") != "1":
                return
            hpm      = float(_cfg("hours_per_month", "24"))
            last_str = _cfg("last_tick_ts")
            if not last_str:
                print("[CALENDAR] No last_tick_ts set — run /calendar start", flush=True)
                return
            elapsed = (
                datetime.now(timezone.utc) - datetime.fromisoformat(last_str)
            ).total_seconds() / 3600
            if elapsed < hpm:
                return

            months = max(1, int(elapsed / hpm))
            _cfg_set("last_tick_ts", datetime.now(timezone.utc).isoformat())
            print(f"[CALENDAR] {months} month(s) elapsed, running tick...", flush=True)

            ch_id = _cfg("announce_channel_id")
            print(f"[CALENDAR] Announce channel ID: {ch_id!r}", flush=True)
            ch = self.bot.get_channel(int(ch_id)) if ch_id else None
            print(f"[CALENDAR] Channel object: {ch}", flush=True)

            for i in range(months):
                month, year, summaries = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _run_tick(1)
                )
                print(f"[CALENDAR] Month {month}/{year} ticked. Summaries: {summaries}", flush=True)
                if not ch:
                    print("[CALENDAR] No channel found — skipping announcement.", flush=True)
                    continue
                mname = _month_name(month, _lang(interaction) if hasattr(interaction, "locale") else "en")
                embed = discord.Embed(
                    title=f"📅 New Month: {mname}, Year {year}",
                    description="A new month has begun. Nations have collected their income.",
                    color=discord.Color.gold(),
                )
                if summaries:
                    embed.add_field(
                        name="⚙️ Resource Tick",
                        value="\n".join(summaries[:20]),
                        inline=False,
                    )
                try:
                    await ch.send(embed=embed)
                    print(f"[CALENDAR] Announcement sent to #{ch.name}", flush=True)
                except Exception as send_err:
                    print(f"[CALENDAR] Failed to send announcement: {send_err}", flush=True)

        except Exception as e:
            import traceback
            print(f"[CALENDAR ERROR] {e}", flush=True)
            traceback.print_exc()

    @calendar_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ---- Command groups -------------------------------------------------
    buildings_grp = app_commands.Group(name="buildings",    description="Building commands / Budynki")
    mp_grp        = app_commands.Group(name="megaproject",  description="Megaproject commands")
    trade_grp     = app_commands.Group(name="trade",        description="Trade commands / Handel")
    calendar_grp  = app_commands.Group(name="calendar",     description="Calendar commands / Kalendarz")
    admineco_grp  = app_commands.Group(name="admineco",     description="GM economy admin")

    # ======================================================================
    # STANDALONE SLASH COMMANDS
    # ======================================================================

    @app_commands.command(name="resources", description="View your resources / Twoje zasoby")
    async def resources(self, interaction: discord.Interaction):
        lang = _lang(interaction)
        n = _nation_owner(str(interaction.user.id))
        if not n:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        res  = json.loads(n["resources_json"])

        # Calculate food needs
        with db.cursor() as c:
            c.execute(
                "SELECT COALESCE(SUM(population),0) as total_pop "
                "FROM provinces WHERE owner_nation_id=? AND active=1",
                (n["id"],)
            )
            total_pop = c.fetchone()["total_pop"] or 0
            c.execute(
                "SELECT COALESCE(SUM(quantity),0) as total "
                "FROM military_units WHERE nation_id=?",
                (n["id"],)
            )
            total_units = c.fetchone()["total"] or 0

        # Calculate food production rate per tick
        food_prod = 0.0
        with db.cursor() as c:
            c.execute(
                "SELECT buildings_json, base_resources_json FROM provinces "
                "WHERE owner_nation_id=? AND active=1",
                (n["id"],)
            )
            prod_provs = c.fetchall()
        for pp in prod_provs:
            base = json.loads(pp["base_resources_json"])
            food_prod += base.get("food", 0)
            for bkey in json.loads(pp["buildings_json"]):
                bd = _bdef(bkey)
                if bd:
                    food_prod += json.loads(bd["effect_json"]).get("food", 0)

        food_balance = food_prod - food_needed
        if food_needed > 0:
            food_ratio = food_have / food_needed if food_have > 0 else 0
            if food_ratio >= 1.2:
                food_status = f"✅ Well-fed ({food_have:.0f} stored)"
            elif food_ratio >= 1.0:
                food_status = f"🟡 Sufficient ({food_have:.0f} stored)"
            elif food_ratio >= 0.5:
                food_status = f"🟠 Shortage ({food_have:.0f} stored) — stability declining"
            else:
                food_status = f"🔴 Severe shortage ({food_have:.0f} stored) — population declining"
            food_status += (
                f"\nNeeds: {food_needed:.0f}/tick | "
                f"Produces: {food_prod:.0f}/tick | "
                f"Balance: {food_balance:+.0f}/tick"
            )
        else:
            food_status = f"✅ No population ({food_have:.0f} stored, +{food_prod:.0f}/tick)"

        # Luxury income preview
        silk_income   = min(50.0, res.get("silk",   0) / 5)
        spices_income = min(50.0, res.get("spices", 0) / 5)
        luxury_income = silk_income + spices_income

        # Build resource display — exclude food (shown separately)
        other_res = {k: v for k, v in sorted(res.items()) if k != "food" and v > 0}
        desc = "\n".join(f"**{k.replace('_',' ').capitalize()}**: {v:,.1f}"
                         for k, v in other_res.items()) or "*No resources yet.*"

        embed = discord.Embed(
            title=f"{n['flag'] or ''} {n['name']} — Resources".strip(),
            description=desc,
            color=discord.Color.green(),
        )
        embed.add_field(name="🌾 Food", value=food_status, inline=False)
        embed.add_field(name="💰 Treasury", value=f"{n['treasury']:,.0f} gold", inline=True)
        if luxury_income > 0:
            embed.add_field(
                name="💎 Luxury Income",
                value=f"+{luxury_income:.0f}g/tick (silk+spices)",
                inline=True,
            )
        embed.add_field(
            name="👥 Population",
            value=f"{total_pop:,} total | {total_units} military units",
            inline=True,
        )
        embed.set_footer(
            text=f"In-game: Month {_cfg('current_month','?')}, Year {_cfg('current_year','?')}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="build", description="Construct a building / Buduj w prowincji")
    @app_commands.describe(cell_id="Province cell ID", building="Building key e.g. farm, mine")
    async def build(self, interaction: discord.Interaction, cell_id: int, building: str):
        lang = _lang(interaction)
        n = _nation_owner(str(interaction.user.id))
        if not n:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,))
            prov = c.fetchone()
        if not prov:
            await interaction.response.send_message(
                i18n.t(lang, "province_not_found", cell_id=cell_id), ephemeral=True)
            return
        if prov["owner_nation_id"] != n["id"]:
            await interaction.response.send_message(i18n.t(lang, "build_not_owner"), ephemeral=True)
            return
        bd = _bdef(building)
        if not bd:
            await interaction.response.send_message(
                i18n.t(lang, "build_unknown", key=building), ephemeral=True)
            return
        if not _terrain_ok(prov["terrain"], bd["requires_terrain"]):
            await interaction.response.send_message(
                i18n.t(lang, "build_wrong_terrain",
                        building=bd["name"], terrain=prov["terrain"]), ephemeral=True)
            return
        if not _tech_ok(n, bd["requires_tech"]):
            await interaction.response.send_message(
                i18n.t(lang, "build_need_tech",
                        building=bd["name"], level=bd["requires_tech"]), ephemeral=True)
            return
        bldgs = json.loads(prov["buildings_json"])
        if building.lower() in bldgs:
            await interaction.response.send_message(
                i18n.t(lang, "build_already_exists", building=bd["name"]), ephemeral=True)
            return
        cost      = json.loads(bd["cost_json"])
        gold_cost = cost.get("gold", 0)
        if n["treasury"] < gold_cost:
            await interaction.response.send_message(
                i18n.t(lang, "build_no_gold", need=gold_cost, have=n["treasury"]), ephemeral=True)
            return
        res = json.loads(n["resources_json"])
        ok, missing = _deduct(res, cost)
        if not ok:
            await interaction.response.send_message(
                i18n.t(lang, "build_no_resource", resource=missing), ephemeral=True)
            return
        bldgs.append(building.lower())
        fort_bonus = 1 if building.lower() == "fort" else 0
        with db.cursor() as c:
            c.execute(
                "UPDATE provinces SET buildings_json=?,"
                "fortification_level=fortification_level+? WHERE azgaar_cell_id=?",
                (json.dumps(bldgs), fort_bonus, cell_id)
            )
            c.execute(
                "UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                (json.dumps(res), n["treasury"] - gold_cost, n["id"])
            )
        _log(n["id"], "system", f"Built {bd['name']} in province {prov['name'] or cell_id}.")
        effects  = json.loads(bd["effect_json"])
        eff_str  = ", ".join(f"+{v} {k}/month" for k, v in effects.items()) or "special"
        embed = discord.Embed(
            title=i18n.t(lang, "build_success_title"),
            description=i18n.t(lang, "build_success_desc",
                                building=bd["name"],
                                province=prov["name"] or f"Cell #{cell_id}",
                                effects=eff_str),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    # ======================================================================
    # BUILDINGS GROUP
    # ======================================================================

    @buildings_grp.command(name="list", description="Browse building types / Lista budynkow")
    async def buildings_list(self, interaction: discord.Interaction):
        with db.cursor() as c:
            c.execute("SELECT * FROM building_defs ORDER BY tier, name")
            rows = c.fetchall()
        pages = []
        cur_embed = discord.Embed(title="🏗️ Available Buildings (1/…)", color=discord.Color.blue())
        for i, b in enumerate(rows):
            cost_str = ", ".join(f"{v} {k}" for k, v in json.loads(b["cost_json"]).items())
            eff_str  = ", ".join(f"+{v} {k}/tick" for k, v in json.loads(b["effect_json"]).items()) or "special"
            terrain  = b["requires_terrain"] or "any"
            tech     = f" | Tech≥{b['requires_tech']}" if b["requires_tech"] > 0 else ""
            cur_embed.add_field(
                name=f"T{b['tier']} `{b['key']}` — {b['name']}",
                value=f"{b['description']}\nCost: {cost_str} | Terrain: {terrain}{tech}\nProduces: {eff_str}",
                inline=False,
            )
            if (i + 1) % 5 == 0:
                pages.append(cur_embed)
                cur_embed = discord.Embed(
                    title=f"🏗️ Available Buildings ({len(pages)+1}/…)",
                    color=discord.Color.blue(),
                )
        if cur_embed.fields:
            pages.append(cur_embed)
        for idx, p in enumerate(pages):
            p.title = f"🏗️ Available Buildings ({idx+1}/{len(pages)})"
        view = PageView(pages)
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)

    @buildings_grp.command(name="province", description="Buildings in a province / Budynki w prowincji")
    @app_commands.describe(cell_id="Province cell ID")
    async def buildings_province(self, interaction: discord.Interaction, cell_id: int):
        with db.cursor() as c:
            c.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,))
            prov = c.fetchone()
        if not prov:
            await interaction.response.send_message(
                i18n.t(_lang(interaction), "province_not_found", cell_id=cell_id), ephemeral=True)
            return
        bldgs = json.loads(prov["buildings_json"])
        if not bldgs:
            await interaction.response.send_message(
                f"No buildings in **{prov['name'] or f'Cell #{cell_id}'}**.", ephemeral=True)
            return
        lines = []
        for bkey in bldgs:
            bd  = _bdef(bkey)
            nm  = bd["name"] if bd else bkey
            eff = json.loads(bd["effect_json"]) if bd else {}
            lines.append(
                f"**{nm}** — "
                + (", ".join(f"+{v} {k}/tick" for k, v in eff.items()) or "special")
            )
        embed = discord.Embed(
            title=f"Buildings — {prov['name'] or f'Cell #{cell_id}'}",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.add_field(name="Fortification", value=str(prov["fortification_level"]), inline=True)
        await interaction.response.send_message(embed=embed)

    # ======================================================================
    # MEGAPROJECT GROUP
    # ======================================================================

    @mp_grp.command(name="propose", description="Propose a megaproject / Zaproponuj megaprojekt")
    @app_commands.describe(name="Project name", effect="Desired effect", gold_budget="Gold budget")
    async def mp_propose(self, interaction: discord.Interaction,
                         name: str, effect: str, gold_budget: int):
        lang = _lang(interaction)
        n = _nation_owner(str(interaction.user.id))
        if not n:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute(
                "INSERT INTO megaprojects(nation_id,name,proposed_effect,cost_json,status)"
                " VALUES(?,?,?,?,?)",
                (n["id"], name, effect, json.dumps({"gold": gold_budget}), "proposed")
            )
            mp_id = c.lastrowid
        _log(n["id"], "player",
             f"Proposed megaproject '{name}' (#{mp_id}): {effect}. Budget: {gold_budget}g.")
        embed = discord.Embed(
            title="Megaproject Proposed",
            description=(
                f"**{name}** (ID: {mp_id})\n{effect}\n"
                f"Budget: {gold_budget:,} gold\n\nAwaiting GM approval."
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @mp_grp.command(name="build", description="Start building an approved megaproject / Rozpocznij budowe")
    @app_commands.describe(mp_id="Megaproject ID")
    async def mp_build(self, interaction: discord.Interaction, mp_id: int):
        lang = _lang(interaction)
        nat  = _nation_owner(str(interaction.user.id))
        if not nat:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM megaprojects WHERE id=? AND nation_id=?", (mp_id, nat["id"]))
            mp = c.fetchone()
        if not mp:
            await interaction.response.send_message(f"Megaproject #{mp_id} not found.", ephemeral=True)
            return
        if mp["status"] != "approved":
            await interaction.response.send_message(
                f"Megaproject #{mp_id} is **{mp['status']}** — only approved projects can be started.",
                ephemeral=True)
            return
        cost      = json.loads(mp["cost_json"])
        gold_cost = cost.get("gold", 0)
        if nat["treasury"] < gold_cost:
            await interaction.response.send_message(
                f"Not enough gold. Need **{gold_cost:,}g**, have **{nat['treasury']:,.0f}g**.",
                ephemeral=True)
            return
        res = json.loads(nat["resources_json"])
        ok, missing = _deduct(res, cost)
        if not ok:
            await interaction.response.send_message(f"Not enough **{missing}**.", ephemeral=True)
            return
        new_status = "building" if mp["duration_months"] > 0 else "complete"
        with db.cursor() as c:
            c.execute(
                "UPDATE megaprojects SET status=?,months_spent=0 WHERE id=?",
                (new_status, mp_id)
            )
            c.execute("UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                      (json.dumps(res), nat["treasury"] - gold_cost, nat["id"]))
        if new_status == "complete":
            _apply_mp_effect(nat["id"], mp["effect_json"], mp["name"])
            _log(nat["id"], "system", f"Megaproject '{mp['name']}' completed instantly.")
            await interaction.response.send_message(
                f"✅ **{mp['name']}** built and completed! Effects applied.", ephemeral=False)
        else:
            _log(nat["id"], "player",
                 f"Started construction of megaproject '{mp['name']}' "
                 f"({mp['duration_months']} months). Cost paid.")
            await interaction.response.send_message(
                f"🔨 **{mp['name']}** construction started! "
                f"Estimated completion: **{mp['duration_months']}** in-game month(s).",
                ephemeral=False)

    @mp_grp.command(name="list", description="List your megaprojects / Lista megaprojektow")
    async def mp_list(self, interaction: discord.Interaction):
        lang  = _lang(interaction)
        is_gm = _gm(interaction)
        n     = _nation_owner(str(interaction.user.id))
        with db.cursor() as c:
            if is_gm:
                c.execute(
                    "SELECT m.*,na.name as nname FROM megaprojects m"
                    " JOIN nations na ON m.nation_id=na.id ORDER BY m.id DESC"
                )
            elif n:
                c.execute(
                    "SELECT m.*,na.name as nname FROM megaprojects m"
                    " JOIN nations na ON m.nation_id=na.id"
                    " WHERE m.nation_id=? ORDER BY m.id DESC",
                    (n["id"],)
                )
            else:
                await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
                return
            rows = c.fetchall()
        if not rows:
            await interaction.response.send_message("No megaprojects found.", ephemeral=True)
            return
        EMOJI = {"proposed":"🟡","approved":"🟢","building":"🔨","complete":"✅"}
        lines = []
        for r in rows:
            cost = json.loads(r["cost_json"])
            if r["status"] == "building" and r["duration_months"] > 0:
                pct     = int((r["months_spent"] / r["duration_months"]) * 100)
                bar     = "█" * (pct // 10) + "░" * (10 - pct // 10)
                prog    = f"\n  `{bar}` {r['months_spent']}/{r['duration_months']} months ({pct}%)"
            elif r["status"] == "building":
                prog = " (instant — pending completion)"
            else:
                prog = ""
            lines.append(
                f"{EMOJI.get(r['status'],'❓')} **[{r['id']}] {r['name']}** ({r['nname']}){prog}\n"
                f"  {r['proposed_effect']}\n"
                f"  Cost: {', '.join(f'{v} {k}' for k,v in cost.items())} | {r['status']}"
            )
        embed = discord.Embed(
            title="Megaprojects" + (" — All Nations" if is_gm else ""),
            description="\n\n".join(lines),
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ======================================================================
    # TRADE GROUP
    # ======================================================================

    @trade_grp.command(name="offer", description="Propose a trade / Zaproponuj handel")
    @app_commands.describe(
        to_nation="Nation to trade with",
        give_resources='Resources you give e.g. {"wood":50}',
        give_gold="Gold you give",
        receive_resources='Resources you receive e.g. {"iron":20}',
        receive_gold="Gold you receive",
        public_note="Note visible to everyone",
        private_note="Note visible only to both parties and GM",
    )
    async def trade_offer(self, interaction: discord.Interaction,
                          to_nation: str,
                          give_resources: str = "{}",
                          give_gold: float = 0.0,
                          receive_resources: str = "{}",
                          receive_gold: float = 0.0,
                          public_note: str = "",
                          private_note: str = ""):
        lang = _lang(interaction)
        fn   = _nation_owner(str(interaction.user.id))
        if not fn:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        tn = _nation_name(to_nation)
        if not tn:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True)
            return
        if tn["id"] == fn["id"]:
            await interaction.response.send_message("Cannot trade with yourself.", ephemeral=True)
            return
        try:
            give_res = json.loads(give_resources)
            recv_res = json.loads(receive_resources)
        except json.JSONDecodeError:
            await interaction.response.send_message("Invalid JSON for resources.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute(
                "INSERT INTO trades(from_nation_id,to_nation_id,offer_resources_json,offer_gold,"
                "receive_resources_json,receive_gold,public_note,private_note,status)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (fn["id"], tn["id"],
                 json.dumps(give_res), give_gold,
                 json.dumps(recv_res), receive_gold,
                 public_note, private_note, "pending")
            )
            trade_id = c.lastrowid
        _log(fn["id"], "player", f"Sent trade offer #{trade_id} to {tn['name']}.")
        give_str = ", ".join(f"{v} {k}" for k, v in give_res.items())
        if give_gold:
            give_str += f", {give_gold:.0f} gold"
        recv_str = ", ".join(f"{v} {k}" for k, v in recv_res.items())
        if receive_gold:
            recv_str += f", {receive_gold:.0f} gold"
        embed = discord.Embed(title=f"Trade Offer #{trade_id}", color=discord.Color.blue())
        embed.add_field(name=f"{fn['name']} gives", value=give_str or "—", inline=True)
        embed.add_field(name=f"{tn['name']} gives", value=recv_str or "—", inline=True)
        if public_note:
            embed.add_field(name="Public note", value=public_note, inline=False)
        embed.set_footer(text=f"Use /trade accept {trade_id} to accept.")
        await interaction.response.send_message(embed=embed)
        # DM the receiver with private terms
        if interaction.guild:
            member = interaction.guild.get_member(int(tn["owner_id"]))
            if member:
                dm = discord.Embed(
                    title=f"Trade Offer #{trade_id} from {fn['name']}",
                    color=discord.Color.blue()
                )
                dm.add_field(name="They give", value=give_str or "—", inline=True)
                dm.add_field(name="They want", value=recv_str or "—", inline=True)
                if public_note:
                    dm.add_field(name="Public note", value=public_note, inline=False)
                if private_note:
                    dm.add_field(name="🔒 Private note", value=private_note, inline=False)
                dm.set_footer(text=f"Use /trade accept {trade_id} or /trade cancel {trade_id}")
                try:
                    await member.send(embed=dm)
                except discord.Forbidden:
                    pass

    @trade_grp.command(name="accept", description="Accept a trade / Zaakceptuj handel")
    @app_commands.describe(trade_id="Trade ID")
    async def trade_accept(self, interaction: discord.Interaction, trade_id: int):
        lang = _lang(interaction)
        n    = _nation_owner(str(interaction.user.id))
        if not n:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
            trade = c.fetchone()
        if not trade:
            await interaction.response.send_message(f"Trade #{trade_id} not found.", ephemeral=True)
            return
        if trade["to_nation_id"] != n["id"] and not _gm(interaction):
            await interaction.response.send_message(
                "This trade is not addressed to your nation.", ephemeral=True)
            return
        if trade["status"] != "pending":
            await interaction.response.send_message(
                f"Trade #{trade_id} is already {trade['status']}.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM nations WHERE id=?", (trade["from_nation_id"],))
            fn = c.fetchone()
            c.execute("SELECT * FROM nations WHERE id=?", (trade["to_nation_id"],))
            tn = c.fetchone()
        give_res  = json.loads(trade["offer_resources_json"])
        recv_res  = json.loads(trade["receive_resources_json"])
        give_gold = trade["offer_gold"]
        recv_gold = trade["receive_gold"]
        fn_res    = json.loads(fn["resources_json"])
        tn_res    = json.loads(tn["resources_json"])
        for r, a in give_res.items():
            if fn_res.get(r, 0) < a:
                await interaction.response.send_message(
                    f"{fn['name']} no longer has enough {r}.", ephemeral=True)
                return
        if fn["treasury"] < give_gold:
            await interaction.response.send_message(
                f"{fn['name']} no longer has enough gold.", ephemeral=True)
            return
        for r, a in recv_res.items():
            if tn_res.get(r, 0) < a:
                await interaction.response.send_message(
                    f"{tn['name']} does not have enough {r}.", ephemeral=True)
                return
        if tn["treasury"] < recv_gold:
            await interaction.response.send_message(
                f"{tn['name']} does not have enough gold.", ephemeral=True)
            return
        for r, a in give_res.items():
            fn_res[r] = fn_res.get(r, 0) - a
            tn_res[r] = tn_res.get(r, 0) + a
        for r, a in recv_res.items():
            tn_res[r] = tn_res.get(r, 0) - a
            fn_res[r] = fn_res.get(r, 0) + a
        fn_treasury = fn["treasury"] - give_gold + recv_gold
        tn_treasury = tn["treasury"] - recv_gold + give_gold
        with db.cursor() as c:
            c.execute("UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                      (json.dumps(fn_res), fn_treasury, fn["id"]))
            c.execute("UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                      (json.dumps(tn_res), tn_treasury, tn["id"]))
            c.execute("UPDATE trades SET status='accepted',resolved_at=datetime('now') WHERE id=?",
                      (trade_id,))
        give_str = ", ".join(f"{v} {k}" for k, v in give_res.items())
        if give_gold:
            give_str += f", {give_gold:.0f} gold"
        recv_str = ", ".join(f"{v} {k}" for k, v in recv_res.items())
        if recv_gold:
            recv_str += f", {recv_gold:.0f} gold"
        log_priv = (f"Trade #{trade_id} with {tn['name']}. Gave: {give_str or '—'}."
                    f" Received: {recv_str or '—'}.")
        if trade["private_note"]:
            log_priv += f" (Private: {trade['private_note']})"
        _log(fn["id"], "trade_private", log_priv)
        _log(tn["id"], "trade_private", log_priv)
        _log(fn["id"], "system", f"Trade #{trade_id} accepted by {tn['name']}.")
        _log(tn["id"], "system", f"Trade #{trade_id} accepted.")
        embed = discord.Embed(title=f"✅ Trade #{trade_id} Accepted", color=discord.Color.green())
        embed.add_field(name=f"{fn['name']} gave", value=give_str or "—", inline=True)
        embed.add_field(name=f"{tn['name']} gave", value=recv_str or "—", inline=True)
        if trade["public_note"]:
            embed.add_field(name="Note", value=trade["public_note"], inline=False)
        await interaction.response.send_message(embed=embed)

    @trade_grp.command(name="cancel", description="Cancel/decline a trade / Anuluj handel")
    @app_commands.describe(trade_id="Trade ID")
    async def trade_cancel(self, interaction: discord.Interaction, trade_id: int):
        lang  = _lang(interaction)
        n     = _nation_owner(str(interaction.user.id))
        is_gm = _gm(interaction)
        with db.cursor() as c:
            c.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
            trade = c.fetchone()
        if not trade:
            await interaction.response.send_message(f"Trade #{trade_id} not found.", ephemeral=True)
            return
        if not is_gm and n and trade["from_nation_id"] != n["id"] and trade["to_nation_id"] != n["id"]:
            await interaction.response.send_message("You are not party to this trade.", ephemeral=True)
            return
        if trade["status"] != "pending":
            await interaction.response.send_message(
                f"Trade #{trade_id} is already {trade['status']}.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("UPDATE trades SET status='cancelled',resolved_at=datetime('now') WHERE id=?",
                      (trade_id,))
        await interaction.response.send_message(f"Trade #{trade_id} cancelled.", ephemeral=True)

    @trade_grp.command(name="list", description="List pending trades / Lista ofert handlowych")
    async def trade_list(self, interaction: discord.Interaction):
        lang  = _lang(interaction)
        n     = _nation_owner(str(interaction.user.id))
        is_gm = _gm(interaction)
        if not n and not is_gm:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        with db.cursor() as c:
            if is_gm and not n:
                c.execute(
                    "SELECT t.*,fn.name as fname,tn.name as tname"
                    " FROM trades t JOIN nations fn ON t.from_nation_id=fn.id"
                    " JOIN nations tn ON t.to_nation_id=tn.id"
                    " WHERE t.status='pending' ORDER BY t.id DESC"
                )
            else:
                c.execute(
                    "SELECT t.*,fn.name as fname,tn.name as tname"
                    " FROM trades t JOIN nations fn ON t.from_nation_id=fn.id"
                    " JOIN nations tn ON t.to_nation_id=tn.id"
                    " WHERE (t.from_nation_id=? OR t.to_nation_id=?) AND t.status='pending'"
                    " ORDER BY t.id DESC",
                    (n["id"], n["id"])
                )
            rows = c.fetchall()
        if not rows:
            await interaction.response.send_message("No pending trades.", ephemeral=True)
            return
        lines = []
        for r in rows:
            arrow = "→" if (n and r["from_nation_id"] == n["id"]) else "←"
            other = r["tname"] if (n and r["from_nation_id"] == n["id"]) else r["fname"]
            lines.append(f"**#{r['id']}** {arrow} **{other}** | {r['public_note'] or '—'}")
        embed = discord.Embed(
            title="Pending Trades",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Use /trade view <id> for full details.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @trade_grp.command(name="view", description="View trade details / Szczegoly transakcji")
    @app_commands.describe(trade_id="Trade ID")
    async def trade_view(self, interaction: discord.Interaction, trade_id: int):
        lang  = _lang(interaction)
        n     = _nation_owner(str(interaction.user.id))
        is_gm = _gm(interaction)
        with db.cursor() as c:
            c.execute(
                "SELECT t.*,fn.name as fname,fn.flag as fflag,tn.name as tname,tn.flag as tflag"
                " FROM trades t JOIN nations fn ON t.from_nation_id=fn.id"
                " JOIN nations tn ON t.to_nation_id=tn.id WHERE t.id=?",
                (trade_id,)
            )
            t = c.fetchone()
        if not t:
            await interaction.response.send_message(f"Trade #{trade_id} not found.", ephemeral=True)
            return
        is_party = n and (t["from_nation_id"] == n["id"] or t["to_nation_id"] == n["id"])
        STATUS   = {"pending":"🟡","accepted":"✅","cancelled":"❌"}
        embed = discord.Embed(
            title=f"{STATUS.get(t['status'],'❓')} Trade #{trade_id}",
            description=f"**{t['fflag'] or ''} {t['fname']}** ↔ **{t['tflag'] or ''} {t['tname']}**",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Status", value=t["status"].capitalize(), inline=True)
        embed.add_field(name="Date",   value=t["created_at"][:10],     inline=True)
        give_res  = json.loads(t["offer_resources_json"])
        recv_res  = json.loads(t["receive_resources_json"])
        give_str  = ", ".join(f"{v} {k}" for k, v in give_res.items())
        if t["offer_gold"]:
            give_str += f", {t['offer_gold']:.0f} gold"
        recv_str = ", ".join(f"{v} {k}" for k, v in recv_res.items())
        if t["receive_gold"]:
            recv_str += f", {t['receive_gold']:.0f} gold"
        embed.add_field(name=f"{t['fname']} gives", value=give_str or "—", inline=True)
        embed.add_field(name=f"{t['tname']} gives", value=recv_str or "—", inline=True)
        if t["public_note"]:
            embed.add_field(name="Public note", value=t["public_note"], inline=False)
        if is_party or is_gm:
            embed.add_field(
                name="🔒 Private terms",
                value=t["private_note"] or "—",
                inline=False,
            )
        else:
            embed.add_field(
                name="🔒 Private terms",
                value="*Hidden — visible to parties and GM only.*",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ======================================================================
    # CALENDAR GROUP
    # ======================================================================

    @calendar_grp.command(name="set", description="[GM] Configure calendar / [GM] Skonfiguruj kalendarz")
    @app_commands.describe(
        hours_per_month="Real hours per in-game month",
        channel="Announcement channel",
        start_month="Starting month (1-12)",
        start_year="Starting year",
    )
    async def calendar_set(self, interaction: discord.Interaction,
                           hours_per_month: float, channel: discord.TextChannel,
                           start_month: int = 1, start_year: int = 1):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        _cfg_set("hours_per_month",    hours_per_month)
        _cfg_set("announce_channel_id", channel.id)
        _cfg_set("current_month",      start_month)
        _cfg_set("current_year",       start_year)
        await interaction.response.send_message(
            f"✅ Calendar: **{hours_per_month}h** IRL = 1 month → {channel.mention}\n"
            f"Starting: Month {start_month}, Year {start_year}\n"
            "Use `/calendar start` to begin.",
            ephemeral=True,
        )

    @calendar_grp.command(name="start", description="[GM] Start the calendar / [GM] Uruchom kalendarz")
    async def calendar_start(self, interaction: discord.Interaction):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        _cfg_set("calendar_running", "1")
        _cfg_set("last_tick_ts", datetime.now(timezone.utc).isoformat())
        await interaction.response.send_message("✅ Calendar started.", ephemeral=True)

    @calendar_grp.command(name="stop", description="[GM] Pause the calendar / [GM] Zatrzymaj kalendarz")
    async def calendar_stop(self, interaction: discord.Interaction):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        _cfg_set("calendar_running", "0")
        await interaction.response.send_message("⏸️ Calendar paused.", ephemeral=True)

    @calendar_grp.command(name="status", description="Current in-game date / Aktualna data w grze")
    async def calendar_status(self, interaction: discord.Interaction):
        month   = _cfg("current_month", "1")
        year    = _cfg("current_year",  "1")
        running = _cfg("calendar_running", "0") == "1"
        hpm     = _cfg("hours_per_month", "—")
        try:
            mname = _month_name(int(month), _lang(interaction))
        except (ValueError, IndexError):
            mname = f"Month {month}"
        embed = discord.Embed(title="📅 In-Game Calendar", color=discord.Color.gold())
        embed.add_field(name="Current Date", value=f"{mname}, Year {year}", inline=True)
        embed.add_field(name="Status",       value="▶️ Running" if running else "⏸️ Paused", inline=True)
        embed.add_field(name="Speed",        value=f"{hpm}h IRL = 1 month", inline=True)
        await interaction.response.send_message(embed=embed)

    # ======================================================================
    # ADMINECO GROUP
    # ======================================================================

    @admineco_grp.command(name="tick", description="[GM] Manual resource tick / [GM] Recznie uruchom tick")
    @app_commands.describe(months="Months to advance (default 1)")
    async def admin_tick(self, interaction: discord.Interaction, months: int = 1):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            ch_id = _cfg("announce_channel_id")
            ch    = self.bot.get_channel(int(ch_id)) if ch_id else None
            for _ in range(months):
                month, year, summaries = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _run_tick(1)
                )
                if ch:
                    mname = _month_name(month, _lang(interaction) if hasattr(interaction, "locale") else "en")
                    embed = discord.Embed(
                        title=f"📅 New Month: {mname}, Year {year}",
                        description="A new month has begun. Nations have collected their income.",
                        color=discord.Color.gold(),
                    )
                    if summaries:
                        embed.add_field(
                            name="⚙️ Resource Tick",
                            value="\n".join(summaries[:20]),
                            inline=False,
                        )
                    try:
                        await ch.send(embed=embed)
                    except discord.Forbidden:
                        await interaction.followup.send(
                            f"⚠️ Tick ran but bot lacks **Send Messages** / **Embed Links** "
                            f"permission in <#{ch.id}>. Fix the channel permissions in Discord.",
                            ephemeral=True,
                        )
                    except Exception as send_err:
                        await interaction.followup.send(
                            f"⚠️ Tick ran but announcement failed: {send_err}", ephemeral=True
                        )
            report = "\n".join(summaries) if summaries else "No nations."
            await interaction.followup.send(
                f"✅ Advanced **{months}** month(s) → {_month_name(month, _lang(interaction))}, Year {year}\n"
                f"```\n{report}\n```",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Tick failed: {e}", ephemeral=True)

    @admineco_grp.command(name="grant", description="[GM] Give resources to a nation / [GM] Dodaj zasoby")
    @app_commands.describe(
        nation="Nation name", resource="Resource name or 'gold'",
        amount="Amount", reason="Reason (logged)"
    )
    async def admin_grant(self, interaction: discord.Interaction,
                          nation: str, resource: str, amount: float, reason: str = "GM grant"):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        n = _nation_name(nation)
        if not n:
            await interaction.response.send_message(i18n.t(_lang(interaction), "nation_not_found"), ephemeral=True)
            return

        KNOWN_RESOURCES = {
            "gold", "food", "wood", "stone", "iron", "copper", "coal", "clay",
            "cloth", "tar", "gunpowder", "horses", "spices", "silk", "algae",
            "universal_knowledge",
        }
        resource_key = resource.lower().strip()
        warning = ""
        if resource_key != "gold" and resource_key not in KNOWN_RESOURCES:
            warning = (
                f"\n⚠️ **'{resource_key}'** is not a recognised resource name. "
                f"It was added anyway — double-check the spelling.\n"
                f"Known resources: {', '.join(sorted(KNOWN_RESOURCES - {'gold'}))}."
            )

        if resource_key == "gold":
            with db.cursor() as c:
                c.execute("UPDATE nations SET treasury=treasury+? WHERE id=?", (amount, n["id"]))
        else:
            with db.cursor() as c:
                c.execute("SELECT resources_json FROM nations WHERE id=?", (n["id"],))
                res = json.loads(c.fetchone()["resources_json"])
            res[resource_key] = res.get(resource_key, 0) + amount
            with db.cursor() as c:
                c.execute("UPDATE nations SET resources_json=? WHERE id=?",
                          (json.dumps(res), n["id"]))
        _log(n["id"], "gm", f"GM grant: +{amount} {resource_key}. Reason: {reason}")
        await interaction.response.send_message(
            f"✅ Granted **{amount} {resource_key}** to **{n['name']}**.\nReason: {reason}{warning}",
            ephemeral=True,
        )

    @admineco_grp.command(name="mp_approve", description="[GM] Approve megaproject / [GM] Zatwierdz megaprojekt")
    @app_commands.describe(
        mp_id="Megaproject ID",
        final_effect="Effect description (public)",
        effect_json='Effects JSON e.g. {"resources_once":{"gold":500},"stability":5}',
        cost_json='Final cost e.g. {"gold":1000,"wood":200} — use {} for free',
        duration_months="Months to build (0 = instant)",
        gm_notes="Private GM notes",
    )
    async def mp_approve(self, interaction: discord.Interaction,
                         mp_id: int, final_effect: str, effect_json: str,
                         cost_json: str = "{}", duration_months: int = 0, gm_notes: str = ""):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        try:
            parsed_effect = json.loads(effect_json)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(f"❌ Invalid effect_json: {e}\nExample: {{\"resources_once\":{{\"gold\":500}},\"stability\":5}}", ephemeral=True)
            return
        try:
            cost = json.loads(cost_json)
            if not isinstance(cost, dict):
                raise ValueError("cost_json must be a JSON object like {} or {\"gold\":100}")
        except (json.JSONDecodeError, ValueError) as e:
            await interaction.response.send_message(f"❌ Invalid cost_json: {e}\nUse {{}} for free, or {{\"gold\":100}} etc.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM megaprojects WHERE id=?", (mp_id,))
            mp = c.fetchone()
        if not mp:
            await interaction.response.send_message(f"Megaproject #{mp_id} not found.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM nations WHERE id=?", (mp["nation_id"],))
            nat = c.fetchone()
        gold = cost.get("gold", 0)
        if gold > 0 and nat["treasury"] < gold:
            await interaction.response.send_message(
                f"{nat['name']} cannot afford this ({gold:.0f}g needed, {nat['treasury']:.0f}g available).",
                ephemeral=True)
            return
        res = json.loads(nat["resources_json"])
        if cost:
            ok, missing = _deduct(res, cost)
            if not ok:
                await interaction.response.send_message(f"{nat['name']} lacks enough {missing}.", ephemeral=True)
                return
        new_status = "approved"
        with db.cursor() as c:
            c.execute(
                "UPDATE megaprojects SET status=?,proposed_effect=?,effect_json=?,"
                "cost_json=?,duration_months=?,gm_notes=? WHERE id=?",
                (new_status, final_effect, json.dumps(parsed_effect),
                 json.dumps(cost), duration_months, gm_notes, mp_id)
            )
        _log(mp["nation_id"], "gm",
             f"Megaproject '{mp['name']}' approved by GM. "
             f"Effect: {final_effect}. Cost: {json.dumps(cost)}. "
             f"Duration: {duration_months} month(s). "
             f"Player must run /megaproject build {mp_id} to start.")
        await interaction.response.send_message(
            f"✅ **{mp['name']}** approved.\n"
            f"Effect: {final_effect}\n"
            f"Cost: {', '.join(f'{v} {k}' for k,v in cost.items()) or 'free'}\n"
            f"Duration: {duration_months} month(s)\n\n"
            f"The player can now run `/megaproject build {mp_id}` to pay and start construction.",
            ephemeral=True,
        )

    @admineco_grp.command(name="mp_advance", description="[GM] Advance megaproject / [GM] Przyspiesz budowe")
    @app_commands.describe(mp_id="Megaproject ID", months="Months to advance")
    async def mp_advance(self, interaction: discord.Interaction, mp_id: int, months: int):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM megaprojects WHERE id=?", (mp_id,))
            mp = c.fetchone()
        if not mp or mp["status"] != "building":
            await interaction.response.send_message(
                f"Megaproject #{mp_id} not found or not under construction.", ephemeral=True)
            return
        new_spent = min(mp["months_spent"] + months, mp["duration_months"])
        complete  = new_spent >= mp["duration_months"]
        with db.cursor() as c:
            if complete:
                c.execute(
                    "UPDATE megaprojects SET status='complete',months_spent=?,"
                    "completed_at=datetime('now') WHERE id=?",
                    (new_spent, mp_id)
                )
            else:
                c.execute("UPDATE megaprojects SET months_spent=? WHERE id=?", (new_spent, mp_id))
        if complete:
            _apply_mp_effect(mp["nation_id"], mp["effect_json"], mp["name"])
            await interaction.response.send_message(
                f"✅ **{mp['name']}** completed! Effects applied.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"⏩ Advanced **{mp['name']}** by {months} month(s). ({new_spent}/{mp['duration_months']})",
                ephemeral=True)

    @admineco_grp.command(name="tech_set", description="[GM] Set a nation's tech level / [GM] Ustaw poziom technologii")
    @app_commands.describe(
        nation="Nation name / Nazwa narodu",
        category="Category: naval / land / economy / colonial",
        level="New level (0.0 - 10.0)",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="Naval",    value="naval"),
        app_commands.Choice(name="Land",     value="land"),
        app_commands.Choice(name="Economy",  value="economy"),
        app_commands.Choice(name="Colonial", value="colonial"),
    ])
    async def tech_set(self, interaction: discord.Interaction,
                       nation: str, category: app_commands.Choice[str], level: float):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        n = _nation_name(nation)
        if not n:
            await interaction.response.send_message(i18n.t(_lang(interaction), "nation_not_found"), ephemeral=True)
            return
        level = max(0.0, min(10.0, round(level, 2)))
        tech  = json.loads(n["tech_json"])
        old   = tech.get(category.value, 3.0)
        tech[category.value] = level
        with db.cursor() as c:
            c.execute("UPDATE nations SET tech_json=? WHERE id=?", (json.dumps(tech), n["id"]))
        _log(n["id"], "gm",
             f"GM set {category.value.capitalize()} tech: {old:.1f} → {level:.1f}.")
        await interaction.response.send_message(
            f"✅ **{n['name']}** {category.name} tech set to **{level:.1f}**.", ephemeral=True)

    @admineco_grp.command(name="starter_pack",
                          description="[GM] Give starting resources to a nation or all nations")
    @app_commands.describe(
        nation="Nation name, or 'all' for every nation / Nazwa narodu lub 'all'",
    )
    async def starter_pack(self, interaction: discord.Interaction, nation: str = "all"):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return

        STARTER = {
            "food":       200,
            "wood":       150,
            "stone":      100,
            "iron":        80,
            "copper":      40,
            "coal":        40,
            "clay":        60,
            "cloth":       30,
            "tar":         30,
            "gunpowder":   20,
            "horses":      10,
            "spices":      10,
            "silk":         5,
        }
        STARTER_GOLD = 500

        with db.cursor() as c:
            if nation.lower() == "all":
                c.execute("SELECT * FROM nations")
            else:
                c.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (nation,))
            targets = c.fetchall()

        if not targets:
            await interaction.response.send_message(
                "No nations found." if nation.lower() != "all" else "No nations exist yet.",
                ephemeral=True)
            return

        for nat in targets:
            res = json.loads(nat["resources_json"])
            for k, v in STARTER.items():
                res[k] = res.get(k, 0) + v
            with db.cursor() as c:
                c.execute(
                    "UPDATE nations SET resources_json=?,treasury=treasury+? WHERE id=?",
                    (json.dumps(res), STARTER_GOLD, nat["id"])
                )
                c.execute(
                    "INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)",
                    (nat["id"], "gm",
                     f"Received starter pack: {STARTER_GOLD}g + "
                     + ", ".join(f"{v} {k}" for k, v in STARTER.items()) + ".")
                )

        res_preview = ", ".join(f"{v} {k}" for k, v in STARTER.items())
        target_str  = "all nations" if nation.lower() == "all" else f"**{targets[0]['name']}**"
        await interaction.response.send_message(
            f"✅ Starter pack given to {target_str} ({len(targets)} nation(s)).\n"
            f"**Gold:** +{STARTER_GOLD}g\n"
            f"**Resources:** {res_preview}",
            ephemeral=True,
        )
    @app_commands.describe(key="Building key", field="Field to change", value="New value")
    async def building_set(self, interaction: discord.Interaction, key: str, field: str, value: str):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        allowed = {"cost_json","effect_json","upkeep_json","description",
                   "requires_terrain","requires_tech","name","tier"}
        if field not in allowed:
            await interaction.response.send_message(
                f"Unknown field. Allowed: {', '.join(sorted(allowed))}", ephemeral=True)
            return
        if not _bdef(key):
            await interaction.response.send_message(f"Building `{key}` not found.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute(f"UPDATE building_defs SET {field}=? WHERE key=?", (value, key))
        await interaction.response.send_message(f"✅ `{key}.{field}` = `{value}`", ephemeral=True)

    @admineco_grp.command(name="building_new", description="[GM] Add building type")
    @app_commands.describe(
        key="Unique key", name="Display name", tier="Tier 1-3",
        cost_json='e.g. {"gold":100}', effect_json='e.g. {"food":10}',
        requires_terrain="Comma list or blank", description="Short description"
    )
    async def building_new(self, interaction: discord.Interaction,
                           key: str, name: str, tier: int,
                           cost_json: str, effect_json: str,
                           requires_terrain: str = "", description: str = ""):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        try:
            json.loads(cost_json)
            json.loads(effect_json)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(f"Invalid JSON: {e}", ephemeral=True)
            return
        if _bdef(key.lower()):
            await interaction.response.send_message(f"Building `{key}` already exists.", ephemeral=True)
            return
        with db.cursor() as c:
            c.execute(
                "INSERT INTO building_defs(key,name,tier,cost_json,effect_json,upkeep_json,"
                "requires_terrain,requires_tech,description) VALUES(?,?,?,?,?,'{}',?,0.0,?)",
                (key.lower(), name, tier, cost_json, effect_json, requires_terrain, description)
            )
        await interaction.response.send_message(f"✅ Building `{key}` created.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
