"""Durable nation identity, public milestones, event memory and optional goals."""
import json
import re
from datetime import datetime, timezone

import db
import i18n
from economy_engine import read_json, _config, lock_nation


def tr(pl, en):
    return pl if i18n.current_language() == 'pl' else en


def world_lock(c):
    # Shared order: world -> trades -> nations -> provinces/units.
    c.execute("INSERT INTO economy_meta(key,value) VALUES('world_lock','1') ON CONFLICT(key) DO NOTHING")
    c.execute("SELECT value FROM economy_meta WHERE key='world_lock'" + (' FOR UPDATE' if db.USE_POSTGRES else ''))
    c.fetchone()


def month_index(c):
    return int(_config(c,'current_year','1'))*12+int(_config(c,'current_month','1'))-1


def owned(c, nid, owner_id):
    n=lock_nation(c,nid)
    if n['owner_id'] != str(owner_id):
        raise ValueError(tr('Państwo zmieniło właściciela. Otwórz panel ponownie.',
                            'Nation ownership changed. Open the panel again.'))
    return n


def profile(c,nid):
    c.execute('SELECT * FROM nation_profiles WHERE nation_id=?',(nid,))
    return c.fetchone() or {'prestige':0,'reputation':50}


def reward(c,nid,prestige=0,reputation=0):
    p=profile(c,nid)
    c.execute('INSERT INTO nation_profiles(nation_id,prestige,reputation) VALUES(?,?,?) '
              'ON CONFLICT(nation_id) DO UPDATE SET prestige=excluded.prestige,reputation=excluded.reputation',
              (nid,max(0,p['prestige']+prestige),min(100,max(0,p['reputation']+reputation))))


# This whitelist deliberately excludes private plans, resources and arbitrary prose.
ACTIVITY_FIELDS={
    'building':{'building','level','cell'}, 'colony':{'cell'}, 'expansion':{'cell'},
    'research':{'category','completed'}, 'project':set(), 'event':set(),
    'battle':{'winner'}, 'war':set(), 'treaty':{'kind'}, 'breach':{'kind'},
    'goal':{'code'}, 'guarantee':set(), 'marriage':set(), 'labor_reform':{'mode'}, 'captives':{'quantity'},
}
SCORES={'war':100,'battle':90,'breach':85,'treaty':80,'guarantee':75,'goal':70,
        'event':60,'expansion':55,'colony':50,'project':40,'research':30,'building':10,
        'marriage':78,'labor_reform':65,'captives':72}


def activity(c,kind,nid,source_key,payload=None,other=None):
    if kind not in ACTIVITY_FIELDS:raise ValueError('Unknown public activity')
    safe={k:v for k,v in (payload or {}).items() if k in ACTIVITY_FIELDS[kind] and isinstance(v,(str,int,float))}
    c.execute('INSERT INTO world_activity(nation_id,other_nation_id,kind,source_key,payload_json,occurred_at) '
              'VALUES(?,?,?,?,?,?) ON CONFLICT(source_key) DO NOTHING',
              (nid,other,kind,source_key,json.dumps(safe),datetime.now(timezone.utc).isoformat()))


def remember_event(c,state):
    if not state.get('resolved') or 'applied' not in state:return
    payload={'event_id':state['event_id'],'opening':state['opening'],
             'decisions':[{'action':h['action'],'custom':h.get('custom',False)} for h in state['history']],
             'outcome':state['applied'],'language':state['lang']}
    c.execute('INSERT INTO nation_memories(nation_id,source_key,payload_json) VALUES(?,?,?) '
              'ON CONFLICT(nation_id,source_key) DO NOTHING',
              (state['nation_id'],f"event:{state['event_id']}",json.dumps(payload,ensure_ascii=False)))


def memories(nid,topic='',limit=6):
    with db.cursor() as c:
        c.execute('SELECT id,payload_json FROM nation_memories WHERE nation_id=? ORDER BY id DESC LIMIT 100',(nid,))
        rows=c.fetchall()
    words=set(re.findall(r'\w{4,}',topic.lower()))
    ranked=[]
    for row in rows:
        p=json.loads(row['payload_json'])
        score=len(words & set(re.findall(r'\w{4,}',(p['opening']+' '+ ' '.join(d['action'] for d in p['decisions'])).lower())))
        ranked.append((score,row['id'],p))
    return [dict(event_id=p['event_id'],opening=p['opening'][:600],
                 decisions=[d['action'][:350] for d in p['decisions']],outcome=p['outcome'])
            for _,_,p in sorted(ranked,key=lambda x:(x[0],x[1]),reverse=True)[:limit]]


