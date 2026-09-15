"""GM-scheduled collapse and public ruin context; all changes share the world tick."""
import json
import db
from world_service import world_lock, month_index, tr


def state(c,nid):
    c.execute('SELECT * FROM nation_decay WHERE nation_id=?',(nid,))
    return c.fetchone()


def require_playable(c,nid):
    row=state(c,nid)
    if row and row['status']=='ruins':
        raise ValueError(tr('To państwo upadło i jest niegrywalne. Pozostały po nim ruiny.',
                            'This nation has fallen and is no longer playable. Only its ruins remain.'))


def set_decay(nid,gm_id,reason='',cancel=False):
    if len(reason)>1000:raise ValueError(tr('Powód: maksymalnie 1000 znaków.','Reason: at most 1000 characters.'))
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM nations WHERE id=?',(nid,));n=c.fetchone()
        if not n:raise ValueError(tr('Nie znaleziono państwa.','Nation not found.'))
        old=state(c,nid)
        require_playable(c,nid)
        if cancel:
            if not old or old['status']!='decaying':raise ValueError(tr('Państwo nie jest w rozpadzie.','The nation is not decaying.'))
            c.execute("UPDATE nation_decay SET status='cancelled' WHERE nation_id=?",(nid,))
        else:
            if old and old['status']=='decaying':raise ValueError(tr('Rozpad już trwa; termin nie został przesunięty.','Decay is already in progress; the deadline was not changed.'))
            now=month_index(c)
            c.execute("INSERT INTO nation_decay(nation_id,status,started_month,due_month,gm_id,reason,former_owner) VALUES(?,'decaying',?,?,?,?,?) "
                      "ON CONFLICT(nation_id) DO UPDATE SET status='decaying',started_month=excluded.started_month,due_month=excluded.due_month,gm_id=excluded.gm_id,reason=excluded.reason,former_owner=excluded.former_owner",
                      (nid,now,now+3,str(gm_id),reason.strip(),n['owner_id']))
        c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)',
                  (nid,'system','Rozpad zatrzymany / Decay cancelled.' if cancel else 'Rozpoczęto rozpad: upadek za 3 miesiące gry / Decay started: collapse in 3 game months.'))
        return state(c,nid)


def tick(c,target):
    c.execute("SELECT d.*,n.owner_id FROM nation_decay d JOIN nations n ON n.id=d.nation_id WHERE d.status='decaying' AND d.due_month<=?",(target,))
    for d in c.fetchall():
        nid=d['nation_id']
        c.execute("UPDATE nation_decay SET status='ruins',former_owner=? WHERE nation_id=?",(d['owner_id'],nid))
        # Keep the nation, provinces and history, but release the player account.
        c.execute('UPDATE nations SET owner_id=? WHERE id=?',(f'ruins:{nid}',nid))
        c.execute('INSERT INTO ruin_sites(nation_id,cell_id) SELECT ?,azgaar_cell_id FROM provinces WHERE owner_nation_id=? AND active=1 ON CONFLICT DO NOTHING',(nid,nid))
        c.execute("UPDATE trades SET status='cancelled' WHERE status='pending' AND (from_nation_id=? OR to_nation_id=?)",(nid,nid))
        c.execute("UPDATE trade_contracts SET status='ended' WHERE trade_id IN (SELECT id FROM trades WHERE from_nation_id=? OR to_nation_id=?)",(nid,nid))
        c.execute("UPDATE treaties SET status='ended',version=version+1 WHERE status IN ('draft','proposed','active') AND (proposer_id=? OR recipient_id=?)",(nid,nid))
        c.execute("UPDATE dynastic_marriages SET status='ended' WHERE status IN ('proposed','active') AND (proposer_id=? OR recipient_id=?)",(nid,nid))
        c.execute("UPDATE guarantee_calls SET status='expired' WHERE status='pending' AND (attacker_id=? OR defender_id=? OR treaty_id IN (SELECT id FROM treaties WHERE proposer_id=? OR recipient_id=?))",(nid,nid,nid,nid))
        c.execute("UPDATE relations SET status='neutral' WHERE nation_a_id=? OR nation_b_id=?",(nid,nid))
        c.execute("UPDATE company_concessions SET status='ended' WHERE company_nation_id=? OR host_nation_id=?",(nid,nid))
        c.execute('DELETE FROM company_plants WHERE company_nation_id=? OR host_nation_id=?',(nid,nid))
        c.execute('SELECT state_json FROM companies WHERE nation_id=?',(nid,));company=c.fetchone()
        if company:
            s=json.loads(company['state_json']);s['paused']=True;s['version']+=1
            c.execute('UPDATE companies SET state_json=? WHERE nation_id=?',(json.dumps(s),nid))
        c.execute("UPDATE explorations SET status='cancelled',version=version+1 WHERE nation_id=? AND status='active'",(nid,))
        c.execute('UPDATE captive_opportunities SET used=1 WHERE winner_id=? OR loser_id=?',(nid,nid))
        c.execute("UPDATE battles SET status='cancelled' WHERE status='pending' AND (plan_a_id IN (SELECT id FROM battle_plans WHERE nation_id=?) OR plan_b_id IN (SELECT id FROM battle_plans WHERE nation_id=?))",(nid,nid))
        c.execute("UPDATE battle_plans SET status='unmatched' WHERE nation_id<>? AND status='matched' AND id IN (SELECT plan_a_id FROM battles WHERE status='cancelled' UNION SELECT plan_b_id FROM battles WHERE status='cancelled') AND NOT EXISTS (SELECT 1 FROM battles WHERE status='pending' AND (plan_a_id=battle_plans.id OR plan_b_id=battle_plans.id))",(nid,))
        c.execute("UPDATE battle_plans SET status='cancelled' WHERE nation_id=? AND status IN ('matched','unmatched')",(nid,))
        c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)',
                  (nid,'system','Państwo upadło. Jest archiwum; jego ruiny mogą odkrywać sąsiedzi. / The nation fell. It is archived; neighbors may discover its ruins.'))


