"""Paid, limited biome projects settled by the atomic game calendar."""
import json

import db
import i18n
import province_geography as geography
from economy_engine import read_json,lock_nation
from world_service import tr,world_lock,month_index

COOLDOWN=12
MIN_POPULATION=1000
MIN_STABILITY=40
PROJECTS={
    'afforest':dict(pl='Zalesianie',en='Afforestation',sources=('Temperate Grassland',),
                    target='Temperate Deciduous Forest',months=6,tech=4,cost=dict(gold=800,wood=100,stone=50)),
    'clear_forest':dict(pl='Przygotowanie pól',en='Land clearing',sources=('Temperate Deciduous Forest',),
                        target='Temperate Grassland',months=6,tech=4,cost=dict(gold=800,wood=50,stone=50,iron=50)),
    'drain_wetland':dict(pl='Osuszanie mokradeł',en='Wetland drainage',sources=('Wetland',),
                         target='Temperate Grassland',months=9,tech=5,cost=dict(gold=1200,wood=150,stone=150,iron=50)),
    'irrigate_desert':dict(pl='Nawadnianie pustyni',en='Desert irrigation',sources=('Desert','Hot Desert'),
                           target='Savanna',months=12,tech=5,cost=dict(gold=1600,wood=100,stone=200,iron=75)),
    'restore_tundra':dict(pl='Zalesianie tundry',en='Tundra afforestation',sources=('Tundra',),
                          target='Taiga',months=12,tech=6,cost=dict(gold=1800,wood=150,stone=150,iron=100)),
}


def name(key):
    rule=PROJECTS.get(key)
    return tr(rule['pl'],rule['en']) if rule else key


def date(month):
    return f'{month%12+1}/{month//12}'


def map_biomes(c):
    from azgaar_format import biome_ids
    c.execute('SELECT data_json FROM azgaar_world WHERE id=1')
    saved=c.fetchone()
    return biome_ids(json.loads(saved['data_json'])) if saved else None


def _nation(c,uid,expected=None,lock=False):
    from nation_access import find_nation,can_manage
    nation=find_nation(uid,c)
    if not nation or (expected is not None and nation['id']!=expected) or not can_manage(nation['id'],uid,c):
        raise ValueError(tr('Brak dostępu do państwa. Otwórz ponownie terraformację.',
                            'Nation access unavailable. Reopen terraforming.'))
    return lock_nation(c,nation['id']) if lock else nation


def _choices(province,cells):
    height=cells.get(province['azgaar_cell_id'],{}).get('h',0)
    if geography.is_water(province,cells) or province['terrain'].casefold()=='mountains' or height>70:
        return []
    biome=province['biome'].strip().casefold()
    return [key for key,rule in PROJECTS.items() if biome in {source.casefold() for source in rule['sources']}]


def incompatible(c,province,target_biome,target_terrain):
    from cogs.economy import _building_terrain_ok
    c.execute('SELECT * FROM building_defs')
    definitions={row['key']:row for row in c.fetchall()}
    target=dict(province,biome=target_biome,terrain=target_terrain)
    return [key for key in read_json(province['buildings_json'],[])
            if key not in definitions or not _building_terrain_ok(target,definitions[key])]


def require_compatible_build(c,province,definition):
    from cogs.economy import _building_terrain_ok
    c.execute("SELECT target_biome,target_terrain FROM province_terraforming WHERE province_id=? AND status='building'",(province['id'],))
    project=c.fetchone()
    if project and not _building_terrain_ok(dict(province,biome=project['target_biome'],terrain=project['target_terrain']),definition):
        raise ValueError(tr('Ten budynek nie pasuje do biomu planowanej terraformacji.',
                            'This building is incompatible with the planned terraforming biome.'))


