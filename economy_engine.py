"""Monthly economy: automatic staffing, explicit inputs, atomic settlement."""
import copy
import json
import math
from datetime import datetime, timezone, timedelta

import db
import i18n

TAX = {'low': 8, 'normal': 12, 'high': 16}
LEVEL_OUTPUT = {1: 1, 2: 1.7, 3: 2.4}
LEVEL_WORK = {1: 1, 2: 1.5, 3: 2}
WORKERS = {'farm':200, 'pasture':200, 'fishing_wharf':200, 'plantation':250,
           'mine':300, 'copper_mine':250, 'lumber_camp':200, 'clay_pit':150,
           'tar_works':200, 'powder_mill':250, 'cannon_foundry':300,
           'textile_mill':250, 'silk_workshop':250, 'market':150, 'port':200,
           'university':400, 'algae_farm':250, 'granary':50, 'fort':0}
FOOD = {'farm','pasture','fishing_wharf','plantation'}
TRADE = {'market','port','silk_workshop','plantation'}
DEFAULT_POLICY = dict(tax='normal', priority='balanced', luxury='auto', unrest=0.,
                      arrears=0., unpaid_months=0, hunger_months=0)


def read_json(value, default=None):
    if isinstance(value,str) and value:return json.loads(value)
    if value is None or value=='':return copy.deepcopy(default if default is not None else {})
    return value


def lock_nation(c, nid):
    c.execute('SELECT * FROM nations WHERE id=?' + (' FOR UPDATE' if db.USE_POSTGRES else ''), (nid,))
    nation = c.fetchone()
    if not nation:
        raise ValueError(i18n.text('Nation not found.'))
    return nation


def policy(c, nid):
    c.execute('SELECT * FROM economy_policy WHERE nation_id=?', (nid,))
    return dict(c.fetchone() or DEFAULT_POLICY)


def save_policy(c, nid, p):
    keys = list(DEFAULT_POLICY)
    c.execute('INSERT INTO economy_policy(nation_id,' + ','.join(keys) + ') VALUES(' + ','.join('?' for _ in range(len(keys)+1)) +
              ') ON CONFLICT(nation_id) DO UPDATE SET ' + ','.join(k+'=excluded.'+k for k in keys),
              (nid, *(p[k] for k in keys)))


def set_policy(nid, key, value, uid=None):
    choices = {'tax': TAX, 'priority': ('balanced','food','industry','trade','science'),
               'luxury': ('auto','stockpile','consume','sell')}
    if key not in choices or value not in choices[key]:
        raise ValueError('Invalid economy policy')
    with db.atomic() as c:
        from world_service import world_lock,owned
        world_lock(c)
        if uid is not None:owned(c,nid,uid)
        else:lock_nation(c, nid)
        p = policy(c, nid)
        p[key] = value
        save_policy(c, nid, p)


def military_cost(c, nid):
    from cogs.military import HULLS, LAND_UNITS
    from technology import bonuses
    effects=bonuses(c,nid)
    c.execute('SELECT u.*,b.type AS btype,b.hull,m.mode FROM military_units u LEFT JOIN blueprints b '
              'ON u.blueprint_id=b.id LEFT JOIN military_posture m ON m.unit_id=u.id WHERE u.nation_id=?', (nid,))
    units = c.fetchall()
    total = 0.
    for u in units:
        catalog = HULLS if u['btype'] == 'ship' else LAND_UNITS
        base = catalog.get(u['hull'], {}).get('peace_upkeep', 5 if u['btype']=='ship' else 2)
        discount=effects.get('naval_upkeep' if u['btype']=='ship' else 'land_upkeep',0)
        total += base * u['quantity'] * {'reserve':.35, 'deployed':1.5}.get(u['mode'], 1) * (1+discount)
    return total, units


