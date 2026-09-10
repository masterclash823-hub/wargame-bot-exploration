"""Additive setup: preserve custom GM definitions and existing populations."""
import json
import db

NEW_EFFECTS={'farm':{'food':20},'pasture':{'food':8,'horses':1},
             'fishing_wharf':{'food':15},'plantation':{'food':6,'spices':3},
             'powder_mill':{'gunpowder':4,'coal':-2,'copper':-1},
             'silk_workshop':{'silk':2,'cloth':-2},'market':{},'port':{}}
NEW_DESCRIPTIONS={
    'market':'Adds 15% to province taxes and sells surplus luxuries; 150 workers.',
    'port':'Enables luxury exports through assigned cargo ships; 200 workers.',
    'powder_mill':'Produces 4 gunpowder using 2 coal and 1 copper per month; 250 workers.',
    'silk_workshop':'Produces 2 silk using 2 cloth per month; 250 workers.',
}


def seed_buildings(defaults):
    with db.atomic() as c:
        c.execute("SELECT value FROM economy_meta WHERE key='buildings_v2'")
        migrated=bool(c.fetchone())
        for b in defaults:
            effect=NEW_EFFECTS.get(b['key'],b['effect'])
            c.execute('SELECT * FROM building_defs WHERE key=?',(b['key'],));existing=c.fetchone()
            if existing is None:
                c.execute('INSERT INTO building_defs(key,name,tier,cost_json,effect_json,upkeep_json,requires_terrain,requires_tech,description) VALUES(?,?,?,?,?,?,?,?,?)',
                          (b['key'],b['name'],b['tier'],json.dumps(b['cost']),json.dumps(effect),json.dumps(b['upkeep']),b['terrain'],b['tech'],NEW_DESCRIPTIONS.get(b['key'],b['desc'])))
            elif not migrated and b['key'] in NEW_EFFECTS and json.loads(existing['effect_json'])==b['effect']:
                c.execute('UPDATE building_defs SET effect_json=? WHERE key=?',(json.dumps(effect),b['key']))
            if existing and not migrated and existing['description']==b['desc'] and b['key'] in NEW_DESCRIPTIONS:
                c.execute('UPDATE building_defs SET description=? WHERE key=?',(NEW_DESCRIPTIONS[b['key']],b['key']))
        c.execute("INSERT INTO building_defs(key,name,cost_json,effect_json,upkeep_json,description) VALUES('granary','Granary',?,?,?,?) ON CONFLICT(key) DO NOTHING",
                  ('{"gold":100,"wood":40}','{}','{"gold":1}','Reduces food spoilage; 50 workers.'))
        c.execute("INSERT INTO economy_meta(key,value) VALUES('buildings_v2','1') ON CONFLICT(key) DO NOTHING")
