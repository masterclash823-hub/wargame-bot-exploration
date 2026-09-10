"""Bilateral treaties, enforceable settlements and explicit guarantee obligations."""
import json
import math
import uuid

import db
from economy_engine import read_json
from world_service import world_lock, owned, month_index, activity, reward, tr

KINDS={
    'peace':('Traktat pokojowy','Peace settlement'),
    'non_aggression':('Pakt o nieagresji','Non-aggression pact'),
    'alliance':('Sojusz','Alliance'),
    'military_access':('Wzajemny dostęp wojskowy','Mutual military access'),
    'guarantee':('Gwarancja bezpieczeństwa','Security guarantee'),
}


def pair(a,b):return min(a,b),max(a,b)


def relation(c,a,b):
    c.execute('SELECT status FROM relations WHERE nation_a_id=? AND nation_b_id=?',pair(a,b))
    r=c.fetchone()
    return r['status'] if r else 'peace'


def set_relation(c,a,b,status):
    c.execute('INSERT INTO relations(nation_a_id,nation_b_id,status) VALUES(?,?,?) '
              'ON CONFLICT(nation_a_id,nation_b_id) DO UPDATE SET status=excluded.status',(*pair(a,b),status))


def cells(raw):
    try:
        result=[int(x.strip()) for x in raw.split(',') if x.strip()] if isinstance(raw,str) else list(raw)
        if len(result)>25 or len(result)!=len(set(result)) or any(type(x)!=int or x<0 for x in result):raise ValueError
        return result
    except (TypeError,ValueError):raise ValueError(tr('Podaj do 25 różnych ID prowincji, oddzielonych przecinkami.','Provide up to 25 distinct province IDs separated by commas.'))


def validate_terms(terms,duration):
    t=dict(terms)
    for key in ('give_gold','receive_gold','tribute_gold'):
        value=t.setdefault(key,0)
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1_000_000:
            raise ValueError(tr('Kwoty muszą wynosić od 0 do 1 000 000.','Amounts must be between 0 and 1,000,000.'))
    for key in ('give_cells','receive_cells'):t[key]=cells(t.get(key,[]))
    if set(t['give_cells']) & set(t['receive_cells']):raise ValueError(tr('Ta sama prowincja występuje po obu stronach.','The same province appears on both sides.'))
    count=t.setdefault('tribute_months',0)
    if type(count)!=int or not 0<=count<=duration or bool(count)!=bool(t['tribute_gold']):
        raise ValueError(tr('Reparacje wymagają kwoty i liczby rat od 1 do czasu trwania traktatu.','Reparations need an amount and 1 to treaty-duration monthly installments.'))
    if t.setdefault('payer','recipient') not in ('proposer','recipient'):raise ValueError('Invalid payer')
    if not isinstance(t.setdefault('note',''),str) or len(t['note'])>2000:raise ValueError(tr('Opis może mieć do 2000 znaków.','The note may contain up to 2000 characters.'))
    return t


def _parties(c,a,b):
    c.execute('SELECT * FROM nations WHERE id IN (?,?) ORDER BY id'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(a,b))
    rows={r['id']:r for r in c.fetchall()}
    if a==b or len(rows)!=2:raise ValueError(tr('Wybierz dwa różne istniejące państwa.','Choose two different existing nations.'))
    return rows[a],rows[b]


