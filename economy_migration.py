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
        c.execute("SELECT value FROM economy_meta WHERE key='algae_buildings_v1'")
        if not c.fetchone():
            c.execute("SELECT * FROM building_defs WHERE key='algae_farm'");old=c.fetchone()
            if old:
                if json.loads(old['effect_json'])=={'algae':1}:
                    c.execute("UPDATE building_defs SET effect_json=? WHERE key='algae_farm'",('{"algae":0.5}',))
                if old['description']=='Rare Algae. Requires tech 6.':
                    c.execute("UPDATE building_defs SET description=? WHERE key='algae_farm'",('Rare deposit only: /algae locations. Economy 6, 250 workers; 0.5 algae per month.',))
                if old['requires_terrain']=='wetland':
                    c.execute("UPDATE building_defs SET requires_terrain='' WHERE key='algae_farm'")
            c.execute("INSERT INTO economy_meta(key,value) VALUES('algae_buildings_v1','1') ON CONFLICT(key) DO NOTHING")
        c.execute("SELECT value FROM economy_meta WHERE key='algae_automatic_v2'")
        if not c.fetchone():
            c.execute("SELECT * FROM building_defs WHERE key='algae_farm'");old=c.fetchone()
            if old:
                if old['requires_tech']==6:
                    c.execute("UPDATE building_defs SET requires_tech=3 WHERE key='algae_farm'")
                if old['description'] in ('Rare Algae. Requires tech 6.', 'Rare deposit only: /algae locations. Economy 6, 250 workers; 0.5 algae per month.'):
                    desc=next(b['desc'] for b in defaults if b['key']=='algae_farm')
                    c.execute("UPDATE building_defs SET description=? WHERE key='algae_farm'",(desc,))
            c.execute("INSERT INTO economy_meta(key,value) VALUES('algae_automatic_v2','1') ON CONFLICT(key) DO NOTHING")
