"""
Colonialism and Trade Route commands.
Colony stages: outpost(50%) -> settlement(75%) -> colony(90%) -> province(100%)
Trade routes: income = ship cargo x 2g/tick, fallback 20g if no ships.
"""
import json
import discord
from discord import app_commands
from discord.ext import commands
import config, db, i18n
from utils import short_date

COLONY_STAGES = ["outpost", "settlement", "colony", "province"]
STAGE_CONFIG = {
    "outpost":    {"yield_pct":0.50,"found_cost":{"gold":500},"min_cargo":5, "advance_cost":{"gold":300}, "advance_months":6, "tech_required":0.0,"desc":"50% yield outpost."},
    "settlement": {"yield_pct":0.75,"found_cost":{},          "min_cargo":0, "advance_cost":{"gold":800}, "advance_months":12,"tech_required":3.0,"desc":"75% yield settlement."},
    "colony":     {"yield_pct":0.90,"found_cost":{},          "min_cargo":0, "advance_cost":{"gold":2000},"advance_months":24,"tech_required":5.0,"desc":"90% yield colony."},
    "province":   {"yield_pct":1.00,"found_cost":{},          "min_cargo":0, "advance_cost":{},           "advance_months":0, "tech_required":0.0,"desc":"100% yield province."},
}
STAGE_EMOJI = {"outpost":"🏕️","settlement":"🏘️","colony":"🏙️","province":"🏛️"}

def _lang(i): return i18n.get_user_language(i.user.id, i.locale.value if i.locale else None)
from utils import gm_only as _gm
def _nat_owner(uid):
    with db.cursor() as c: c.execute("SELECT * FROM nations WHERE owner_id=?", (str(uid),)); return c.fetchone()
def _nat_name(name):
    with db.cursor() as c: c.execute("SELECT * FROM nations WHERE LOWER(name)=LOWER(?)", (name,)); return c.fetchone()
def _log(nid, src, txt):
    with db.cursor() as c: c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)", (nid,src,txt))
def _prov_by_cell(cell_id):
    with db.cursor() as c: c.execute("SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1", (cell_id,)); return c.fetchone()
def _colony_by_prov(pid):
    with db.cursor() as c: c.execute("SELECT * FROM colonies WHERE province_id=?", (pid,)); return c.fetchone()
def _colonial_tech(nat): return json.loads(nat["tech_json"]).get("colonial", 3.0)
def _cfg(key, default=""):
    with db.cursor() as c: c.execute("SELECT value FROM game_config WHERE key=?", (key,)); row=c.fetchone(); return row["value"] if row else default

def _total_cargo(nation_id):
    with db.cursor() as c:
        c.execute("SELECT u.quantity,b.stats_json FROM military_units u JOIN blueprints b ON u.blueprint_id=b.id WHERE u.nation_id=? AND b.type='ship'", (nation_id,))
        ships = c.fetchall()
    return sum(json.loads(s["stats_json"]).get("cargo",0)*s["quantity"] for s in ships)

def _route_income(nation_id):
    with db.cursor() as c:
        c.execute("SELECT * FROM trade_routes WHERE nation_id=? AND active=1", (nation_id,))
        routes = c.fetchall()
    total = 0.0
    for r in routes:
        cargo = 0.0
        for cid in [r["from_cell_id"], r["to_cell_id"]]:
            prov = _prov_by_cell(cid)
            if not prov: continue
            with db.cursor() as c:
                c.execute("SELECT u.quantity,b.stats_json FROM military_units u JOIN blueprints b ON u.blueprint_id=b.id WHERE u.province_id=? AND b.type='ship'", (prov["id"],))
                ships = c.fetchall()
            cargo += sum(json.loads(s["stats_json"]).get("cargo",0)*s["quantity"] for s in ships)
        total += cargo*2.0 if cargo>0 else 20.0
    return total

