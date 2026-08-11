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

COLONY_STAGES = ["outpost", "settlement", "colony", "province"]
STAGE_CONFIG = {
    "outpost":    {"yield_pct":0.50,"found_cost":{"gold":500},"min_cargo":5, "advance_cost":{"gold":300}, "advance_months":6, "tech_required":0.0,"desc":"50% yield outpost."},
    "settlement": {"yield_pct":0.75,"found_cost":{},          "min_cargo":0, "advance_cost":{"gold":800}, "advance_months":12,"tech_required":3.0,"desc":"75% yield settlement."},
    "colony":     {"yield_pct":0.90,"found_cost":{},          "min_cargo":0, "advance_cost":{"gold":2000},"advance_months":24,"tech_required":5.0,"desc":"90% yield colony."},
    "province":   {"yield_pct":1.00,"found_cost":{},          "min_cargo":0, "advance_cost":{},           "advance_months":0, "tech_required":0.0,"desc":"100% yield province."},
}
STAGE_EMOJI = {"outpost":"🏕️","settlement":"🏘️","colony":"🏙️","province":"🏛️"}

def _lang(i): return i18n.get_user_language(i.user.id, i.locale.value if i.locale else None)
def _gm(i):   return bool(i.guild) and any(r.name==config.GM_ROLE_NAME for r in i.user.roles)
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