def propose(nid,owner_id,recipient,kind,duration=12,visibility='public',draft=False,**terms):
    if kind not in KINDS or type(duration)!=int or not 1<=duration<=120 or visibility not in ('public','private'):
        raise ValueError(tr('Nieprawidłowy typ, czas lub widoczność traktatu.','Invalid treaty type, duration or visibility.'))
    terms=validate_terms(terms,duration)
    with db.atomic() as c:
        world_lock(c)
        a,b=_parties(c,nid,recipient);owned(c,nid,owner_id)
        war=relation(c,nid,recipient)=='war'
        if (kind=='peace')!=war:
            raise ValueError(tr('W czasie wojny zaproponuj traktat pokojowy; pozostałe traktaty zawiera się w pokoju.',
                                'Propose a peace settlement during war; other treaties require peace.'))
        c.execute("SELECT id FROM treaties WHERE kind=? AND status IN ('draft','proposed','active') "
                  'AND ((proposer_id=? AND recipient_id=?) OR (proposer_id=? AND recipient_id=?))',
                  (kind,nid,recipient,recipient,nid))
        if c.fetchone():raise ValueError(tr('Taki traktat jest już aktywny lub oczekuje na odpowiedź.','This treaty is already active or awaiting an answer.'))
        month=month_index(c)
        return db.insert_returning_id('INSERT INTO treaties(proposer_id,recipient_id,proposer_owner,recipient_owner,kind,duration,visibility,terms_json,created_month,status,submitted_month) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                      (nid,recipient,a['owner_id'],b['owner_id'],kind,duration,visibility,json.dumps(terms),month,'draft' if draft else 'proposed',None if draft else month))


def submit_proposal(tid,owner_id,expected_version):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM treaties WHERE id=?',(tid,));t=c.fetchone()
        if not t or t['status']!='draft' or t['version']!=expected_version:
            raise ValueError(tr('Szkic zmienił się lub został już wysłany. Otwórz go ponownie.',
                                'The draft changed or was already sent. Open it again.'))
        owned(c,t['proposer_id'],owner_id)
        c.execute("UPDATE treaties SET status='proposed',submitted_month=? WHERE id=?",(month_index(c),tid))


def get_treaty(tid,owner_id,is_gm=False):
    with db.cursor() as c:
        c.execute('SELECT t.*,a.name AS a_name,a.owner_id AS a_owner,a.flag AS a_flag,'
                  'b.name AS b_name,b.owner_id AS b_owner,b.flag AS b_flag FROM treaties t '
                  'JOIN nations a ON a.id=t.proposer_id JOIN nations b ON b.id=t.recipient_id WHERE t.id=?',(tid,))
        t=c.fetchone()
    if not t or (not is_gm and str(owner_id) not in (t['a_owner'],t['b_owner'])):
        raise ValueError(tr('Traktat jest dostępny wyłącznie jego stronom i GM-owi.','Only the treaty parties and GM can inspect it.'))
    if t['submitted_month'] is None and not is_gm and str(owner_id)!=t['a_owner']:
        raise ValueError(tr('Autor nie wysłał jeszcze tej propozycji.','The author has not sent this proposal yet.'))
    return t


def amend_tribute(tid,owner_id,amount,months,payer,note=None,visibility=None):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM treaties WHERE id=?',(tid,));t=c.fetchone()
        if not t or t['status'] not in ('draft','proposed'):raise ValueError(tr('Można zmienić tylko szkic lub oczekującą propozycję.','Only a draft or pending proposal can be amended.'))
        owned(c,t['proposer_id'],owner_id)
        terms=read_json(t['terms_json']);terms.update(tribute_gold=amount,tribute_months=months,payer=payer)
        if note is not None:terms['note']=note
        terms=validate_terms(terms,t['duration'])
        visibility=t['visibility'] if visibility is None else visibility
        if visibility not in ('public','private'):raise ValueError(tr('Widoczność: publiczne albo prywatne.','Visibility: public or private.'))
        c.execute('UPDATE treaties SET terms_json=?,visibility=?,version=version+1 WHERE id=?',(json.dumps(terms),visibility,tid))


def _ceded_provinces(c,terms,a,b):
    transfers=[]
    for ids,source,target in ((terms['give_cells'],a,b),(terms['receive_cells'],b,a)):
        for cell in ids:
            c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(cell,))
            p=c.fetchone()
            if not p or p['owner_nation_id']!=source['id']:
                raise ValueError(tr(f'Prowincja {cell} nie należy już do strony, która ją przekazuje.',f'Province {cell} is no longer owned by the ceding party.'))
            transfers.append((p,source['id'],target['id']))
    return transfers