def seed_world():
    """Backfill completed decisions, never reapply their rewards or publish old news."""
    with db.atomic() as c:
        world_lock(c)
        c.execute("SELECT value FROM economy_meta WHERE key='event_memory_v1'")
        if c.fetchone():return
        c.execute("SELECT state_json FROM event_runs r JOIN events e ON e.id=r.event_id WHERE e.status='resolved'")
        for row in c.fetchall():remember_event(c,json.loads(row['state_json']))
        c.execute("INSERT INTO economy_meta(key,value) VALUES('event_memory_v1','1')")


def create_nation(player_id,name,history,flag,government,gm_id):
    name,history=name.strip(),history.strip()
    if not 1<=len(name)<=80 or not 1<=len(history)<=4000 or len(flag)>512 or len(government)>80:
        raise ValueError(tr('Nazwa: 1–80 znaków; historia: 1–4000; flaga: do 512; ustrój: do 80.',
                            'Name: 1–80 characters; lore: 1–4000; flag: up to 512; government: up to 80.'))
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT id FROM nations WHERE owner_id=?',(str(player_id),))
        if c.fetchone():raise ValueError(tr('Ten gracz ma już państwo.','This player already owns a nation.'))
        c.execute('SELECT id FROM nations WHERE LOWER(name)=LOWER(?)',(name,))
        if c.fetchone():raise ValueError(tr('Ta nazwa państwa jest zajęta.','This nation name is already taken.'))
        nid=db.insert_returning_id('INSERT INTO nations(owner_id,name,flag,government_type) VALUES(?,?,?,?)',
                                  (str(player_id),name,flag,government or 'Monarchy'))
        from cogs.military import seed_nation_blueprints
        seed_nation_blueprints(nid)
        c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)',(nid,'lore',history))
        c.execute('INSERT INTO ownership_changes(nation_id,previous_owner,new_owner,gm_id) VALUES(?,?,?,?)',
                  (nid,'',str(player_id),str(gm_id)))
        return nid


def transfer_nation(nid,new_owner,expected_owner,gm_id):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT id FROM trades ORDER BY id'+(' FOR UPDATE' if db.USE_POSTGRES else ''));c.fetchall()
        n=lock_nation(c,nid)
        if n['owner_id']!=str(expected_owner):raise ValueError(tr('Właściciel zmienił się. Otwórz przekazanie ponownie.','Owner changed. Open the transfer again.'))
        c.execute('SELECT id FROM nations WHERE owner_id=?',(str(new_owner),))
        if c.fetchone():raise ValueError(tr('Ten gracz ma już państwo.','This player already owns a nation.'))
        c.execute('UPDATE nations SET owner_id=? WHERE id=?',(str(new_owner),nid))
        c.execute('INSERT INTO ownership_changes(nation_id,previous_owner,new_owner,gm_id) VALUES(?,?,?,?)',
                  (nid,n['owner_id'],str(new_owner),str(gm_id)))
        c.execute("UPDATE treaties SET status='cancelled' WHERE status IN ('draft','proposed') AND (proposer_id=? OR recipient_id=?)",(nid,nid))
        c.execute("UPDATE dynastic_marriages SET status='cancelled' WHERE status='proposed' AND (proposer_id=? OR recipient_id=?)",(nid,nid))
        c.execute("UPDATE trades SET status='cancelled',resolved_at=CURRENT_TIMESTAMP WHERE status='pending' AND (from_nation_id=? OR to_nation_id=?)",(nid,nid))
        return n


GOALS={
    'food_security':('Bezpieczne zapasy','Food security','Przez 3 kolejne miesiące: brak głodu, zapas na 2 miesiące i stabilność ≥60.','For 3 consecutive months: no hunger, two months of food and stability ≥60.'),
    'development':('Rozwój państwa','National development','Wybuduj lub ulepsz 2 budynki. Cel trwa co najmniej 3 miesiące.','Build or upgrade 2 buildings. The goal takes at least 3 months.'),
    'scholarship':('Postęp naukowy','Scientific progress','Ukończ projekt badawczy po wybraniu celu. Co najmniej 3 miesiące.','Complete a research project after choosing the goal. At least 3 months.'),
}


def choose_goal(nid,owner_id,code):
    if code not in GOALS:raise ValueError('Unknown goal')
    with db.atomic() as c:
        world_lock(c);n=owned(c,nid,owner_id)
        c.execute("SELECT id FROM nation_goals WHERE nation_id=? AND status='active'",(nid,))
        if c.fetchone():raise ValueError(tr('Masz już aktywny cel.','You already have an active goal.'))
        c.execute('SELECT COALESCE(MAX(id),0) AS id FROM world_activity');after=c.fetchone()['id']
        month=month_index(c)
        return db.insert_returning_id('INSERT INTO nation_goals(nation_id,code,start_month,last_month,baseline_json,activity_after) VALUES(?,?,?,?,?,?)',
                                      (nid,code,month,month,n['tech_json'],after))


