"""Atomic province edits shared by GM slash commands and the admin panel."""
import json
import db
from world_service import world_lock,tr


def totals(c,nids):
    for nid in set(nids)-{None}:
        c.execute('UPDATE nations SET population=(SELECT COALESCE(SUM(population),0) FROM provinces '
                  'WHERE owner_nation_id=? AND active=1) WHERE id=?',(nid,nid))
        c.execute('UPDATE nations SET capital_province_id=NULL WHERE id=? AND capital_province_id IS NOT NULL '
                  'AND NOT EXISTS (SELECT 1 FROM provinces p WHERE p.id=nations.capital_province_id AND p.owner_nation_id=nations.id AND p.active=1)',(nid,))


def _province(c,cell):
    c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(cell,))
    row=c.fetchone()
    if not row:raise ValueError(tr('Nie znaleziono aktywnej prowincji.', 'Active province not found.'))
    return row


def edit(cell,*,population=None,biome=None,coastal=None):
    from cogs.provinces import BIOME_RESOURCES,_terrain_label
    if population is not None and (type(population) is not int or not 0<=population<=1_000_000_000):
        raise ValueError(tr('Populacja: liczba całkowita od 0 do 1 000 000 000.', 'Population: whole number from 0 to 1,000,000,000.'))
    if biome is not None:
        biome=next((key for key in BIOME_RESOURCES if key.casefold()==biome.strip().casefold()),None)
        if biome is None:raise ValueError(tr('Wybierz biom z listy.', 'Choose a biome from the list.'))
    with db.atomic() as c:
        world_lock(c)
        p=_province(c,cell)
        if population is not None:
            c.execute('UPDATE provinces SET population=? WHERE id=?',(population,p['id']))
        if biome is not None:
            # Preserve geographic mineral/river additions and GM deposits; replace only biome yield.
            resources=json.loads(p['base_resources_json'])
            old=BIOME_RESOURCES.get(p['biome'],{});new=BIOME_RESOURCES[biome]
            for key in old.keys()|new.keys():
                resources[key]=max(0,resources.get(key,0)-old.get(key,0))+new.get(key,0)
                if not resources[key]:resources.pop(key)
            terrain=p['terrain'] if p['terrain'] in ('mountains','hills') else _terrain_label(20,biome)
            if biome=='Marine':terrain='water'
            c.execute('UPDATE provinces SET biome=?,terrain=?,base_resources_json=? WHERE id=?',
                      (biome,terrain,json.dumps(resources),p['id']))
        if coastal is not None:
            if type(coastal) is not bool:raise ValueError('Invalid coastline')
            if coastal and (biome=='Marine' or (biome is None and p['terrain'] in ('water','sea','ocean'))):
                raise ValueError(tr('Wybrzeże oznacza ląd graniczący z wodą.', 'Coastal means land bordering water.'))
        if biome=='Marine':coastal=False
        if coastal is not None:
            c.execute('INSERT INTO province_coasts(province_id,coastal) VALUES(?,?) '
                      'ON CONFLICT(province_id) DO UPDATE SET coastal=excluded.coastal',(p['id'],int(coastal)))
        totals(c,[p['owner_nation_id']])
        if p['owner_nation_id']:
            note=f"Province #{cell}: population={population}, biome={biome}, coastal={coastal}"
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,'gm',?)",(p['owner_nation_id'],note))
        return _province(c,cell)


def claim(nid,ids,normalize=False):
    from cogs.provinces import _parse_ids
    from economy_services import normalize_population
    from economy_engine import lock_nation
    cells=list(dict.fromkeys(_parse_ids(ids)))
    if len(cells)>10000:raise ValueError(tr('Maksymalnie 10000 prowincji naraz.', 'At most 10000 provinces at once.'))
    with db.atomic() as c:
        world_lock(c)
        if nid is not None:lock_nation(c,nid)
        changed=missing=0;owners={nid}
        for cell in cells:
            c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1',(cell,));p=c.fetchone()
            if not p:missing+=1;continue
            owners.add(p['owner_nation_id'])
            if p['owner_nation_id']!=nid:
                c.execute('DELETE FROM province_labor WHERE province_id=?',(p['id'],))
                c.execute('UPDATE colonies SET nation_id=? WHERE province_id=?',(nid,p['id'])) if nid else c.execute('DELETE FROM colonies WHERE province_id=?',(p['id'],))
            c.execute('UPDATE provinces SET owner_nation_id=? WHERE id=?',(nid,p['id']))
            changed+=1
        normalized=normalize_population(nid,True) if normalize and nid is not None and changed else []
        totals(c,owners)
        if nid is not None:
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,'gm',?)",(nid,f'Claimed cells: {ids}. Population normalized: {bool(normalized)}'))
        return changed,missing,len(normalized)
