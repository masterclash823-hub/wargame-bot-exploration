"""
Economy cog: resources, buildings, calendar, megaprojects, trades, admineco.
All slash commands use @app_commands.command or group subcommands — no hybrid.
"""
from flags import flag_text, flagged_embed
import psycopg2
import psycopg2.extras

import json, asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config, db, i18n
from utils import gm_only, short_date
from trade_service import parse_resources, validate_gold, accept_trade

MONTH_NAMES = [
    "January","February","March","April","May","June",
    "July","August","September","October","November","December",
]
MONTH_NAMES_PL = [
    "Styczeń","Luty","Marzec","Kwiecień","Maj","Czerwiec",
    "Lipiec","Sierpień","Wrzesień","Październik","Listopad","Grudzień",
]

def _month_name(month: int, lang: str = None) -> str:
    names = MONTH_NAMES_PL if (lang or i18n.current_language()) == "pl" else MONTH_NAMES
    try:
        return names[month - 1]
    except (IndexError, TypeError):
        return str(month)

DEFAULT_BUILDINGS = [
    {"key":"farm",            "name":"Farm",            "tier":1,"cost":{"gold":100,"wood":50},                      "effect":{"food":10},                  "upkeep":{"gold":2}, "terrain":"plains,grassland",       "tech":0.0,"desc":"Food on plains/grassland."},
    {"key":"fishing_wharf",   "name":"Fishing Wharf",   "tier":1,"cost":{"gold":80,"wood":60},                       "effect":{"food":8},                   "upkeep":{"gold":2}, "terrain":"", "tech":0.0,"desc":"Food on coastal/water provinces."},
    {"key":"plantation",      "name":"Plantation",      "tier":2,"cost":{"gold":150,"wood":40},                      "effect":{"food":6,"spices":2},        "upkeep":{"gold":3}, "terrain":"forest,jungle",          "tech":3.0,"desc":"Food+spices in tropical/forest provinces."},
    {"key":"pasture",         "name":"Pasture",          "tier":1,"cost":{"gold":60,"wood":20},                       "effect":{"food":5,"horses":1},        "upkeep":{"gold":1}, "terrain":"plains,grassland,hills", "tech":0.0,"desc":"Food+horses on open terrain."},
    {"key":"lumber_camp",     "name":"Lumber Camp",      "tier":1,"cost":{"gold":80},                                 "effect":{"wood":8},                   "upkeep":{"gold":1}, "terrain":"forest,taiga,tropical rainforest,temperate rainforest,temperate deciduous forest,tropical seasonal forest",           "tech":0.0,"desc":"Wood from forests."},
    {"key":"mine",            "name":"Mine",             "tier":1,"cost":{"gold":120,"wood":30},                      "effect":{"iron":6,"stone":4,"coal":3},"upkeep":{"gold":2}, "terrain":"hills,mountains",        "tech":0.0,"desc":"Iron/stone/coal from hills/mountains."},
    {"key":"copper_mine",     "name":"Copper Mine",      "tier":1,"cost":{"gold":100,"wood":20},                      "effect":{"copper":5},                 "upkeep":{"gold":2}, "terrain":"hills,mountains",        "tech":0.0,"desc":"Copper from hills/mountains."},
    {"key":"clay_pit",        "name":"Clay Pit",         "tier":1,"cost":{"gold":60},                                 "effect":{"clay":6},                   "upkeep":{"gold":1}, "terrain":"wetland,plains",         "tech":0.0,"desc":"Clay from wetlands/plains."},
    {"key":"tar_works",       "name":"Tar Works",        "tier":1,"cost":{"gold":80,"wood":20},                       "effect":{"tar":5},                    "upkeep":{"gold":1}, "terrain":"forest,wetland,taiga",   "tech":0.0,"desc":"Tar from forests/wetlands."},
    {"key":"powder_mill",     "name":"Powder Mill",      "tier":2,"cost":{"gold":200,"stone":50,"iron":20,"coal":20,"copper":10}, "effect":{"gunpowder":4},  "upkeep":{"gold":5}, "terrain":"",                       "tech":4.0,"desc":"Gunpowder. Requires coal+copper+iron. Tech 4."},
    {"key":"cannon_foundry",  "name":"Cannon Foundry",   "tier":2,"cost":{"gold":250,"iron":40,"coal":30,"copper":20},"effect":{"gunpowder":6,"iron":-2},   "upkeep":{"gold":6}, "terrain":"",                       "tech":4.0,"desc":"More gunpowder output, consumes iron. Requires coal+copper. Tech 4."},
    {"key":"textile_mill",    "name":"Textile Mill",     "tier":2,"cost":{"gold":150,"wood":40},                      "effect":{"cloth":6},                  "upkeep":{"gold":3}, "terrain":"",                       "tech":3.0,"desc":"Cloth. Requires tech 3."},
    {"key":"silk_workshop",   "name":"Silk Workshop",    "tier":2,"cost":{"gold":200,"wood":30,"cloth":20},           "effect":{"silk":3},                   "upkeep":{"gold":4}, "terrain":"plains,grassland",       "tech":3.0,"desc":"Silk production. Requires cloth. Tech 3."},
    {"key":"market",          "name":"Market",           "tier":1,"cost":{"gold":100,"wood":30},                      "effect":{"gold":15},                  "upkeep":{},         "terrain":"",                       "tech":0.0,"desc":"Gold income each tick."},
    {"key":"port",            "name":"Port",             "tier":1,"cost":{"gold":150,"wood":80},                      "effect":{"gold":10},                  "upkeep":{"gold":2}, "terrain":"", "tech":0.0,"desc":"Trade gold on coastal/water provinces."},
    {"key":"fort",            "name":"Fort",             "tier":1,"cost":{"gold":200,"stone":80,"clay":40},           "effect":{},                           "upkeep":{"gold":5}, "terrain":"",                       "tech":0.0,"desc":"+1 fortification. Requires clay."},
    {"key":"university",      "name":"University",       "tier":3,"cost":{"gold":500,"stone":100,"wood":50,"clay":60},"effect":{"universal_knowledge":1},   "upkeep":{"gold":10},"terrain":"",                       "tech":5.0,"desc":"Universal Knowledge each tick. Requires clay. Tech 5."},
    {"key":"algae_farm",      "name":"Algae Farm",       "tier":3,"cost":{"gold":400,"wood":60},                      "effect":{"algae":1},                  "upkeep":{"gold":8}, "terrain":"wetland",        "tech":6.0,"desc":"Rare Algae. Requires tech 6."},
]
BUILDING_TRANSLATIONS_PL = {
    "farm":           ("Farma",           "Produkuje żywność na równinach i trawiastych terenach."),
    "fishing_wharf":  ("Przystań Rybacka","Produkuje żywność w prowincjach przybrzeżnych."),
    "plantation":     ("Plantacja",       "Żywność i przyprawy w tropikalnych/leśnych prowincjach."),
    "pasture":        ("Pastwisko",       "Żywność i konie na otwartym terenie."),
    "lumber_camp":    ("Obóz Drwali",     "Drewno z lasów i tajgi."),
    "mine":           ("Kopalnia",        "Żelazo, kamień i węgiel z wzgórz i gór."),
    "copper_mine":    ("Kopalnia Miedzi", "Miedź z wzgórz i gór."),
    "clay_pit":       ("Glinianka",       "Glina z mokradeł i równin."),
    "tar_works":      ("Smolarnia",       "Smoła z lasów i mokradeł."),
    "powder_mill":    ("Młyn Prochowy",   "Proch. Wymaga węgla+miedzi+żelaza. Tech 4."),
    "cannon_foundry": ("Ludwisarnia",     "Więcej prochu, zużywa żelazo. Wymaga węgla+miedzi. Tech 4."),
    "textile_mill":   ("Tkacalnia",       "Sukno. Wymaga tech 3."),
    "silk_workshop":  ("Warsztat Jedwabiu","Produkcja jedwabiu. Wymaga sukna. Tech 3."),
    "market":         ("Rynek",           "Dochód złota każdy tick."),
    "port":           ("Port",            "Złoto handlowe w prowincjach przybrzeżnych."),
    "fort":           ("Fort",            "+1 fortyfikacja. Wymaga gliny."),
    "university":     ("Uniwersytet",     "Powszechna Wiedza każdy tick. Wymaga gliny. Tech 5."),
    "algae_farm":     ("Farma Alg",       "Rzadkie Algi. Wymaga tech 6."),
}
# ---------------------------------------------------------------------------
# Pure helper functions (no discord imports needed)
# ---------------------------------------------------------------------------
def _lang(interaction):
    locale = interaction.locale.value if interaction.locale else None
    return i18n.get_user_language(interaction.user.id, locale)

def _gm(interaction):
    return gm_only(interaction)

def _nation_owner(uid):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE owner_id=?", (str(uid),))
        return c.fetchone()

def _nation_name(name):
    with db.cursor() as c:
        c.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (name,))
        return c.fetchone()

def _bdef(key):
    key = i18n.normalize_key(key)
    with db.cursor() as c:
        c.execute("SELECT * FROM building_defs WHERE key=?", (key.lower(),))
        return c.fetchone()


def _building_label(row, lang=None):
    """Only localize built-in names; retain custom names entered by the GM."""
    default = next((b for b in DEFAULT_BUILDINGS if b['key'] == row['key']), None)
    if default and row['name'] == default['name']:
        return i18n.term(row['key'], lang)
    return row['name']