def snapshot(c, nid):
    c.execute('SELECT * FROM nations WHERE id=?', (nid,))
    n = c.fetchone()
    from technology import bonuses
    n=dict(n, research_bonuses=bonuses(c,nid))
    c.execute('SELECT p.*,d.levels_json,c.status AS colony_status,a.province_id AS algae_site,l.allocations_json FROM provinces p '
              'LEFT JOIN province_development d ON d.province_id=p.id LEFT JOIN colonies c ON c.province_id=p.id '
              'LEFT JOIN algae_sites a ON a.province_id=p.id '
              'LEFT JOIN province_labor l ON l.province_id=p.id AND l.nation_id=p.owner_nation_id '
              'WHERE p.owner_nation_id=? AND p.active=1 ORDER BY p.id', (nid,))
    provinces = c.fetchall()
    c.execute('SELECT * FROM building_defs ORDER BY key')
    definitions = {r['key']: r for r in c.fetchall()}
    cost, units = military_cost(c, nid)
    c.execute('SELECT r.*,a.ship_id,b.stats_json,u.quantity,m.mode FROM trade_routes r '
              'JOIN route_assignments a ON a.route_id=r.id JOIN military_units u ON u.id=a.ship_id AND u.nation_id=r.nation_id '
              'JOIN blueprints b ON b.id=u.blueprint_id LEFT JOIN military_posture m ON m.unit_id=u.id '
              'WHERE r.nation_id=? AND r.active=1', (nid,))
    capacity = 0.
    by_cell = {p['azgaar_cell_id']:p for p in provinces}
    for route in c.fetchall():
        # Only one assigned ship group per route; a functioning home port is required.
        if route['mode'] in ('reserve','mobilizing'):
            continue
        if any('port' in read_json(by_cell.get(route[k], {}).get('buildings_json'), [])
               for k in ('from_cell_id','to_cell_id')):
            capacity += max(0, read_json(route['stats_json']).get('cargo', 0)) * route['quantity']
    capacity *= 1+n['research_bonuses'].get('cargo',0)
    return n, provinces, definitions, policy(c,nid), cost, units, capacity


