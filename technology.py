"""Monthly research and scarce algae. All effects are deterministic game rules."""
import hashlib
import json
import math
import uuid

import db
import i18n

CATEGORIES = ('economy', 'land', 'naval', 'colonial')
TECH_MAX = 10.0
BASE_KNOWLEDGE = 1
MAX_ALGAE_SITES = 5
ALGAE_YIELD = .5


def tr(pl, en):
    return pl if i18n.current_language() == 'pl' else en


def _project(category, pl, en, effects, *, tier=1, algae=0, repeat=False):
    cost, months, gain, level = {1:(3,3,.5,0), 2:(8,4,1,4), 3:(16,6,1,5)}[tier]
    return dict(category=category, pl=pl, en=en, effects=effects, knowledge=cost,
                duration=months, gain=gain, level=6 if algae else level,
                algae=algae, repeat=repeat)


PROJECTS = {
    'crop_rotation': _project('economy','Trójpolówka','Crop rotation',{'farm_food':.10}),
    'food_storage': _project('economy','Przechowywanie żywności','Food storage',{'spoilage':-.25}),
    'public_accounts': _project('economy','Rachunkowość państwowa','Public accounts',{'taxes':.05},tier=2),
    'scientific_method': _project('economy','Metoda naukowa','Scientific method',{'knowledge':.25},tier=3),
    'army_logistics': _project('land','Tabory wojskowe','Army logistics',{'land_upkeep':-.05}),
    'field_drill': _project('land','Musztra polowa','Field drill',{'land_attack':.05}),
    'field_engineering': _project('land','Inżynieria polowa','Field engineering',{'land_defense':.10},tier=2),
    'general_staff': _project('land','Sztab generalny','General staff',{'land_attack':.05,'land_defense':.05},tier=3),
    'cargo_design': _project('naval','Ulepszone ładownie','Improved holds',{'cargo':.10}),
    'naval_logistics': _project('naval','Logistyka morska','Naval logistics',{'naval_upkeep':-.05}),
    'shipwrights': _project('naval','Szkoła szkutnicza','School of shipbuilding',{'naval_defense':.10},tier=2),
    'navigation': _project('naval','Nawigacja oceaniczna','Ocean navigation',{'cargo':.10,'naval_attack':.05},tier=3),
    'colonial_admin': _project('colonial','Administracja kolonialna','Colonial administration',{'colony_cost':-.10}),
    'surveyors': _project('colonial','Służba miernicza','Survey service',{'colony_time':-.10}),
    'settler_medicine': _project('colonial','Medycyna osadnicza','Settler medicine',{'colony_time':-.10},tier=2),
    'colonial_bureau': _project('colonial','Urząd kolonialny','Colonial bureau',{'colony_cost':-.10},tier=3),
    'algae_economy': _project('economy','Algae: biokataliza','Algae: biocatalysis',{'farm_food':.25,'production':.15},tier=3,algae=4),
    'algae_land': _project('land','Algae: medycyna wojskowa','Algae: military medicine',{'land_attack':.20,'land_defense':.20},tier=3,algae=4),
    'algae_naval': _project('naval','Algae: żywe poszycie','Algae: living hulls',{'naval_attack':.20,'naval_defense':.20,'cargo':.25},tier=3,algae=4),
    'algae_colonial': _project('colonial','Algae: odporni osadnicy','Algae: resilient settlers',{'colony_cost':-.30,'colony_time':-.25},tier=3,algae=4),
}
for _category in CATEGORIES:
    PROJECTS['mastery_'+_category] = _project(
        _category, 'Dalsze badania: '+{'economy':'gospodarka','land':'wojska lądowe','naval':'marynarka','colonial':'kolonie'}[_category],
        'Further research: '+_category, {}, tier=3, repeat=True)