def _terrain_label(value):
    return ', '.join(i18n.term(t.strip()) for t in value.split(','))


@i18n.localized
async def _building_choices(interaction: discord.Interaction, current: str):
    with db.cursor() as c:
        c.execute("SELECT * FROM building_defs ORDER BY tier, name")
        rows = c.fetchall()
    needle = current.casefold()
    return [app_commands.Choice(name=_building_label(row)[:100], value=row['key'])
            for row in rows if needle in _building_label(row).casefold() or needle in row['key']][:25]

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

def _tech_ok(nation: dict, req_tech: float, building_key: str = "") -> bool:
    if not req_tech or req_tech <= 0:
        return True

    tech_data = json.loads(nation.get("tech_json", "{}"))
    
    # Przypisanie budynków do konkretnych kategorii technologii
    category_map = {
        # --- ECONOMY (Gospodarka i nauka) ---
        "farm": "economy",
        "pasture": "economy",
        "lumber_camp": "economy",
        "mine": "economy",
        "copper_mine": "economy",
        "clay_pit": "economy",
        "textile_mill": "economy",
        "silk_workshop": "economy",
        "market": "economy",
        "university": "economy",
        "algae_farm": "economy",

        # --- LAND (Militaria lądowe) ---
        "fort": "land",
        "powder_mill": "land",
        "cannon_foundry": "land",

        # --- NAVAL (Morskie i przybrzeżne) ---
        "port": "naval",
        "fishing_wharf": "naval",
        "tar_works": "naval",

        # --- COLONIAL (Egzotyczne i zamorskie) ---
        "plantation": "colonial",
    }
    
    # Pobranie odpowiedniej kategorii (domyślnie 'economy' w razie braku wpisu)
    cat = category_map.get(building_key.lower(), "economy")
    current_level = float(tech_data.get(cat, 3.0))

    return current_level >= float(req_tech)

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
        parts.append(f"+{v} {i18n.term(k)}")
    gold_once = effect.get("gold_once", 0)
    if gold_once:
        treasury += gold_once
        parts.append(i18n.text('+{p0} gold', p0=gold_once))
    stab = effect.get("stability", 0)
    if stab:
        stability = min(100.0, stability + stab)
        parts.append(i18n.text('+{p0} stability', p0=stab))
    with db.cursor() as c:
        c.execute(
            "UPDATE nations SET resources_json=?,treasury=?,stability=? WHERE id=?",
            (json.dumps(res), treasury, stability, nid)
        )
    entry = i18n.text("Megaproject '{p0}' completed.", p0=mp_name)
    if parts:
        entry += i18n.text(' Granted: {p0}.', p0=', '.join(parts))
    res_tick = effect.get("resources_per_tick", {})
    if res_tick:
        entry += i18n.text(' Ongoing: {p0}.', p0=', '.join((i18n.text('+{p0} {p1}/tick', p0=v, p1=i18n.term(k)) for k, v in res_tick.items())))
    special = effect.get("special_note", "")
    if special:
        entry += f" {special}"
    _log(nid, "system", entry)

def _seed_buildings():
    with db.cursor() as c:
        for b in DEFAULT_BUILDINGS:
            c.execute(
                """
                INSERT INTO building_defs 
                (key, name, tier, cost_json, effect_json, upkeep_json, requires_terrain, requires_tech, description) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET 
                    name = EXCLUDED.name,
                    tier = EXCLUDED.tier,
                    cost_json = EXCLUDED.cost_json,
                    effect_json = EXCLUDED.effect_json,
                    upkeep_json = EXCLUDED.upkeep_json,
                    requires_terrain = EXCLUDED.requires_terrain,
                    requires_tech = EXCLUDED.requires_tech,
                    description = EXCLUDED.description
                """,
                (
                    b["key"],
                    b["name"],
                    b["tier"],
                    json.dumps(b["cost"]),
                    json.dumps(b["effect"]),
                    json.dumps(b["upkeep"]),
                    b["terrain"],
                    b["tech"],
                    b["desc"],
                ),
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
        nid       = nat["id"]
        res       = json.loads(nat["resources_json"])
        treasury  = nat["treasury"]
        upkeep    = 0.0
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
            # Apply colony yield modifier if this is a colony province
            try:
                from cogs.colonialism import get_colony_yield_modifier
                col_mod = get_colony_yield_modifier(prov["id"])
            except Exception:
                col_mod = 1.0
            for k, v in base.items():
                res[k] = res.get(k, 0) + v * months * stab_mod * col_mod
            for bkey in json.loads(prov["buildings_json"]):
                bd = _bdef(bkey)
                if not bd:
                    continue
                for k, v in json.loads(bd["effect_json"]).items():
                    if k == "gold":
                        treasury += v * months * stab_mod * col_mod
                    else:
                        res[k] = res.get(k, 0) + v * months * stab_mod * col_mod
                upkeep += json.loads(bd["upkeep_json"]).get("gold", 0) * months

        # ---- MEGAPROJECTS (Yields processed BEFORE food consumption) ----
        with db.cursor() as c:
            c.execute(
                "SELECT * FROM megaprojects WHERE nation_id=? AND status IN ('building','complete')",
                (nid,)
            )
            mps = c.fetchall()
        for mp in mps:
            try:
                eff = json.loads(mp["effect_json"]) if mp["effect_json"] else {}
            except Exception:
                eff = {}

            # 1. Handle completed megaproject monthly output
            if mp["status"] == "complete":
                # Support nested resources_per_tick OR flat JSON structures (e.g. {"food": 500})
                per_tick = eff.get("resources_per_tick", {})
                if not per_tick and isinstance(eff, dict):
                    # Exclude non-resource keys
                    per_tick = {k: v for k, v in eff.items() if k not in ("gold", "gold_per_tick", "resources_per_tick")}

                for k, v in per_tick.items():
                    # Safeguard: Extract numeric value if v is a dictionary or string
                    if isinstance(v, dict):
                        val = v.get("amount", v.get("value", 0))
                    else:
                        val = v
                    
                    try:
                        amount = float(val)
                    except (ValueError, TypeError):
                        amount = 0

                    res[k] = res.get(k, 0) + (amount * months)
                
                # Safely extract gold/treasury output
                gold_val = eff.get("gold_per_tick", eff.get("gold", 0))
                if isinstance(gold_val, dict):
                    gold_val = gold_val.get("amount", gold_val.get("value", 0))
                try:
                    gold_amount = float(gold_val)
                except (ValueError, TypeError):
                    gold_amount = 0

                treasury += gold_amount * months

            # 2. Handle ongoing megaproject construction
            elif mp["status"] == "building" and mp.get("duration_months", 0) > 0:
                new_spent = min(mp.get("months_spent", 0) + months, mp["duration_months"])
                if new_spent >= mp["duration_months"]:
                    with db.cursor() as c:
                        # Fixed PostgreSQL placeholders (%s instead of ?)
                        c.execute(
                            "UPDATE megaprojects SET status='complete', months_spent=%s, "
                            "completed_at=NOW() WHERE id=%s",
                            (new_spent, mp["id"])
                        )
                    _apply_mp_effect(nid, mp["effect_json"], mp["name"])
                    
                    # Refetch resources & treasury to catch instant payout changes from _apply_mp_effect
                    with db.cursor() as c:
                        # Fixed PostgreSQL placeholders (%s instead of ?)
                        c.execute("SELECT resources_json, treasury FROM nations WHERE id=%s", (nid,))
                        updated_nat = c.fetchone()
                        if updated_nat:
                            res_raw = updated_nat["resources_json"]
                            res = json.loads(res_raw) if isinstance(res_raw, str) else res_raw
                            treasury = updated_nat["treasury"]
                else:
                    with db.cursor() as c:
                        # Fixed PostgreSQL placeholders (%s instead of ?)
                        c.execute(
                            "UPDATE megaprojects SET months_spent=%s WHERE id=%s",
                            (new_spent, mp["id"])
                        )
        # Military upkeep
        try:
            from cogs.military import compute_military_upkeep
            upkeep += compute_military_upkeep(nid) * months
        except Exception:
            pass

        # Colony progress must not be blocked by a failure in trade-route income.
        try:
            from cogs.colonialism import tick_colonies
            tick_colonies(nid, months)
        except Exception as e:
            print(f"[TICK] Colony error for nation {nid}: {e}", flush=True)
        try:
            from cogs.colonialism import compute_trade_route_income
            treasury += compute_trade_route_income(nid) * months
        except Exception as e:
            print(f"[TICK] Trade-route error for nation {nid}: {e}", flush=True)

        # ---- FOOD: feeds population + military ----
        food_needed = 0.0
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
            food_for_pop      = (total_pop / 100.0) * months
            food_for_military = (total_units / 10.0) * months
            food_needed       = food_for_pop + food_for_military
            food_have         = res.get("food", 0)

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
                     i18n.text('Food shortage! Needed {p0:.0f} (pop {p1:,} + {p2} units), had {p3:.0f}. Stability -{p4}.', p0=food_needed, p1=total_pop, p2=total_units, p3=food_have, p4=stab_penalty)
                     + (i18n.text(' Population declining.') if shortage_ratio < 0.5 else ""))
        except Exception as e:
            print(f"[TICK] Food calc error for nation {nid}: {e}", flush=True)

        # SILK + SPICES: luxury income (1 gold per 5 units held, capped at 50g/tick)
        silk_income   = min(50.0, res.get("silk",   0) / 5) * months
        spices_income = min(50.0, res.get("spices", 0) / 5) * months
        luxury_income = silk_income + spices_income
        if luxury_income > 0:
            treasury += luxury_income

        treasury = max(0.0, treasury - upkeep)
        with db.cursor() as c:
            c.execute(
                "UPDATE nations SET resources_json=?,treasury=?,population=? WHERE id=?",
                (json.dumps(res), treasury, total_pop, nid)
            )
        summary_parts = [i18n.text('{p0}: -{p1:.0f}g upkeep, {p2:.0f}g treasury', p0=nat['name'], p1=upkeep, p2=treasury)]
        if luxury_income > 0:
            summary_parts.append(i18n.text('+{p0:.0f}g luxury', p0=luxury_income))
        _log(nid, "system",
             i18n.text('Month {p0}/{p1}: upkeep -{p2:.0f}g', p0=month, p1=year, p2=upkeep)
             + (i18n.text(', luxury income +{p0:.0f}g', p0=luxury_income) if luxury_income > 0 else "")
             + i18n.text(', treasury {p0:.0f}g.', p0=treasury))
        summaries.append(", ".join(summary_parts))
    return month, year, summaries

