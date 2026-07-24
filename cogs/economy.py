"""
Economy cog - step 4 extended:
  /resources                - view stockpile
  /build                    - build in a province
  /buildings list/province  - building info
  /megaproject propose/list - megaprojects (own only visible to player)
  /trade offer/accept/cancel/list - resource trading with private notes
  /admineco tick            - manual tick
  /admineco grant           - give resources to a nation
  /admineco building_set/new - building config
  /admineco mp_approve      - approve megaproject with effect, cost, duration
  /admineco mp_advance      - advance megaproject construction by N months
  /calendar set/start/stop/status - in-game calendar tied to real time
"""
import json
import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db
import i18n

# ---------------------------------------------------------------------------
# Default buildings
# ---------------------------------------------------------------------------
DEFAULT_BUILDINGS = [
    {"key":"farm",          "name":"Farm",           "tier":1, "cost_json":{"gold":100,"wood":50},           "effect_json":{"food":10},              "upkeep_json":{"gold":2},  "requires_terrain":"plains,grassland",      "requires_tech":0.0, "description":"Produces food on plains and grassland."},
    {"key":"fishing_wharf", "name":"Fishing Wharf",  "tier":1, "cost_json":{"gold":80,"wood":60},            "effect_json":{"food":8},               "upkeep_json":{"gold":2},  "requires_terrain":"coastal",               "requires_tech":0.0, "description":"Produces food on coastal provinces."},
    {"key":"plantation",    "name":"Plantation",     "tier":2, "cost_json":{"gold":150,"wood":40},           "effect_json":{"food":6,"spices":2},    "upkeep_json":{"gold":3},  "requires_terrain":"forest,jungle",         "requires_tech":3.0, "description":"Produces food and spices in tropical/forest provinces."},
    {"key":"pasture",       "name":"Pasture",        "tier":1, "cost_json":{"gold":60,"wood":20},            "effect_json":{"food":5,"horses":1},    "upkeep_json":{"gold":1},  "requires_terrain":"plains,grassland,hills","requires_tech":0.0, "description":"Produces food and horses on open terrain."},
    {"key":"lumber_camp",   "name":"Lumber Camp",    "tier":1, "cost_json":{"gold":80},                     "effect_json":{"wood":8},               "upkeep_json":{"gold":1},  "requires_terrain":"forest,taiga",          "requires_tech":0.0, "description":"Produces wood in forested provinces."},
    {"key":"mine",          "name":"Mine",           "tier":1, "cost_json":{"gold":120,"wood":30},           "effect_json":{"iron":6,"stone":4,"coal":3}, "upkeep_json":{"gold":2},"requires_terrain":"hills,mountains",    "requires_tech":0.0, "description":"Extracts iron, stone and coal."},
    {"key":"copper_mine",   "name":"Copper Mine",    "tier":1, "cost_json":{"gold":100,"wood":20},           "effect_json":{"copper":5},             "upkeep_json":{"gold":2},  "requires_terrain":"hills,mountains",       "requires_tech":0.0, "description":"Extracts copper."},
    {"key":"clay_pit",      "name":"Clay Pit",       "tier":1, "cost_json":{"gold":60},                     "effect_json":{"clay":6},               "upkeep_json":{"gold":1},  "requires_terrain":"wetland,plains",        "requires_tech":0.0, "description":"Extracts clay."},
    {"key":"tar_works",     "name":"Tar Works",      "tier":1, "cost_json":{"gold":80,"wood":20},            "effect_json":{"tar":5},                "upkeep_json":{"gold":1},  "requires_terrain":"forest,wetland,taiga",  "requires_tech":0.0, "description":"Produces tar."},
    {"key":"powder_mill",   "name":"Powder Mill",    "tier":2, "cost_json":{"gold":200,"stone":50,"iron":20},"effect_json":{"gunpowder":4},          "upkeep_json":{"gold":5},  "requires_terrain":"",                      "requires_tech":4.0, "description":"Produces gunpowder. Tech 4 required."},
    {"key":"textile_mill",  "name":"Textile Mill",   "tier":2, "cost_json":{"gold":150,"wood":40},           "effect_json":{"cloth":6},              "upkeep_json":{"gold":3},  "requires_terrain":"",                      "requires_tech":3.0, "description":"Produces cloth. Tech 3 required."},
    {"key":"market",        "name":"Market",         "tier":1, "cost_json":{"gold":100,"wood":30},           "effect_json":{"gold":15},              "upkeep_json":{},          "requires_terrain":"",                      "requires_tech":0.0, "description":"Generates gold each tick."},
    {"key":"port",          "name":"Port",           "tier":1, "cost_json":{"gold":150,"wood":80},           "effect_json":{"gold":10},              "upkeep_json":{"gold":2},  "requires_terrain":"coastal",               "requires_tech":0.0, "description":"Trade gold on coastal provinces. Enables ship construction."},
    {"key":"fort",          "name":"Fort",           "tier":1, "cost_json":{"gold":200,"stone":80},          "effect_json":{},                       "upkeep_json":{"gold":5},  "requires_terrain":"",                      "requires_tech":0.0, "description":"Raises fortification level by 1."},
    {"key":"university",    "name":"University",     "tier":3, "cost_json":{"gold":500,"stone":100,"wood":50},"effect_json":{"tech_points":1},       "upkeep_json":{"gold":10}, "requires_terrain":"",                      "requires_tech":5.0, "description":"Generates tech points. Tech 5 required."},
    {"key":"algae_farm",    "name":"Algae Farm",     "tier":3, "cost_json":{"gold":400,"wood":60},           "effect_json":{"algae":1},              "upkeep_json":{"gold":8},  "requires_terrain":"coastal,wetland",       "requires_tech":6.0, "description":"Produces rare Algae. Tech 6 required."},
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _lang(interaction): 
    locale = interaction.locale.value if interaction.locale else None
    return i18n.get_user_language(interaction.user.id, locale)

def _gm(interaction):
    if not interaction.guild: return False
    return any(r.name == config.GM_ROLE_NAME for r in interaction.user.roles)

def _nation_by_owner(owner_id):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations WHERE owner_id=?", (str(owner_id),))
        return cur.fetchone()

def _nation_by_name(name):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (name,))
        return cur.fetchone()

