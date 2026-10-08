"""Bounded, one-time starting packages, applied in the nation approval transaction."""
import json
import math
import db
from game_setup import budget
from world_service import world_lock,tr

FIELDS={
    'resources':('Surowce początkowe','Starting resources'),
    'gold':('Płynna gotówka','Cash'),
    'territory':('Wielkość państwa','Nation size'),
    'culture':('Siła kultury','Culture strength'),
    'religion':('Siła religii','Religion strength'),
    'technology':('Technologia','Technology'),
}


def defaults(cells,total):
    p=dict.fromkeys(FIELDS,0)
    p['territory']=min(10,max(1,len(cells)),total)
    # A transparent starting suggestion; the player can redistribute every point.
    while sum(p.values())<total:
        available=[k for k in FIELDS if p[k]<10 and (k!='territory' or all(p[x]==10 for x in FIELDS if x!='territory'))]
        for k in available:
            if sum(p.values())==total:break
            p[k]+=1
    return p


def choices(a):
    with db.cursor() as c:
        c.execute('SELECT points_json FROM nation_start_choices WHERE application_id=?',(a['id'],));r=c.fetchone()
    return json.loads(r['points_json']) if r else defaults(json.loads(a['cells_json']),budget(a['guild_id']))


def legacy(a):
    if a['status']!='approved':return False
    with db.cursor() as c:
        c.execute('SELECT application_id FROM nation_start_choices WHERE application_id=?',(a['id'],))
        return c.fetchone() is None


def validate(p,total,cells=None):
    if not isinstance(p,dict) or set(p)!=set(FIELDS) or any(type(v)!=int or not 0<=v<=10 for v in p.values()) or p['territory']<1:
        raise ValueError(tr('Kategorie: 0–10; wielkość państwa: 1–10.','Categories: 0–10; nation size: 1–10.'))
    if sum(p.values())!=total:raise ValueError(tr(f'Rozdziel dokładnie {total} punktów.',f'Allocate exactly {total} points.'))
    if cells is not None and len(cells)!=p['territory']:
        raise ValueError(tr(f"W formularzu wskaż dokładnie {p['territory']} prowincji albo zmień bonus wielkości państwa.",
                            f"Select exactly {p['territory']} provinces in the application or adjust the nation size bonus."))


def effects(p):
    from nation_applications import STARTER
    return dict(gold=200+60*p['gold'],
        resources={k:math.floor(v*(.5+.1*p['resources'])+.5) for k,v in STARTER.items()},
        technology=round(2.5+.1*p['technology'],1),
        culture=round(.5+.15*p['culture'],2),religion=round(.5+.15*p['religion'],2),territory=p['territory'])


def save(uid,guild,aid,version,p):
    from nation_applications import get
    with db.atomic() as c:
        world_lock(c)
        a=get(aid,guild,uid)
        if a['status']!='pending' or a['version']!=version:raise ValueError(tr('Zgłoszenie zmieniło się. Otwórz aktualny kreator.','Application changed. Reopen the builder.'))
        validate(p,budget(guild))
        c.execute('INSERT INTO nation_start_choices(application_id,points_json) VALUES(?,?) ON CONFLICT(application_id) DO UPDATE SET points_json=excluded.points_json',
                  (aid,json.dumps(p)))
        c.execute('UPDATE nation_applications SET version=version+1 WHERE id=?',(aid,))


def apply(c,a,nid,provinces):
    p=choices(a);validate(p,budget(a['guild_id']),provinces);e=effects(p)
    c.execute('INSERT INTO nation_start_choices(application_id,points_json) VALUES(?,?) ON CONFLICT(application_id) DO NOTHING',
              (a['id'],json.dumps(p)))
    c.execute('UPDATE nations SET treasury=?,resources_json=?,tech_json=? WHERE id=?',
              (e['gold'],json.dumps(e['resources']),json.dumps(dict.fromkeys(('land','naval','economy','colonial'),e['technology'])),nid))
    # Clone the capital's identities. Shared map entities must never be boosted
    # for all countries just because one player paid starting points.
    capital=provinces[0]['azgaar_cell_id']
    c.execute('SELECT * FROM azgaar_cells WHERE cell_id=?',(capital,));origin=c.fetchone() or {}
    new={}
    for field,kind in (('culture','cultures'),('religion','religions')):
        c.execute('SELECT data_json FROM azgaar_entities WHERE kind=? AND entity_id=?',(kind,origin.get(field+'_id',0)))
        source=c.fetchone();entity=json.loads(source['data_json']) if source else {}
        c.execute('SELECT COALESCE(MAX(entity_id),0)+1 AS id FROM azgaar_entities WHERE kind=?',(kind,));eid=c.fetchone()['id']
        entity.update(i=eid,name=a['name']+(' — kultura' if field=='culture' else ' — religia'),
            expansionism=e[field],center=capital,removed=False)
        entity.setdefault('color',f'#{(nid*2654435761)&0xffffff:06x}')
        entity.setdefault('type','Generic' if field=='culture' else 'Organized')
        if field=='culture':entity.setdefault('base',0)
        else:entity.update(culture=new['culture'],origin=0)
        c.execute('INSERT INTO azgaar_entities(kind,entity_id,data_json) VALUES(?,?,?)',(kind,eid,json.dumps(entity)))
        new[field]=eid
    for province in provinces:
        c.execute('INSERT INTO azgaar_cells(cell_id,state_id,culture_id,religion_id) VALUES(?,0,?,?) '
                  'ON CONFLICT(cell_id) DO UPDATE SET culture_id=excluded.culture_id,religion_id=excluded.religion_id',
                  (province['azgaar_cell_id'],new['culture'],new['religion']))
    return e
