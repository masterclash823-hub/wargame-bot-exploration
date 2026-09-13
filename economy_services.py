"""Player actions for the economy; all deductions recheck current balances."""
import json
import math
import db
import i18n
from economy_engine import lock_nation,read_json,policy,save_policy,_config


def spend(c,n, cost):
    res=read_json(n['resources_json'])
    for key,amount in cost.items():
        if not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<0:
            raise ValueError(i18n.text('Invalid cost.'))
        have=n['treasury'] if key=='gold' else res.get(key,0)
        if have<amount: raise ValueError(i18n.text('Not enough {p0}.',p0=i18n.term(key)))
    for key,amount in cost.items():
        if key!='gold': res[key]=res.get(key,0)-amount
    c.execute('UPDATE nations SET resources_json=?,treasury=treasury-? WHERE id=?',
              (json.dumps(res),cost.get('gold',0),n['id']))


def build(nid,cell,key,upgrade=False,uid=None):
    from cogs.economy import _tech_ok,_terrain_ok
    from world_service import world_lock,owned
    with db.atomic() as c:
        world_lock(c)
        n=owned(c,nid,uid) if uid is not None else lock_nation(c,nid)
        c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND owner_nation_id=? AND active=1'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(cell,nid))
        p=c.fetchone()
        if not p: raise ValueError(i18n.text('Province not found or not yours.'))
        c.execute('SELECT * FROM building_defs WHERE key=?',(key,)); b=c.fetchone()
        if not b: raise ValueError(i18n.text('Unknown building.'))
        buildings=read_json(p['buildings_json'],[])
        c.execute('SELECT levels_json FROM province_development WHERE province_id=?',(p['id'],)); row=c.fetchone()
        levels=read_json(row['levels_json']) if row else {}
        old=levels.get(key,1) if key in buildings else 0
        if (upgrade and not old) or (not upgrade and old) or old>=3:
            raise ValueError(i18n.text('Building already exists, is missing, or has reached level 3.'))
        if key=='algae_farm':
            c.execute('SELECT province_id FROM algae_sites WHERE province_id=?',(p['id'],))
            if not c.fetchone():
                raise ValueError(i18n.text('Algae extraction requires a rare deposit. See /algae locations.'))
        required=max(6 if upgrade else 3,b['requires_tech']) if key=='algae_farm' else b['requires_tech']
        if (key!='algae_farm' and not _terrain_ok(p['terrain'],b['requires_terrain'])) or not _tech_ok(n,required,key):
            raise ValueError(i18n.text('Terrain or technology requirements are not met.'))
        multiplier={0:1,1:1.5,2:2}[old]
        cost={k:v*multiplier for k,v in read_json(b['cost_json']).items()}
        spend(c,n,cost)
        levels[key]=old+1
        if not old: buildings.append(key)
        c.execute('UPDATE provinces SET buildings_json=?,fortification_level=fortification_level+? WHERE id=?',
                  (json.dumps(buildings),1 if key=='fort' else 0,p['id']))
        c.execute('INSERT INTO province_development(province_id,levels_json) VALUES(?,?) '
                  'ON CONFLICT(province_id) DO UPDATE SET levels_json=excluded.levels_json',(p['id'],json.dumps(levels)))
        from world_service import activity
        activity(c,'building',nid,f"building:{p['id']}:{key}:{old+1}",{'building':key,'level':old+1,'cell':cell})
        return old+1,cost