def nearby(c,nid):
    require_playable(c,nid)
    c.execute("SELECT DISTINCT n.id,n.name,n.flag FROM nations n JOIN nation_decay d ON d.nation_id=n.id JOIN ruin_sites s ON s.nation_id=n.id JOIN provinces r ON r.azgaar_cell_id=s.cell_id "
              "WHERE d.status='ruins' AND r.active=1 AND (r.owner_nation_id=? OR EXISTS (SELECT 1 FROM province_neighbors e JOIN provinces p ON p.azgaar_cell_id=CASE WHEN e.cell_id=s.cell_id THEN e.neighbor_cell_id ELSE e.cell_id END WHERE (e.cell_id=s.cell_id OR e.neighbor_cell_id=s.cell_id) AND p.owner_nation_id=? AND p.active=1)) ORDER BY n.name",(nid,nid))
    return c.fetchall()


def context(c,nid,ruin_id):
    ruin=next((r for r in nearby(c,nid) if r['id']==ruin_id),None)
    if not ruin:raise ValueError(tr('Te ruiny nie graniczą z twoim państwem lub jeszcze nie powstały.','These ruins do not border your nation or do not exist yet.'))
    c.execute("SELECT entry_text FROM nation_history WHERE nation_id=? AND source='lore' ORDER BY id LIMIT 1",(ruin_id,));lore=c.fetchone()
    c.execute('SELECT s.cell_id FROM ruin_sites s JOIN provinces p ON p.azgaar_cell_id=s.cell_id WHERE s.nation_id=? AND p.active=1 ORDER BY s.cell_id',(ruin_id,))
    return dict(id=ruin_id,name=ruin['name'],cells=[r['cell_id'] for r in c.fetchall()],lore=lore['entry_text'][:1500] if lore else '')


def request_event(nid,uid,ruin_id):
    import i18n
    from world_service import owned
    with db.atomic() as c:
        world_lock(c);owned(c,nid,uid)
        ruin=context(c,nid,ruin_id)
        c.execute('SELECT l.event_id FROM ruin_event_links l JOIN events e ON e.id=l.event_id WHERE e.nation_id=? AND l.ruin_nation_id=? ORDER BY l.event_id LIMIT 1',(nid,ruin_id))
        old=c.fetchone()
        if old:return old['event_id']
        with i18n.using_language(i18n.get_user_language(uid)):
            text=tr('Zwiadowcy natrafiają przy granicy na ślady dawnego państwa ', 'Scouts near the border find traces of the fallen nation ')+ruin['name']+'. '+tr('Zrujnowane drogi prowadzą w stronę opuszczonych osad. Jak przygotujesz odkrycie ruin?', 'Ruined roads lead toward abandoned settlements. How will you approach their discovery?')
        event=db.insert_returning_id("INSERT INTO events(nation_id,ai_draft_text,gm_final_text,effects_json,status) VALUES(?,?,?,'{}','draft')",(nid,text,text))
        c.execute('INSERT INTO ruin_event_links(event_id,ruin_nation_id,context_json) VALUES(?,?,?)',(event,ruin_id,json.dumps(ruin)))
        return event