def accept(tid,owner_id,expected_version):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM treaties WHERE id=?',(tid,));t=c.fetchone()
        if not t or t['status']!='proposed':raise ValueError(tr('Propozycja nie jest już aktualna.','This proposal is no longer pending.'))
        if t['version']!=expected_version:raise ValueError(tr('Warunki zmieniły się. Otwórz traktat ponownie.','Terms changed. Open the treaty again.'))
        a,b=_parties(c,t['proposer_id'],t['recipient_id']);owned(c,b['id'],owner_id)
        if a['owner_id']!=t['proposer_owner'] or b['owner_id']!=t['recipient_owner']:
            raise ValueError(tr('Zmienił się właściciel państwa. Potrzebna jest nowa propozycja.','A nation changed owner. A new proposal is required.'))
        if (t['kind']=='peace')!=(relation(c,a['id'],b['id'])=='war'):
            raise ValueError(tr('Sytuacja dyplomatyczna zmieniła się. Przygotuj nową propozycję.','Diplomatic circumstances changed. Prepare a new proposal.'))
        terms=validate_terms(read_json(t['terms_json']),t['duration'])
        if a['treasury']<terms['give_gold'] or b['treasury']<terms['receive_gold']:
            raise ValueError(tr('Brakuje złota na uzgodnioną jednorazową płatność.','The agreed immediate payment is not funded.'))
        transfers=_ceded_provinces(c,terms,a,b)
        delta=terms['receive_gold']-terms['give_gold']
        c.execute('UPDATE nations SET treasury=treasury+? WHERE id=?',(delta,a['id']))
        c.execute('UPDATE nations SET treasury=treasury-? WHERE id=?',(delta,b['id']))
        for p,source,target in transfers:
            c.execute('UPDATE provinces SET owner_nation_id=? WHERE id=?',(target,p['id']))
            c.execute('UPDATE colonies SET nation_id=? WHERE province_id=?',(target,p['id']))
            c.execute('UPDATE megaprojects SET nation_id=? WHERE province_id=?',(target,p['id']))
            c.execute('UPDATE military_units SET province_id=NULL WHERE province_id=? AND nation_id=?',(p['id'],source))
            c.execute('UPDATE nations SET capital_province_id=NULL WHERE id=? AND capital_province_id=?',(source,p['id']))
        for n in (a,b):
            c.execute('UPDATE nations SET population=(SELECT COALESCE(SUM(population),0) FROM provinces WHERE owner_nation_id=? AND active=1) WHERE id=?',(n['id'],n['id']))
            c.execute('SELECT r.id,r.from_cell_id,r.to_cell_id FROM trade_routes r WHERE nation_id=? AND active=1',(n['id'],))
            for r in c.fetchall():
                c.execute('SELECT buildings_json FROM provinces WHERE owner_nation_id=? AND active=1 AND azgaar_cell_id IN (?,?)',(n['id'],r['from_cell_id'],r['to_cell_id']))
                if not any('port' in read_json(p['buildings_json'],[]) for p in c.fetchall()):
                    c.execute('UPDATE trade_routes SET active=0 WHERE id=?',(r['id'],))
                    c.execute('DELETE FROM route_assignments WHERE route_id=?',(r['id'],))
        month=month_index(c)
        c.execute("UPDATE treaties SET status='active',accepted_month=?,expires_month=?,last_paid_month=?,payments_left=? WHERE id=?",
                  (month,month+t['duration'],month,terms['tribute_months'],tid))
        if t['kind']=='peace':set_relation(c,a['id'],b['id'],'peace')
        if t['kind']=='alliance':set_relation(c,a['id'],b['id'],'alliance')
        if t['visibility']=='public':activity(c,'treaty',a['id'],f'treaty:{tid}',{'kind':t['kind']},b['id'])
        return tid


def _break(c,t,offender,penalty=True):
    c.execute("UPDATE treaties SET status='broken',broken_by=? WHERE id=? AND status='active'",(offender,t['id']))
    if not c.rowcount:return
    if penalty:reward(c,offender,reputation=-10)
    if t['kind']=='alliance' and relation(c,t['proposer_id'],t['recipient_id'])=='alliance':
        set_relation(c,t['proposer_id'],t['recipient_id'],'peace')
    if t['visibility']=='public':activity(c,'breach',offender,f"breach:{t['id']}",{'kind':t['kind']},t['recipient_id'] if offender==t['proposer_id'] else t['proposer_id'])