def set_posture(nid,unit_id,mode):
    if mode not in ('reserve','active','deployed'): raise ValueError('Invalid mode')
    with db.atomic() as c:
        lock_nation(c,nid)
        c.execute('SELECT * FROM military_units WHERE id=? AND nation_id=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(unit_id,nid))
        if not c.fetchone(): raise ValueError(i18n.text('Unit not found or not yours.'))
        c.execute("SELECT forces_json FROM battle_plans WHERE nation_id=? AND status IN ('unmatched','matched')",(nid,))
        if any(any(int(x.get('unit_id',0))==unit_id for x in read_json(p['forces_json'],[])) for p in c.fetchall()):
            raise ValueError(i18n.text('This unit is assigned to a pending battle plan.'))
        c.execute('SELECT route_id FROM route_assignments WHERE ship_id=?',(unit_id,))
        if c.fetchone(): raise ValueError(i18n.text('Cancel the trade route before changing this ship.'))
        c.execute('SELECT * FROM military_posture WHERE unit_id=?',(unit_id,)); old=c.fetchone()
        ready=0
        if old and old['mode']=='mobilizing' and mode!='reserve': return 'mobilizing'
        if old and old['mode']=='reserve' and mode!='reserve':
            mode='mobilizing'
            ready=int(_config(c,'current_year','1'))*12+int(_config(c,'current_month','1'))
        c.execute('INSERT INTO military_posture(unit_id,mode,ready_month) VALUES(?,?,?) '
                  'ON CONFLICT(unit_id) DO UPDATE SET mode=excluded.mode,ready_month=excluded.ready_month',(unit_id,mode,ready))
        return mode


def assert_ready(c,nid,unit_id):
    c.execute('SELECT mode FROM military_posture WHERE unit_id=?',(unit_id,)); r=c.fetchone()
    if r and r['mode'] in ('reserve','mobilizing'):
        raise ValueError(i18n.text('Mobilize this unit before assigning it to a battle.'))
    c.execute('SELECT route_id FROM route_assignments WHERE ship_id=?',(unit_id,))
    if c.fetchone(): raise ValueError(i18n.text('This ship is assigned to a trade route.'))


def submit_plan(nid, forces, location, orders, note):
    """Lock the nation before rechecking commitments and reserving units."""
    with db.atomic() as c:
        lock_nation(c,nid)
        c.execute("SELECT forces_json FROM battle_plans WHERE nation_id=? AND status IN ('unmatched','matched')",(nid,))
        committed={int(f['unit_id']) for p in c.fetchall() for f in read_json(p['forces_json'],[])}
        fresh=[]
        for uid in dict.fromkeys(int(f['unit_id']) for f in forces):
            if uid in committed:raise ValueError(i18n.text('This unit is assigned to a pending battle plan.'))
            c.execute('SELECT quantity FROM military_units WHERE id=? AND nation_id=?',(uid,nid))
            row=c.fetchone()
            if not row or row['quantity']<=0:raise ValueError(i18n.text('Unit not found or not yours.'))
            assert_ready(c,nid,uid)
            fresh.append({'unit_id':uid,'qty':row['quantity']})
        pid=db.insert_returning_id(
            'INSERT INTO battle_plans(nation_id,forces_json,provinces_json,orders_text,status) VALUES(?,?,?,?,?)',
            (nid,json.dumps(fresh),json.dumps([location]),f"{orders} | Location: {location} | Forces: {note or 'see unit_ids'}",'unmatched'))
        for f in fresh:
            c.execute("INSERT INTO military_posture(unit_id,mode) VALUES(?,'deployed') "
                      "ON CONFLICT(unit_id) DO UPDATE SET mode='deployed',ready_month=0",(f['unit_id'],))
        return pid,fresh


def set_recurring(trade_id,nid):
    with db.atomic() as c:
        c.execute('SELECT * FROM trades WHERE id=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(trade_id,))
        t=c.fetchone()
        if not t or t['from_nation_id']!=nid or t['status']!='pending':
            raise ValueError(i18n.text('Only the author can make a pending trade recurring.'))
        c.execute("INSERT INTO trade_contracts(trade_id,status) VALUES(?,'proposed') ON CONFLICT(trade_id) DO NOTHING",(trade_id,))


def cancel_contract(trade_id,nid):
    with db.atomic() as c:
        c.execute('SELECT * FROM trades WHERE id=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(trade_id,)); t=c.fetchone()
        if not t or nid not in (t['from_nation_id'],t['to_nation_id']):
            raise ValueError(i18n.text('This contract is not yours.'))
        c.execute("UPDATE trade_contracts SET status='cancelled' WHERE trade_id=?",(trade_id,))


def settle_contracts(c,month):
    c.execute("SELECT t.* FROM trades t JOIN trade_contracts x ON t.id=x.trade_id "
              "WHERE x.status IN ('active','waiting') AND x.last_month<? ORDER BY t.id",(month,))
    from trade_service import parse_resources,validate_gold
    for t in c.fetchall():
        n1=lock_nation(c,t['from_nation_id']); n2=lock_nation(c,t['to_nation_id'])
        give=parse_resources(t['offer_resources_json']); receive=parse_resources(t['receive_resources_json'])
        g1=t['offer_gold'];g2=t['receive_gold'];validate_gold(g1);validate_gold(g2)
        r1=read_json(n1['resources_json']);r2=read_json(n2['resources_json'])
        funded=(n1['treasury']>=g1 and n2['treasury']>=g2 and all(r1.get(k,0)>=v for k,v in give.items())
                and all(r2.get(k,0)>=v for k,v in receive.items()))
        if funded:
            for source,destination,res in ((r1,r2,give),(r2,r1,receive)):
                for k,v in res.items():source[k]=source.get(k,0)-v;destination[k]=destination.get(k,0)+v
            for n,r,g in ((n1,r1,g2-g1),(n2,r2,g1-g2)):
                c.execute('UPDATE nations SET resources_json=?,treasury=treasury+? WHERE id=?',(json.dumps(r),g,n['id']))
        c.execute('UPDATE trade_contracts SET status=?,last_month=? WHERE trade_id=?',('active' if funded else 'waiting',month,t['id']))


def create_route(nid,name,from_cell,to_cell,ship_id):
    with db.atomic() as c:
        lock_nation(c,nid)
        c.execute('SELECT * FROM provinces WHERE azgaar_cell_id IN (?,?) AND active=1',(from_cell,to_cell)); ends=c.fetchall()
        if from_cell==to_cell or len(ends)!=2:raise ValueError(i18n.text('Choose two different existing provinces.'))
        if not any(p['owner_nation_id']==nid and 'port' in read_json(p['buildings_json'],[]) for p in ends):
            raise ValueError(i18n.text('One endpoint needs your port.'))
        assert_ready(c,nid,ship_id)
        c.execute('SELECT u.quantity,b.stats_json FROM military_units u JOIN blueprints b ON b.id=u.blueprint_id '
                  "WHERE u.id=? AND u.nation_id=? AND b.type='ship'",(ship_id,nid)); ship=c.fetchone()
        if not ship or read_json(ship['stats_json']).get('cargo',0)<=0: raise ValueError(i18n.text('Choose your cargo ship.'))
        c.execute("SELECT forces_json FROM battle_plans WHERE nation_id=? AND status IN ('unmatched','matched')",(nid,))
        if any(any(x.get('unit_id')==ship_id for x in read_json(p['forces_json'],[])) for p in c.fetchall()):
            raise ValueError(i18n.text('This unit is assigned to a pending battle plan.'))
        rid=db.insert_returning_id('INSERT INTO trade_routes(nation_id,name,from_cell_id,to_cell_id) VALUES(?,?,?,?)',
                                  (nid,name,from_cell,to_cell))
        c.execute('INSERT INTO route_assignments(route_id,ship_id) VALUES(?,?)',(rid,ship_id))
        return rid


def move_settlers(c,nid,target,source=None):
    """Move people, do not create them; preserve existing inhabitants of the target."""
    c.execute('SELECT * FROM provinces WHERE owner_nation_id=? AND active=1 AND id!=? ORDER BY population DESC,id', (nid,target))
    candidates=c.fetchall()
    if source is not None: candidates=[p for p in candidates if p['id']==source]
    origin=next((p for p in candidates if p['population']>=800),None)
    if origin is None:raise ValueError(i18n.text('Need 300 settlers from a province with at least 800 inhabitants.'))
    c.execute('UPDATE provinces SET population=population-300 WHERE id=?',(origin['id'],))
    c.execute('UPDATE provinces SET population=population+300 WHERE id=?',(target,))


def found_colony(nid,cell,name):
    with db.atomic() as c:
        n=lock_nation(c,nid)
        c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(cell,))
        p=c.fetchone()
        if not p or p['owner_nation_id'] is not None:raise ValueError(i18n.text('Province already owned or missing.'))
        c.execute('SELECT u.id,u.quantity,b.stats_json FROM military_units u JOIN blueprints b ON u.blueprint_id=b.id '
                  "LEFT JOIN military_posture m ON m.unit_id=u.id LEFT JOIN route_assignments a ON a.ship_id=u.id "
                  "WHERE u.nation_id=? AND b.type='ship' AND a.ship_id IS NULL AND (m.mode IS NULL OR m.mode IN ('active','deployed'))",(nid,))
        ships=c.fetchall()
        c.execute("SELECT forces_json FROM battle_plans WHERE nation_id=? AND status IN ('unmatched','matched')",(nid,))
        committed={int(f['unit_id']) for p in c.fetchall() for f in read_json(p['forces_json'],[])}
        cargo=sum(read_json(s['stats_json']).get('cargo',0)*s['quantity'] for s in ships if s['id'] not in committed)
        from technology import bonuses
        cargo*=1+bonuses(c,nid).get('cargo',0)
        if cargo<5:raise ValueError(i18n.text('Need 5 available cargo capacity to transport settlers.'))
        spend(c,n,{'gold':500})
        move_settlers(c,nid,p['id'])
        c.execute('UPDATE provinces SET owner_nation_id=? WHERE id=?',(nid,p['id']))
        cid=db.insert_returning_id("INSERT INTO colonies(nation_id,province_id,name,status) VALUES(?,?,?,'outpost')",(nid,p['id'],name))
        c.execute('UPDATE nations SET population=(SELECT COALESCE(SUM(population),0) FROM provinces WHERE owner_nation_id=? AND active=1) WHERE id=?',(nid,nid))
        from world_service import activity
        activity(c,'colony',nid,f'colony:{cid}',{'cell':cell})
        return cid


def normalize_population(nid,apply=False):
    with db.atomic() as c:
        n=lock_nation(c,nid)
        c.execute('SELECT p.* FROM provinces p LEFT JOIN colonies x ON x.province_id=p.id '
                  "WHERE p.owner_nation_id=? AND p.active=1 AND (x.id IS NULL OR x.status='province') ORDER BY p.id",(nid,))
        provinces=c.fetchall()
        if not provinces: return []
        weights=[2.5 if p['id']==n['capital_province_id'] else max(.5,min(2,p['population']/2000)) for p in provinces]
        factor=2000*len(provinces)/sum(weights)
        changes=[dict(id=p['id'],cell=p['azgaar_cell_id'],old=p['population'],new=round(w*factor)) for p,w in zip(provinces,weights)]
        if apply:
            for row in changes:c.execute('UPDATE provinces SET population=? WHERE id=?',(row['new'],row['id']))
            c.execute('UPDATE nations SET population=(SELECT COALESCE(SUM(population),0) FROM provinces WHERE owner_nation_id=? AND active=1) WHERE id=?',(nid,nid))
        return changes


def start_megaproject(nid,mpid):
    from cogs.economy import _apply_mp_effect
    with db.atomic() as c:
        n=lock_nation(c,nid)
        c.execute('SELECT * FROM megaprojects WHERE id=? AND nation_id=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(mpid,nid))
        mp=c.fetchone()
        if not mp or mp['status']!='approved':raise ValueError(i18n.text('Project must be approved first.'))
        spend(c,n,read_json(mp['cost_json']))
        state='building' if mp['duration_months']>0 else 'complete'
        c.execute('UPDATE megaprojects SET status=?,months_spent=0 WHERE id=?',(state,mpid))
        if state=='complete':
            c.execute('UPDATE megaprojects SET completed_at=CURRENT_TIMESTAMP WHERE id=?',(mpid,))
            _apply_mp_effect(nid,mp['effect_json'],mp['name'],mp['id'])
        return state


def advance_megaproject(mpid,months):
    if months<1:raise ValueError(i18n.text('Months must be positive.'))
    from cogs.economy import _apply_mp_effect
    with db.atomic() as c:
        c.execute('SELECT nation_id FROM megaprojects WHERE id=?',(mpid,)); row=c.fetchone()
        if not row:raise ValueError(i18n.text('Project not found.'))
        lock_nation(c,row['nation_id'])
        c.execute('SELECT * FROM megaprojects WHERE id=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(mpid,));mp=c.fetchone()
        if mp['status']!='building':raise ValueError(i18n.text('Project is not under construction.'))
        spent=min(mp['duration_months'],mp['months_spent']+months)
        state='complete' if spent>=mp['duration_months'] else 'building'
        c.execute('UPDATE megaprojects SET status=?,months_spent=? WHERE id=?',(state,spent,mpid))
        if state=='complete':
            c.execute('UPDATE megaprojects SET completed_at=CURRENT_TIMESTAMP WHERE id=?',(mpid,))
            _apply_mp_effect(mp['nation_id'],mp['effect_json'],mp['name'],mp['id'])
        return state