def compute_trade_route_income(nation_id: int) -> float:
    with db.cursor() as c:
        c.execute(
            "SELECT COALESCE(SUM(income_per_tick), 0) as total_income "
            "FROM trade_routes WHERE nation_id=%s",
            (nation_id,)
        )
        row = c.fetchone()
        return float(row["total_income"] if isinstance(row, dict) else row[0])

def get_colony_yield_modifier(province_id):
    col = _colony_by_prov(province_id)
    if not col: return 1.0
    return STAGE_CONFIG.get(col["status"],{}).get("yield_pct",1.0)

def tick_colonies(nation_id, months=1):
    with db.cursor() as c:
        c.execute("SELECT * FROM colonies WHERE nation_id=? AND status!='province'", (nation_id,))
        cols = c.fetchall()
    for col in cols:
        with db.cursor() as c:
            c.execute("UPDATE colonies SET months_in_status=months_in_status+? WHERE id=?", (months, col["id"]))


class ColonialismCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    traderoute_grp   = app_commands.Group(name="traderoute", description="Trade route commands")
    colony_grp       = app_commands.Group(name="colony",     description="Colony commands")
    colony_admin_grp = app_commands.Group(name="colonymgr",  description="[GM] Colony management")

    # ---- TRADE ROUTES ----

    @traderoute_grp.command(name="add", description="Establish a trade route / Ustanow szlak handlowy")
    @i18n.localized
    async def traderoute_add(
        self, 
        interaction: discord.Interaction, 
        from_cell: int, 
        to_cell: int, 
        route_name: str, 
        ship_id: int
    ):
        nat = _nation_owner(interaction.user.id)
        if not nat:
            return await interaction.response.send_message(i18n.text('❌ You do not own a nation.'), ephemeral=True)
        
        nid = nat["id"]
    
        with db.cursor() as c:
            # 1. Check maximum trade routes cap (e.g., max 5 active routes per nation)
            c.execute("SELECT COUNT(*) as cnt FROM trade_routes WHERE nation_id=%s", (nid,))
            row = c.fetchone()
            route_count = row["cnt"] if isinstance(row, dict) else row[0]
            if route_count >= 5:
                return await interaction.response.send_message(
                    i18n.text('❌ You have reached your maximum limit of 5 trade routes!'), ephemeral=True
                )
    
            # 2. Check for existing duplicate route
            c.execute(
                "SELECT id FROM trade_routes WHERE nation_id=%s AND from_cell=%s AND to_cell=%s",
                (nid, from_cell, to_cell)
            )
            if c.fetchone():
                return await interaction.response.send_message(
                    i18n.text('❌ A trade route between these two provinces already exists!'), ephemeral=True
                )
    
            # 3. Validate that the specified ship exists, belongs to nation, has cargo capacity, and is not already assigned
            c.execute(
                "SELECT u.id, b.stats_json FROM military_units u "
                "JOIN unit_blueprints b ON u.blueprint_id = b.id "
                "WHERE u.id=%s AND u.nation_id=%s AND b.unit_type='ship'",
                (ship_id, nid)
            )
            ship = c.fetchone()
            if not ship:
                return await interaction.response.send_message(
                    i18n.text('❌ Valid ship not found or does not belong to your nation.'), ephemeral=True
                )
    
            # Check if ship is already assigned to another trade route
            c.execute("SELECT id FROM trade_routes WHERE ship_id=%s", (ship_id,))
            if c.fetchone():
                return await interaction.response.send_message(
                    i18n.text('❌ This ship is already assigned to another trade route!'), ephemeral=True
                )
    
            # Read cargo capacity from ship's blueprint stats
            stats = json.loads(ship["stats_json"]) if isinstance(ship["stats_json"], str) else ship["stats_json"]
            cargo_capacity = stats.get("cargo", 0)
    
            if cargo_capacity <= 0:
                return await interaction.response.send_message(
                    i18n.text('❌ This ship has 0 cargo capacity and cannot carry trade cargo!'), ephemeral=True
                )
    
            # Income formula: Cargo * 2g / tick
            income_per_tick = cargo_capacity * 2
    
            # 4. Insert trade route with attached ship ID
            c.execute(
                "INSERT INTO trade_routes (nation_id, name, from_cell, to_cell, ship_id, income_per_tick) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (nid, route_name, from_cell, to_cell, ship_id, income_per_tick)
            )
    
        await interaction.response.send_message(
            i18n.text('✅ Trade route **{p0}** established using Ship #{p1}! Yielding **+{p2}g/month** (Cargo: {p3}).', p0=route_name, p1=ship_id, p2=income_per_tick, p3=cargo_capacity)
        )

    @traderoute_grp.command(name="remove", description="Remove a trade route / Usun szlak")
    @i18n.localized
    async def traderoute_remove(self, interaction: discord.Interaction, route_id: int):
        nat = _nation_owner(interaction.user.id)
        if not nat:
            return await interaction.response.send_message(i18n.text('❌ You do not own a nation.'), ephemeral=True)
        
        nid = nat["id"]
    
        with db.cursor() as c:
            # 1. Fetch trade route to verify ownership and grab info
            c.execute(
                "SELECT id, name, ship_id FROM trade_routes WHERE id=%s AND nation_id=%s",
                (route_id, nid)
            )
            route = c.fetchone()
            if not route:
                return await interaction.response.send_message(
                    i18n.text('❌ Trade route not found or does not belong to your nation.'), ephemeral=True
                )
    
            # Handle dict vs tuple cursors safely
            route_name = route["name"] if isinstance(route, dict) else route[1]
            ship_id = route["ship_id"] if isinstance(route, dict) else route[2]
    
            # 2. Delete the route record
            c.execute("DELETE FROM trade_routes WHERE id=%s AND nation_id=%s", (route_id, nid))
    
        ship_msg = i18n.text(' Ship **#{p0}** is now unassigned and available.', p0=ship_id) if ship_id else ""
        await interaction.response.send_message(
            i18n.text('✅ Trade route **{p0}** (ID: `{p1}`) has been cancelled.{p2}', p0=route_name, p1=route_id, p2=ship_msg)
        )

    @traderoute_grp.command(name="list", description="List your trade routes / Lista szlakow")
    @i18n.localized
    async def traderoute_list(self, interaction: discord.Interaction):
        nat = _nation_owner(interaction.user.id)
        if not nat:
            return await interaction.response.send_message(i18n.text('❌ You do not own a nation.'), ephemeral=True)
    
        nid = nat["id"]
        with db.cursor() as c:
            c.execute("SELECT id, name, from_cell, to_cell, ship_id, income_per_tick FROM trade_routes WHERE nation_id=%s", (nid,))
            routes = c.fetchall()
    
        if not routes:
            return await interaction.response.send_message(i18n.text('ℹ️ You currently have no active trade routes.'), ephemeral=True)
    
        embed = discord.Embed(title=i18n.text('📜 Trade Routes — {p0}', p0=nat['name']), color=discord.Color.gold())
        
        for r in routes:
            r_id = r["id"] if isinstance(r, dict) else r[0]
            r_name = r["name"] if isinstance(r, dict) else r[1]
            from_c = r["from_cell"] if isinstance(r, dict) else r[2]
            to_c = r["to_cell"] if isinstance(r, dict) else r[3]
            s_id = r["ship_id"] if isinstance(r, dict) else r[4]
            income = r["income_per_tick"] if isinstance(r, dict) else r[5]
    
            embed.add_field(
                name=f"ID `{r_id}`: {r_name}",
                value=i18n.text('• **From:** Cell {p0} ➔ **To:** Cell {p1}\n• **Ship Assigned:** #{p2}\n• **Income:** +{p3}g/month', p0=from_c, p1=to_c, p2=s_id, p3=income),
                inline=False
            )
    
        await interaction.response.send_message(embed=embed)

    # ---- COLONIES ----

    @colony_grp.command(name="found", description="Found a colony / Zaloz kolonie")
    @app_commands.describe(cell_id="Province cell ID", name="Colony name")
    @i18n.localized
    async def colony_found(self, interaction: discord.Interaction, cell_id: int, name: str):
        lang=_lang(interaction); nat=_nat_owner(str(interaction.user.id))
        if not nat: await interaction.response.send_message(i18n.t(lang,"no_nation"),ephemeral=True); return
        prov=_prov_by_cell(cell_id)
        if not prov: await interaction.response.send_message(i18n.text('Province {p0} not found.', p0=cell_id),ephemeral=True); return
        if prov["owner_nation_id"] is not None: await interaction.response.send_message(i18n.text('Province already owned.'),ephemeral=True); return
        if _colony_by_prov(prov["id"]): await interaction.response.send_message(i18n.text('Already a colony.'),ephemeral=True); return
        cfg=STAGE_CONFIG["outpost"]; gold_cost=cfg["found_cost"].get("gold",500); min_cargo=cfg["min_cargo"]
        if nat["treasury"]<gold_cost: await interaction.response.send_message(i18n.text('Need {p0}g, have {p1:.0f}g.', p0=gold_cost, p1=nat['treasury']),ephemeral=True); return
        if _total_cargo(nat["id"])<min_cargo: await interaction.response.send_message(i18n.text('Need {p0} fleet cargo capacity. Build ships with cargo modules.', p0=min_cargo),ephemeral=True); return
        with db.cursor() as c:
            c.execute("UPDATE provinces SET owner_nation_id=? WHERE id=?",(nat["id"],prov["id"]))
            c.execute("INSERT INTO colonies(nation_id,province_id,name,status) VALUES(?,?,?,?)",(nat["id"],prov["id"],name,"outpost")); col_id=c.lastrowid
            c.execute("UPDATE nations SET treasury=treasury-? WHERE id=?",(gold_cost,nat["id"]))
        _log(nat["id"],"system",i18n.text("Founded colony '{p0}' (#{p1}) at {p2}. Cost: {p3}g.", p0=name, p1=col_id, p2=prov['name'] or i18n.text('Cell #{p0}', p0=cell_id), p3=gold_cost))
        embed=discord.Embed(title=i18n.text('🏕️ Colony Founded — {p0}', p0=name),description=i18n.text('Outpost established at **{p0}**.\nProduces **50%** of base resources.\nInvest and wait 6 months to advance to Settlement.', p0=prov['name'] or i18n.text('Cell #{p0}', p0=cell_id)),color=discord.Color.green())
        embed.add_field(name=i18n.text('Cost'),value=f"{gold_cost}g",inline=True); embed.add_field(name=i18n.text('Status'),value=i18n.text('🏕️ Outpost'),inline=True)
        await interaction.response.send_message(embed=embed)

    @colony_grp.command(name="develop", description="Invest in colony / Inwestuj w kolonie")
    @app_commands.describe(cell_id="Province cell ID", gold_amount="Gold to invest")
    @i18n.localized
    async def colony_develop(self, interaction: discord.Interaction, cell_id: int, gold_amount: int):
        lang=_lang(interaction); nat=_nat_owner(str(interaction.user.id))
        if not nat: await interaction.response.send_message(i18n.t(lang,"no_nation"),ephemeral=True); return
        prov=_prov_by_cell(cell_id)
        if not prov or prov["owner_nation_id"]!=nat["id"]: await interaction.response.send_message(i18n.text('Province not found or not yours.'),ephemeral=True); return
        col=_colony_by_prov(prov["id"])
        if not col: await interaction.response.send_message(i18n.text('No colony here. Use /colony found first.'),ephemeral=True); return
        if col["status"]=="province": await interaction.response.send_message(i18n.text('Already fully integrated.'),ephemeral=True); return
        if col["nation_id"]!=nat["id"]: await interaction.response.send_message(i18n.text('Not your colony.'),ephemeral=True); return
        if nat["treasury"]<gold_amount: await interaction.response.send_message(i18n.text('Not enough gold. Have {p0:.0f}g.', p0=nat['treasury']),ephemeral=True); return
        inv=json.loads(col["investment_json"]); inv["gold"]=inv.get("gold",0)+gold_amount
        cfg=STAGE_CONFIG[col["status"]]; adv_cost=cfg["advance_cost"].get("gold",0); adv_mo=cfg["advance_months"]
        with db.cursor() as c:
            c.execute("UPDATE colonies SET investment_json=? WHERE id=?",(json.dumps(inv),col["id"]))
            c.execute("UPDATE nations SET treasury=treasury-? WHERE id=?",(gold_amount,nat["id"]))
        _log(nat["id"],"player",i18n.text("Invested {p0}g in colony '{p1}'. Total: {p2:.0f}/{p3}g.", p0=gold_amount, p1=col['name'], p2=inv['gold'], p3=adv_cost))
        pct=min(100,int(inv["gold"]/adv_cost*100)) if adv_cost>0 else 100
        bar="█"*(pct//10)+"░"*(10-pct//10)
        next_stage=COLONY_STAGES[COLONY_STAGES.index(col["status"])+1] if col["status"]!="province" else "province"
        ready=pct>=100 and col["months_in_status"]>=adv_mo
        embed=discord.Embed(title=i18n.text('💰 Investment — {p0}', p0=col['name']),color=discord.Color.gold())
        embed.add_field(name=i18n.text('Invested'),value=f"{gold_amount}g",inline=True)
        embed.add_field(name=i18n.text('Total'),   value=f"{inv['gold']:.0f}/{adv_cost}g",inline=True)
        embed.add_field(name=f"→ {i18n.term(next_stage)}",value=i18n.text('`{p0}` {p1}% gold | {p2}/{p3} months', p0=bar, p1=pct, p2=col['months_in_status'], p3=adv_mo),inline=False)
        if ready: embed.add_field(name=i18n.text('✅ Ready!'),value=i18n.text('Ask the GM to run /colonymgr advance.'),inline=False)
        await interaction.response.send_message(embed=embed)

    @colony_grp.command(name="list", description="List colonies / Lista kolonii")
    @app_commands.describe(nation="Nation name or blank for your own")
    @i18n.localized
    async def colony_list(self, interaction: discord.Interaction, nation: str=""):
        lang=_lang(interaction); nat=_nat_name(nation) if nation else _nat_owner(str(interaction.user.id))
        if not nat: await interaction.response.send_message(i18n.t(lang,"nation_not_found" if nation else "no_nation"),ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT c.*,p.name as pname,p.azgaar_cell_id FROM colonies c JOIN provinces p ON c.province_id=p.id WHERE c.nation_id=? ORDER BY c.id",(nat["id"],)); cols=c.fetchall()
        if not cols: await interaction.response.send_message(i18n.text('**{p0}** has no colonies.', p0=nat['name']),ephemeral=True); return
        embed=discord.Embed(title=i18n.text('🗺️ Colonies — {p0} {p1}', p0=nat['flag'] or '', p1=nat['name']),color=discord.Color.dark_green())
        for col in cols:
            cfg=STAGE_CONFIG[col["status"]]; inv=json.loads(col["investment_json"])
            adv_cost=cfg["advance_cost"].get("gold",0); adv_mo=cfg["advance_months"]
            pname=col["pname"] or i18n.text('Cell #{p0}', p0=col['azgaar_cell_id'])
            pct_g=min(100,int(inv.get("gold",0)/adv_cost*100)) if adv_cost>0 else 100
            pct_t=min(100,int(col["months_in_status"]/adv_mo*100)) if adv_mo>0 else 100
            ready=pct_g>=100 and pct_t>=100 and col["status"]!="province"
            embed.add_field(name=f"{STAGE_EMOJI.get(col['status'],'🏕️')} {col['name']} — {pname}",
                value=i18n.text('**{p0}** | {p1:.0f}% yield\n{p2}/{p3}mo | {p4:.0f}/{p5}g', p0=i18n.term(col['status']), p1=cfg['yield_pct'] * 100, p2=col['months_in_status'], p3=adv_mo, p4=inv.get('gold', 0), p5=adv_cost)+(i18n.text('\n✅ Ready — ask GM to advance') if ready else ""),inline=False)
        await interaction.response.send_message(embed=embed,ephemeral=True)

    @colony_grp.command(name="view", description="View colony details / Szczegoly kolonii")
    @app_commands.describe(cell_id="Province cell ID")
    @i18n.localized
    async def colony_view(self, interaction: discord.Interaction, cell_id: int):
        prov=_prov_by_cell(cell_id)
        if not prov: await interaction.response.send_message(i18n.text('Province {p0} not found.', p0=cell_id),ephemeral=True); return
        col=_colony_by_prov(prov["id"])
        if not col: await interaction.response.send_message(i18n.text('No colony here.'),ephemeral=True); return
        with db.cursor() as c: c.execute("SELECT * FROM nations WHERE id=?",(col["nation_id"],)); owner=c.fetchone()
        cfg=STAGE_CONFIG[col["status"]]; inv=json.loads(col["investment_json"])
        adv_cost=cfg["advance_cost"].get("gold",0); adv_mo=cfg["advance_months"]
        base_res=json.loads(prov["base_resources_json"])
        eff_res={k:v*cfg["yield_pct"] for k,v in base_res.items()}
        embed=discord.Embed(title=f"{STAGE_EMOJI.get(col['status'],'🏕️')} {col['name']}",color=discord.Color.dark_green())
        embed.add_field(name=i18n.text('Owner'),  value=f"{owner['flag'] or ''} {owner['name']}",inline=True)
        embed.add_field(name=i18n.text('Status'), value=i18n.term(col["status"]),               inline=True)
        embed.add_field(name=i18n.text('Yield'),  value=f"{cfg['yield_pct']*100:.0f}%",          inline=True)
        embed.add_field(name=i18n.text('Resources'),value=", ".join(f"{i18n.term(k)}:{v:.1f}" for k,v in eff_res.items() if v>0) or "—",inline=False)
        if col["status"]!="province":
            pct_g=min(100,int(inv.get("gold",0)/adv_cost*100)) if adv_cost>0 else 100
            pct_t=min(100,int(col["months_in_status"]/adv_mo*100)) if adv_mo>0 else 100
            bg="█"*(pct_g//10)+"░"*(10-pct_g//10); bt="█"*(pct_t//10)+"░"*(10-pct_t//10)
            idx=COLONY_STAGES.index(col["status"]); next_s=COLONY_STAGES[idx+1]
            embed.add_field(name=f"→ {i18n.term(next_s)}",value=i18n.text('💰 `{p0}` {p1:.0f}/{p2}g\n📅 `{p3}` {p4}/{p5}mo\n🔬 Tech req: {p6:.0f}', p0=bg, p1=inv.get('gold', 0), p2=adv_cost, p3=bt, p4=col['months_in_status'], p5=adv_mo, p6=STAGE_CONFIG[next_s].get('tech_required', 0)),inline=False)
        if col["gm_notes"]: embed.add_field(name=i18n.text('GM Notes'),value=col["gm_notes"],inline=False)
        embed.set_footer(text=i18n.text('Founded: {p0}', p0=short_date(col['founded_at'])))
        await interaction.response.send_message(embed=embed)

    # ---- GM COLONY MANAGEMENT ----

    @colony_admin_grp.command(name="advance", description="[GM] Advance colony / [GM] Awansuj kolonie")
    @app_commands.describe(cell_id="Province cell ID", gm_notes="Optional notes")
    @i18n.localized
    async def colony_advance(self, interaction: discord.Interaction, cell_id: int, gm_notes: str=""):
        if not _gm(interaction): await interaction.response.send_message(i18n.t(_lang(interaction),"gm_only"),ephemeral=True); return
        prov=_prov_by_cell(cell_id)
        if not prov: await interaction.response.send_message(i18n.text('Province {p0} not found.', p0=cell_id),ephemeral=True); return
        col=_colony_by_prov(prov["id"])
        if not col: await interaction.response.send_message(i18n.text('No colony here.'),ephemeral=True); return
        if col["status"]=="province": await interaction.response.send_message(i18n.text('Already fully integrated.'),ephemeral=True); return
        with db.cursor() as c: c.execute("SELECT * FROM nations WHERE id=?",(col["nation_id"],)); owner=c.fetchone()
        idx=COLONY_STAGES.index(col["status"]); new_stage=COLONY_STAGES[idx+1]
        next_cfg=STAGE_CONFIG[new_stage]
        if _colonial_tech(owner)<next_cfg.get("tech_required",0):
            await interaction.response.send_message(i18n.text('{p0} lacks Colonial tech (needs {p1:.0f}).', p0=owner['name'], p1=next_cfg['tech_required']),ephemeral=True); return
        with db.cursor() as c:
            c.execute("UPDATE colonies SET status=?,months_in_status=0,investment_json='{}',gm_notes=? WHERE id=?",(new_stage,gm_notes,col["id"]))
        _log(col["nation_id"],"gm",i18n.text("Colony '{p0}' advanced: {p1} → {p2}. {p3}", p0=col['name'], p1=i18n.term(col['status']), p2=i18n.term(new_stage), p3=gm_notes))
        embed=discord.Embed(title=i18n.text('{p0} Colony Advanced!', p0=STAGE_EMOJI.get(new_stage, '🏛️')),
            description=i18n.text("**{p0} {p1}**'s **{p2}** advanced to **{p3}**!\nNew yield: {p4:.0f}%", p0=owner['flag'] or '', p1=owner['name'], p2=col['name'], p3=i18n.term(new_stage), p4=next_cfg['yield_pct'] * 100),color=discord.Color.gold())
        ch_id=_cfg("announce_channel_id"); ch=self.bot.get_channel(int(ch_id)) if ch_id else None
        if ch:
            try: await ch.send(embed=embed)
            except discord.Forbidden: pass
        await interaction.response.send_message(i18n.text('✅ Colony **{p0}** → **{p1}**.', p0=col['name'], p1=i18n.term(new_stage)),ephemeral=True)

    @colony_admin_grp.command(name="setback", description="[GM] Set colony back / [GM] Cofnij kolonie")
    @app_commands.describe(cell_id="Province cell ID", reason="Reason")
    @i18n.localized
    async def colony_setback(self, interaction: discord.Interaction, cell_id: int, reason: str=""):
        if not _gm(interaction): await interaction.response.send_message(i18n.t(_lang(interaction),"gm_only"),ephemeral=True); return
        prov=_prov_by_cell(cell_id)
        if not prov: await interaction.response.send_message(i18n.text('Province {p0} not found.', p0=cell_id),ephemeral=True); return
        col=_colony_by_prov(prov["id"])
        if not col: await interaction.response.send_message(i18n.text('No colony here.'),ephemeral=True); return
        idx=COLONY_STAGES.index(col["status"])
        if idx==0:
            with db.cursor() as c:
                c.execute("DELETE FROM colonies WHERE id=?",(col["id"],))
                c.execute("UPDATE provinces SET owner_nation_id=NULL WHERE id=?",(prov["id"],))
            _log(col["nation_id"],"gm",i18n.text("Colony '{p0}' destroyed. {p1}", p0=col['name'], p1=reason))
            await interaction.response.send_message(i18n.text('🗑️ Colony **{p0}** destroyed.', p0=col['name']),ephemeral=True); return
        prev=COLONY_STAGES[idx-1]
        with db.cursor() as c:
            c.execute("UPDATE colonies SET status=?,months_in_status=0,investment_json='{}' WHERE id=?",(prev,col["id"]))
        _log(col["nation_id"],"gm",i18n.text("Colony '{p0}' set back: {p1} → {p2}. {p3}", p0=col['name'], p1=i18n.term(col['status']), p2=i18n.term(prev), p3=reason))
        await interaction.response.send_message(i18n.text('⬇️ Colony **{p0}** → **{p1}**. {p2}', p0=col['name'], p1=i18n.term(prev), p2=reason),ephemeral=True)


async def setup(bot):
    await bot.add_cog(ColonialismCog(bot))
