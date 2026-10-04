"""Two-nation battle plans with explicit invitation and acceptance."""
import json

import db
from economy_engine import lock_nation
from nation_access import can_manage
from world_service import world_lock,tr


def entries(plan):
    """Return canonical force entries; legacy entries belong to the plan leader."""
    try:raw=json.loads(plan['forces_json'] or '[]')
    except (TypeError,ValueError):raw=[]
    result=[]
    for item in raw if isinstance(raw,list) else []:
        if not isinstance(item,dict):continue
        try:
            uid=int(item['unit_id']);qty=int(item.get('qty',1));nid=int(item.get('nation_id',plan['nation_id']))
        except (KeyError,TypeError,ValueError):continue
        if uid>0 and qty>0 and nid>0:result.append(dict(unit_id=uid,qty=qty,nation_id=nid))
    merged={}
    for item in result:
        key=(item['unit_id'],item['nation_id'])
        merged[key]=merged.get(key,0)+item['qty']
    return [dict(unit_id=uid,nation_id=nid,qty=qty) for (uid,nid),qty in merged.items()]


def participants(c,plan):
    result={int(plan['nation_id'])}
    result.update(item['nation_id'] for item in entries(plan))
    c.execute("SELECT nation_id FROM battle_plan_allies WHERE plan_id=? AND status='joined'",(plan['id'],))
    result.update(row['nation_id'] for row in c.fetchall())
    return result


def participant_rows(c,plan):
    ids=participants(c,plan)
    placeholders=','.join('?' for _ in ids)
    c.execute(f'SELECT * FROM nations WHERE id IN ({placeholders}) ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END,name',
              (*ids,plan['nation_id']))
    return c.fetchall()


def label(c,plan):
    return ' + '.join(row['name'] for row in participant_rows(c,plan))


def validate_side(c,plan):
    """Recheck that a joined ally is still permitted when the GM matches plans."""
    c.execute("SELECT nation_id FROM battle_plan_allies WHERE plan_id=? AND status='joined'",(plan['id'],))
    ally=c.fetchone()
    if ally and not eligible(c,plan['nation_id'],ally['nation_id']):
        raise ValueError(tr('Sojusznik w planie nie jest już po tej samej stronie wojny.',
                            'The plan ally is no longer on the same side of the war.'))
    return participants(c,plan)


def committed(c,unit_id):
    c.execute("SELECT * FROM battle_plans WHERE status IN ('unmatched','matched') ORDER BY id")
    return next((plan['id'] for plan in c.fetchall() if any(x['unit_id']==unit_id for x in entries(plan))),None)


def eligible(c,leader,ally):
    if leader==ally:return False
    from treaty_service import relation
    if relation(c,leader,ally)=='war':return False
    if relation(c,leader,ally)=='alliance':return True
    c.execute("SELECT nation_a_id,nation_b_id FROM relations WHERE status='war' AND (nation_a_id=? OR nation_b_id=?)",(leader,leader))
    enemies={row['nation_b_id'] if row['nation_a_id']==leader else row['nation_a_id'] for row in c.fetchall()}
    c.execute("SELECT nation_a_id,nation_b_id FROM relations WHERE status='war' AND (nation_a_id=? OR nation_b_id=?)",(ally,ally))
    return any((row['nation_b_id'] if row['nation_a_id']==ally else row['nation_a_id']) in enemies for row in c.fetchall())


def invite(plan_id,uid,ally_id):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM battle_plans WHERE id=?',(plan_id,));plan=c.fetchone()
        if not plan or plan['status']!='unmatched':
            raise ValueError(tr('Plan nie istnieje albo został już połączony w bitwę.','The plan is missing or already matched into a battle.'))
        if not can_manage(plan['nation_id'],uid,c):raise ValueError(tr('Tylko autor planu może zaprosić sojusznika.','Only the plan owner can invite an ally.'))
        for nid in sorted({plan['nation_id'],ally_id}):lock_nation(c,nid)
        if not eligible(c,plan['nation_id'],ally_id):
            raise ValueError(tr('Drugie państwo musi być sojusznikiem albo walczyć z tym samym przeciwnikiem.',
                                'The second nation must be an ally or fight the same enemy.'))
        c.execute('SELECT * FROM battle_plan_allies WHERE plan_id=?',(plan_id,));old=c.fetchone()
        if old and old['status']=='joined':raise ValueError(tr('Druga armia już dołączyła do planu.','A second army has already joined this plan.'))
        c.execute("INSERT INTO battle_plan_allies(plan_id,nation_id,status) VALUES(?,?,'invited') "
                  "ON CONFLICT(plan_id) DO UPDATE SET nation_id=excluded.nation_id,status='invited'",(plan_id,ally_id))
        return plan


