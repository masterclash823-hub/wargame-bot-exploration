"""Player-authored nation applications; resources and territory exist only after approval."""
import json
import unicodedata
import db
import province_geography as geography
from nation_access import find_nation
from world_service import world_lock,create_nation,tr

STARTER_GOLD=500
STARTER={'food':200,'wood':150,'stone':100,'iron':80,'copper':40,'coal':40,'clay':60,
         'cloth':30,'tar':30,'gunpowder':20,'horses':10,'spices':10,'silk':5}


def key(name):return unicodedata.normalize('NFKC',name).casefold().strip()


def land(c,ids):
    if not ids or len(ids)>10 or len(ids)!=len(set(ids)) or any(type(x)!=int or not 0<=x<=2147483647 for x in ids):
        raise ValueError(tr('Podaj 1–10 różnych ID prowincji; pierwsza będzie stolicą.','Provide 1–10 distinct province IDs; the first becomes the capital.'))
    cells,_=geography.load(c);rows=[]
    for cell in ids:
        c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1',(cell,));p=c.fetchone()
        if not p or p['owner_nation_id'] is not None or geography.is_water(p,cells):
            raise ValueError(tr(f'Prowincja #{cell} jest zajęta, nieaktywna lub wodna.',f'Province #{cell} is occupied, inactive or water.'))
        rows.append(p)
    if len(ids)>1:
        reached={ids[0]}
        c.execute('SELECT cell_id,neighbor_cell_id FROM province_neighbors')
        edges=c.fetchall()
        while True:
            before=len(reached)
            for edge in edges:
                if edge['cell_id'] in reached and edge['neighbor_cell_id'] in ids:reached.add(edge['neighbor_cell_id'])
                if edge['neighbor_cell_id'] in reached and edge['cell_id'] in ids:reached.add(edge['cell_id'])
            if len(reached)==before:break
        if len(reached)!=len(ids):raise ValueError(tr('Prowincje startowe muszą tworzyć połączone terytorium.','Starting provinces must form connected territory.'))
    return rows


def submit(uid,guild_id,name,history,flag,government,ids,expected_version=None):
    name,history,flag,government=(x.strip() for x in (name,history,flag,government))
    if not guild_id:raise ValueError(tr('Zgłoszenie wymaga serwera gry.','Apply from the game server.'))
    if not 1<=len(name)<=80 or not 1<=len(history)<=4000 or len(flag)>512 or len(government)>80 or any('\x00' in x for x in (name,history,flag,government)):
        raise ValueError(tr('Nazwa: 1–80 znaków; historia: 1–4000; flaga: do 512; ustrój: do 80.',
                            'Name: 1–80 characters; lore: 1–4000; flag: up to 512; government: up to 80.'))
    try:ids=[int(x.strip()) for x in ids.split(',') if x.strip()] if isinstance(ids,str) else ids
    except ValueError:raise ValueError(tr('ID prowincji oddziel przecinkami.','Separate province IDs with commas.'))
    with db.atomic() as c:
        world_lock(c)
        if find_nation(uid,c):raise ValueError(tr('Masz już państwo lub dostęp coop.','You already belong to a nation.'))
        c.execute('SELECT name FROM nations')
        if any(key(n['name'])==key(name) for n in c.fetchall()):raise ValueError(tr('Ta nazwa jest już zajęta.','This name is already taken.'))
        c.execute("SELECT * FROM nation_applications WHERE player_id=? AND status='pending'",(str(uid),));old=c.fetchone()
        if old and (old['guild_id']!=str(guild_id) or expected_version!=old['version']):
            raise ValueError(tr('Masz już zgłoszenie. Otwórz /nation application, aby je edytować.','You already have an application. Open /nation application to edit it.'))
        if not old and expected_version is not None:raise ValueError(tr('Status zgłoszenia zmienił się. Otwórz je ponownie.','Application status changed. Open it again.'))
        c.execute("SELECT id FROM nation_applications WHERE name_key=? AND status='pending' AND player_id<>?",(key(name),str(uid)))
        if c.fetchone():raise ValueError(tr('Ta nazwa jest w innym oczekującym zgłoszeniu.','Another pending application uses this name.'))
        land(c,ids)
        values=(name,key(name),history,flag,government,json.dumps(ids))
        if old:
            c.execute('UPDATE nation_applications SET name=?,name_key=?,history=?,flag=?,government=?,cells_json=?,version=version+1 WHERE id=?',(*values,old['id']))
            return old['id']
        aid=db.insert_returning_id('INSERT INTO nation_applications(name,name_key,history,flag,government,cells_json,player_id,guild_id) VALUES(?,?,?,?,?,?,?,?)',
            (*values,str(uid),str(guild_id)))
        from starting_bonuses import defaults
        from game_setup import budget
        c.execute('INSERT INTO nation_start_choices(application_id,points_json) VALUES(?,?)',(aid,json.dumps(defaults(ids,budget(guild_id)))))
        return aid