def compute_trade_route_income(nation_id): return _route_income(nation_id)

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
    @app_commands.describe(from_cell="Start cell ID", to_cell="End cell ID", name="Route name")
    async def route_add(self, interaction: discord.Interaction, from_cell: int, to_cell: int, name: str):
        lang = _lang(interaction)
        nat  = _nat_owner(str(interaction.user.id))
        if not nat: await interaction.response.send_message(i18n.t(lang,"no_nation"),ephemeral=True); return
        if from_cell == to_cell: await interaction.response.send_message("Start and end must differ.",ephemeral=True); return
        pf = _prov_by_cell(from_cell); pt = _prov_by_cell(to_cell)
        if not pf: await interaction.response.send_message(f"Province {from_cell} not found.",ephemeral=True); return
        if not pt: await interaction.response.send_message(f"Province {to_cell} not found.",ephemeral=True); return
        if pf["owner_nation_id"] != nat["id"]: await interaction.response.send_message("You don't own the starting province.",ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT id FROM trade_routes WHERE nation_id=? AND from_cell_id=? AND to_cell_id=? AND active=1", (nat["id"],from_cell,to_cell))
            if c.fetchone(): await interaction.response.send_message("Route already exists.",ephemeral=True); return
            c.execute("INSERT INTO trade_routes(nation_id,from_cell_id,to_cell_id,name) VALUES(?,?,?,?)", (nat["id"],from_cell,to_cell,name))
            route_id = c.lastrowid
        fn = pf["name"] or f"Cell #{from_cell}"; tn = pt["name"] or f"Cell #{to_cell}"
        _log(nat["id"],"player",f"Established trade route '{name}' (#{route_id}): {fn} → {tn}.")
        embed = discord.Embed(title=f"🚢 Trade Route — {name}",color=discord.Color.blue())
        embed.add_field(name="From",value=fn,inline=True); embed.add_field(name="To",value=tn,inline=True)
        embed.add_field(name="Income", value="Ships cargo×2g/tick at endpoints, or 20g flat with no ships.", inline=False)
        embed.set_footer(text=f"Route ID: {route_id} | Assign ships with /military move")
        await interaction.response.send_message(embed=embed)

    @traderoute_grp.command(name="remove", description="Remove a trade route / Usun szlak")
    @app_commands.describe(route_id="Route ID")
    async def route_remove(self, interaction: discord.Interaction, route_id: int):
        lang=_lang(interaction); nat=_nat_owner(str(interaction.user.id))
        if not nat: await interaction.response.send_message(i18n.t(lang,"no_nation"),ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT * FROM trade_routes WHERE id=? AND nation_id=?",(route_id,nat["id"])); r=c.fetchone()
        if not r: await interaction.response.send_message(f"Route #{route_id} not found.",ephemeral=True); return
        with db.cursor() as c: c.execute("UPDATE trade_routes SET active=0 WHERE id=?",(route_id,))
        _log(nat["id"],"player",f"Removed trade route '{r['name']}' (#{route_id}).")
        await interaction.response.send_message(f"🚫 Trade route **{r['name']}** removed.",ephemeral=True)

    @traderoute_grp.command(name="list", description="List your trade routes / Lista szlakow")
    async def route_list(self, interaction: discord.Interaction):
        lang=_lang(interaction); nat=_nat_owner(str(interaction.user.id))
        if not nat: await interaction.response.send_message(i18n.t(lang,"no_nation"),ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT * FROM trade_routes WHERE nation_id=? AND active=1 ORDER BY id",(nat["id"],)); routes=c.fetchall()
        if not routes: await interaction.response.send_message("No active trade routes.",ephemeral=True); return
        total_cargo = _total_cargo(nat["id"]); lines=[]; total_income=0.0
        for r in routes:
            pf=_prov_by_cell(r["from_cell_id"]); pt=_prov_by_cell(r["to_cell_id"])
            fn=pf["name"] if pf and pf["name"] else f"Cell #{r['from_cell_id']}"
            tn=pt["name"] if pt and pt["name"] else f"Cell #{r['to_cell_id']}"
            cargo=0.0
            for prov in [pf,pt]:
                if not prov: continue
                with db.cursor() as c:
                    c.execute("SELECT u.quantity,b.stats_json FROM military_units u JOIN blueprints b ON u.blueprint_id=b.id WHERE u.province_id=? AND b.type='ship'",(prov["id"],)); ships=c.fetchall()
                cargo+=sum(json.loads(s["stats_json"]).get("cargo",0)*s["quantity"] for s in ships)
            income=cargo*2.0 if cargo>0 else 20.0; total_income+=income
            lines.append(f"**#{r['id']} {r['name']}** — {fn} → {tn}\n  {income:.0f}g/tick"+( f" ({cargo:.0f} cargo)" if cargo>0 else " (flat 20g — no ships)"))
        embed=discord.Embed(title=f"🚢 Trade Routes — {nat['flag'] or ''} {nat['name']}",description="\n\n".join(lines),color=discord.Color.blue())
        embed.add_field(name="Total income",value=f"{total_income:.0f}g/tick",inline=True)
        embed.add_field(name="Fleet cargo", value=f"{total_cargo:.0f}",        inline=True)
        await interaction.response.send_message(embed=embed,ephemeral=True)

    # ---- COLONIES ----

    @colony_grp.command(name="found", description="Found a colony / Zaloz kolonie")
    @app_commands.describe(cell_id="Province cell ID", name="Colony name")
    async def colony_found(self, interaction: discord.Interaction, cell_id: int, name: str):
        lang=_lang(interaction); nat=_nat_owner(str(interaction.user.id))
        if not nat: await interaction.response.send_message(i18n.t(lang,"no_nation"),ephemeral=True); return
        prov=_prov_by_cell(cell_id)
        if not prov: await interaction.response.send_message(f"Province {cell_id} not found.",ephemeral=True); return
        if prov["owner_nation_id"] is not None: await interaction.response.send_message("Province already owned.",ephemeral=True); return
        if _colony_by_prov(prov["id"]): await interaction.response.send_message("Already a colony.",ephemeral=True); return
        cfg=STAGE_CONFIG["outpost"]; gold_cost=cfg["found_cost"].get("gold",500); min_cargo=cfg["min_cargo"]
        if nat["treasury"]<gold_cost: await interaction.response.send_message(f"Need {gold_cost}g, have {nat['treasury']:.0f}g.",ephemeral=True); return
        if _total_cargo(nat["id"])<min_cargo: await interaction.response.send_message(f"Need {min_cargo} fleet cargo capacity. Build ships with cargo modules.",ephemeral=True); return
        with db.cursor() as c:
            c.execute("UPDATE provinces SET owner_nation_id=? WHERE id=?",(nat["id"],prov["id"]))
            c.execute("INSERT INTO colonies(nation_id,province_id,name,status) VALUES(?,?,?,?)",(nat["id"],prov["id"],name,"outpost")); col_id=c.lastrowid
            c.execute("UPDATE nations SET treasury=treasury-? WHERE id=?",(gold_cost,nat["id"]))
        _log(nat["id"],"system",f"Founded colony '{name}' (#{col_id}) at {prov['name'] or f'Cell #{cell_id}'}. Cost: {gold_cost}g.")
        embed=discord.Embed(title=f"🏕️ Colony Founded — {name}",description=f"Outpost established at **{prov['name'] or f'Cell #{cell_id}'}**.\nProduces **50%** of base resources.\nInvest and wait 6 months to advance to Settlement.",color=discord.Color.green())
        embed.add_field(name="Cost",value=f"{gold_cost}g",inline=True); embed.add_field(name="Status",value="🏕️ Outpost",inline=True)
        await interaction.response.send_message(embed=embed)

    @colony_grp.command(name="develop", description="Invest in colony / Inwestuj w kolonie")
    @app_commands.describe(cell_id="Province cell ID", gold_amount="Gold to invest")
    async def colony_develop(self, interaction: discord.Interaction, cell_id: int, gold_amount: int):
        lang=_lang(interaction); nat=_nat_owner(str(interaction.user.id))
        if not nat: await interaction.response.send_message(i18n.t(lang,"no_nation"),ephemeral=True); return
        prov=_prov_by_cell(cell_id)
        if not prov or prov["owner_nation_id"]!=nat["id"]: await interaction.response.send_message("Province not found or not yours.",ephemeral=True); return
        col=_colony_by_prov(prov["id"])
        if not col: await interaction.response.send_message("No colony here. Use /colony found first.",ephemeral=True); return
        if col["status"]=="province": await interaction.response.send_message("Already fully integrated.",ephemeral=True); return
        if col["nation_id"]!=nat["id"]: await interaction.response.send_message("Not your colony.",ephemeral=True); return
        if nat["treasury"]<gold_amount: await interaction.response.send_message(f"Not enough gold. Have {nat['treasury']:.0f}g.",ephemeral=True); return
        inv=json.loads(col["investment_json"]); inv["gold"]=inv.get("gold",0)+gold_amount
        cfg=STAGE_CONFIG[col["status"]]; adv_cost=cfg["advance_cost"].get("gold",0); adv_mo=cfg["advance_months"]
        with db.cursor() as c:
            c.execute("UPDATE colonies SET investment_json=? WHERE id=?",(json.dumps(inv),col["id"]))
            c.execute("UPDATE nations SET treasury=treasury-? WHERE id=?",(gold_amount,nat["id"]))
        _log(nat["id"],"player",f"Invested {gold_amount}g in colony '{col['name']}'. Total: {inv['gold']:.0f}/{adv_cost}g.")
        pct=min(100,int(inv["gold"]/adv_cost*100)) if adv_cost>0 else 100
        bar="█"*(pct//10)+"░"*(10-pct//10)
        next_stage=COLONY_STAGES[COLONY_STAGES.index(col["status"])+1] if col["status"]!="province" else "province"
        ready=pct>=100 and col["months_in_status"]>=adv_mo
        embed=discord.Embed(title=f"💰 Investment — {col['name']}",color=discord.Color.gold())
        embed.add_field(name="Invested",value=f"{gold_amount}g",inline=True)
        embed.add_field(name="Total",   value=f"{inv['gold']:.0f}/{adv_cost}g",inline=True)
        embed.add_field(name=f"→ {next_stage.capitalize()}",value=f"`{bar}` {pct}% gold | {col['months_in_status']}/{adv_mo} months",inline=False)
        if ready: embed.add_field(name="✅ Ready!",value="Ask the GM to run /colonymgr advance.",inline=False)
        await interaction.response.send_message(embed=embed)

    @colony_grp.command(name="list", description="List colonies / Lista kolonii")
    @app_commands.describe(nation="Nation name or blank for your own")
    async def colony_list(self, interaction: discord.Interaction, nation: str=""):
        lang=_lang(interaction); nat=_nat_name(nation) if nation else _nat_owner(str(interaction.user.id))
        if not nat: await interaction.response.send_message(i18n.t(lang,"nation_not_found" if nation else "no_nation"),ephemeral=True); return
        with db.cursor() as c:
            c.execute("SELECT c.*,p.name as pname,p.azgaar_cell_id FROM colonies c JOIN provinces p ON c.province_id=p.id WHERE c.nation_id=? ORDER BY c.id",(nat["id"],)); cols=c.fetchall()
        if not cols: await interaction.response.send_message(f"**{nat['name']}** has no colonies.",ephemeral=True); return
        embed=discord.Embed(title=f"🗺️ Colonies — {nat['flag'] or ''} {nat['name']}",color=discord.Color.dark_green())
        for col in cols:
            cfg=STAGE_CONFIG[col["status"]]; inv=json.loads(col["investment_json"])
            adv_cost=cfg["advance_cost"].get("gold",0); adv_mo=cfg["advance_months"]
            pname=col["pname"] or f"Cell #{col['azgaar_cell_id']}"
            pct_g=min(100,int(inv.get("gold",0)/adv_cost*100)) if adv_cost>0 else 100
            pct_t=min(100,int(col["months_in_status"]/adv_mo*100)) if adv_mo>0 else 100
            ready=pct_g>=100 and pct_t>=100 and col["status"]!="province"
            embed.add_field(name=f"{STAGE_EMOJI.get(col['status'],'🏕️')} {col['name']} — {pname}",
                value=f"**{col['status'].capitalize()}** | {cfg['yield_pct']*100:.0f}% yield\n{col['months_in_status']}/{adv_mo}mo | {inv.get('gold',0):.0f}/{adv_cost}g"+("\n✅ Ready — ask GM to advance" if ready else ""),inline=False)
        await interaction.response.send_message(embed=embed,ephemeral=True)

    @colony_grp.command(name="view", description="View colony details / Szczegoly kolonii")
    @app_commands.describe(cell_id="Province cell ID")
    async def colony_view(self, interaction: discord.Interaction, cell_id: int):
        prov=_prov_by_cell(cell_id)
        if not prov: await interaction.response.send_message(f"Province {cell_id} not found.",ephemeral=True); return
        col=_colony_by_prov(prov["id"])
        if not col: await interaction.response.send_message("No colony here.",ephemeral=True); return
        with db.cursor() as c: c.execute("SELECT * FROM nations WHERE id=?",(col["nation_id"],)); owner=c.fetchone()
        cfg=STAGE_CONFIG[col["status"]]; inv=json.loads(col["investment_json"])
        adv_cost=cfg["advance_cost"].get("gold",0); adv_mo=cfg["advance_months"]
        base_res=json.loads(prov["base_resources_json"])
        eff_res={k:v*cfg["yield_pct"] for k,v in base_res.items()}
        embed=discord.Embed(title=f"{STAGE_EMOJI.get(col['status'],'🏕️')} {col['name']}",color=discord.Color.dark_green())
        embed.add_field(name="Owner",  value=f"{owner['flag'] or ''} {owner['name']}",inline=True)
        embed.add_field(name="Status", value=col["status"].capitalize(),               inline=True)
        embed.add_field(name="Yield",  value=f"{cfg['yield_pct']*100:.0f}%",          inline=True)
        embed.add_field(name="Resources",value=", ".join(f"{k}:{v:.1f}" for k,v in eff_res.items() if v>0) or "—",inline=False)
        if col["status"]!="province":
            pct_g=min(100,int(inv.get("gold",0)/adv_cost*100)) if adv_cost>0 else 100
            pct_t=min(100,int(col["months_in_status"]/adv_mo*100)) if adv_mo>0 else 100
            bg="█"*(pct_g//10)+"░"*(10-pct_g//10); bt="█"*(pct_t//10)+"░"*(10-pct_t//10)
            idx=COLONY_STAGES.index(col["status"]); next_s=COLONY_STAGES[idx+1]
            embed.add_field(name=f"→ {next_s.capitalize()}",value=f"💰 `{bg}` {inv.get('gold',0):.0f}/{adv_cost}g\n📅 `{bt}` {col['months_in_status']}/{adv_mo}mo\n🔬 Tech req: {STAGE_CONFIG[next_s].get('tech_required',0):.0f}",inline=False)
        if col["gm_notes"]: embed.add_field(name="GM Notes",value=col["gm_notes"],inline=False)
        embed.set_footer(text=f"Founded: {col['founded_at'][:10]}")
        await interaction.response.send_message(embed=embed)

    # ---- GM COLONY MANAGEMENT ----

    @colony_admin_grp.command(name="advance", description="[GM] Advance colony / [GM] Awansuj kolonie")
    @app_commands.describe(cell_id="Province cell ID", gm_notes="Optional notes")
    async def colony_advance(self, interaction: discord.Interaction, cell_id: int, gm_notes: str=""):
        if not _gm(interaction): await interaction.response.send_message(i18n.t(_lang(interaction),"gm_only"),ephemeral=True); return
        prov=_prov_by_cell(cell_id)
        if not prov: await interaction.response.send_message(f"Province {cell_id} not found.",ephemeral=True); return
        col=_colony_by_prov(prov["id"])
        if not col: await interaction.response.send_message("No colony here.",ephemeral=True); return
        if col["status"]=="province": await interaction.response.send_message("Already fully integrated.",ephemeral=True); return
        with db.cursor() as c: c.execute("SELECT * FROM nations WHERE id=?",(col["nation_id"],)); owner=c.fetchone()
        idx=COLONY_STAGES.index(col["status"]); new_stage=COLONY_STAGES[idx+1]
        next_cfg=STAGE_CONFIG[new_stage]
        if _colonial_tech(owner)<next_cfg.get("tech_required",0):
            await interaction.response.send_message(f"{owner['name']} lacks Colonial tech (needs {next_cfg['tech_required']:.0f}).",ephemeral=True); return
        with db.cursor() as c:
            c.execute("UPDATE colonies SET status=?,months_in_status=0,investment_json='{}',gm_notes=? WHERE id=?",(new_stage,gm_notes,col["id"]))
        _log(col["nation_id"],"gm",f"Colony '{col['name']}' advanced: {col['status']} → {new_stage}. {gm_notes}")
        embed=discord.Embed(title=f"{STAGE_EMOJI.get(new_stage,'🏛️')} Colony Advanced!",
            description=f"**{owner['flag'] or ''} {owner['name']}**'s **{col['name']}** advanced to **{new_stage.capitalize()}**!\nNew yield: {next_cfg['yield_pct']*100:.0f}%",color=discord.Color.gold())
        ch_id=_cfg("announce_channel_id"); ch=self.bot.get_channel(int(ch_id)) if ch_id else None
        if ch:
            try: await ch.send(embed=embed)
            except discord.Forbidden: pass
        await interaction.response.send_message(f"✅ Colony **{col['name']}** → **{new_stage}**.",ephemeral=True)

    @colony_admin_grp.command(name="setback", description="[GM] Set colony back / [GM] Cofnij kolonie")
    @app_commands.describe(cell_id="Province cell ID", reason="Reason")
    async def colony_setback(self, interaction: discord.Interaction, cell_id: int, reason: str=""):
        if not _gm(interaction): await interaction.response.send_message(i18n.t(_lang(interaction),"gm_only"),ephemeral=True); return
        prov=_prov_by_cell(cell_id)
        if not prov: await interaction.response.send_message(f"Province {cell_id} not found.",ephemeral=True); return
        col=_colony_by_prov(prov["id"])
        if not col: await interaction.response.send_message("No colony here.",ephemeral=True); return
        idx=COLONY_STAGES.index(col["status"])
        if idx==0:
            with db.cursor() as c:
                c.execute("DELETE FROM colonies WHERE id=?",(col["id"],))
                c.execute("UPDATE provinces SET owner_nation_id=NULL WHERE id=?",(prov["id"],))
            _log(col["nation_id"],"gm",f"Colony '{col['name']}' destroyed. {reason}")
            await interaction.response.send_message(f"🗑️ Colony **{col['name']}** destroyed.",ephemeral=True); return
        prev=COLONY_STAGES[idx-1]
        with db.cursor() as c:
            c.execute("UPDATE colonies SET status=?,months_in_status=0,investment_json='{}' WHERE id=?",(prev,col["id"]))
        _log(col["nation_id"],"gm",f"Colony '{col['name']}' set back: {col['status']} → {prev}. {reason}")
        await interaction.response.send_message(f"⬇️ Colony **{col['name']}** → **{prev}**. {reason}",ephemeral=True)


async def setup(bot):
    await bot.add_cog(ColonialismCog(bot))