def _bdef(key):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM building_defs WHERE key=?", (key.lower(),))
        return cur.fetchone()

def _cfg(key, default=None):
    with db.cursor() as cur:
        cur.execute("SELECT value FROM game_config WHERE key=?", (key,))
        row = cur.fetchone()
    return row["value"] if row else default

def _cfg_set(key, value):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO game_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )

def _log(nation_id, source, text):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)",
            (nation_id, source, text)
        )

def _seed_buildings():
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) as cnt FROM building_defs")
        if cur.fetchone()["cnt"] > 0: return
        for b in DEFAULT_BUILDINGS:
            cur.execute(
                """INSERT OR IGNORE INTO building_defs
                   (key,name,tier,cost_json,effect_json,upkeep_json,requires_terrain,requires_tech,description)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (b["key"],b["name"],b["tier"],
                 json.dumps(b["cost_json"]),json.dumps(b["effect_json"]),json.dumps(b["upkeep_json"]),
                 b["requires_terrain"],b["requires_tech"],b["description"])
            )

def _terrain_ok(terrain, requires):
    if not requires: return True
    return terrain.lower() in [t.strip().lower() for t in requires.split(",")]

def _tech_ok(nation, req):
    if req <= 0: return True
    tech = json.loads(nation["tech_json"])
    return sum(tech.values()) / len(tech) >= req

def _deduct_resources(resources, cost):
    """Returns (ok, missing_key). Does NOT deduct gold (handled separately)."""
    for res, amt in cost.items():
        if res == "gold": continue
        if resources.get(res, 0) < amt:
            return False, res
    for res, amt in cost.items():
        if res == "gold": continue
        resources[res] = resources.get(res, 0) - amt
    return True, ""

def _apply_mp_effect(nation_id, effect_json_str, mp_name):
    """Apply a completed megaproject's effect_json to its nation."""
    effect = json.loads(effect_json_str) if effect_json_str else {}
    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations WHERE id=?", (nation_id,))
        nation = cur.fetchone()
    if not nation: return

    resources = json.loads(nation["resources_json"])
    treasury  = nation["treasury"]
    stability = nation["stability"]

    for key, val in effect.items():
        if key == "gold":
            treasury += val
        elif key == "stability":
            stability = max(0, min(100, stability + val))
        elif key == "tech_naval":
            tech = json.loads(nation["tech_json"])
            tech["naval"] = min(10.0, tech["naval"] + val)
            with db.cursor() as cur:
                cur.execute("UPDATE nations SET tech_json=? WHERE id=?", (json.dumps(tech), nation_id))
        elif key == "tech_land":
            tech = json.loads(nation["tech_json"])
            tech["land"] = min(10.0, tech["land"] + val)
            with db.cursor() as cur:
                cur.execute("UPDATE nations SET tech_json=? WHERE id=?", (json.dumps(tech), nation_id))
        elif key == "tech_economy":
            tech = json.loads(nation["tech_json"])
            tech["economy"] = min(10.0, tech["economy"] + val)
            with db.cursor() as cur:
                cur.execute("UPDATE nations SET tech_json=? WHERE id=?", (json.dumps(tech), nation_id))
        elif key == "fortification" and effect.get("province_cell"):
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE provinces SET fortification_level=fortification_level+? WHERE azgaar_cell_id=?",
                    (val, effect["province_cell"])
                )
        else:
            resources[key] = resources.get(key, 0) + val

    with db.cursor() as cur:
        cur.execute(
            "UPDATE nations SET resources_json=?,treasury=?,stability=? WHERE id=?",
            (json.dumps(resources), treasury, stability, nation_id)
        )
    _log(nation_id, "system", f"Megaproject '{mp_name}' completed. Effects applied: {json.dumps(effect)}")