EFFECTS = {
    'farm_food':('Żywność z farm','Farm food'), 'spoilage':('Psucie zapasów','Food spoilage'),
    'taxes':('Dochody podatkowe','Tax income'), 'knowledge':('Wiedza z uniwersytetów','University knowledge'),
    'production':('Produkcja budynków poza wiedzą i algae','Building output excluding knowledge and algae'),
    'land_upkeep':('Utrzymanie armii w złocie','Army gold upkeep'),
    'naval_upkeep':('Utrzymanie floty w złocie','Fleet gold upkeep'),
    'land_attack':('Atak wojsk lądowych','Land attack'), 'land_defense':('Obrona wojsk lądowych','Land defense'),
    'naval_attack':('Atak okrętów','Ship attack'), 'naval_defense':('Obrona okrętów','Ship defense'),
    'cargo':('Ładowność floty','Fleet cargo'), 'colony_cost':('Złoto na awans kolonii','Colony advancement gold'),
    'colony_time':('Czas awansu kolonii','Colony advancement time'),
}


def name(code):
    return PROJECTS[code]['pl' if i18n.current_language() == 'pl' else 'en']


def effect_text(effects):
    return '; '.join(f'{tr(*EFFECTS[k])}: {v:+.0%}' for k,v in effects.items()) or tr('Wyższy poziom dziedziny.', 'Higher technology level.')


def discovery_story(nation_name,code):
    stories={
        'economy':('Rada państwa {n} przyjęła nowe metody opracowane przez uczonych i zarządców.', 'The council of {n} has adopted new methods developed by its scholars and administrators.'),
        'land':('Dowódcy państwa {n} wprowadzają odkrycie do szkolenia i organizacji wojsk.', 'The commanders of {n} are bringing the discovery into military training and organization.'),
        'naval':('Szkutnicy i admirałowie państwa {n} zakończyli próby nowego rozwiązania.', 'The shipwrights and admirals of {n} have completed trials of a new development.'),
        'colonial':('Z wypraw państwa {n} wrócili doświadczeni osadnicy; ich wiedza trafia do kolonii.', 'Experienced settlers have returned from the expeditions of {n}; their knowledge is reaching the colonies.'),
    }
    return tr(*stories[PROJECTS[code]['category']]).format(n=nation_name)


def seed_algae_sites(c):
    """Select once, independently of ownership. Resync never rerolls deposits."""
    c.execute("INSERT INTO economy_meta(key,value) VALUES('algae_sites_lock','1') ON CONFLICT(key) DO NOTHING")
    c.execute("SELECT value FROM economy_meta WHERE key='algae_sites_lock'"+(' FOR UPDATE' if db.USE_POSTGRES else ''))
    c.fetchone()
    c.execute("SELECT value FROM economy_meta WHERE key='algae_sites_v1'")
    if c.fetchone(): return
    c.execute("SELECT * FROM provinces WHERE active=1 AND terrain NOT IN ('water','ocean','sea') ORDER BY azgaar_cell_id")
    rows=c.fetchall()
    if not rows: return  # The first map import will select the sites.
    count=min(MAX_ALGAE_SITES,max(1,math.ceil(len(rows)/200)))
    def rank(p):
        existing='algae_farm' in json.loads(p['buildings_json'] or '[]')
        habitat=p['terrain']=='wetland' or p['biome'].lower()=='wetland'
        river=json.loads(p['base_resources_json'] or '{}').get('clay',0)>0
        return (not existing,not habitat,not river,hashlib.sha256(f"algae-v1:{p['azgaar_cell_id']}".encode()).hexdigest())
    ordered=sorted(rows,key=rank)
    chosen=[]
    for p in ordered:
        if len(chosen)>=count:break
        c.execute('SELECT neighbor_cell_id FROM province_neighbors WHERE cell_id=?',(p['azgaar_cell_id'],))
        neighbors={r['neighbor_cell_id'] for r in c.fetchall()}
        if any(x['azgaar_cell_id'] in neighbors for x in chosen):continue
        chosen.append(p)
    for p in ordered:
        if len(chosen)>=count:break
        if p not in chosen:chosen.append(p)
    for p in chosen:
        c.execute('INSERT INTO algae_sites(province_id) VALUES(?) ON CONFLICT(province_id) DO NOTHING',(p['id'],))
    c.execute("INSERT INTO economy_meta(key,value) VALUES('algae_sites_v1','1') ON CONFLICT(key) DO NOTHING")


def discoveries(c,nid):
    c.execute('SELECT * FROM research_discoveries WHERE nation_id=? ORDER BY completed_month DESC,code',(nid,))
    return {r['code']:r for r in c.fetchall()}