def _quote(c,nation,province,key,cells,now):
    from province_admin import biome_result
    if key not in _choices(province,cells):
        raise ValueError(tr('Ten projekt nie jest dostępny dla tej prowincji.',
                            'This project is unavailable for this province.'))
    rule=PROJECTS[key]
    target=biome_result(province,rule['target'])
    reasons=[]
    catalogue=map_biomes(c)
    if catalogue is not None and rule['target'].casefold() not in catalogue:
        reasons.append(tr('Mapa nie zawiera docelowego biomu. GM musi zaimportować pełny katalog biomów.',
                          'The map has no target biome. A GM must import its full biome catalogue.'))
    if read_json(nation['tech_json']).get('economy',3)<rule['tech']:
        reasons.append(tr('Wymagana technologia gospodarcza: ','Economy technology required: ')+str(rule['tech']))
    if nation['stability']<MIN_STABILITY:
        reasons.append(tr('Wymagana stabilność: ','Stability required: ')+str(MIN_STABILITY))
    if province['population']<MIN_POPULATION:
        reasons.append(tr('Wymagana ludność prowincji: ','Province population required: ')+str(MIN_POPULATION))
    c.execute('SELECT status FROM colonies WHERE province_id=?',(province['id'],))
    colony=c.fetchone()
    if colony and colony['status']!='province':
        reasons.append(tr('Najpierw rozwiń kolonię do pełnej prowincji.','Develop the colony into a full province first.'))
    c.execute("SELECT id FROM province_terraforming WHERE status='building' AND (nation_id=? OR province_id=?) LIMIT 1",
              (nation['id'],province['id']))
    if c.fetchone():
        reasons.append(tr('Państwo lub prowincja ma już trwającą terraformację.',
                          'The nation or province already has a terraforming project underway.'))
    c.execute("SELECT MAX(finished_month) AS finished FROM province_terraforming WHERE province_id=? AND status='complete'",(province['id'],))
    last=c.fetchone()['finished']
    if last is not None and now<last+COOLDOWN:
        reasons.append(tr('Kolejna terraformacja od: ','Next terraforming available: ')+date(last+COOLDOWN))
    blocked=incompatible(c,province,target['biome'],target['terrain'])
    if blocked:
        reasons.append(tr('Budynki niezgodne z nowym biomem: ','Buildings incompatible with the new biome: ')+
                       ', '.join(i18n.term(key) for key in blocked))
    resources=read_json(nation['resources_json'])
    missing=[f'{i18n.term(key)}: {amount-(nation["treasury"] if key=="gold" else resources.get(key,0)):g}'
             for key,amount in rule['cost'].items() if (nation['treasury'] if key=='gold' else resources.get(key,0))<amount]
    if missing:reasons.append(tr('Brakuje: ','Missing: ')+', '.join(missing))
    return dict(key=key,target=target,cost=dict(rule['cost']),months=rule['months'],tech=rule['tech'],
                due=now+rule['months'],reasons=reasons)


def preview(uid,cell=None,key=None,*,nation_id=None):
    with db.cursor() as c:
        nation=_nation(c,uid,nation_id)
        cells,_=geography.load(c)
        now=month_index(c)
        c.execute('SELECT * FROM provinces WHERE owner_nation_id=? AND active=1 ORDER BY azgaar_cell_id',(nation['id'],))
        rows=[row for row in c.fetchall() if not geography.is_water(row,cells)]
        selected=next((row for row in rows if row['azgaar_cell_id']==cell),None)
        if cell is not None and selected is None:
            raise ValueError(tr('Wybierz własną aktywną prowincję lądową.','Choose your own active land province.'))
        choices=_choices(selected,cells) if selected else []
        if key is None and len(choices)==1:key=choices[0]
        quote=_quote(c,nation,selected,key,cells,now) if selected and key is not None else None
        c.execute('SELECT t.*,p.azgaar_cell_id FROM province_terraforming t JOIN provinces p ON p.id=t.province_id '
                  "WHERE t.nation_id=? ORDER BY CASE WHEN t.status='building' THEN 0 ELSE 1 END,t.id DESC LIMIT 5",(nation['id'],))
        projects=c.fetchall()
        return dict(nation=nation,rows=rows,selected=selected,choices=choices,quote=quote,projects=projects,month=now)