def project(nation, provinces, definitions, prefs, military_upkeep=0, units=(), cargo=0):
    """Pure one-month forecast. Shared unchanged by dashboard and settlement."""
    p = dict(prefs)
    effects=nation.get('research_bonuses',{})
    res = {k:max(0,float(v)) for k,v in read_json(nation['resources_json']).items() if isinstance(v,(float,int))}
    start_gold = float(nation['treasury'])
    stability = min(100, max(0, float(nation['stability'])))
    stab = .75 + stability / 400
    taxes = output_gold = building_upkeep = 0.
    production, staffing, populations = {}, [], {}
    population = sum(max(0,r['population']) for r in provinces)
    food_need = population/100 + sum(u['quantity'] for u in units)/10
    granaries = 0
    market_capacity = 0.
    port_staff = []
    jobs = []
    for prov in provinces:
        pop = max(0, prov['population'])
        workforce = pop * .4
        col = {'outpost':.5,'settlement':.75,'colony':.9}.get(prov.get('colony_status'), 1)
        levels = read_json(prov.get('levels_json'))
        buildings = list(dict.fromkeys(read_json(prov['buildings_json'], [])))
        blocked_algae=not prov.get('algae_site') or read_json(nation['tech_json']).get('economy',3)<6
        manual={k:min(max(0,v),WORKERS.get(k,200)*LEVEL_WORK[min(3,max(1,int(levels.get(k,1))))])
                for k,v in read_json(prov.get('allocations_json')).items()
                if k in buildings and k in definitions and isinstance(v,(int,float)) and math.isfinite(v)
                and not (k=='algae_farm' and blocked_algae)}
        requested=sum(manual.values())
        scale=min(1,workforce/requested) if requested else 1
        assigned={k:v*scale for k,v in manual.items()}
        workforce=max(0,workforce-sum(assigned.values()))
        def rank(key):
            if key in FOOD: return 0  # Feeding people is automatic, including industrial/science presets.
            preferred = {'science':{'university'}, 'trade':TRADE, 'industry':set(WORKERS)-FOOD-TRADE-{'university'}}.get(p['priority'], set())
            return 1 if key in preferred else 2
        market_bonus = 0.
        for key in sorted(buildings, key=lambda k:(rank(k),k)):
            bd = definitions.get(key)
            if not bd: continue
            if key=='algae_farm' and blocked_algae:
                staffing.append(dict(cell=prov['azgaar_cell_id'],building=key,level=levels.get(key,1),staff=0,workers=0,need=250*LEVEL_WORK[min(3,max(1,int(levels.get(key,1))))],blocked='algae_site_or_tech'))
                continue  # Dormant legacy farms have no workers or upkeep.
            level = min(3,max(1,int(levels.get(key,1))))
            need = WORKERS.get(key,200)*LEVEL_WORK[level]
            workers=assigned[key] if key in assigned else min(need,workforce)
            ratio=workers/need if need else 1.
            if key not in assigned:workforce=max(0,workforce-workers)
            amount = LEVEL_OUTPUT[level]*ratio*stab*col
            staffing.append(dict(cell=prov['azgaar_cell_id'],building=key,level=level,staff=round(ratio,3),
                                 workers=round(workers,2),need=need,manual=key in manual,
                                 requested=manual.get(key),scaled=key in manual and scale<1))
            building_upkeep += max(0, read_json(bd['upkeep_json']).get('gold',0))*LEVEL_WORK[level]*(.25+.75*ratio)
            if key=='market':
                market_bonus += .15*LEVEL_OUTPUT[level]*ratio
                market_capacity += pop/1000*ratio
            if key=='port':
                port_staff.append(ratio)
            if key=='granary': granaries += ratio*LEVEL_OUTPUT[level]
            outputs={k:v for k,v in read_json(bd['effect_json']).items() if isinstance(v,(int,float)) and math.isfinite(v)}
            from technology import ALGAE_YIELD
            if outputs.get('algae',0)>0:
                if key=='algae_farm':outputs['algae']=min(ALGAE_YIELD,outputs['algae'])
                else:outputs.pop('algae')
            for resource,value in list(outputs.items()):
                if value<=0 or resource=='algae':continue
                bonus=effects.get('knowledge',0) if resource=='universal_knowledge' else effects.get('production',0)
                if key=='farm' and resource=='food':bonus+=effects.get('farm_food',0)
                outputs[resource]=value*(1+bonus)
            jobs.append((key,outputs,amount))
        taxes += pop/1000*TAX[p['tax']]*stab*col*(1+market_bonus)*max(.5,1-p['unrest']/200)*(1+effects.get('taxes',0))
        for k,v in read_json(prov['base_resources_json']).items():
            if k=='algae':continue  # A deposit needs a staffed extractor.
            if isinstance(v,(int,float)):
                if k=='gold':output_gold+=max(0,v)*stab*col
                else:production[k] = production.get(k,0)+max(0,v)*stab*col
    # Extractors first; downstream producers can use this month's raw materials.
    for key,outputs,amount in sorted(jobs,key=lambda j: any(v<0 for v in j[1].values() if isinstance(v,(int,float)))):
        ratio = 1.
        for k,v in outputs.items():
            if v < 0:
                available = start_gold + taxes + output_gold if k=='gold' else res.get(k,0)+production.get(k,0)
                ratio = min(ratio,max(0,available/(-v*amount))) if amount else 0
        for k,v in outputs.items():
            delta = v*amount*ratio
            if k=='gold': output_gold += delta
            else: production[k] = production.get(k,0)+delta
    for k,v in production.items():
        res[k] = max(0,res.get(k,0)+v)
    # Consumption uses goods. Auto keeps three months of luxury use and exports surplus.
    luxury_gold = luxury_used = 0.
    use = population/1000
    export_capacity = market_capacity + cargo*(max(port_staff) if port_staff else 0)
    for key,price in (('silk',4),('spices',3)):
        if p['luxury'] in ('auto','consume'):
            used = min(res.get(key,0),use)
            res[key] = res.get(key,0)-used
            luxury_used += used
        if p['luxury'] in ('auto','sell'):
            reserve = use*3 if p['luxury']=='auto' else 0
            sold = min(export_capacity,max(0,res.get(key,0)-reserve))
            res[key] = res.get(key,0)-sold
            luxury_gold += sold*price
            export_capacity -= sold
    food_have = res.get('food',0)
    shortage = max(0,food_need-food_have)
    fed = min(1,food_have/food_need) if food_need else 1
    res['food'] = max(0,food_have-food_need)
    p['hunger_months'] = p['hunger_months']+1 if shortage else 0
    p['unrest'] = min(100,max(0,p['unrest']+{'low':-3,'normal':-1,'high':3}[p['tax']]))
    growth = 0.
    if shortage:
        if p['hunger_months'] >= 2: stability -= (1-fed)*8
        if p['hunger_months'] >= 3 and fed<.5: growth = -(0.5-fed)*.02
    elif food_have >= food_need*1.2:
        growth = {'low':.003,'normal':.002,'high':.0005}[p['tax']]
        if luxury_used >= use and use: growth += .001
    if luxury_used and use: stability += min(1,luxury_used/(use*2))
    if p['tax']=='high': stability -= p['unrest']/100
    elif p['tax']=='low': stability += .25
    spoilage = max(0,res['food']-food_need*3)*(.02-.015*min(1,granaries))*(1+effects.get('spoilage',0))
    res['food'] -= spoilage
    for prov in provinces:
        populations[prov['id']] = max(0,int(prov['population']*(1+growth)))
    gold_income = taxes+output_gold+luxury_gold
    upkeep = building_upkeep+military_upkeep
    due = upkeep+p['arrears']
    cash = max(0,start_gold+gold_income)
    paid = min(cash,due)
    p['arrears'] = round(max(0,due-paid),2)
    p['unpaid_months'] = p['unpaid_months']+1 if p['arrears']>.01 else 0
    # Two-month adjustment period is represented by a zero arrears history at migration.
    return dict(resources=res,treasury=round(cash-paid,2),stability=min(100,max(0,stability)),
                population=sum(populations.values()),populations=populations,policy=p,staffing=staffing,
                income=round(gold_income,2),taxes=round(taxes,2),upkeep=round(upkeep,2),
                balance=round(gold_income-upkeep,2),food_needed=food_need,food_shortage=shortage,
                food_change=res['food']-read_json(nation['resources_json']).get('food',0),
                food_months=res['food']/food_need if food_need else None,spoilage=spoilage,
                luxury_income=luxury_gold,production=production)