# ---------------------------------------------------------------------------
# Tick logic
# ---------------------------------------------------------------------------
def _run_tick(months_to_advance: int = 1):
    """
    Core tick: advances in-game time by months_to_advance months,
    processes resource income, upkeep, and megaproject construction.
    Returns list of summary strings.
    """
    summaries = []

    # Advance in-game date
    current_month = int(_cfg("current_month", 1))
    current_year  = int(_cfg("current_year",  1))
    for _ in range(months_to_advance):
        current_month += 1
        if current_month > 12:
            current_month = 1
            current_year += 1
    _cfg_set("current_month", current_month)
    _cfg_set("current_year",  current_year)

    with db.cursor() as cur:
        cur.execute("SELECT * FROM nations")
        nations = cur.fetchall()

    for nation in nations:
        nation_id = nation["id"]
        resources = json.loads(nation["resources_json"])
        treasury  = nation["treasury"]
        upkeep    = 0.0

        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM provinces WHERE owner_nation_id=? AND active=1", (nation_id,)
            )
            provinces = cur.fetchall()

        for prov in provinces:
            base  = json.loads(prov["base_resources_json"])
            bldgs = json.loads(prov["buildings_json"])
            for res, amt in base.items():
                resources[res] = resources.get(res, 0) + amt * months_to_advance
            for bkey in bldgs:
                bd = _bdef(bkey)
                if not bd: continue
                for res, amt in json.loads(bd["effect_json"]).items():
                    if res == "gold":
                        treasury += amt * months_to_advance
                    elif res != "tech_points":
                        resources[res] = resources.get(res, 0) + amt * months_to_advance
                upkeep += json.loads(bd["upkeep_json"]).get("gold", 0) * months_to_advance

        treasury = max(0.0, treasury - upkeep)

        with db.cursor() as cur:
            cur.execute(
                "UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                (json.dumps(resources), treasury, nation_id)
            )

        # Advance megaproject construction
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM megaprojects WHERE nation_id=? AND status='building'", (nation_id,)
            )
            mps = cur.fetchall()

        for mp in mps:
            new_spent = mp["months_spent"] + months_to_advance
            if new_spent >= mp["duration_months"]:
                with db.cursor() as cur:
                    cur.execute(
                        "UPDATE megaprojects SET status='complete',months_spent=?,completed_at=datetime('now') WHERE id=?",
                        (mp["duration_months"], mp["id"])
                    )
                _apply_mp_effect(nation_id, mp["effect_json"], mp["name"])
            else:
                with db.cursor() as cur:
                    cur.execute(
                        "UPDATE megaprojects SET months_spent=? WHERE id=?",
                        (new_spent, mp["id"])
                    )

        _log(nation_id, "system",
             f"Month {current_month}/{current_year}: income processed, upkeep -{upkeep:.0f}g, treasury {treasury:.0f}g.")
        summaries.append(f"{nation['name']}: -{upkeep:.0f}g upkeep, {treasury:.0f}g treasury")

    return current_month, current_year, summaries


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class EconomyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _seed_buildings()
        self.calendar_tick.start()

    def cog_unload(self):
        self.calendar_tick.cancel()

    # ---- Background calendar loop ----------------------------------------
    @tasks.loop(minutes=1)
    async def calendar_tick(self):
        """Checks every minute if enough real time has passed for a new in-game month."""
        try:
            running = _cfg("calendar_running", "0")
            if running != "1": return

            hours_per_month = float(_cfg("hours_per_month", "24"))
            last_tick_str   = _cfg("last_tick_ts")
            if not last_tick_str: return

            last_tick = datetime.fromisoformat(last_tick_str)
            now       = datetime.now(timezone.utc)
            elapsed_hours = (now - last_tick).total_seconds() / 3600

            if elapsed_hours < hours_per_month: return

            # How many months have passed?
            months = max(1, int(elapsed_hours / hours_per_month))
            _cfg_set("last_tick_ts", now.isoformat())

            month, year, summaries = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _run_tick(months)
            )
            print(f"[CALENDAR] Advanced {months} month(s) → {month}/{year}", flush=True)

            channel_id = _cfg("announce_channel_id")
            if not channel_id: return
            channel = self.bot.get_channel(int(channel_id))
            if not channel: return

            embed = discord.Embed(
                title=f"📅 New Month — {month:02d}/{year}",
                description="\n".join(summaries) if summaries else "No nations yet.",
                color=discord.Color.gold(),
            )
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[CALENDAR] ERROR: {e}", flush=True)

    @calendar_tick.before_loop
    async def before_calendar(self):
        await self.bot.wait_until_ready()

    # ---- Command groups --------------------------------------------------
    calendar_grp  = app_commands.Group(name="calendar",   description="In-game calendar / Kalendarz")
    buildings_grp = app_commands.Group(name="buildings",  description="Building commands / Budynki")
    mp_grp        = app_commands.Group(name="megaproject", description="Megaproject commands")
    trade_grp     = app_commands.Group(name="trade",       description="Trade commands / Handel")
    admineco_grp  = app_commands.Group(name="admineco",    description="GM economy admin")

    # ======================================================================
    # CALENDAR
    # ======================================================================
    @calendar_grp.command(name="set", description="[GM] Configure calendar / [GM] Skonfiguruj kalendarz")
    @app_commands.describe(
        hours_per_month="Real-world hours per in-game month / Godziny IRL na miesiac",
        channel="Channel for month announcements / Kanal ogloszen",
        start_month="Starting month (1-12) / Miesiac startowy",
        start_year="Starting year / Rok startowy",
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
            f"✅ Calendar configured: **{hours_per_month}h** IRL = 1 in-game month.\n"
            f"Announcements → {channel.mention}\n"
            f"Starting date: Month {start_month}, Year {start_year}\n"
            f"Use `/calendar start` to begin.",
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

    @calendar_grp.command(name="status", description="Show current in-game date / Aktualna data w grze")
    async def calendar_status(self, interaction: discord.Interaction):
        month   = _cfg("current_month", "?")
        year    = _cfg("current_year",  "?")
        running = _cfg("calendar_running", "0") == "1"
        hpm     = _cfg("hours_per_month", "—")
        embed = discord.Embed(
            title="📅 In-Game Calendar",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Current Date", value=f"Month {month}, Year {year}", inline=True)
        embed.add_field(name="Status",       value="▶️ Running" if running else "⏸️ Paused", inline=True)
        embed.add_field(name="Speed",        value=f"{hpm}h IRL = 1 month", inline=True)
        await interaction.response.send_message(embed=embed)

    # ======================================================================
    # RESOURCES
    # ======================================================================
    @app_commands.command(name="resources", description="View your resources / Twoje zasoby")
    async def resources(self, interaction: discord.Interaction):
        lang   = _lang(interaction)
        nation = _nation_by_owner(str(interaction.user.id))
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True)
            return
        res = json.loads(nation["resources_json"])
        desc = "\n".join(
            f"**{k.capitalize()}**: {v:,.1f}" for k, v in sorted(res.items()) if v > 0
        ) or "*No resources yet.*"
        embed = discord.Embed(
            title=f"{nation['flag'] or ''} {nation['name']} — Resources".strip(),
            description=desc, color=discord.Color.green()
        )
        embed.add_field(name="Treasury", value=f"{nation['treasury']:,.0f} gold", inline=True)
        month = _cfg("current_month", "?")
        year  = _cfg("current_year",  "?")
        embed.set_footer(text=f"In-game date: Month {month}, Year {year}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ======================================================================
    # BUILD
    # ======================================================================
    @app_commands.command(name="build", description="Build in a province / Buduj w prowincji")
    @app_commands.describe(
        cell_id="Province cell ID", building="Building key e.g. farm, mine"
    )
    async def build(self, interaction: discord.Interaction, cell_id: int, building: str):
        lang   = _lang(interaction)
        nation = _nation_by_owner(str(interaction.user.id))
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True); return

        with db.cursor() as cur:
            cur.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,))
            prov = cur.fetchone()
        if not prov:
            await interaction.response.send_message(i18n.t(lang, "province_not_found", cell_id=cell_id), ephemeral=True); return
        if prov["owner_nation_id"] != nation["id"]:
            await interaction.response.send_message(i18n.t(lang, "build_not_owner"), ephemeral=True); return

        bd = _bdef(building)
        if not bd:
            await interaction.response.send_message(i18n.t(lang, "build_unknown", key=building), ephemeral=True); return
        if not _terrain_ok(prov["terrain"], bd["requires_terrain"]):
            await interaction.response.send_message(i18n.t(lang, "build_wrong_terrain", building=bd["name"], terrain=prov["terrain"]), ephemeral=True); return
        if not _tech_ok(nation, bd["requires_tech"]):
            await interaction.response.send_message(i18n.t(lang, "build_need_tech", building=bd["name"], level=bd["requires_tech"]), ephemeral=True); return

        bldgs = json.loads(prov["buildings_json"])
        if building.lower() in bldgs:
            await interaction.response.send_message(i18n.t(lang, "build_already_exists", building=bd["name"]), ephemeral=True); return

        cost      = json.loads(bd["cost_json"])
        gold_cost = cost.get("gold", 0)
        if nation["treasury"] < gold_cost:
            await interaction.response.send_message(i18n.t(lang, "build_no_gold", need=gold_cost, have=nation["treasury"]), ephemeral=True); return

        resources = json.loads(nation["resources_json"])
        ok, missing = _deduct_resources(resources, cost)
        if not ok:
            await interaction.response.send_message(i18n.t(lang, "build_no_resource", resource=missing), ephemeral=True); return

        bldgs.append(building.lower())
        fort_bonus = 1 if building.lower() == "fort" else 0
        with db.cursor() as cur:
            cur.execute(
                "UPDATE provinces SET buildings_json=?,fortification_level=fortification_level+? WHERE azgaar_cell_id=?",
                (json.dumps(bldgs), fort_bonus, cell_id)
            )
            cur.execute(
                "UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                (json.dumps(resources), nation["treasury"] - gold_cost, nation["id"])
            )
        _log(nation["id"], "system", f"Built {bd['name']} in province {prov['name'] or cell_id}.")

        effects = json.loads(bd["effect_json"])
        eff_str = ", ".join(f"+{v} {k}/month" for k, v in effects.items()) or "special"
        embed = discord.Embed(
            title=i18n.t(lang, "build_success_title"),
            description=i18n.t(lang, "build_success_desc",
                                building=bd["name"],
                                province=prov["name"] or f"Cell #{cell_id}",
                                effects=eff_str),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    # ======================================================================
    # BUILDINGS
    # ======================================================================
    @buildings_grp.command(name="list", description="List building types / Lista budynkow")
    async def buildings_list(self, interaction: discord.Interaction):
        with db.cursor() as cur:
            cur.execute("SELECT * FROM building_defs ORDER BY tier, name")
            rows = cur.fetchall()
        pages = []
        current = discord.Embed(title="Available Buildings", color=discord.Color.blue())
        for i, b in enumerate(rows):
            cost_str = ", ".join(f"{v} {k}" for k, v in json.loads(b["cost_json"]).items())
            eff_str  = ", ".join(f"+{v} {k}/month" for k, v in json.loads(b["effect_json"]).items()) or "special"
            terrain  = b["requires_terrain"] or "any"
            tech     = f" | Tech≥{b['requires_tech']}" if b["requires_tech"] > 0 else ""
            current.add_field(
                name=f"T{b['tier']} `{b['key']}` — {b['name']}",
                value=f"{b['description']}\nCost: {cost_str} | Terrain: {terrain}{tech}\nProduces: {eff_str}",
                inline=False
            )
            if (i + 1) % 5 == 0:
                pages.append(current)
                current = discord.Embed(title="Available Buildings (cont.)", color=discord.Color.blue())
        if current.fields: pages.append(current)
        await interaction.response.send_message(embed=pages[0], ephemeral=True)
        for p in pages[1:]:
            await interaction.followup.send(embed=p, ephemeral=True)

    @buildings_grp.command(name="province", description="Buildings in a province / Budynki w prowincji")
    @app_commands.describe(cell_id="Province cell ID")
    async def buildings_province(self, interaction: discord.Interaction, cell_id: int):
        with db.cursor() as cur:
            cur.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,))
            prov = cur.fetchone()
        if not prov:
            await interaction.response.send_message(i18n.t(_lang(interaction), "province_not_found", cell_id=cell_id), ephemeral=True); return
        bldgs = json.loads(prov["buildings_json"])
        if not bldgs:
            await interaction.response.send_message(f"No buildings in **{prov['name'] or f'Cell #{cell_id}'}**.", ephemeral=True); return
        lines = []
        for bkey in bldgs:
            bd = _bdef(bkey)
            name = bd["name"] if bd else bkey
            eff  = json.loads(bd["effect_json"]) if bd else {}
            lines.append(f"**{name}** — {', '.join(f'+{v} {k}/month' for k, v in eff.items()) or 'special'}")
        embed = discord.Embed(
            title=f"Buildings — {prov['name'] or f'Cell #{cell_id}'}",
            description="\n".join(lines), color=discord.Color.blue()
        )
        embed.add_field(name="Fortification", value=str(prov["fortification_level"]), inline=True)
        await interaction.response.send_message(embed=embed)

    # ======================================================================
    # MEGAPROJECTS
    # ======================================================================
    @mp_grp.command(name="propose", description="Propose a megaproject / Zaproponuj megaprojekt")
    @app_commands.describe(
        name="Project name", effect="Desired effect description", gold_budget="Gold budget"
    )
    async def mp_propose(self, interaction: discord.Interaction,
                         name: str, effect: str, gold_budget: int):
        lang   = _lang(interaction)
        nation = _nation_by_owner(str(interaction.user.id))
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True); return
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO megaprojects(nation_id,name,proposed_effect,cost_json,status) VALUES(?,?,?,?,?)",
                (nation["id"], name, effect, json.dumps({"gold": gold_budget}), "proposed")
            )
            mp_id = cur.lastrowid
        _log(nation["id"], "player", f"Proposed megaproject '{name}' (ID:{mp_id}): {effect}. Budget: {gold_budget}g.")
        embed = discord.Embed(
            title="Megaproject Proposed",
            description=f"**{name}** (ID: {mp_id})\n{effect}\nBudget: {gold_budget:,} gold\n\nAwaiting GM approval.",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @mp_grp.command(name="list", description="List your megaprojects / Lista megaprojektow")
    async def mp_list(self, interaction: discord.Interaction):
        lang   = _lang(interaction)
        is_gm  = _gm(interaction)
        nation = _nation_by_owner(str(interaction.user.id))

        with db.cursor() as cur:
            if is_gm:
                cur.execute("SELECT m.*,n.name as nname FROM megaprojects m JOIN nations n ON m.nation_id=n.id ORDER BY m.id DESC")
            elif nation:
                cur.execute("SELECT m.*,n.name as nname FROM megaprojects m JOIN nations n ON m.nation_id=n.id WHERE m.nation_id=? ORDER BY m.id DESC", (nation["id"],))
            else:
                await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True); return
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message("No megaprojects found.", ephemeral=True); return

        EMOJI = {"proposed":"🟡","approved":"🟢","building":"🔨","complete":"✅"}
        lines = []
        for r in rows:
            prog = f" ({r['months_spent']}/{r['duration_months']} months)" if r["status"] == "building" else ""
            cost = json.loads(r["cost_json"])
            cost_str = ", ".join(f"{v} {k}" for k, v in cost.items())
            lines.append(
                f"{EMOJI.get(r['status'],'❓')} **[{r['id']}] {r['name']}** ({r['nname']}){prog}\n"
                f"  {r['proposed_effect']}\n"
                f"  Cost: {cost_str} | Status: {r['status']}"
            )

        embed = discord.Embed(
            title="Megaprojects" + (" — All Nations" if is_gm else ""),
            description="\n\n".join(lines), color=discord.Color.purple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ======================================================================
    # TRADE
    # ======================================================================
    @trade_grp.command(name="offer", description="Offer a trade / Zaproponuj handel")
    @app_commands.describe(
        to_nation="Nation to trade with",
        give_resources='JSON of resources you give e.g. {"wood":50}',
        give_gold="Gold you give",
        receive_resources='JSON of resources you receive e.g. {"iron":20}',
        receive_gold="Gold you receive",
        public_note="Note visible to everyone",
        private_note="Note visible only to both parties and GM",
    )
    async def trade_offer(self, interaction: discord.Interaction,
                          to_nation: str,
                          give_resources: str = "{}",
                          give_gold: float = 0,
                          receive_resources: str = "{}",
                          receive_gold: float = 0,
                          public_note: str = "",
                          private_note: str = ""):
        lang = _lang(interaction)
        from_nation = _nation_by_owner(str(interaction.user.id))
        if not from_nation:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True); return
        target = _nation_by_name(to_nation)
        if not target:
            await interaction.response.send_message(i18n.t(lang, "nation_not_found"), ephemeral=True); return
        if target["id"] == from_nation["id"]:
            await interaction.response.send_message("You cannot trade with yourself.", ephemeral=True); return
        try:
            give_res = json.loads(give_resources)
            recv_res = json.loads(receive_resources)
        except json.JSONDecodeError:
            await interaction.response.send_message("Invalid JSON for resources.", ephemeral=True); return

        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO trades
                   (from_nation_id,to_nation_id,offer_resources_json,offer_gold,
                    receive_resources_json,receive_gold,public_note,private_note,status)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (from_nation["id"], target["id"],
                 json.dumps(give_res), give_gold,
                 json.dumps(recv_res), receive_gold,
                 public_note, private_note, "pending")
            )
            trade_id = cur.lastrowid

        _log(from_nation["id"], "player", f"Sent trade offer #{trade_id} to {target['name']}.")

        give_str = ", ".join(f"{v} {k}" for k, v in give_res.items())
        if give_gold: give_str += f", {give_gold:.0f} gold"
        recv_str = ", ".join(f"{v} {k}" for k, v in recv_res.items())
        if receive_gold: recv_str += f", {receive_gold:.0f} gold"

        embed = discord.Embed(
            title=f"Trade Offer #{trade_id}",
            color=discord.Color.blue()
        )
        embed.add_field(name=f"{from_nation['flag'] or ''} {from_nation['name']} gives", value=give_str or "—", inline=True)
        embed.add_field(name=f"{target['flag'] or ''} {target['name']} gives", value=recv_str or "—", inline=True)
        if public_note:
            embed.add_field(name="Note", value=public_note, inline=False)
        embed.set_footer(text=f"Trade #{trade_id} — awaiting acceptance. Use /trade accept {trade_id}")
        await interaction.response.send_message(embed=embed)

        # Notify target privately with private note
        target_user = interaction.guild.get_member(int(target["owner_id"])) if interaction.guild else None
        if target_user:
            dm_embed = discord.Embed(
                title=f"Trade Offer #{trade_id} from {from_nation['name']}",
                color=discord.Color.blue()
            )
            dm_embed.add_field(name="They give", value=give_str or "—", inline=True)
            dm_embed.add_field(name="They want", value=recv_str or "—", inline=True)
            if public_note:
                dm_embed.add_field(name="Public note", value=public_note, inline=False)
            if private_note:
                dm_embed.add_field(name="Private note", value=private_note, inline=False)
            dm_embed.set_footer(text=f"Use /trade accept {trade_id} to accept, /trade cancel {trade_id} to decline.")
            try:
                await target_user.send(embed=dm_embed)
            except Exception:
                pass

    @trade_grp.command(name="accept", description="Accept a trade offer / Zaakceptuj handel")
    @app_commands.describe(trade_id="Trade ID / ID handlu")
    async def trade_accept(self, interaction: discord.Interaction, trade_id: int):
        lang = _lang(interaction)
        nation = _nation_by_owner(str(interaction.user.id))
        if not nation:
            await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True); return

        with db.cursor() as cur:
            cur.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
            trade = cur.fetchone()

        if not trade:
            await interaction.response.send_message(f"Trade #{trade_id} not found.", ephemeral=True); return
        is_gm = _gm(interaction)
        if trade["to_nation_id"] != nation["id"] and not is_gm:
            await interaction.response.send_message("This trade is not addressed to your nation.", ephemeral=True); return
        if trade["status"] != "pending":
            await interaction.response.send_message(f"Trade #{trade_id} is already {trade['status']}.", ephemeral=True); return

        with db.cursor() as cur:
            cur.execute("SELECT * FROM nations WHERE id=?", (trade["from_nation_id"],))
            from_nation = cur.fetchone()
            cur.execute("SELECT * FROM nations WHERE id=?", (trade["to_nation_id"],))
            to_nation = cur.fetchone()

        give_res   = json.loads(trade["offer_resources_json"])
        recv_res   = json.loads(trade["receive_resources_json"])
        give_gold  = trade["offer_gold"]
        recv_gold  = trade["receive_gold"]

        from_res = json.loads(from_nation["resources_json"])
        to_res   = json.loads(to_nation["resources_json"])

        # Validate from_nation has what they offered
        for res, amt in give_res.items():
            if from_res.get(res, 0) < amt:
                await interaction.response.send_message(
                    f"{from_nation['name']} no longer has enough {res} for this trade.", ephemeral=True); return
        if from_nation["treasury"] < give_gold:
            await interaction.response.send_message(
                f"{from_nation['name']} no longer has enough gold.", ephemeral=True); return

        # Validate to_nation has what they're giving
        for res, amt in recv_res.items():
            if to_res.get(res, 0) < amt:
                await interaction.response.send_message(
                    f"{to_nation['name']} does not have enough {res}.", ephemeral=True); return
        if to_nation["treasury"] < recv_gold:
            await interaction.response.send_message(
                f"{to_nation['name']} does not have enough gold.", ephemeral=True); return

        # Execute trade
        for res, amt in give_res.items():
            from_res[res] = from_res.get(res, 0) - amt
            to_res[res]   = to_res.get(res, 0) + amt
        for res, amt in recv_res.items():
            to_res[res]   = to_res.get(res, 0) - amt
            from_res[res] = from_res.get(res, 0) + amt

        from_treasury = from_nation["treasury"] - give_gold + recv_gold
        to_treasury   = to_nation["treasury"]   - recv_gold + give_gold

        with db.cursor() as cur:
            cur.execute("UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                        (json.dumps(from_res), from_treasury, from_nation["id"]))
            cur.execute("UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                        (json.dumps(to_res), to_treasury, to_nation["id"]))
            cur.execute("UPDATE trades SET status='accepted',resolved_at=datetime('now') WHERE id=?",
                        (trade_id,))

        give_str = ", ".join(f"{v} {k}" for k, v in give_res.items())
        if give_gold: give_str += f", {give_gold:.0f} gold"
        recv_str = ", ".join(f"{v} {k}" for k, v in recv_res.items())
        if recv_gold: recv_str += f", {recv_gold:.0f} gold"

        log_public = f"Trade #{trade_id} completed with {to_nation['name']}. Gave: {give_str or '—'}. Received: {recv_str or '—'}."
        if trade["public_note"]: log_public += f" Note: {trade['public_note']}"
        log_private = log_public
        if trade["private_note"]: log_private += f" (Private: {trade['private_note']})"

        _log(from_nation["id"], "system", log_private)
        _log(to_nation["id"],   "system", log_private)

        embed = discord.Embed(
            title=f"✅ Trade #{trade_id} Accepted",
            color=discord.Color.green()
        )
        embed.add_field(name=f"{from_nation['name']} gave", value=give_str or "—", inline=True)
        embed.add_field(name=f"{to_nation['name']} gave",   value=recv_str or "—", inline=True)
        if trade["public_note"]:
            embed.add_field(name="Note", value=trade["public_note"], inline=False)
        await interaction.response.send_message(embed=embed)

    @trade_grp.command(name="cancel", description="Cancel/decline a trade / Anuluj handel")
    @app_commands.describe(trade_id="Trade ID / ID handlu")
    async def trade_cancel(self, interaction: discord.Interaction, trade_id: int):
        lang   = _lang(interaction)
        nation = _nation_by_owner(str(interaction.user.id))
        is_gm  = _gm(interaction)
        with db.cursor() as cur:
            cur.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
            trade = cur.fetchone()
        if not trade:
            await interaction.response.send_message(f"Trade #{trade_id} not found.", ephemeral=True); return
        if not is_gm and nation and trade["from_nation_id"] != nation["id"] and trade["to_nation_id"] != nation["id"]:
            await interaction.response.send_message("You are not party to this trade.", ephemeral=True); return
        if trade["status"] != "pending":
            await interaction.response.send_message(f"Trade #{trade_id} is already {trade['status']}.", ephemeral=True); return
        with db.cursor() as cur:
            cur.execute("UPDATE trades SET status='cancelled',resolved_at=datetime('now') WHERE id=?", (trade_id,))
        await interaction.response.send_message(f"Trade #{trade_id} cancelled.", ephemeral=True)

    @trade_grp.command(name="list", description="List pending trades / Lista ofert handlowych")
    async def trade_list(self, interaction: discord.Interaction):
        lang   = _lang(interaction)
        nation = _nation_by_owner(str(interaction.user.id))
        is_gm  = _gm(interaction)

        with db.cursor() as cur:
            if is_gm:
                cur.execute("""SELECT t.*,fn.name as fname,tn.name as tname
                               FROM trades t
                               JOIN nations fn ON t.from_nation_id=fn.id
                               JOIN nations tn ON t.to_nation_id=tn.id
                               WHERE t.status='pending' ORDER BY t.id DESC""")
            elif nation:
                cur.execute("""SELECT t.*,fn.name as fname,tn.name as tname
                               FROM trades t
                               JOIN nations fn ON t.from_nation_id=fn.id
                               JOIN nations tn ON t.to_nation_id=tn.id
                               WHERE t.status='pending'
                               AND (t.from_nation_id=? OR t.to_nation_id=?)
                               ORDER BY t.id DESC""",
                            (nation["id"], nation["id"]))
            else:
                await interaction.response.send_message(i18n.t(lang, "no_nation"), ephemeral=True); return
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message("No pending trades.", ephemeral=True); return

        is_party = nation is not None
        lines = []
        for r in rows:
            give_str = ", ".join(f"{v} {k}" for k, v in json.loads(r["offer_resources_json"]).items())
            if r["offer_gold"]: give_str += f", {r['offer_gold']:.0f}g"
            recv_str = ", ".join(f"{v} {k}" for k, v in json.loads(r["receive_resources_json"]).items())
            if r["receive_gold"]: recv_str += f", {r['receive_gold']:.0f}g"
            line = f"**#{r['id']}** {r['fname']} → {r['tname']}: gives [{give_str or '—'}], wants [{recv_str or '—'}]"
            if r["public_note"]: line += f"\n  Note: {r['public_note']}"
            # Private note only to parties and GM
            if r["private_note"] and (is_gm or (nation and (nation["id"] == r["from_nation_id"] or nation["id"] == r["to_nation_id"]))):
                line += f"\n  🔒 Private: {r['private_note']}"
            lines.append(line)

        embed = discord.Embed(title="Pending Trades", description="\n\n".join(lines), color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ======================================================================
    # ADMINECO
    # ======================================================================
    @admineco_grp.command(name="tick", description="[GM] Manual tick / [GM] Recznie uruchom tick")
    @app_commands.describe(months="Number of months to advance / Liczba miesiecy")
    async def admin_tick(self, interaction: discord.Interaction, months: int = 1):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        try:
            month, year, summaries = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _run_tick(months)
            )
            report = "\n".join(summaries) or "No nations."
            await interaction.followup.send(
                f"✅ Advanced **{months}** month(s) → Month {month}, Year {year}\n```\n{report}\n```",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Tick failed: {e}", ephemeral=True)
            raise

    @admineco_grp.command(name="grant", description="[GM] Give resources to a nation / [GM] Dodaj zasoby")
    @app_commands.describe(
        nation="Nation name", resource="Resource name or 'gold'",
        amount="Amount", reason="Reason for the grant (logged)"
    )
    async def admin_grant(self, interaction: discord.Interaction,
                          nation: str, resource: str, amount: float, reason: str = "GM grant"):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True); return
        nat = _nation_by_name(nation)
        if not nat:
            await interaction.response.send_message(i18n.t(_lang(interaction), "nation_not_found"), ephemeral=True); return

        if resource.lower() == "gold":
            with db.cursor() as cur:
                cur.execute("UPDATE nations SET treasury=treasury+? WHERE id=?", (amount, nat["id"]))
        else:
            res = json.loads(nat["resources_json"])
            res[resource.lower()] = res.get(resource.lower(), 0) + amount
            with db.cursor() as cur:
                cur.execute("UPDATE nations SET resources_json=? WHERE id=?", (json.dumps(res), nat["id"]))

        _log(nat["id"], "gm", f"GM grant: +{amount} {resource}. Reason: {reason}")
        await interaction.response.send_message(
            f"✅ Granted **{amount} {resource}** to **{nat['name']}**.\nReason: {reason}",
            ephemeral=True
        )

    @admineco_grp.command(name="mp_approve", description="[GM] Approve megaproject / [GM] Zatwierdz megaprojekt")
    @app_commands.describe(
        mp_id="Megaproject ID",
        final_effect="Effect description shown publicly",
        effect_json='Machine-readable effects e.g. {"food":20,"stability":5}',
        cost_json='Final cost e.g. {"gold":1000,"wood":200}',
        duration_months="Months to build (0 = instant)",
        gm_notes="Private GM notes"
    )
    async def mp_approve(self, interaction: discord.Interaction,
                         mp_id: int, final_effect: str, effect_json: str,
                         cost_json: str, duration_months: int = 0, gm_notes: str = ""):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True); return
        try:
            json.loads(effect_json)
            json.loads(cost_json)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(f"Invalid JSON: {e}", ephemeral=True); return

        with db.cursor() as cur:
            cur.execute("SELECT * FROM megaprojects WHERE id=?", (mp_id,))
            mp = cur.fetchone()
        if not mp:
            await interaction.response.send_message(f"Megaproject #{mp_id} not found.", ephemeral=True); return

        # Deduct cost immediately from nation
        with db.cursor() as cur:
            cur.execute("SELECT * FROM nations WHERE id=?", (mp["nation_id"],))
            nat = cur.fetchone()
        cost  = json.loads(cost_json)
        gold  = cost.get("gold", 0)
        if nat["treasury"] < gold:
            await interaction.response.send_message(
                f"{nat['name']} cannot afford this ({gold:.0f}g needed, {nat['treasury']:.0f}g available).",
                ephemeral=True); return
        res = json.loads(nat["resources_json"])
        ok, missing = _deduct_resources(res, cost)
        if not ok:
            await interaction.response.send_message(f"{nat['name']} lacks enough {missing}.", ephemeral=True); return

        new_status = "building" if duration_months > 0 else "complete"
        with db.cursor() as cur:
            cur.execute(
                """UPDATE megaprojects SET status=?,proposed_effect=?,effect_json=?,
                   cost_json=?,duration_months=?,gm_notes=? WHERE id=?""",
                (new_status, final_effect, effect_json, cost_json, duration_months, gm_notes, mp_id)
            )
            cur.execute("UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                        (json.dumps(res), nat["treasury"] - gold, nat["id"]))

        if new_status == "complete":
            _apply_mp_effect(mp["nation_id"], effect_json, mp["name"])
        else:
            _log(mp["nation_id"], "gm",
                 f"Megaproject '{mp['name']}' approved. Construction begins ({duration_months} months). Effect: {final_effect}")

        await interaction.response.send_message(
            f"✅ **{mp['name']}** approved.\n"
            f"Effect: {final_effect}\n"
            f"{'Completed instantly.' if new_status == 'complete' else f'Under construction: {duration_months} months.'}",
            ephemeral=True
        )

    @admineco_grp.command(name="mp_advance", description="[GM] Advance megaproject construction / [GM] Przyspiesz budowe")
    @app_commands.describe(mp_id="Megaproject ID", months="Months to advance")
    async def mp_advance(self, interaction: discord.Interaction, mp_id: int, months: int):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True); return
        with db.cursor() as cur:
            cur.execute("SELECT * FROM megaprojects WHERE id=?", (mp_id,))
            mp = cur.fetchone()
        if not mp or mp["status"] != "building":
            await interaction.response.send_message(f"Megaproject #{mp_id} not found or not under construction.", ephemeral=True); return

        new_spent = min(mp["months_spent"] + months, mp["duration_months"])
        complete  = new_spent >= mp["duration_months"]
        with db.cursor() as cur:
            if complete:
                cur.execute(
                    "UPDATE megaprojects SET status='complete',months_spent=?,completed_at=datetime('now') WHERE id=?",
                    (new_spent, mp_id)
                )
            else:
                cur.execute("UPDATE megaprojects SET months_spent=? WHERE id=?", (new_spent, mp_id))

        if complete:
            _apply_mp_effect(mp["nation_id"], mp["effect_json"], mp["name"])
            await interaction.response.send_message(
                f"✅ **{mp['name']}** completed! Effects applied.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"⏩ Advanced **{mp['name']}** by {months} month(s). ({new_spent}/{mp['duration_months']})",
                ephemeral=True)

    @admineco_grp.command(name="building_set", description="[GM] Edit building definition / [GM] Edytuj budynek")
    @app_commands.describe(key="Building key", field="Field to change", value="New value")
    async def building_set(self, interaction: discord.Interaction, key: str, field: str, value: str):
        if not _gm(interaction):
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True); return
        allowed = {"cost_json","effect_json","upkeep_json","description","requires_terrain","requires_tech","name","tier"}
        if field not in allowed:
            await interaction.response.send_message(f"Unknown field. Allowed: {', '.join(sorted(allowed))}", ephemeral=True); return
        if not _bdef(key):
            await interaction.response.send_message(f"Building `{key}` not found.", ephemeral=True); return
        with db.cursor() as cur:
            cur.execute(f"UPDATE building_defs SET {field}=? WHERE key=?", (value, key))
        await interaction.response.send_message(f"✅ `{key}.{field}` = `{value}`", ephemeral=True)

    @admineco_grp.command(name="building_new", description="[GM] Add building type / [GM] Dodaj budynek")
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
            await interaction.response.send_message(i18n.t(_lang(interaction), "gm_only"), ephemeral=True); return
        try:
            json.loads(cost_json); json.loads(effect_json)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(f"Invalid JSON: {e}", ephemeral=True); return
        if _bdef(key.lower()):
            await interaction.response.send_message(f"Building `{key}` already exists.", ephemeral=True); return
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO building_defs(key,name,tier,cost_json,effect_json,upkeep_json,requires_terrain,requires_tech,description) VALUES(?,?,?,?,?,'{}',?,0.0,?)",
                (key.lower(), name, tier, cost_json, effect_json, requires_terrain, description)
            )
        await interaction.response.send_message(f"✅ Building `{key}` created.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