def start(uid,cell,key,*,nation_id=None,expected_biome=None,expected_terrain=None):
    from economy_services import spend
    with db.atomic() as c:
        world_lock(c)
        nation=_nation(c,uid,nation_id,lock=True)
        c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND owner_nation_id=? AND active=1'+
                  (' FOR UPDATE' if db.USE_POSTGRES else ''),(cell,nation['id']))
        province=c.fetchone()
        if not province:raise ValueError(tr('Ta prowincja nie należy już do twojego państwa.','This province no longer belongs to your nation.'))
        if ((expected_biome is not None and province['biome']!=expected_biome) or
                (expected_terrain is not None and province['terrain']!=expected_terrain)):
            raise ValueError(tr('Prowincja zmieniła się. Odśwież podgląd.','The province changed. Refresh the preview.'))
        cells,_=geography.load(c)
        now=month_index(c)
        quote=_quote(c,nation,province,key,cells,now)
        if quote['reasons']:raise ValueError('\n'.join(quote['reasons']))
        spend(c,nation,quote['cost'])
        project_id=db.insert_returning_id('INSERT INTO province_terraforming(nation_id,province_id,project_key,'
            'source_biome,source_terrain,target_biome,target_terrain,cost_json,started_month,due_month) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (nation['id'],province['id'],key,province['biome'],province['terrain'],quote['target']['biome'],
             quote['target']['terrain'],json.dumps(quote['cost']),now,quote['due']))
        c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,'system',?)",
                  (nation['id'],f'Terraforming #{project_id}, cell #{cell}: {province["biome"]} → {quote["target"]["biome"]}; due {date(quote["due"])}.'))
        return dict(id=project_id,cell=cell,**quote)


def reason_text(reason):
    return tr(*{
        'ownership':('Zmiana właściciela lub nieaktywna prowincja.','Ownership changed or the province is inactive.'),
        'water':('Prowincja jest polem wodnym.','The province is a water cell.'),
        'source':('Biom lub teren zmienił się w trakcie prac.','The biome or terrain changed during construction.'),
        'buildings':('Nowe budynki nie pasują do docelowego biomu.','New buildings are incompatible with the target biome.'),
        'ruins':('Państwo upadło.','The nation has fallen.'),
        'map':('Mapa nie zawiera już docelowego biomu.','The map no longer contains the target biome.'),
    }[reason])


def tick(c,month):
    """Finish after this month's production; retries cannot repeat a completed job."""
    from province_admin import apply_biome
    c.execute("SELECT * FROM province_terraforming WHERE status='building' ORDER BY id")
    projects=c.fetchall()
    if not projects:return {}
    cells,_=geography.load(c)
    catalogue=map_biomes(c)
    c.execute("SELECT nation_id FROM nation_decay WHERE status='ruins'")
    fallen={row['nation_id'] for row in c.fetchall()}
    events={}
    for project in projects:
        c.execute('SELECT * FROM provinces WHERE id=?',(project['province_id'],))
        province=c.fetchone()
        reason=None
        if not province or not province['active'] or project['nation_id'] is None or province['owner_nation_id']!=project['nation_id']:
            reason='ownership'
        elif project['nation_id'] in fallen:reason='ruins'
        elif geography.is_water(province,cells):reason='water'
        elif catalogue is not None and project['target_biome'].casefold() not in catalogue:reason='map'
        elif (province['biome'],province['terrain'])!=(project['source_biome'],project['source_terrain']):reason='source'
        elif incompatible(c,province,project['target_biome'],project['target_terrain']):reason='buildings'
        if not reason and month<project['due_month']:continue
        status='cancelled' if reason else 'complete'
        if not reason:apply_biome(c,province,project['target_biome'])
        c.execute("UPDATE province_terraforming SET status=?,finished_month=?,reason=? WHERE id=? AND status='building'",
                  (status,month,reason or '',project['id']))
        if project['nation_id'] is not None:
            cell=province['azgaar_cell_id'] if province else None
            event=dict(id=project['id'],cell=cell,status=status,reason=reason,
                       target=project['target_biome'])
            events.setdefault(project['nation_id'],[]).append(event)
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,'system',?)",
                      (project['nation_id'],f'Terraforming #{project["id"]}, cell #{cell}: {status}'+
                       (f' ({reason}); costs not refunded.' if reason else f' → {project["target_biome"]}.')))
    return events