def abandon_goal(nid,owner_id,goal_id):
    with db.atomic() as c:
        world_lock(c);owned(c,nid,owner_id)
        c.execute("UPDATE nation_goals SET status='abandoned' WHERE id=? AND nation_id=? AND status='active'",(goal_id,nid))
        if not c.rowcount:raise ValueError(tr('Cel już nie jest aktywny.','The goal is no longer active.'))


def goal_progress(c,goal,month):
    """Read current actions without awarding rewards from a dashboard preview."""
    progress=read_json(goal['progress_json'])
    progress['months']=max(0,month-goal['start_month'])
    if goal['code'] in ('development','scholarship'):
        c.execute('SELECT kind,payload_json FROM world_activity WHERE nation_id=? AND id>?',
                  (goal['nation_id'],goal['activity_after']))
        entries=c.fetchall()
        progress['count']=(sum(r['kind']=='building' for r in entries) if goal['code']=='development' else
                           sum(r['kind']=='research' and read_json(r['payload_json']).get('completed') is True for r in entries))
    return progress


def finish_goal(c,goal,month,gm_id=None):
    c.execute("UPDATE nation_goals SET status='completed',completed_month=?,last_month=? WHERE id=? AND status='active'",
              (month,month,goal['id']))
    if c.rowcount!=1:raise ValueError(tr('Cel już nie jest aktywny.','The goal is no longer active.'))
    reward(c,goal['nation_id'],prestige=10)
    activity(c,'goal',goal['nation_id'],f"goal:{goal['id']}",{'code':goal['code']})
    c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)',
              (goal['nation_id'],'goal_private',json.dumps(dict(goal_id=goal['id'],completed_month=month,gm_id=str(gm_id) if gm_id is not None else None,reward_prestige=10))))


def gm_create_goal(nid,gm_id,title,description):
    """GM role is checked by the Discord command; one active goal per nation."""
    title,description=title.strip(),description.strip()
    if not 1<=len(title)<=80 or not 1<=len(description)<=1500:
        raise ValueError(tr('Tytuł: 1–80 znaków; warunki: 1–1500 znaków.', 'Title: 1–80 characters; requirements: 1–1500 characters.'))
    with db.atomic() as c:
        world_lock(c);lock_nation(c,nid)
        c.execute("SELECT id FROM nation_goals WHERE nation_id=? AND status='active'",(nid,))
        if c.fetchone():raise ValueError(tr('Państwo ma już aktywny cel.','The nation already has an active goal.'))
        month=month_index(c)
        details=dict(title=title,description=description,created_by=str(gm_id))
        gid=db.insert_returning_id("INSERT INTO nation_goals(nation_id,code,start_month,last_month,baseline_json) VALUES(?,'custom',?,?,?)",
                                   (nid,month,month,json.dumps(details,ensure_ascii=False)))
        c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)',
                  (nid,'goal_private',json.dumps(dict(goal_id=gid,title=title,created_by=str(gm_id)),ensure_ascii=False)))
        return gid


def gm_complete_goal(nid,goal_id,gm_id):
    with db.atomic() as c:
        world_lock(c);lock_nation(c,nid)
        c.execute('SELECT * FROM nation_goals WHERE id=? AND nation_id=?',(goal_id,nid));goal=c.fetchone()
        if not goal or goal['status']!='active':
            raise ValueError(tr('Nie znaleziono aktywnego celu tego państwa.','No matching active goal for this nation.'))
        month=month_index(c)
        progress=goal_progress(c,goal,month);progress['confirmed_by']=str(gm_id)
        c.execute('UPDATE nation_goals SET progress_json=? WHERE id=?',(json.dumps(progress),goal_id))
        finish_goal(c,goal,month,gm_id)


def progress_goals(c,nid,month,report):
    c.execute("SELECT * FROM nation_goals WHERE nation_id=? AND status='active' AND last_month<?",(nid,month))
    goal=c.fetchone()
    if not goal:return
    progress=goal_progress(c,goal,month);elapsed=progress['months']
    if goal['code']=='food_security':
        qualifies=(report['food_needed']>0 and not report['food_shortage'] and report['food_months']>=2 and report['stability']>=60)
        consecutive=progress.get('count',0) if month==goal['last_month']+1 else 0
        progress['count']=consecutive+1 if qualifies else 0
        complete=progress['count']>=3
    elif goal['code']=='development':
        complete=progress['count']>=2
    elif goal['code']=='scholarship':
        complete=progress['count']>=1
    else:
        complete=False  # Custom objectives always need explicit GM confirmation.
    complete=complete and elapsed>=3
    progress['months']=elapsed
    c.execute('UPDATE nation_goals SET progress_json=?,last_month=? WHERE id=?',
              (json.dumps(progress),month,goal['id']))
    if complete:
        finish_goal(c,goal,month)