def bonuses(c,nid):
    result={}
    known=discoveries(c,nid)
    c.execute('SELECT category FROM algae_programs WHERE nation_id=? AND funded=1',(nid,))
    funded={r['category'] for r in c.fetchall()}
    for code in known:
        p=PROJECTS.get(code)
        if not p or (p['algae'] and p['category'] not in funded):continue
        for key,value in p['effects'].items():result[key]=result.get(key,0)+value
    return result


def available(n,known):
    tech=json.loads(n['tech_json'] or '{}')
    return [code for code,p in PROJECTS.items()
            if tech.get(p['category'],3)>=p['level']
            and (code not in known or p['repeat'])
            and (not p['repeat'] or tech.get(p['category'],3)<TECH_MAX)]


def recommendations(c,n):
    options=available(n,discoveries(c,n['id']))
    c.execute('SELECT hunger_months FROM economy_policy WHERE nation_id=?',(n['id'],));policy=c.fetchone()
    c.execute("SELECT COUNT(*) AS n FROM colonies WHERE nation_id=? AND status!='province'",(n['id'],));colonies=c.fetchone()['n']
    c.execute("SELECT b.type FROM military_units u LEFT JOIN blueprints b ON b.id=u.blueprint_id WHERE u.nation_id=?",(n['id'],));types=[r['type'] for r in c.fetchall()]
    c.execute('SELECT buildings_json FROM provinces WHERE owner_nation_id=? AND active=1',(n['id'],))
    buildings={key for row in c.fetchall() for key in json.loads(row['buildings_json'] or '[]')}
    resources=json.loads(n['resources_json'] or '{}')
    def rank(code):
        p=PROJECTS[code]
        need=(5 if code=='crop_rotation' and policy and policy['hunger_months'] else
              3 if code=='crop_rotation' and 'farm' in buildings else
              3 if code=='scientific_method' and 'university' in buildings else
              2 if p['category']=='colonial' and colonies else
              2 if p['category']=='land' and 'unit' in types else
              2 if p['category']=='naval' and 'ship' in types else
              1 if p['category']=='economy' else 0)
        affordable=p['algae']<=resources.get('algae',0)
        return (not affordable,p['repeat'],-need,p['knowledge'],code)
    return sorted(options,key=rank)[:3]


def start(nid,uid,code):
    from economy_services import spend
    from world_service import world_lock,owned,month_index
    if code not in PROJECTS:raise ValueError(tr('Nieznany projekt badawczy.','Unknown research project.'))
    with db.atomic() as c:
        world_lock(c);n=owned(c,nid,uid)
        c.execute('SELECT code FROM research_projects WHERE nation_id=?',(nid,))
        if c.fetchone():raise ValueError(tr('Masz już projekt. Ukończ go lub porzuć.','You already have a project. Complete or abandon it first.'))
        if code not in available(n,discoveries(c,nid)):
            raise ValueError(tr('Projekt jest ukończony albo brakuje wymaganego poziomu.','Project completed or technology requirement not met.'))
        p=PROJECTS[code]
        spend(c,n,{'algae':p['algae']} if p['algae'] else {})
        c.execute('INSERT INTO research_projects(nation_id,code,token,last_month) VALUES(?,?,?,?)',(nid,code,uuid.uuid4().hex,month_index(c)))


def manage(nid,uid,token,action):
    from world_service import world_lock,owned
    with db.atomic() as c:
        world_lock(c);owned(c,nid,uid)
        c.execute('SELECT token FROM research_projects WHERE nation_id=?',(nid,));row=c.fetchone()
        if not row or row['token']!=token:raise ValueError(tr('Projekt się zmienił. Otwórz badania ponownie.','The project changed. Reopen research.'))
        if action=='abandon':c.execute('DELETE FROM research_projects WHERE nation_id=?',(nid,))
        elif action in ('pause','resume'):
            c.execute('UPDATE research_projects SET paused=? WHERE nation_id=?',(int(action=='pause'),nid))
        else:raise ValueError('Unknown research action')