def forecast(nid):
    class PreviewRollback(Exception):
        def __init__(self,result):self.result=result
    try:
        with db.atomic() as c:
            run_month()
            c.execute('SELECT report_json FROM economy_months ORDER BY month_index DESC LIMIT 1')
            result=json.loads(c.fetchone()['report_json'])[str(nid)]
            raise PreviewRollback(result)
    except PreviewRollback as preview:
        return preview.result


def _config(c,key,default=''):
    c.execute('SELECT value FROM game_config WHERE key=?',(key,))
    row=c.fetchone()
    return row['value'] if row else default


def _set(c,key,value):
    c.execute('INSERT INTO game_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))


def _megaprojects(c,nid):
    from cogs.economy import _apply_mp_effect
    c.execute("SELECT * FROM megaprojects WHERE nation_id=? AND status IN ('building','complete') ORDER BY id",(nid,))
    for mp in c.fetchall():
        if mp['status']=='building':
            spent=mp['months_spent']+1
            c.execute('UPDATE megaprojects SET months_spent=? WHERE id=?',(spent,mp['id']))
            if mp['duration_months']>0 and spent>=mp['duration_months']:
                c.execute("UPDATE megaprojects SET status='complete',completed_at=CURRENT_TIMESTAMP WHERE id=?",(mp['id'],))
                _apply_mp_effect(nid,mp['effect_json'],mp['name'],mp['id'])
            continue  # Monthly yields start in the following month.
        effect=read_json(mp['effect_json'])
        excluded={'gold','gold_per_tick','gold_once','resources_once','stability','special_note','resources_per_tick'}
        per=effect.get('resources_per_tick',{k:v for k,v in effect.items() if k not in excluded})
        n=lock_nation(c,nid)
        res=read_json(n['resources_json'])
        resource_gold=0
        for key,value in per.items():
            if isinstance(value,dict): value=value.get('amount',value.get('value',0))
            if isinstance(value,(float,int)) and math.isfinite(value):
                if key=='algae' and value>0:continue  # Recurring extraction is confined to the rare sites.
                if key=='gold':resource_gold+=value
                else:res[key]=max(0,res.get(key,0)+value)
        gold=effect.get('gold_per_tick',effect.get('gold',0))
        if isinstance(gold,dict): gold=gold.get('amount',gold.get('value',0))
        if not isinstance(gold,(float,int)) or not math.isfinite(gold): gold=0
        c.execute('UPDATE nations SET resources_json=?,treasury=? WHERE id=?',(json.dumps(res),max(0,n['treasury']+gold+resource_gold),nid))


def run_month(expected_month=None, scheduled_at=None, hours=24):
    """Calendar, balances and journal commit together. Failed months can be retried."""
    with db.atomic() as c:
        c.execute("INSERT INTO economy_meta(key,value) VALUES('tick_lock','1') ON CONFLICT(key) DO NOTHING")
        c.execute("SELECT value FROM economy_meta WHERE key='tick_lock'"+(' FOR UPDATE' if db.USE_POSTGRES else ''))
        c.fetchone()
        from world_service import world_lock,progress_goals
        world_lock(c)
        month=int(_config(c,'current_month','1')); year=int(_config(c,'current_year','1'))
        current=year*12+month-1
        if expected_month is not None and current!=expected_month: return None
        if scheduled_at is not None:
            if _config(c,'calendar_running','0')!='1': return None
            stamp=_config(c,'last_tick_ts')
            if not stamp: return None
            last=datetime.fromisoformat(stamp)
            hours=float(_config(c,'hours_per_month','24'))
            if hours<=0 or (scheduled_at-last).total_seconds()<hours*3600: return None
        # Match the lock order of trade acceptance, then read all balances only after locks.
        lock=' FOR UPDATE' if db.USE_POSTGRES else ''
        c.execute('SELECT id FROM trades ORDER BY id'+lock); c.fetchall()
        c.execute('SELECT * FROM nations ORDER BY id'+lock); nations=c.fetchall()
        c.execute('SELECT id FROM provinces ORDER BY id'+lock); c.fetchall()
        c.execute('SELECT id FROM military_units ORDER BY id'+lock); c.fetchall()
        target=current+1
        c.execute('SELECT month_index FROM economy_months WHERE month_index=?',(target,))
        if c.fetchone(): raise ValueError('This month has already been settled.')
        from economy_services import settle_contracts
        from treaty_service import tick as treaty_tick
        treaty_tick(c,target)
        settle_contracts(c,target)
        c.execute("UPDATE military_posture SET mode='active' WHERE mode='mobilizing' AND ready_month<=?",(target,))
        reports={}
        for n in nations:
            nid=n['id']
            from technology import fund_programs,tick_research
            programs=fund_programs(c,nid)
            with i18n.using_language(i18n.get_user_language(n['owner_id'])):
                _megaprojects(c,nid)
            data=snapshot(c,nid)
            transfers=data[0]['treasury']-n['treasury']
            result=project(*data)
            result['income']+=transfers
            result['balance']+=transfers
            result['food_change']=result['resources']['food']-read_json(n['resources_json']).get('food',0)
            c.execute('UPDATE nations SET resources_json=?,treasury=?,stability=?,population=? WHERE id=?',
                      (json.dumps(result['resources']),result['treasury'],result['stability'],result['population'],nid))
            save_policy(c,nid,result['policy'])
            with i18n.using_language(i18n.get_user_language(n['owner_id'])):
                completed,resources=tick_research(c,nid,target)
            result['resources']=resources
            result['research_completed']=completed
            result['algae_programs']=programs
            for pid,pop in result['populations'].items(): c.execute('UPDATE provinces SET population=? WHERE id=?',(pop,pid))
            if result['policy']['unpaid_months']>=3:
                from battle_resolution import allocate_losses
                c.execute('SELECT id,quantity FROM military_units WHERE nation_id=?',(nid,))
                groups=[(u['id'],u['quantity'],u['quantity']) for u in c.fetchall()]
                for loss in allocate_losses(groups,2):
                    c.execute('UPDATE military_units SET quantity=quantity-? WHERE id=?',(loss['lost'],loss['unit_id']))
                c.execute('DELETE FROM military_units WHERE nation_id=? AND quantity<=0',(nid,))
            from cogs.colonialism import tick_colonies
            with i18n.using_language(i18n.get_user_language(n['owner_id'])):
                tick_colonies(nid,1)
            progress_goals(c,nid,target,result)
            reports[str(nid)]=result
        year,month=target//12,target%12+1
        _set(c,'current_month',month); _set(c,'current_year',year)
        if scheduled_at is not None: _set(c,'last_tick_ts',(last+timedelta(hours=hours)).isoformat())
        else: _set(c,'last_tick_ts',datetime.now(timezone.utc).isoformat())
        c.execute('INSERT INTO economy_months(month_index,report_json) VALUES(?,?)',(target,json.dumps(reports)))
        summaries=[]
        for n in nations:
            r=reports[str(n['id'])]
            summaries.append(f"{n['name']}: {r['balance']:+.1f}g; {r['treasury']:.1f}g")
            lang=i18n.get_user_language(n['owner_id'])
            debt='zaległości' if lang=='pl' else 'arrears'
            c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)',
                      (n['id'],'system',f"{month}/{year}: {r['balance']:+.1f}g; {debt} {r['policy']['arrears']:.1f}g"))
        return month,year,summaries


def run_tick(months=1):
    if not isinstance(months,int) or not 1<=months<=120: raise ValueError('Months must be 1–120')
    result=None
    for _ in range(months): result=run_month()
    return result