# ---------------------------------------------------------------------------
# UI: paginated buildings list
# ---------------------------------------------------------------------------
class PageView(i18n.LocalizedView):
    def __init__(self, pages):
        super().__init__(timeout=120)
        self.pages = pages
        self.page  = 0
        self._refresh()

    def _refresh(self):
        self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = (self.page == len(self.pages) - 1)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    @i18n.localized
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    @i18n.localized
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
            ("/panel", "Open the private button-first player panel."),
            ("/panel_publish", "[GM] Post the permanent panel launcher in a channel."),
            ("/help", "Browse commands by section using the buttons below."),
            ("/language", "Set your preferred language (en / pl)."),
            ("/translate", "Choose a language; without parameters, enables Polish."),
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
    "colonialism": {
        "title": "🗺️ Colonialism & Trade Routes",
        "color": discord.Color.dark_green(),
        "fields": [
            ("/colony found <cell_id> <name>", "Found a colony on an unclaimed province (costs gold + fleet cargo)."),
            ("/colony develop <cell_id> <gold>", "Invest gold; the colony advances automatically when cost, time and technology are met."),
            ("/colony expand <source> <target> <name>", "Create an outpost on an unclaimed cell adjacent to a Settlement or larger (500g)."),
            ("/colony list [nation]", "List all colonies of a nation."),
            ("/colony view <cell_id>", "View detailed colony status and progress bars."),
            ("/traderoute add <from> <to> <name>", "Establish a trade route. Income = ship cargo × 2g/tick (or 20g flat)."),
            ("/traderoute remove <id>", "Remove a trade route."),
            ("/traderoute list", "List your trade routes and income per tick."),
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
            ("/event play <id>", "Resume an event: 3 choices or a custom response, 3 decisions maximum."),
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
    ("/colonymgr advance <cell_id>", "Manually retry a colony advancement (normal advancement is automatic)."),
    ("/colonymgr setback <cell_id>", "Set a colony back a stage."),
    ("/event generate <nation>", "Generate an AI event based on nation history and stats."),
    ("/event edit <id> <text>", "Edit an event draft before posting."),
    ("/event effects <id> <json>", "Set stat effects for an event draft."),
    ("/event post <id>", "Start a 3-decision event; effects apply at the end."),
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
HELP_SECTIONS_PL = {
    "general": {
        "title": "📖 Ogólne",
        "color": discord.Color.blurple(),
        "fields": [
            ("/panel", "Otwórz prywatny panel gracza obsługiwany przyciskami."),
            ("/panel_publish", "[GM] Opublikuj na kanale stały przycisk otwierający panel."),
            ("/help", "Przeglądaj komendy używając przycisków poniżej."),
            ("/language", "Ustaw preferowany język odpowiedzi bota (en / pl)."),
            ("/translate", "Włącz polski bez parametrów albo wybierz język z listy."),
            ("/calendar status", "Sprawdź aktualną datę w grze."),
            ("/activate <klucz>", "Aktywuj bota na tym serwerze (tylko GM)."),
        ],
    },
    "nation": {
        "title": "🏳️ Naród",
        "color": discord.Color.blue(),
        "fields": [
            ("/nation found <nazwa>", "Załóż swój naród (wymaga historii założenia)."),
            ("/nation stats [nazwa]", "Statystyki narodu. Puste = twój naród."),
            ("/nation list", "Lista wszystkich narodów."),
            ("/nation history <nazwa>", "Publiczna historia narodu."),
            ("/nation delete <nazwa>", "[GM] Usuń naród z potwierdzeniem."),
        ],
    },
    "province": {
        "title": "🗺️ Prowincje",
        "color": discord.Color.green(),
        "fields": [
            ("/province info <id>", "Szczegóły prowincji po ID komórki Azgaar."),
            ("/province list <naród>", "Lista prowincji należących do narodu."),
            ("/province yield [naród]", "Łączna produkcja zasobów ze wszystkich prowincji na tick."),
        ],
    },
    "economy": {
        "title": "💰 Gospodarka",
        "color": discord.Color.gold(),
        "fields": [
            ("/resources", "Zasoby, skarbiec, status żywności i populacja."),
            ("/build <id> <klucz>", "Wybuduj budynek w prowincji."),
            ("/buildings list", "Lista wszystkich typów budynków z kosztami i efektami."),
            ("/buildings province <id>", "Lista budynków w konkretnej prowincji."),
            ("/megaproject propose", "Zaproponuj megaprojekt do zatwierdzenia przez GM."),
            ("/megaproject build <id>", "Zapłać i rozpocznij zatwierdzony megaprojekt."),
            ("/megaproject list", "Lista twoich megaprojektów."),
            ("/tech status", "Poziomy technologii twojego narodu (prywatne)."),
            ("/tech research <kategoria>", "Wydaj złoto + Powszechną Wiedzę aby rozwinąć technologię."),
            ("Mechaniki zasobów",
             "• **Żywność**: zużywana przez populację (1/100) + wojsko (1/10) na tick.\n"
             "• **Jedwab + Przyprawy**: dochód luksusowy (1g/5 jednostek, max 50g/tick każdy).\n"
             "• **Sukno**: zużywane przy budowie jednostek lądowych (1 na 5 jednostek).\n"
             "• **Węgiel + Miedź**: wymagane do Młyna Prochowego i Ludwisarni.\n"
             "• **Glina**: wymagana do budowy Fortu i Uniwersytetu.\n"
             "• **Decay**: zapasy >500 tracą 2%/tick (oprócz żywności/złota/PW/alg)."),
        ],
    },
    "trade": {
        "title": "🤝 Handel",
        "color": discord.Color.orange(),
        "fields": [
            ("/trade offer <naród>", "Zaproponuj handel (notatka publiczna + warunki prywatne)."),
            ("/trade accept <id>", "Zaakceptuj ofertę handlową."),
            ("/trade cancel <id>", "Anuluj lub odrzuć handel."),
            ("/trade list", "Lista twoich oczekujących ofert handlowych."),
            ("/trade view <id>", "Szczegóły transakcji (warunki prywatne widoczne dla stron i GM)."),
        ],
    },
    "military": {
        "title": "⚔️ Wojsko",
        "color": discord.Color.dark_red(),
        "fields": [
            ("/blueprint design_ship <nazwa> <kadłub>", "Zaprojektuj okręt z interaktywnymi przyciskami modułów."),
            ("/blueprint create_unit <nazwa> <typ>", "Utwórz projekt jednostki lądowej."),
            ("/blueprint list", "Lista twoich projektów ze statystykami i utrzymaniem."),
            ("/blueprint delete <id>", "Usuń projekt."),
            ("/military build <id> <ilość> [id_komórki]", "Zbuduj jednostki. Bez id_komórki = pływające."),
            ("/military list", "Twoje siły zbrojne — armia, marynarka i pływające (prywatne)."),
            ("/military move <id_grupy> <id_komórki>", "Przypisz grupę jednostek do prowincji."),
            ("/military unassign <id_grupy>", "Cofnij przydział grupy do puli pływającej."),
            ("/military disband <id_grupy>", "Rozwiąż grupę jednostek na stałe."),
        ],
    },
    "combat": {
        "title": "🗡️ Bitwy i Dyplomacja",
        "color": discord.Color.dark_orange(),
        "fields": [
            ("/battle plan", "Wyślij plan bitwy — lokalizacja (tekst), rozkazy, opcjonalne ID jednostek i URL mapy."),
            ("/battle view <id>", "Szczegóły bitwy. Plany prywatne dla stron i GM."),
            ("/diplomacy war <naród>", "Wypowiedz wojnę. Utrzymanie rośnie do ×3 natychmiast."),
            ("/diplomacy peace <naród>", "Zawrzyj pokój z narodem z którym jesteś w stanie wojny."),
            ("/diplomacy alliance <naród>", "Zaproponuj sojusz innemu narodowi."),
            ("/diplomacy status", "Twoje relacje dyplomatyczne."),
            ("/diplomacy public", "Mapa dyplomatyczna świata — wszystkie aktywne wojny i sojusze."),
            ("/event list [naród]", "Lista opublikowanych eventów dla narodu."),
            ("/event play <id>", "Wznów event: 3 opcje lub własna odpowiedź, maksymalnie 3 decyzje."),
        ],
    },
    "colonialism": {
        "title": "🗺️ Kolonializm i Szlaki Handlowe",
        "color": discord.Color.dark_green(),
        "fields": [
            ("/colony found <id> <nazwa>", "Załóż kolonię na niezajętej prowincji (koszt: złoto + ładowność floty)."),
            ("/colony develop <id> <złoto>", "Zainwestuj złoto; kolonia awansuje automatycznie po spełnieniu kosztu, czasu i technologii."),
            ("/colony expand <źródło> <cel> <nazwa>", "Utwórz placówkę na niezajętym polu sąsiadującym z osadą lub większą kolonią (500 złota)."),
            ("/colony list [naród]", "Lista wszystkich kolonii narodu."),
            ("/colony view <id>", "Szczegóły kolonii z paskami postępu."),
            ("/traderoute add <od> <do> <nazwa>", "Ustanów szlak handlowy. Dochód = ładowność statków ×2g/tick."),
            ("/traderoute remove <id>", "Usuń szlak handlowy."),
            ("/traderoute list", "Lista twoich szlaków handlowych i dochód na tick."),
        ],
    },
}

GM_HELP_FIELDS_PL = [
    ("/nation history_add", "Dodaj ręcznie wpis do historii narodu."),
    ("/nation delete <nazwa>", "Usuń naród z potwierdzeniem (prowincje zwolnione)."),
    ("/province claim", "Przyznaj prowincje narodowi po ID komórek."),
    ("/province unclaim", "Usuń własność prowincji."),
    ("/admin map_import", "Importuj pełny eksport JSON z Azgaar."),
    ("/admin map_resync", "Ponowny import zaktualizowanej mapy z zachowaniem własności."),
    ("/admin map_export_markers", "Generuj skrypt JS z markerami zasobów do Azgaar."),
    ("/admineco tick [miesiące]", "Ręcznie uruchom tick zasobów."),
    ("/admineco grant", "Dodaj zasoby lub złoto do narodu (logowane)."),
    ("/admineco starter_pack [naród|all]", "Daj zestaw startowy jednemu lub wszystkim narodom."),
    ("/admineco tech_set", "Ustaw poziom technologii narodu bezpośrednio."),
    ("/admineco mp_approve", "Zatwierdź megaprojekt z efektami, kosztem i czasem budowy."),
    ("/admineco mp_advance", "Przyspiesz budowę megaprojektu o N miesięcy."),
    ("/admineco building_set", "Edytuj definicję budynku na żywo."),
    ("/admineco building_new", "Dodaj nowy typ budynku."),
    ("/admineco relation_set", "Ustaw relację dyplomatyczną między narodami bezpośrednio."),
    ("/colonymgr advance <id>", "Ręcznie ponów awans kolonii (standardowo awans jest automatyczny)."),
    ("/colonymgr setback <id>", "Cofnij kolonię o etap."),
    ("/event generate <naród>", "Generuj event AI na podstawie historii i statystyk narodu."),
    ("/event edit <id> <tekst>", "Edytuj szkic eventu przed publikacją."),
    ("/event effects <id> <json>", "Ustaw efekty statystyk dla eventu."),
    ("/event post <id>", "Rozpocznij event z 3 decyzjami; efekty dopiero na końcu."),
    ("/battle plans_pending", "Lista wszystkich niedopasowanych planów bitew."),
    ("/battle match <plan_atk> <plan_def>", "Dopasuj dwa plany — wybierz kto atakuje, kto broni."),
    ("/battle resolve <id>", "Pobierz modyfikator AI i rozstrzygnij bitwę."),
    ("/calendar set", "Skonfiguruj prędkość kalendarza, kanał i datę startową."),
    ("/calendar start/stop", "Uruchom lub zatrzymaj kalendarz."),
]
class HelpView(i18n.LocalizedView):
    PLAYER_KEYS = ["general", "nation", "province", "economy", "trade", "military", "combat", "colonialism"]

    def __init__(self, is_gm: bool, current: str = "general", lang: str = None):
        super().__init__(timeout=180)
        self.is_gm   = is_gm
        self.current = current
        self.lang    = lang
        self.page = 0
        self._build()

    def _build(self):
        self.clear_items()
        labels = {"general":"General", "nation":"Nation", "province":"Province", "economy":"Economy",
                  "trade":"Trade", "military":"Military", "combat":"Combat", "colonialism":"Colonialism"}
        for key, label in labels.items():
            btn = discord.ui.Button(
                label=i18n.text(label, lang=self.lang),
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
        if self.current == "gm":
            fields = GM_HELP_FIELDS_PL if self.lang == "pl" else GM_HELP_FIELDS
            count = (len(fields) + 19) // 20
            for label, offset in (("◀", -1), ("▶", 1)):
                btn = discord.ui.Button(label=label, row=2,
                    disabled=not 0 <= self.page + offset < count)
                btn.callback = self._page_cb(offset)
                self.add_item(btn)

    def _page_cb(self, offset):
        @i18n.localized
        async def callback(interaction):
            if not _gm(interaction):
                await interaction.response.send_message(i18n.t(self.lang, "gm_only"), ephemeral=True)
                return
            fields = GM_HELP_FIELDS_PL if self.lang == "pl" else GM_HELP_FIELDS
            self.page = max(0, min((len(fields) - 1) // 20, self.page + offset))
            self._build()
            await interaction.response.edit_message(embed=self._embed(), view=self)
        return callback

    def _cb(self, key: str):
        @i18n.localized
        async def callback(interaction: discord.Interaction):
            if key == "gm" and not _gm(interaction):
                await interaction.response.send_message(i18n.t(self.lang, "gm_only"), ephemeral=True)
                return
            self.current = key
            self.page = 0
            self._build()
            embed = self._embed()
            await interaction.response.edit_message(embed=embed, view=self)
        return callback

    def _embed(self) -> discord.Embed:
        sections = HELP_SECTIONS_PL if self.lang == "pl" else HELP_SECTIONS
        gm_fields = GM_HELP_FIELDS_PL if self.lang == "pl" else GM_HELP_FIELDS
        if self.current == "gm":
            e = discord.Embed(
                title="🔐 Komendy GM" if self.lang == "pl" else i18n.text('🔐 GM Commands'),
                color=discord.Color.red()
            )
            for name, value in gm_fields[self.page * 20:(self.page + 1) * 20]:
                e.add_field(name=name, value=value, inline=False)
            e.set_footer(text=f"{self.page + 1} / {(len(gm_fields) + 19) // 20}")
            return e
        data = sections[self.current]
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
            # Check if calendar is active
            if _cfg("calendar_running", "0") != "1":
                return
            
            hpm = float(_cfg("hours_per_month", "24"))
            last_str = _cfg("last_tick_ts")
            if not last_str:
                print("[CALENDAR] No last_tick_ts set — run /calendar start", flush=True)
                return

            now_utc = datetime.now(timezone.utc)
            elapsed = (now_utc - datetime.fromisoformat(last_str)).total_seconds() / 3600
            if elapsed < hpm:
                return

            months = max(1, int(elapsed / hpm))
            # Cap catch-up to 3 months max per loop iteration
            months = min(months, 3)
            
            print(f"[CALENDAR] {months} month(s) elapsed, running tick...", flush=True)

            ch_id = _cfg("announce_channel_id")
            print(f"[CALENDAR] Announce channel ID: {ch_id!r}", flush=True)
            ch = self.bot.get_channel(int(ch_id)) if ch_id else None
            print(f"[CALENDAR] Channel object: {ch}", flush=True)

            # Process each month tick
            for i in range(months):
                month, year, summaries = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _run_tick(1)
                )
                print(f"[CALENDAR] Month {month}/{year} ticked. Summaries: {summaries}", flush=True)

                if not ch:
                    print("[CALENDAR] No channel found — skipping announcement.", flush=True)
                    continue

                # Safely get language locale without relying on undefined interaction
                lang = config.DEFAULT_LANGUAGE
                mname = _month_name(month, lang)

                # Original embed formatting
                embed = discord.Embed(
                    title=i18n.text('📅 New Month: {p0}, Year {p1}', p0=mname, p1=year),
                    description=i18n.text('A new month has begun. Nations have collected their income.'),
                    color=discord.Color.gold(),
                )
                if summaries:
                    embed.add_field(
                        name=i18n.text('⚙️ Resource Tick'),
                        value="\n".join(summaries[:20]),
                        inline=False,
                    )
                try:
                    await ch.send(embed=embed)
                    print(f"[CALENDAR] Announcement sent to #{ch.name}", flush=True)
                except Exception as send_err:
                    print(f"[CALENDAR] Failed to send announcement: {send_err}", flush=True)

            # Update last_tick_ts after ticks succeed
            _cfg_set("last_tick_ts", now_utc.isoformat())

        except (psycopg2.OperationalError, psycopg2.DatabaseError, psycopg2.Error) as db_err:
            print(f"[CALENDAR WARNING] Temporary DB disconnect, retrying next cycle: {db_err}", flush=True)
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
    @i18n.localized
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
                "SELECT id, buildings_json, base_resources_json FROM provinces "
                "WHERE owner_nation_id=? AND active=1",
                (n["id"],)
            )
            prod_provs = c.fetchall()
        for pp in prod_provs:
            try:
                from cogs.colonialism import get_colony_yield_modifier
                col_mod = get_colony_yield_modifier(pp["id"])
            except Exception:
                col_mod = 1.0
            base = json.loads(pp["base_resources_json"])
            food_prod += base.get("food", 0) * col_mod
            for bkey in json.loads(pp["buildings_json"]):
                bd = _bdef(bkey)
                if bd:
                    food_prod += json.loads(bd["effect_json"]).get("food", 0) * col_mod

       # Ensure all variables used in formatting are populated
        food_have = res.get("food", 0)
        food_for_pop = (total_pop / 100.0)
        food_for_military = (total_units / 10.0)
        food_needed = food_for_pop + food_for_military
        food_balance = food_prod - food_needed

        if total_pop == 0 and total_units == 0:
            food_status = i18n.text('✅ No population ({p0:.0f} stored, +{p1:.0f}/tick)', p0=food_have, p1=food_prod)

        if food_needed > 0:
            food_ratio = food_have / food_needed if food_have > 0 else 0
            if food_ratio >= 1.2:
                food_status = i18n.text('✅ Well-fed ({p0:.0f} stored)', p0=food_have)
            elif food_ratio >= 1.0:
                food_status = i18n.text('🟡 Sufficient ({p0:.0f} stored)', p0=food_have)
            elif food_ratio >= 0.5:
                food_status = i18n.text('🟠 Shortage ({p0:.0f} stored) — stability declining', p0=food_have)
            else:
                food_status = i18n.text('🔴 Severe shortage ({p0:.0f} stored) — population declining', p0=food_have)
            food_status += (
                i18n.text('\nNeeds: {p0:.0f}/tick | Produces: {p1:.0f}/tick | Balance: {p2:+.0f}/tick', p0=food_needed, p1=food_prod, p2=food_balance)
            )
        else:
            food_status = i18n.text('✅ No population ({p0:.0f} stored, +{p1:.0f}/tick)', p0=food_have, p1=food_prod)

        # Luxury income preview
        silk_income   = min(50.0, res.get("silk",   0) / 5)
        spices_income = min(50.0, res.get("spices", 0) / 5)
        luxury_income = silk_income + spices_income

        # Build resource display — exclude food (shown separately)
        other_res = {k: v for k, v in sorted(res.items()) if k != "food" and v > 0}
        desc = "\n".join(f"**{i18n.term(k)}**: {v:,.1f}"
                         for k, v in other_res.items()) or i18n.text('*No resources yet.*')

        embed = flagged_embed(discord.Embed(
            title=i18n.text('{p0} {p1} — Resources', p0=flag_text(n['flag']), p1=n['name']).strip(),
            description=desc,
            color=discord.Color.green(),
        ), (n['flag'], n['name']))
        embed.add_field(name=i18n.text('🌾 Food'), value=food_status, inline=False)
        embed.add_field(name=i18n.text('💰 Treasury'), value=i18n.text('{p0:,.0f} gold', p0=n['treasury']), inline=True)
        if luxury_income > 0:
            embed.add_field(
                name=i18n.text('💎 Luxury Income'),
                value=i18n.text('+{p0:.0f}g/tick (silk+spices)', p0=luxury_income),
                inline=True,
            )
        embed.add_field(
            name=i18n.text('👥 Population'),
            value=i18n.text('{p0:,} total | {p1} military units', p0=total_pop, p1=total_units),
            inline=True,
        )
        embed.set_footer(
            text=i18n.text('In-game: Month {p0}, Year {p1}', p0=_cfg('current_month', '?'), p1=_cfg('current_year', '?'))
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="build", description="Construct a building / Buduj w prowincji")
    @app_commands.describe(cell_id="Province cell ID", building="Building key e.g. farm, mine")
    @app_commands.autocomplete(building=_building_choices)
    @i18n.localized
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
        building = bd['key']
        if not _terrain_ok(prov["terrain"], bd["requires_terrain"]):
            await interaction.response.send_message(
                i18n.t(lang, "build_wrong_terrain",
                        building=_building_label(bd), terrain=i18n.term(prov["terrain"])), ephemeral=True)
            return
        if not _tech_ok(n, bd["requires_tech"], bd["key"]):
            await interaction.response.send_message(
                i18n.t(lang, "build_need_tech",
                        building=_building_label(bd), level=bd["requires_tech"]), ephemeral=True)
            return
        bldgs = json.loads(prov["buildings_json"])
        if building.lower() in bldgs:
            await interaction.response.send_message(
                i18n.t(lang, "build_already_exists", building=_building_label(bd)), ephemeral=True)
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
                i18n.t(lang, "build_no_resource", resource=i18n.term(missing)), ephemeral=True)
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
        _log(n["id"], "system", i18n.text('Built {p0} in province {p1}.', p0=_building_label(bd), p1=prov['name'] or cell_id))
        effects  = json.loads(bd["effect_json"])
        eff_str  = ", ".join(i18n.text('+{p0} {p1}/month', p0=v, p1=i18n.term(k)) for k, v in effects.items()) or i18n.term("special")
        embed = discord.Embed(
            title=i18n.t(lang, "build_success_title"),
            description=i18n.t(lang, "build_success_desc",
                                building=_building_label(bd),
                                province=prov["name"] or i18n.text('Cell #{p0}', p0=cell_id),
                                effects=eff_str),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    # ======================================================================
    # BUILDINGS GROUP
    # ======================================================================

    @buildings_grp.command(name="list", description="Browse building types / Lista budynkow")
    @i18n.localized
    async def buildings_list(self, interaction: discord.Interaction):
        with db.cursor() as c:
            c.execute("SELECT * FROM building_defs ORDER BY tier, name")
            rows = c.fetchall()
        pages = []
        lang = _lang(interaction)
        title_base = "🏗️ Dostępne Budynki" if lang == "pl" else i18n.text('🏗️ Available Buildings')
        cur_embed = discord.Embed(
                    title=f"{title_base} ({len(pages)+1}/…)",
                    color=discord.Color.blue(),
                )
        for i, b in enumerate(rows):
            cost_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in json.loads(b["cost_json"]).items())
            eff_str  = ", ".join(i18n.text('+{p0} {p1}/tick', p0=v, p1=i18n.term(k)) for k, v in json.loads(b["effect_json"]).items()) or i18n.term("special")
            terrain  = b["requires_terrain"] or "any"
            tech     = i18n.text(' | Tech≥{p0}', p0=b['requires_tech']) if b["requires_tech"] > 0 else ""
            lang = _lang(interaction)
            if lang == "pl" and b["key"] in BUILDING_TRANSLATIONS_PL:
                bname, bdesc = BUILDING_TRANSLATIONS_PL[b["key"]]
            else:
                bname, bdesc = b["name"], b["description"]
            terrain_label = _terrain_label(terrain)
            cur_embed.add_field(
                name=f"T{b['tier']} `{b['key']}` — {bname}",
                value=(
                    f"{bdesc}\n"
                    f"{'Koszt' if lang=='pl' else i18n.text('Cost')}: {cost_str} | "
                    f"{'Teren' if lang=='pl' else i18n.text('Terrain')}: {terrain_label}{tech}\n"
                    f"{'Produkuje' if lang=='pl' else i18n.text('Produces')}: {eff_str}"
                ),
                inline=False,
            )
            if (i + 1) % 5 == 0:
                pages.append(cur_embed)
                cur_embed = discord.Embed(
                    title=i18n.text('🏗️ Available Buildings ({p0}/…)', p0=len(pages) + 1),
                    color=discord.Color.blue(),
                )
        if cur_embed.fields:
            pages.append(cur_embed)
        for idx, p in enumerate(pages):
            p.title = f"{title_base} ({idx+1}/{len(pages)})"
        view = PageView(pages)
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)

    @buildings_grp.command(name="province", description="Buildings in a province / Budynki w prowincji")
    @app_commands.describe(cell_id="Province cell ID")
    @i18n.localized
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
                i18n.text('No buildings in **{p0}**.', p0=prov['name'] or i18n.text('Cell #{p0}', p0=cell_id)), ephemeral=True)
            return
        lines = []
        for bkey in bldgs:
            bd  = _bdef(bkey)
            nm  = _building_label(bd) if bd else bkey
            eff = json.loads(bd["effect_json"]) if bd else {}
            lines.append(
                f"**{nm}** — "
                + (", ".join(i18n.text('+{p0} {p1}/tick', p0=v, p1=i18n.term(k)) for k, v in eff.items()) or i18n.term("special"))
            )
        embed = discord.Embed(
            title=i18n.text('Buildings — {p0}', p0=prov['name'] or i18n.text('Cell #{p0}', p0=cell_id)),
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.add_field(name=i18n.text('Fortification'), value=str(prov["fortification_level"]), inline=True)
        await interaction.response.send_message(embed=embed)

    # ======================================================================
    # MEGAPROJECT GROUP
    # ======================================================================

    @mp_grp.command(name="propose", description="Propose a megaproject / Zaproponuj megaprojekt")
    @app_commands.describe(name="Project name", effect="Desired effect", gold_budget="Gold budget")
    @i18n.localized
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
             i18n.text("Proposed megaproject '{p0}' (#{p1}): {p2}. Budget: {p3}g.", p0=name, p1=mp_id, p2=effect, p3=gold_budget))
        embed = discord.Embed(
            title=i18n.text('Megaproject Proposed'),
            description=(
                i18n.text('**{p0}** (ID: {p1})\n{p2}\nBudget: {p3:,} gold\n\nAwaiting GM approval.', p0=name, p1=mp_id, p2=effect, p3=gold_budget)
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @mp_grp.command(name="build", description="Start building an approved megaproject / Rozpocznij budowe")
    @app_commands.describe(mp_id="Megaproject ID")
    @i18n.localized
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
            await interaction.response.send_message(i18n.text('Megaproject #{p0} not found.', p0=mp_id), ephemeral=True)
            return
        if mp["status"] != "approved":
            await interaction.response.send_message(
                i18n.text('Megaproject #{p0} is **{p1}** — only approved projects can be started.', p0=mp_id, p1=i18n.term(mp['status'])),
                ephemeral=True)
            return
        cost      = json.loads(mp["cost_json"])
        gold_cost = cost.get("gold", 0)
        if nat["treasury"] < gold_cost:
            await interaction.response.send_message(
                i18n.text('Not enough gold. Need **{p0:,}g**, have **{p1:,.0f}g**.', p0=gold_cost, p1=nat['treasury']),
                ephemeral=True)
            return
        res = json.loads(nat["resources_json"])
        ok, missing = _deduct(res, cost)
        if not ok:
            await interaction.response.send_message(i18n.text('Not enough **{p0}**.', p0=i18n.term(missing)), ephemeral=True)
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
            _log(nat["id"], "system", i18n.text("Megaproject '{p0}' completed instantly.", p0=mp['name']))
            await interaction.response.send_message(
                i18n.text('✅ **{p0}** built and completed! Effects applied.', p0=mp['name']), ephemeral=False)
        else:
            _log(nat["id"], "player",
                 i18n.text("Started construction of megaproject '{p0}' ({p1} months). Cost paid.", p0=mp['name'], p1=mp['duration_months']))
            await interaction.response.send_message(
                i18n.text('🔨 **{p0}** construction started! Estimated completion: **{p1}** in-game month(s).', p0=mp['name'], p1=mp['duration_months']),
                ephemeral=False)

    @mp_grp.command(name="list", description="List your megaprojects / Lista megaprojektow")
    @i18n.localized
    async def mp_list(self, interaction: discord.Interaction):
        lang  = _lang(interaction)
        is_gm = _gm(interaction)
        n     = _nation_owner(str(interaction.user.id))
        
        with db.cursor() as c:
            if is_gm:
                c.execute(
                    "SELECT m.*, na.name as nname FROM megaprojects m "
                    "JOIN nations na ON m.nation_id=na.id ORDER BY m.id DESC"
                )
            elif n:
                c.execute(
                    "SELECT m.*, na.name as nname FROM megaprojects m "
                    "JOIN nations na ON m.nation_id=na.id "
                    "WHERE m.nation_id=%s ORDER BY m.id DESC",
                    (n["id"],)
                )
            else:
                await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
                return
            rows = c.fetchall()

        if not rows:
            await interaction.response.send_message(i18n.text('No megaprojects found.'), ephemeral=True)
            return

        EMOJI = {"proposed": "🟡", "approved": "🟢", "building": "🔨", "complete": "✅"}
        lines = []
        truncated_count = 0
        current_len = 0
        MAX_EMBED_CHAR_LIMIT = 3900  # Leave buffer space for title/formatting

        for idx, r in enumerate(rows):
            # Parse cost safely
            cost_raw = r["cost_json"]
            if isinstance(cost_raw, str):
                try:
                    cost = json.loads(cost_raw)
                except Exception:
                    cost = {}
            else:
                cost = cost_raw or {}

            # Progress calculation
            if r["status"] == "building" and r["duration_months"] > 0:
                pct  = int((r["months_spent"] / r["duration_months"]) * 100)
                bar  = "█" * (pct // 10) + "░" * (10 - (pct // 10))
                prog = i18n.text('\n  `[{p0}]` {p1}/{p2} months ({p3}%)', p0=bar, p1=r['months_spent'], p2=r['duration_months'], p3=pct)
            elif r["status"] == "building":
                prog = i18n.text(' (instant — pending completion)')
            else:
                prog = ""

            cost_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in cost.items()) if cost else i18n.text('None')
            entry = (
                i18n.text('{p0} **[{p1}] {p2}** ({p3}){p4}\n  {p5}\n  Cost: {p6} | {p7}', p0=EMOJI.get(r['status'], '❓'), p1=r['id'], p2=r['name'], p3=r['nname'], p4=prog, p5=r['proposed_effect'], p6=cost_str, p7=i18n.term(r['status']))
            )

            # Check if adding this entry exceeds Discord's embed description limit
            if current_len + len(entry) + 2 > MAX_EMBED_CHAR_LIMIT:
                truncated_count = len(rows) - idx
                break

            lines.append(entry)
            current_len += len(entry) + 2

        desc = "\n\n".join(lines)
        if truncated_count > 0:
            desc += i18n.text('\n\n*... and {p0} more megaprojects.*', p0=truncated_count)

        embed = discord.Embed(
            title=i18n.text('Megaprojects') + (i18n.text(' — All Nations') if is_gm else ""),
            description=desc,
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
    @i18n.localized
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
            await interaction.response.send_message(i18n.text('Cannot trade with yourself.'), ephemeral=True)
            return
        try:
            give_res = parse_resources(give_resources)
            recv_res = parse_resources(receive_resources)
            validate_gold(give_gold)
            validate_gold(receive_gold)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        trade_id = db.insert_returning_id(
                "INSERT INTO trades(from_nation_id,to_nation_id,offer_resources_json,offer_gold,"
                "receive_resources_json,receive_gold,public_note,private_note,status)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (fn["id"], tn["id"],
                 json.dumps(give_res), give_gold,
                 json.dumps(recv_res), receive_gold,
                 public_note, private_note, "pending")
            )
        _log(fn["id"], "player", i18n.text('Sent trade offer #{p0} to {p1}.', p0=trade_id, p1=tn['name']))
        give_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in give_res.items())
        if give_gold:
            give_str += i18n.text(', {p0:.0f} gold', p0=give_gold)
        recv_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in recv_res.items())
        if receive_gold:
            recv_str += i18n.text(', {p0:.0f} gold', p0=receive_gold)
        embed = discord.Embed(title=i18n.text('Trade Offer #{p0}', p0=trade_id), color=discord.Color.blue())
        embed.add_field(name=i18n.text('{p0} gives', p0=fn['name']), value=give_str or "—", inline=True)
        embed.add_field(name=i18n.text('{p0} gives', p0=tn['name']), value=recv_str or "—", inline=True)
        if public_note:
            embed.add_field(name=i18n.text('Public note'), value=public_note, inline=False)
        embed.set_footer(text=i18n.text('Use /trade accept {p0} to accept.', p0=trade_id))
        await interaction.response.send_message(embed=embed)
        # DM the receiver with private terms
        if interaction.guild:
            member = interaction.guild.get_member(int(tn["owner_id"]))
            if member:
                with i18n.using_language(i18n.get_user_language(tn["owner_id"])):
                    dm = discord.Embed(
                        title=i18n.text('Trade Offer #{p0} from {p1}', p0=trade_id, p1=fn['name']),
                        color=discord.Color.blue()
                    )
                    dm.add_field(name=i18n.text('They give'), value=i18n.resource_list(dict(give_res, **({"gold":give_gold} if give_gold else {}))), inline=True)
                    dm.add_field(name=i18n.text('They want'), value=i18n.resource_list(dict(recv_res, **({"gold":receive_gold} if receive_gold else {}))), inline=True)
                    if public_note:
                        dm.add_field(name=i18n.text('Public note'), value=public_note, inline=False)
                    if private_note:
                        dm.add_field(name=i18n.text('🔒 Private note'), value=private_note, inline=False)
                    dm.set_footer(text=i18n.text('Use /trade accept {p0} or /trade cancel {p1}', p0=trade_id, p1=trade_id))
                    try:
                        await member.send(embed=dm)
                    except discord.Forbidden:
                        pass

    @trade_grp.command(name="accept", description="Accept a trade / Zaakceptuj handel")
    @app_commands.describe(trade_id="Trade ID")
    @i18n.localized
    async def trade_accept(self, interaction: discord.Interaction, trade_id: int):
        await interaction.response.defer()
        try:
            trade, fn, tn, give_res, recv_res, give_gold, recv_gold = accept_trade(
                trade_id, interaction.user.id, _gm(interaction))
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        give_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in give_res.items())
        if give_gold:
            give_str += i18n.text(', {p0:.0f} gold', p0=give_gold)
        recv_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in recv_res.items())
        if recv_gold:
            recv_str += i18n.text(', {p0:.0f} gold', p0=recv_gold)
        log_priv = (i18n.text('Trade #{p0} with {p1}. Gave: {p2}. Received: {p3}.', p0=trade_id, p1=tn['name'], p2=give_str or '—', p3=recv_str or '—'))
        if trade["private_note"]:
            log_priv += i18n.text(' (Private: {p0})', p0=trade['private_note'])
        _log(fn["id"], "trade_private", log_priv)
        _log(tn["id"], "trade_private", log_priv)
        _log(fn["id"], "system", i18n.text('Trade #{p0} accepted by {p1}.', p0=trade_id, p1=tn['name']))
        _log(tn["id"], "system", i18n.text('Trade #{p0} accepted.', p0=trade_id))
        embed = discord.Embed(title=i18n.text('✅ Trade #{p0} Accepted', p0=trade_id), color=discord.Color.green())
        embed.add_field(name=i18n.text('{p0} gave', p0=fn['name']), value=give_str or "—", inline=True)
        embed.add_field(name=i18n.text('{p0} gave', p0=tn['name']), value=recv_str or "—", inline=True)
        if trade["public_note"]:
            embed.add_field(name=i18n.text('Note'), value=trade["public_note"], inline=False)
        await interaction.followup.send(embed=embed)

    @trade_grp.command(name="cancel", description="Cancel/decline a trade / Anuluj handel")
    @app_commands.describe(trade_id="Trade ID")
    @i18n.localized
    async def trade_cancel(self, interaction: discord.Interaction, trade_id: int):
        lang  = _lang(interaction)
        n     = _nation_owner(str(interaction.user.id))
        is_gm = _gm(interaction)
        with db.cursor() as c:
            c.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
            trade = c.fetchone()
        if not trade:
            await interaction.response.send_message(i18n.text('Trade #{p0} not found.', p0=trade_id), ephemeral=True)
            return
        if not is_gm and (not n or n["id"] not in (trade["from_nation_id"], trade["to_nation_id"])):
            await interaction.response.send_message(i18n.text('You are not party to this trade.'), ephemeral=True)
            return
        if trade["status"] != "pending":
            await interaction.response.send_message(
                i18n.text('Trade #{p0} is already {p1}.', p0=trade_id, p1=i18n.term(trade['status'])), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("UPDATE trades SET status='cancelled',resolved_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                      (trade_id,))
            if c.rowcount != 1:
                await interaction.response.send_message(i18n.text('Trade is no longer pending.'), ephemeral=True)
                return
        await interaction.response.send_message(i18n.text('Trade #{p0} cancelled.', p0=trade_id), ephemeral=True)

    @trade_grp.command(name="list", description="List pending trades / Lista ofert handlowych")
    @i18n.localized
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
            await interaction.response.send_message(i18n.text('No pending trades.'), ephemeral=True)
            return
        lines = []
        for r in rows:
            arrow = "→" if (n and r["from_nation_id"] == n["id"]) else "←"
            other = r["tname"] if (n and r["from_nation_id"] == n["id"]) else r["fname"]
            lines.append(f"**#{r['id']}** {arrow} **{other}** | {r['public_note'] or '—'}")
        embed = discord.Embed(
            title=i18n.text('Pending Trades'),
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=i18n.text('Use /trade view <id> for full details.'))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @trade_grp.command(name="view", description="View trade details / Szczegoly transakcji")
    @app_commands.describe(trade_id="Trade ID")
    @i18n.localized
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
            await interaction.response.send_message(i18n.text('Trade #{p0} not found.', p0=trade_id), ephemeral=True)
            return
        is_party = n and (t["from_nation_id"] == n["id"] or t["to_nation_id"] == n["id"])
        STATUS   = {"pending":"🟡","accepted":"✅","cancelled":"❌"}
        embed = flagged_embed(discord.Embed(
            title=i18n.text('{p0} Trade #{p1}', p0=STATUS.get(t['status'], '❓'), p1=trade_id),
            description=f"**{flag_text(t['fflag'])} {t['fname']}** ↔ **{flag_text(t['tflag'])} {t['tname']}**",
            color=discord.Color.orange(),
        ), (t['fflag'], t['fname']), (t['tflag'], t['tname']))
        embed.add_field(name=i18n.text('Status'), value=i18n.term(t["status"]), inline=True)
        embed.add_field(name=i18n.text('Date'),   value=short_date(t["created_at"]), inline=True)
        give_res  = json.loads(t["offer_resources_json"])
        recv_res  = json.loads(t["receive_resources_json"])
        give_str  = ", ".join(f"{v} {i18n.term(k)}" for k, v in give_res.items())
        if t["offer_gold"]:
            give_str += i18n.text(', {p0:.0f} gold', p0=t['offer_gold'])
        recv_str = ", ".join(f"{v} {i18n.term(k)}" for k, v in recv_res.items())
        if t["receive_gold"]:
            recv_str += i18n.text(', {p0:.0f} gold', p0=t['receive_gold'])
        embed.add_field(name=i18n.text('{p0} gives', p0=t['fname']), value=give_str or "—", inline=True)
        embed.add_field(name=i18n.text('{p0} gives', p0=t['tname']), value=recv_str or "—", inline=True)
        if t["public_note"]:
            embed.add_field(name=i18n.text('Public note'), value=t["public_note"], inline=False)
        if is_party or is_gm:
            embed.add_field(
                name=i18n.text('🔒 Private terms'),
                value=t["private_note"] or "—",
                inline=False,
            )
        else:
            embed.add_field(
                name=i18n.text('🔒 Private terms'),
                value=i18n.text('*Hidden — visible to parties and GM only.*'),
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
    @i18n.localized
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
            i18n.text('✅ Calendar: **{p0}h** IRL = 1 month → {p1}\nStarting: Month {p2}, Year {p3}\nUse `/calendar start` to begin.', p0=hours_per_month, p1=channel.mention, p2=start_month, p3=start_year),
            ephemeral=True,
        )

    @calendar_grp.command(name="start", description="[GM] Start the calendar / [GM] Uruchom kalendarz")
    @i18n.localized
    async def calendar_start(self, interaction: discord.Interaction):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        _cfg_set("calendar_running", "1")
        _cfg_set("last_tick_ts", datetime.now(timezone.utc).isoformat())
        await interaction.response.send_message(i18n.text('✅ Calendar started.'), ephemeral=True)

    @calendar_grp.command(name="stop", description="[GM] Pause the calendar / [GM] Zatrzymaj kalendarz")
    @i18n.localized
    async def calendar_stop(self, interaction: discord.Interaction):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        _cfg_set("calendar_running", "0")
        await interaction.response.send_message(i18n.text('⏸️ Calendar paused.'), ephemeral=True)

    @calendar_grp.command(name="status", description="Current in-game date / Aktualna data w grze")
    @i18n.localized
    async def calendar_status(self, interaction: discord.Interaction):
        month   = _cfg("current_month", "1")
        year    = _cfg("current_year",  "1")
        running = _cfg("calendar_running", "0") == "1"
        hpm     = _cfg("hours_per_month", "—")
        try:
            mname = _month_name(int(month), _lang(interaction))
        except (ValueError, IndexError):
            mname = i18n.text('Month {p0}', p0=month)
        embed = discord.Embed(title=i18n.text('📅 In-Game Calendar'), color=discord.Color.gold())
        embed.add_field(name=i18n.text('Current Date'), value=i18n.text('{p0}, Year {p1}', p0=mname, p1=year), inline=True)
        embed.add_field(name=i18n.text('Status'),       value=i18n.text('▶️ Running') if running else i18n.text('⏸️ Paused'), inline=True)
        embed.add_field(name=i18n.text('Speed'),        value=i18n.text('{p0}h IRL = 1 month', p0=hpm), inline=True)
        await interaction.response.send_message(embed=embed)

    # ======================================================================
    # ADMINECO GROUP
    # ======================================================================

    @admineco_grp.command(name="tick", description="[GM] Manual resource tick / [GM] Recznie uruchom tick")
    @app_commands.describe(months="Months to advance (default 1)")
    @i18n.localized
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
                        title=i18n.text('📅 New Month: {p0}, Year {p1}', p0=mname, p1=year),
                        description=i18n.text('A new month has begun. Nations have collected their income.'),
                        color=discord.Color.gold(),
                    )
                    if summaries:
                        embed.add_field(
                            name=i18n.text('⚙️ Resource Tick'),
                            value="\n".join(summaries[:20]),
                            inline=False,
                        )
                    try:
                        await ch.send(embed=embed)
                    except discord.Forbidden:
                        await interaction.followup.send(
                            i18n.text('⚠️ Tick ran but bot lacks **Send Messages** / **Embed Links** permission in <#{p0}>. Fix the channel permissions in Discord.', p0=ch.id),
                            ephemeral=True,
                        )
                    except Exception as send_err:
                        await interaction.followup.send(
                            i18n.text('⚠️ Tick ran but announcement failed: {p0}', p0=send_err), ephemeral=True
                        )
            report = "\n".join(summaries) if summaries else "No nations."
            await interaction.followup.send(
                i18n.text('✅ Advanced **{p0}** month(s) → {p1}, Year {p2}\n```\n{p3}\n```', p0=months, p1=_month_name(month, _lang(interaction)), p2=year, p3=report),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(i18n.text('❌ Tick failed: {p0}', p0=e), ephemeral=True)

    @admineco_grp.command(name="grant", description="[GM] Give resources to a nation / [GM] Dodaj zasoby")
    @app_commands.describe(
        nation="Nation name", resource="Resource name or 'gold'",
        amount="Amount", reason="Reason (logged)"
    )
    @i18n.localized
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
        resource_key = i18n.normalize_key(resource)
        if reason == "GM grant":
            reason = i18n.text("GM grant")
        warning = ""
        if resource_key != "gold" and resource_key not in KNOWN_RESOURCES:
            warning = (
                i18n.text("\n⚠️ **'{p0}'** is not a recognised resource name. It was added anyway — double-check the spelling.\nKnown resources: {p1}.", p0=resource_key, p1=', '.join(i18n.term(k) for k in sorted(KNOWN_RESOURCES - {'gold'})))
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
        _log(n["id"], "gm", i18n.text('GM grant: +{p0} {p1}. Reason: {p2}', p0=amount, p1=i18n.term(resource_key), p2=reason))
        await interaction.response.send_message(
            i18n.text('✅ Granted **{p0} {p1}** to **{p2}**.\nReason: {p3}{p4}', p0=amount, p1=i18n.term(resource_key), p2=n['name'], p3=reason, p4=warning),
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
    @i18n.localized
    async def mp_approve(self, interaction: discord.Interaction,
                         mp_id: int, final_effect: str, effect_json: str,
                         cost_json: str = "{}", duration_months: int = 0, gm_notes: str = ""):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        try:
            parsed_effect = i18n.game_json(effect_json)
            effect_json = json.dumps(parsed_effect)
        except (ValueError, TypeError) as e:
            await interaction.response.send_message(i18n.text('❌ Invalid effect_json: {p0}\nExample: {{"resources_once":{{"gold":500}},"stability":5}}', p0=e), ephemeral=True)
            return
        try:
            cost = i18n.game_json(cost_json)
            cost_json = json.dumps(cost)
            if not isinstance(cost, dict):
                raise ValueError(i18n.text('cost_json must be a JSON object like {} or {"gold":100}'))
        except (json.JSONDecodeError, ValueError) as e:
            await interaction.response.send_message(i18n.text('❌ Invalid cost_json: {p0}\nUse {{}} for free, or {{"gold":100}} etc.', p0=e), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM megaprojects WHERE id=?", (mp_id,))
            mp = c.fetchone()
        if not mp:
            await interaction.response.send_message(i18n.text('Megaproject #{p0} not found.', p0=mp_id), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM nations WHERE id=?", (mp["nation_id"],))
            nat = c.fetchone()
        gold = cost.get("gold", 0)
        if gold > 0 and nat["treasury"] < gold:
            await interaction.response.send_message(
                i18n.text('{p0} cannot afford this ({p1:.0f}g needed, {p2:.0f}g available).', p0=nat['name'], p1=gold, p2=nat['treasury']),
                ephemeral=True)
            return
        res = json.loads(nat["resources_json"])
        if cost:
            ok, missing = _deduct(res, cost)
            if not ok:
                await interaction.response.send_message(i18n.text('{p0} lacks enough {p1}.', p0=nat['name'], p1=i18n.term(missing)), ephemeral=True)
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
             i18n.text("Megaproject '{p0}' approved by GM. Effect: {p1}. Cost: {p2}. Duration: {p3} month(s). Player must run /megaproject build {p4} to start.", p0=mp['name'], p1=final_effect, p2=json.dumps(cost), p3=duration_months, p4=mp_id))
        await interaction.response.send_message(
            i18n.text('✅ **{p0}** approved.\nEffect: {p1}\nCost: {p2}\nDuration: {p3} month(s)\n\nThe player can now run `/megaproject build {p4}` to pay and start construction.', p0=mp['name'], p1=final_effect, p2=', '.join((f'{v} {i18n.term(k)}' for k, v in cost.items())) or i18n.term('free'), p3=duration_months, p4=mp_id),
            ephemeral=True,
        )

    @admineco_grp.command(name="mp_advance", description="[GM] Advance megaproject / [GM] Przyspiesz budowe")
    @app_commands.describe(mp_id="Megaproject ID", months="Months to advance")
    @i18n.localized
    async def mp_advance(self, interaction: discord.Interaction, mp_id: int, months: int):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute("SELECT * FROM megaprojects WHERE id=?", (mp_id,))
            mp = c.fetchone()
        if not mp or mp["status"] != "building":
            await interaction.response.send_message(
                i18n.text('Megaproject #{p0} not found or not under construction.', p0=mp_id), ephemeral=True)
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
                i18n.text('✅ **{p0}** completed! Effects applied.', p0=mp['name']), ephemeral=True)
        else:
            await interaction.response.send_message(
                i18n.text('⏩ Advanced **{p0}** by {p1} month(s). ({p2}/{p3})', p0=mp['name'], p1=months, p2=new_spent, p3=mp['duration_months']),
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
    @i18n.localized
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
             i18n.text('GM set {p0} tech: {p1:.1f} → {p2:.1f}.', p0=i18n.term(category.value), p1=old, p2=level))
        await interaction.response.send_message(
            i18n.text('✅ **{p0}** {p1} tech set to **{p2:.1f}**.', p0=n['name'], p1=i18n.term(category.value), p2=level), ephemeral=True)

    @admineco_grp.command(name="starter_pack",
                          description="[GM] Give starting resources to a nation or all nations")
    @app_commands.describe(
        nation="Nation name, or 'all' for every nation / Nazwa narodu lub 'all'",
    )
    @i18n.localized
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
            if nation.lower() in ("all", "wszyscy", "wszystkie"):
                c.execute("SELECT * FROM nations")
            else:
                c.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (nation,))
            targets = c.fetchall()

        if not targets:
            await interaction.response.send_message(
                i18n.text('No nations found.') if nation.lower() != "all" else i18n.text('No nations exist yet.'),
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
                     i18n.text('Received starter pack: {p0}g + ', p0=STARTER_GOLD)
                     + ", ".join(f"{v} {i18n.term(k)}" for k, v in STARTER.items()) + ".")
                )

        res_preview = ", ".join(f"{v} {i18n.term(k)}" for k, v in STARTER.items())
        target_str  = i18n.text('all nations') if nation.lower() in ("all", "wszyscy", "wszystkie") else f"**{targets[0]['name']}**"
        await interaction.response.send_message(
            i18n.text('✅ Starter pack given to {p0} ({p1} nation(s)).\n**Gold:** +{p2}g\n**Resources:** {p3}', p0=target_str, p1=len(targets), p2=STARTER_GOLD, p3=res_preview),
            ephemeral=True,
        )

    @admineco_grp.command(name="building_set", description="[GM] Edit building definition")
    @app_commands.describe(key="Building key", field="Field to change", value="New value")
    @i18n.localized
    async def building_set(self, interaction: discord.Interaction, key: str, field: str, value: str):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        allowed = {"cost_json","effect_json","upkeep_json","description",
                   "requires_terrain","requires_tech","name","tier"}
        field = i18n.normalize_key(field)
        key = i18n.normalize_key(key)
        if field not in allowed:
            await interaction.response.send_message(
                i18n.text('Unknown field. Allowed: {p0}', p0=', '.join(i18n.term(k) for k in sorted(allowed))), ephemeral=True)
            return
        if not _bdef(key):
            await interaction.response.send_message(i18n.text('Building `{p0}` not found.', p0=key), ephemeral=True)
            return
        if field.endswith('_json'):
            try:
                value = json.dumps(i18n.game_json(value))
            except ValueError:
                await interaction.response.send_message(i18n.text('Invalid JSON: {p0}', p0=i18n.text('Check the JSON syntax.')), ephemeral=True)
                return
        if field == 'requires_terrain':
            value = ','.join(i18n.normalize_key(t) for t in value.split(','))
        with db.cursor() as c:
            c.execute(f"UPDATE building_defs SET {field}=? WHERE key=?", (value, key))
        await interaction.response.send_message(f"✅ `{key}.{field}` = `{value}`", ephemeral=True)

    @admineco_grp.command(name="building_new", description="[GM] Add building type")
    @app_commands.describe(
        key="Unique key", name="Display name", tier="Tier 1-3",
        cost_json='e.g. {"gold":100}', effect_json='e.g. {"food":10}',
        requires_terrain="Comma list or blank", description="Short description"
    )
    @i18n.localized
    async def building_new(self, interaction: discord.Interaction,
                           key: str, name: str, tier: int,
                           cost_json: str, effect_json: str,
                           requires_terrain: str = "", description: str = ""):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True)
            return
        try:
            cost_json = json.dumps(i18n.game_json(cost_json))
            effect_json = json.dumps(i18n.game_json(effect_json))
            requires_terrain = ','.join(i18n.normalize_key(t) for t in requires_terrain.split(','))
        except (ValueError, TypeError) as e:
            await interaction.response.send_message(i18n.text('Invalid JSON: {p0}', p0=e), ephemeral=True)
            return
        if _bdef(key.lower()):
            await interaction.response.send_message(i18n.text('Building `{p0}` already exists.', p0=key), ephemeral=True)
            return
        with db.cursor() as c:
            c.execute(
                "INSERT INTO building_defs(key,name,tier,cost_json,effect_json,upkeep_json,"
                "requires_terrain,requires_tech,description) VALUES(?,?,?,?,?,'{}',?,0.0,?)",
                (key.lower(), name, tier, cost_json, effect_json, requires_terrain, description)
            )
        await interaction.response.send_message(i18n.text('✅ Building `{p0}` created.', p0=key), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
