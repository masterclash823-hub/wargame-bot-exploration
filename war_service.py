"""Player-led battles: a frozen challenge, explicit defense, one atomic settlement."""
import asyncio
import json
import threading

import db
import battle_coalitions as coalitions
import battle_plan_text
import battle_resolution as resolution
from economy_engine import lock_nation, policy
from economy_services import assert_ready
from nation_access import can_manage, find_nation
from treaty_service import relation
from world_service import world_lock, month_index, tr


_review_guard = threading.Lock()
_active_reviews = set()


def _plan(c,pid,nid,status='unmatched'):
    c.execute('SELECT * FROM battle_plans WHERE id=?',(pid,));p=c.fetchone()
    if not p or p['nation_id']!=nid or p['status']!=status:
        raise ValueError(tr('Wybierz własny oczekujący plan.','Choose your own available plan.'))
    side=coalitions.validate_side(c,p)
    forces=coalitions.entries(p)
    if not forces:raise ValueError(tr('Plan musi mieć przypisane jednostki.','The plan needs assigned units.'))
    for f in forces:
        lock_nation(c,f['nation_id'])
        assert_ready(c,f['nation_id'],f['unit_id'])
        c.execute('SELECT quantity FROM military_units WHERE id=? AND nation_id=?',(f['unit_id'],f['nation_id']))
        u=c.fetchone()
        if not u or u['quantity']<=0:raise ValueError(tr('Jednostki planu zmieniły się. Przygotuj nowy plan.','The plan units changed. Prepare a new plan.'))
        # Legacy plans might contain a group that was also submitted elsewhere.
        c.execute("SELECT * FROM battle_plans WHERE status IN ('unmatched','offered','matched') AND id<>?",(pid,))
        if any(any(e['unit_id']==f['unit_id'] for e in coalitions.entries(other)) for other in c.fetchall()):
            raise ValueError(tr('Jednostka jest przypisana do kilku planów.','A unit belongs to multiple plans.'))
    return p,side


def _field(c,cell,a,b):
    c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1',(cell,));p=c.fetchone()
    if not p or p['owner_nation_id'] not in (None,a,b):
        raise ValueError(tr('Pole bitwy musi należeć do jednej ze stron lub być niezajęte.','The battlefield must belong to either side or be unclaimed.'))
    return p


def _close(c,e,status):
    c.execute('UPDATE war_engagements SET status=? WHERE id=? AND status=\'pending\'',(status,e['id']))
    c.execute("UPDATE battle_plans SET status='unmatched' WHERE id=? AND status='offered'",(e['plan_id'],))


def expire(c,month=None):
    """Tick, peace and ownership changes release unanswered plans without losses."""
    month=month_index(c) if month is None else month
    c.execute("SELECT e.*,a.owner_id AS current_a,d.owner_id AS current_d FROM war_engagements e "
              "JOIN nations a ON a.id=e.attacker_id JOIN nations d ON d.id=e.defender_id WHERE e.status='pending'")
    for e in c.fetchall():
        stale=(e['current_a']!=e['attacker_owner'] or e['current_d']!=e['defender_owner']
               or relation(c,e['attacker_id'],e['defender_id'])!='war')
        if stale or month>=e['expires_month']:_close(c,e,'cancelled' if stale else 'expired')


def challenge(uid,target,pid,cell):
    with db.atomic() as c:
        world_lock(c);expire(c)
        n=find_nation(uid,c)
        if not n or not can_manage(n['id'],uid,c):raise ValueError(tr('Nie masz dostępu do państwa.','Nation access unavailable.'))
        enemy=lock_nation(c,target)
        if relation(c,n['id'],target)!='war':raise ValueError(tr('Najpierw wypowiedz wojnę temu państwu.','Declare war on this nation first.'))
        p,side=_plan(c,pid,n['id'])
        if target in side:raise ValueError(tr('Obrońca jest już w Twojej koalicji.','The defender is already in your coalition.'))
        _field(c,cell,n['id'],target)
        c.execute("SELECT id FROM war_engagements WHERE status='pending' AND "
                  "((attacker_id=? AND defender_id=?) OR (attacker_id=? AND defender_id=?))",(n['id'],target,target,n['id']))
        if c.fetchone():raise ValueError(tr('Ta para państw ma już oczekującą bitwę.','These nations already have a pending challenge.'))
        now=month_index(c)
        eid=db.insert_returning_id('INSERT INTO war_engagements(attacker_id,defender_id,attacker_owner,defender_owner,plan_id,cell_id,created_month,expires_month) VALUES(?,?,?,?,?,?,?,?)',
            (n['id'],target,n['owner_id'],enemy['owner_id'],pid,cell,now,now+2))
        c.execute("UPDATE battle_plans SET status='offered' WHERE id=?",(pid,))
        return eid


def _pending(c,eid):
    c.execute('SELECT * FROM war_engagements WHERE id=?',(eid,));e=c.fetchone()
    if not e or e['status']!='pending':raise ValueError(tr('To wyzwanie nie jest już aktualne.','This challenge is no longer pending.'))
    if month_index(c)>=e['expires_month']:raise ValueError(tr('Upłynął termin wyzwania. Odśwież panel.','The challenge expired. Refresh the panel.'))
    for nid,key in ((e['attacker_id'],'attacker_owner'),(e['defender_id'],'defender_owner')):
        if lock_nation(c,nid)['owner_id']!=e[key]:raise ValueError(tr('Właściciel państwa zmienił się.','Nation ownership changed.'))
    if relation(c,e['attacker_id'],e['defender_id'])!='war':raise ValueError(tr('Wojna już się zakończyła.','The war has ended.'))
    return e