def get(aid,guild_id,uid=None):
    with db.cursor() as c:
        c.execute('SELECT * FROM nation_applications WHERE id=? AND guild_id=?',(aid,str(guild_id)));a=c.fetchone()
    if not a or (uid is not None and a['player_id']!=str(uid)):raise ValueError(tr('Zgłoszenie niedostępne.','Application unavailable.'))
    return a


def latest(uid,guild_id):
    with db.cursor() as c:
        c.execute('SELECT * FROM nation_applications WHERE player_id=? AND guild_id=? ORDER BY id DESC LIMIT 1',(str(uid),str(guild_id)))
        return c.fetchone()


def pending(guild_id):
    with db.cursor() as c:
        c.execute("SELECT * FROM nation_applications WHERE guild_id=? AND status='pending' ORDER BY id",(str(guild_id),))
        return c.fetchall()


def decide(aid,guild_id,actor,version,action,reason=''):
    """GM authorization is checked at the Discord boundary, including old buttons."""
    if action not in ('approve','reject','withdraw'):raise ValueError('Invalid decision')
    if action=='reject' and not reason.strip():raise ValueError(tr('Podaj powód odrzucenia.','Provide a rejection reason.'))
    if len(reason)>500:raise ValueError(tr('Powód: do 500 znaków.','Reason: up to 500 characters.'))
    with db.atomic() as c:
        world_lock(c)
        a=get(aid,guild_id)
        if a['status']!='pending' or a['version']!=version:raise ValueError(tr('Zgłoszenie zmieniło się lub już rozpatrzono. Otwórz aktualny podgląd.','The application changed or was reviewed. Open the current preview.'))
        if action=='withdraw' and a['player_id']!=str(actor):raise ValueError(tr('To nie jest Twoje zgłoszenie.','This is not your application.'))
        nid=None
        if action=='approve':
            provinces=land(c,json.loads(a['cells_json']))
            from starting_bonuses import choices,validate,apply
            from game_setup import budget
            validate(choices(a),budget(guild_id),provinces)
            if find_nation(a['player_id'],c):raise ValueError(tr('Gracz otrzymał już inne państwo.','The player already received another nation.'))
            c.execute('SELECT name FROM nations')
            if any(key(n['name'])==a['name_key'] for n in c.fetchall()):raise ValueError(tr('Nazwa została zajęta.','The name is now taken.'))
            nid=create_nation(a['player_id'],a['name'],a['history'],a['flag'],a['government'],actor)
            for p in provinces:
                c.execute('DELETE FROM province_labor WHERE province_id=?',(p['id'],))
                c.execute('UPDATE provinces SET owner_nation_id=? WHERE id=?',(nid,p['id']))
            c.execute('UPDATE nations SET capital_province_id=?,population=?,treasury=?,resources_json=? WHERE id=?',
                (provinces[0]['id'],sum(p['population'] for p in provinces),STARTER_GOLD,json.dumps(STARTER),nid))
            apply(c,a,nid,provinces)
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,'system',?)",(nid,
                tr(f'Zatwierdzono zgłoszenie #{aid}. Przyznano prowincje, stolicę i pakiet startowy.',f'Application #{aid} approved. Starting provinces, capital and supplies granted.')))
        status={'approve':'approved','reject':'rejected','withdraw':'withdrawn'}[action]
        c.execute('UPDATE nation_applications SET status=?,reviewer_id=?,reason=?,nation_id=?,reviewed_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?',
            (status,str(actor),reason.strip(),nid,aid))
        return nid