def join(plan_id,uid,unit_ids):
    ids=list(dict.fromkeys(int(x) for x in unit_ids))
    if not ids or len(ids)>25 or any(x<=0 for x in ids):
        raise ValueError(tr('Wybierz od 1 do 25 grup jednostek.','Choose 1 to 25 unit groups.'))
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM battle_plans WHERE id=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(plan_id,));plan=c.fetchone()
        if not plan or plan['status']!='unmatched':raise ValueError(tr('Plan nie jest już dostępny.','The plan is no longer available.'))
        c.execute("SELECT * FROM battle_plan_allies WHERE plan_id=? AND status='invited'",(plan_id,));invitation=c.fetchone()
        if not invitation or not can_manage(invitation['nation_id'],uid,c):
            raise ValueError(tr('Nie masz aktualnego zaproszenia do tego planu.','You do not have a current invitation to this plan.'))
        ally=invitation['nation_id']
        for nid in sorted({plan['nation_id'],ally}):lock_nation(c,nid)
        if not eligible(c,plan['nation_id'],ally):raise ValueError(tr('Wspólna strona wojny nie jest już aktualna.','The shared war side is no longer valid.'))
        fresh=[]
        from economy_services import assert_ready
        for unit_id in ids:
            c.execute('SELECT quantity FROM military_units WHERE id=? AND nation_id=?',(unit_id,ally));unit=c.fetchone()
            if not unit or unit['quantity']<=0:raise ValueError(tr('Jednostka nie istnieje albo nie należy do zaproszonego państwa.','A unit is missing or does not belong to the invited nation.'))
            conflict=committed(c,unit_id)
            if conflict:raise ValueError(tr('Jednostka jest już w planie #','The unit is already in plan #')+str(conflict)+'.')
            assert_ready(c,ally,unit_id)
            fresh.append(dict(unit_id=unit_id,qty=unit['quantity'],nation_id=ally))
        combined=entries(plan)+fresh
        c.execute('UPDATE battle_plans SET forces_json=? WHERE id=? AND status=?',(json.dumps(combined),plan_id,'unmatched'))
        if c.rowcount!=1:raise ValueError(tr('Plan zmienił się. Spróbuj ponownie.','The plan changed. Try again.'))
        c.execute("UPDATE battle_plan_allies SET status='joined' WHERE plan_id=? AND nation_id=? AND status='invited'",(plan_id,ally))
        if c.rowcount!=1:raise ValueError(tr('Zaproszenie zmieniło się. Spróbuj ponownie.','The invitation changed. Try again.'))
        c.executemany("INSERT INTO military_posture(unit_id,mode) VALUES(?,'deployed') "
                      "ON CONFLICT(unit_id) DO UPDATE SET mode='deployed',ready_month=0",[(x,) for x in ids])
        return dict(plan=plan,ally=ally,units=fresh)


def leave(plan_id,uid):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM battle_plans WHERE id=?',(plan_id,));plan=c.fetchone()
        c.execute('SELECT * FROM battle_plan_allies WHERE plan_id=?',(plan_id,));invitation=c.fetchone()
        if not plan or plan['status']!='unmatched' or not invitation or not can_manage(invitation['nation_id'],uid,c):
            raise ValueError(tr('Nie możesz wycofać tego udziału.','You cannot withdraw this participation.'))
        ally=invitation['nation_id'];removed=[x for x in entries(plan) if x['nation_id']==ally]
        kept=[x for x in entries(plan) if x['nation_id']!=ally]
        c.execute('UPDATE battle_plans SET forces_json=? WHERE id=?',(json.dumps(kept),plan_id))
        c.execute("UPDATE battle_plan_allies SET status='declined' WHERE plan_id=?",(plan_id,))
        c.executemany("UPDATE military_posture SET mode='active',ready_month=0 WHERE unit_id=? AND mode='deployed'",[(x['unit_id'],) for x in removed])
        return len(removed)


def match(attacker_plan_id,defender_plan_id,gm_note=''):
    """Atomically validate and match two coalition plans."""
    with db.atomic() as c:
        world_lock(c)
        plans=[]
        for pid in sorted((attacker_plan_id,defender_plan_id)):
            c.execute('SELECT * FROM battle_plans WHERE id=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(pid,))
            plans.append(c.fetchone())
        by_id={p['id']:p for p in plans if p}
        plan_a=by_id.get(attacker_plan_id);plan_b=by_id.get(defender_plan_id)
        if not plan_a or not plan_b:raise ValueError(tr('Nie znaleziono jednego z planów.','One of the plans was not found.'))
        if plan_a['status']!='unmatched' or plan_b['status']!='unmatched':
            raise ValueError(tr('Oba plany muszą nadal oczekiwać.','Both plans must still be unmatched.'))
        if validate_side(c,plan_a)&validate_side(c,plan_b):
            raise ValueError(tr('Przeciwne plany zawierają to samo państwo.','The opposing plans contain the same nation.'))
        bid=db.insert_returning_id('INSERT INTO battles(plan_a_id,plan_b_id,gm_note,status) VALUES(?,?,?,?)',
                                   (attacker_plan_id,defender_plan_id,gm_note,'pending'))
        c.execute("UPDATE battle_plans SET status='matched' WHERE id IN (?,?) AND status='unmatched'",(attacker_plan_id,defender_plan_id))
        if c.rowcount!=2:raise ValueError(tr('Plany zmieniły się. Spróbuj ponownie.','The plans changed. Try again.'))
        return bid,plan_a,plan_b