def set_program(nid,uid,category,enabled):
    from world_service import world_lock,owned
    with db.atomic() as c:
        world_lock(c);owned(c,nid,uid)
        if 'algae_'+category not in discoveries(c,nid):
            raise ValueError(tr('Najpierw ukończ odpowiednie badanie algae.','Complete the matching algae research first.'))
        c.execute('INSERT INTO algae_programs(nation_id,category,enabled) VALUES(?,?,?) '
                  'ON CONFLICT(nation_id,category) DO UPDATE SET enabled=excluded.enabled',(nid,category,int(enabled)))


def fund_programs(c,nid):
    """Pay once at month start. Purchased effects last until the next tick."""
    from economy_engine import lock_nation,read_json
    n=lock_nation(c,nid);res=read_json(n['resources_json']);known=discoveries(c,nid)
    c.execute('SELECT * FROM algae_programs WHERE nation_id=? ORDER BY category',(nid,))
    result=[]
    for row in sorted(c.fetchall(),key=lambda row:CATEGORIES.index(row['category'])):
        funded=bool(row['enabled'] and 'algae_'+row['category'] in known and res.get('algae',0)>=1)
        if funded:res['algae']-=1
        c.execute('UPDATE algae_programs SET funded=? WHERE nation_id=? AND category=?',(int(funded),nid,row['category']))
        result.append(dict(category=row['category'],enabled=bool(row['enabled']),funded=funded))
    c.execute('UPDATE nations SET resources_json=? WHERE id=?',(json.dumps(res),nid))
    return result


def tick_research(c,nid,month):
    """Called only inside the atomic monthly settlement, after university output."""
    from economy_engine import lock_nation,read_json
    from world_service import activity
    n=lock_nation(c,nid);res=read_json(n['resources_json'])
    res['universal_knowledge']=res.get('universal_knowledge',0)+BASE_KNOWLEDGE
    c.execute('SELECT * FROM research_projects WHERE nation_id=?',(nid,));row=c.fetchone()
    completed=None
    if row and not row['paused'] and row['last_month']<month:
        p=PROJECTS[row['code']]
        used=min(res['universal_knowledge'],max(0,p['knowledge']-row['knowledge']))
        res['universal_knowledge']-=used
        knowledge=row['knowledge']+used;months=row['months']+1
        if knowledge>=p['knowledge'] and months>=p['duration']:
            tech=read_json(n['tech_json']);old=tech.get(p['category'],3)
            tech[p['category']]=min(TECH_MAX,round(old+p['gain'],2))
            c.execute('UPDATE nations SET tech_json=? WHERE id=?',(json.dumps(tech),nid))
            c.execute('INSERT INTO research_discoveries(nation_id,code,completed_month) VALUES(?,?,?) '
                      'ON CONFLICT(nation_id,code) DO UPDATE SET completions=research_discoveries.completions+1,'
                      'completed_month=excluded.completed_month,notified=0',(nid,row['code'],month))
            c.execute('DELETE FROM research_projects WHERE nation_id=?',(nid,))
            completed=row['code']
            activity(c,'research',nid,f'research:{nid}:{month}',{'category':p['category'],'code':completed,'completed':True})
            text=discovery_story(n['name'],completed)+' '+tr('Ukończono badanie','Research completed')+f': {name(completed)}. {old:g} → {tech[p["category"]]:g}. '
            text+=effect_text(p['effects'])
            if p['algae']:text+=' '+tr('Premie wymagają włączenia programu i 1 algae miesięcznie.','Bonuses require enabling the program and 1 algae per month.')
            c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)',(nid,'research_private',text))
        else:
            c.execute('UPDATE research_projects SET knowledge=?,months=?,last_month=? WHERE nation_id=?',(knowledge,months,month,nid))
    c.execute('UPDATE nations SET resources_json=? WHERE id=?',(json.dumps(res),nid))
    return completed,res


def colony_requirements(nid,status):
    from cogs.colonialism import STAGE_CONFIG
    with db.cursor() as c:effect=bonuses(c,nid)
    cfg=STAGE_CONFIG[status]
    gold=math.ceil(cfg['advance_cost'].get('gold',0)*max(.5,1+effect.get('colony_cost',0)))
    months=math.ceil(cfg['advance_months']*max(.5,1+effect.get('colony_time',0)))
    return gold,months