def end(tid,owner_id,expected_status):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM treaties WHERE id=?',(tid,));t=c.fetchone()
        if not t:raise ValueError(tr('Nie znaleziono traktatu.','Treaty not found.'))
        if t['status']!=expected_status:
            raise ValueError(tr('Status traktatu zmienił się. Otwórz go ponownie.',
                                'Treaty status changed. Open it again.'))
        a,b=_parties(c,t['proposer_id'],t['recipient_id'])
        n=next((n for n in (a,b) if n['owner_id']==str(owner_id)),None)
        if not n:raise ValueError(tr('Nie jesteś stroną traktatu.','You are not a treaty party.'))
        if t['status']=='draft' and n['id']!=a['id']:
            raise ValueError(tr('Tylko autor może usunąć szkic.','Only the author can discard a draft.'))
        if t['status'] in ('draft','proposed'):c.execute("UPDATE treaties SET status=? WHERE id=?",('cancelled' if n['id']==a['id'] else 'rejected',tid))
        elif t['status']=='active':_break(c,t,n['id'])
        else:raise ValueError(tr('Traktat już się zakończył.','The treaty has already ended.'))


def _war(c,nid,target):
    _parties(c,nid,target)
    if relation(c,nid,target)=='war':raise ValueError(tr('Te państwa są już w stanie wojny.','These nations are already at war.'))
    c.execute("SELECT * FROM treaties WHERE status='active' AND ((proposer_id=? AND recipient_id=?) OR (proposer_id=? AND recipient_id=?))",(nid,target,target,nid))
    for index,t in enumerate(c.fetchall()):_break(c,t,nid,penalty=index==0)
    c.execute("UPDATE treaties SET status='cancelled' WHERE status IN ('draft','proposed') AND ((proposer_id=? AND recipient_id=?) OR (proposer_id=? AND recipient_id=?))",(nid,target,target,nid))
    set_relation(c,nid,target,'war')
    source='war:'+uuid.uuid4().hex
    activity(c,'war',nid,source,other=target)
    c.execute("SELECT * FROM treaties WHERE kind='guarantee' AND status='active' AND recipient_id=? AND proposer_id!=?",(target,nid))
    for t in c.fetchall():
        c.execute("INSERT INTO guarantee_calls(treaty_id,attacker_id,defender_id,deadline_month,source_key) VALUES(?,?,?,?,?)",
                  (t['id'],nid,target,month_index(c)+1,source+f":{t['id']}"))


def declare_war(nid,owner_id,target):
    with db.atomic() as c:
        world_lock(c);_parties(c,nid,target);owned(c,nid,owner_id)
        _war(c,nid,target)


def respond_guarantee(call_id,owner_id,join):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM guarantee_calls WHERE id=?',(call_id,));call=c.fetchone()
        if not call or call['status']!='pending' or month_index(c)>=call['deadline_month']:
            raise ValueError(tr('Wezwanie nie jest już aktualne.','The guarantee call is no longer pending.'))
        c.execute('SELECT * FROM treaties WHERE id=?',(call['treaty_id'],));t=c.fetchone()
        owned(c,t['proposer_id'],owner_id)
        if t['status']!='active':raise ValueError(tr('Gwarancja nie jest już aktywna.','The guarantee is no longer active.'))
        if relation(c,call['attacker_id'],call['defender_id'])!='war':
            c.execute("UPDATE guarantee_calls SET status='closed' WHERE id=?",(call_id,));return
        if join:
            if relation(c,t['proposer_id'],call['attacker_id'])!='war':_war(c,t['proposer_id'],call['attacker_id'])
            reward(c,t['proposer_id'],reputation=2)
            if t['visibility']=='public':activity(c,'guarantee',t['proposer_id'],f'guarantee:{call_id}',other=call['defender_id'])
        else:_break(c,t,t['proposer_id'])
        c.execute('UPDATE guarantee_calls SET status=? WHERE id=?',('honored' if join else 'declined',call_id))