def close(uid,eid):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM war_engagements WHERE id=?',(eid,));e=c.fetchone()
        if not e or e['status']!='pending':raise ValueError(tr('Wyzwanie zostało już zamknięte.','The challenge is already closed.'))
        if can_manage(e['attacker_id'],uid,c):status='cancelled'
        elif can_manage(e['defender_id'],uid,c):status='declined'
        else:raise ValueError(tr('Nie jesteś stroną tej bitwy.','You are not a party to this battle.'))
        _close(c,e,status)


def _defense_context(c,uid,eid,pid):
    """Capture combat inputs under the world lock; never hold it while awaiting AI."""
    e=_pending(c,eid)
    if not can_manage(e['defender_id'],uid,c):raise ValueError(tr('Tylko obrońca może przyjąć wyzwanie.','Only the defender can accept.'))
    a,side_a=_plan(c,e['plan_id'],e['attacker_id'],'offered')
    b,side_b=_plan(c,pid,e['defender_id'])
    if side_a & side_b:raise ValueError(tr('Państwo nie może walczyć po obu stronach.','A nation cannot fight on both sides.'))
    field=_field(c,e['cell_id'],e['attacker_id'],e['defender_id'])
    nations=[];forces=[]
    for plan in (a,b):
        rows=coalitions.participant_rows(c,plan)
        members=[{key:n[key] for key in ('id','name','owner_id','tech_json')} for n in rows]
        for n in members:n['morale_multiplier']=max(.5,1-.1*policy(c,n['id'])['unpaid_months'])
        nations.append(members)
        snapshot=resolution.force_snapshot(plan,rows[0])
        by_id={n['id']:n for n in members}
        for unit in snapshot:
            owner=by_id[unit['nation_id']]
            unit['technology']=json.loads(owner['tech_json'] or '{}')
            unit['morale_multiplier']=owner['morale_multiplier']
        forces.append(snapshot)
    return dict(engagement=e,plans=[a,b],nations=nations,forces=forces,
                battlefield=resolution.location_context(str(e['cell_id'])),
                field_owner=field['owner_nation_id'],month=month_index(c))


def _prepare_defense(uid,eid,pid):
    with db.atomic() as c:
        world_lock(c)
        return _defense_context(c,uid,eid,pid)


async def defend_with_ai(uid,eid,pid):
    """Review both plans for every battle, then revalidate and settle."""
    with _review_guard:
        if eid in _active_reviews:
            raise ValueError(tr('Ocena tej bitwy już trwa. Poczekaj na wynik.',
                                'This battle is already being reviewed. Wait for the result.'))
        _active_reviews.add(eid)
    try:
        context=await asyncio.to_thread(_prepare_defense,uid,eid,pid)
        from cogs.combat import _get_ai_modifier
        plans=[battle_plan_text.unpack(p) for p in context['plans']]
        leaders=[dict(side[0],name=' + '.join(n['name'] for n in side)) for side in context['nations']]
        ai=await _get_ai_modifier(*plans,*leaders,context['battlefield'],*context['forces'],required=True)
        return await asyncio.to_thread(defend,uid,eid,pid,review=(context,ai))
    finally:
        with _review_guard:_active_reviews.discard(eid)


def defend(uid,eid,pid,*,review=None):
    """Atomically settle only after an AI review of the current combat inputs."""
    with db.atomic() as c:
        world_lock(c)
        context=_defense_context(c,uid,eid,pid)
        e=context['engagement'];a,b=context['plans'];fa,fb=context['forces']
        if review is None:
            raise ValueError(tr('Plany bitwy wymagają oceny AI. Potwierdź bitwę w panelu wojen.',
                                'Battle plans require AI review. Confirm the battle in the war panel.'))
        if context!=review[0]:
            raise ValueError(tr('Plany, armie lub pole bitwy zmieniły się podczas oceny AI. Potwierdź ponownie.',
                                'Plans, armies or the battlefield changed during AI review. Confirm again.'))
        ai=review[1]
        # The frozen attacker plan is made matchable only inside this transaction.
        c.execute("UPDATE battle_plans SET status='unmatched' WHERE id=?",(a['id'],))
        bid,a,b=coalitions.match(a['id'],b['id'])
        resolution.resolve(bid,ai,final_location=str(e['cell_id']))
        report=resolution.attach_narrative(bid,{},fa,fb)
        c.execute("UPDATE war_engagements SET status='resolved',battle_id=? WHERE id=?",(bid,eid))
        return bid,report


def dashboard(uid):
    with db.atomic() as c:
        world_lock(c);expire(c)
        n=find_nation(uid,c)
        if not n or not can_manage(n['id'],uid,c):raise ValueError(tr('Nie masz dostępu do państwa.','Nation access unavailable.'))
        c.execute("SELECT n.id,n.name FROM relations r JOIN nations n ON n.id=CASE WHEN r.nation_a_id=? THEN r.nation_b_id ELSE r.nation_a_id END "
                  "WHERE r.status='war' AND (r.nation_a_id=? OR r.nation_b_id=?) ORDER BY n.name",(n['id'],n['id'],n['id']))
        enemies=c.fetchall()
        c.execute("SELECT e.*,a.name AS attacker,d.name AS defender,p.name AS place,p.terrain,p.fortification_level FROM war_engagements e "
                  "JOIN nations a ON a.id=e.attacker_id JOIN nations d ON d.id=e.defender_id "
                  "LEFT JOIN provinces p ON p.azgaar_cell_id=e.cell_id WHERE e.attacker_id=? OR e.defender_id=? ORDER BY e.id DESC",(n['id'],n['id']))
        challenges=c.fetchall()
        c.execute("SELECT * FROM battle_plans WHERE nation_id=? AND status='unmatched' ORDER BY id DESC",(n['id'],))
        plans=c.fetchall()
        return n,enemies,challenges,plans