def can_enter(c,nid,target):
    if target is None or nid==target:return True
    if relation(c,nid,target) in ('war','alliance'):return True
    c.execute("SELECT id FROM treaties WHERE status='active' AND kind IN ('military_access','alliance') AND expires_month>? "
              'AND ((proposer_id=? AND recipient_id=?) OR (proposer_id=? AND recipient_id=?))',
              (month_index(c),nid,target,target,nid))
    return bool(c.fetchone())


def move_unit(nid,owner_id,uid,cell):
    with db.atomic() as c:
        world_lock(c);owned(c,nid,owner_id)
        c.execute('SELECT * FROM military_units WHERE id=? AND nation_id=?',(uid,nid));unit=c.fetchone()
        c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1',(cell,));p=c.fetchone()
        if not unit or not p:raise ValueError(tr('Nie znaleziono jednostki lub prowincji.','Unit or province not found.'))
        if not can_enter(c,nid,p['owner_nation_id']):raise ValueError(tr('Wejście na cudze terytorium wymaga sojuszu, dostępu wojskowego lub wojny.','Foreign territory requires an alliance, military access or war.'))
        c.execute('SELECT route_id FROM route_assignments WHERE ship_id=?',(uid,))
        if c.fetchone():raise ValueError(tr('Najpierw usuń przypisanie okrętu do szlaku.','Release this ship from its route first.'))
        c.execute('UPDATE military_units SET province_id=? WHERE id=?',(p['id'],uid))
        c.execute('SELECT name FROM blueprints WHERE id=?',(unit['blueprint_id'],));bp=c.fetchone()
        return dict(unit,bname=bp['name'] if bp else unit['unit_type']),p


def tick(c,month):
    """Called inside the economy transaction, after nation locks, before production."""
    c.execute("SELECT * FROM treaties WHERE status NOT IN ('proposed','cancelled','rejected') AND last_paid_month<? AND (payments_left>0 OR arrears>0)",(month,))
    for t in c.fetchall():
        terms=read_json(t['terms_json']);a,b=_parties(c,t['proposer_id'],t['recipient_id'])
        payer,payee=(a,b) if terms['payer']=='proposer' else (b,a)
        due=t['arrears']+(terms['tribute_gold'] if t['payments_left']>0 else 0)
        paid=min(max(0,payer['treasury']),due);arrears=round(due-paid,2)
        c.execute('UPDATE nations SET treasury=treasury-? WHERE id=?',(paid,payer['id']))
        c.execute('UPDATE nations SET treasury=treasury+? WHERE id=?',(paid,payee['id']))
        missed=t['missed_payments']+1 if arrears>.01 else 0
        c.execute('UPDATE treaties SET payments_left=?,arrears=?,missed_payments=?,last_paid_month=? WHERE id=?',
                  (max(0,t['payments_left']-1),arrears,missed,month,t['id']))
        if missed>=2 and t['status']=='active':_break(c,t,payer['id'])
    c.execute("SELECT * FROM guarantee_calls WHERE status='pending' AND deadline_month<=?",(month,))
    for call in c.fetchall():
        c.execute('SELECT * FROM treaties WHERE id=?',(call['treaty_id'],));t=c.fetchone()
        status='closed'
        if t['status']=='active' and relation(c,call['attacker_id'],call['defender_id'])=='war':
            if relation(c,t['proposer_id'],call['attacker_id'])=='war':status='honored'
            else:_break(c,t,t['proposer_id']);status='declined'
        c.execute('UPDATE guarantee_calls SET status=? WHERE id=?',(status,call['id']))
    c.execute("SELECT * FROM treaties WHERE status='active' AND expires_month<=?",(month,))
    for t in c.fetchall():
        if t['kind']=='alliance' and relation(c,t['proposer_id'],t['recipient_id'])=='alliance':set_relation(c,t['proposer_id'],t['recipient_id'],'peace')
        c.execute("UPDATE treaties SET status='expired' WHERE id=?",(t['id'],))
    c.execute("UPDATE treaties SET status='expired' WHERE status IN ('draft','proposed') AND created_month+6<=?",(month,))
