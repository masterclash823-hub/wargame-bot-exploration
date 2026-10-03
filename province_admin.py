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


def _nation_identity(c,nation):
    """Identity present in currently owned land, independent of capital selection."""
    c.execute('SELECT DISTINCT a.culture_id,a.religion_id FROM provinces p JOIN azgaar_cells a '
              'ON a.cell_id=p.azgaar_cell_id WHERE p.owner_nation_id=? AND p.active=1 '
              "AND p.terrain NOT IN ('water','sea','ocean')",(nation['id'],))
    rows=c.fetchall()
    return {key:{row[key] for row in rows if (row[key] or 0)>0}
            for key in ('culture_id','religion_id')}


def _price(target,identity):
    culture=bool(target and target['culture_id'] in identity['culture_id'])
    religion=bool(target and target['religion_id'] in identity['religion_id'])
    return dict(cost=500-100*int(culture)-100*int(religion),culture=culture,religion=religion)


def purchase_options(uid,nation_id=None):
    """Read the player's current border and prices without reserving or buying land."""
    from nation_access import find_nation,can_manage
    with db.cursor() as c:
        nation=find_nation(uid,c)
        if not nation or (nation_id is not None and nation['id']!=nation_id) or not can_manage(nation['id'],uid,c):
            raise ValueError(tr('Brak dostępu do państwa. Otwórz ponownie /province buy.',
                                'Nation access unavailable. Open /province buy again.'))
        identity=_nation_identity(c,nation)
        c.execute('SELECT p.*,a.culture_id,a.religion_id,culture.data_json AS culture_json,'
                  'religion.data_json AS religion_json FROM provinces p '
                  'LEFT JOIN azgaar_cells a ON a.cell_id=p.azgaar_cell_id '
                  "LEFT JOIN azgaar_entities culture ON culture.kind='cultures' AND culture.entity_id=a.culture_id "
                  "LEFT JOIN azgaar_entities religion ON religion.kind='religions' AND religion.entity_id=a.religion_id "
                  "WHERE p.active=1 AND p.owner_nation_id IS NULL AND p.terrain NOT IN ('water','sea','ocean') "
                  'AND EXISTS (SELECT 1 FROM province_neighbors edge JOIN provinces owned '
                  'ON owned.azgaar_cell_id=edge.neighbor_cell_id WHERE edge.cell_id=p.azgaar_cell_id '
                  'AND owned.owner_nation_id=? AND owned.active=1) ORDER BY p.azgaar_cell_id',(nation['id'],))
        rows=c.fetchall()
        for row in rows:row.update(_price(row,identity))
        return nation,rows


def buy(cell, uid, *, nation_id=None, expected_cost=None):
    """Purchase adjacent unclaimed land, charging the nation's treasury atomically."""
    from nation_access import find_nation
    from economy_engine import lock_nation
    from world_service import activity
    with db.atomic() as c:
        world_lock(c)
        nation=find_nation(uid,c)
        if not nation:
            raise ValueError(tr('Nie masz państwa.', 'You have no nation.'))
        if nation_id is not None and nation['id']!=nation_id:
            raise ValueError(tr('Zmieniło się twoje państwo. Otwórz ponownie /province buy.',
                                'Your nation changed. Open /province buy again.'))
        nation=lock_nation(c,nation['id'])
        # A co-op may manage the nation, but fallen nations cannot buy land.
        p=_province(c,cell)
        if p['owner_nation_id'] is not None:
            raise ValueError(tr('Ta prowincja ma już właściciela.', 'This province already has an owner.'))
        if p['terrain'] in ('water','sea','ocean'):
            raise ValueError(tr('Nie można kupić pola wodnego.', 'A water cell cannot be purchased.'))
        c.execute('SELECT 1 FROM province_neighbors edge JOIN provinces owned '
                  'ON owned.azgaar_cell_id=edge.neighbor_cell_id '
                  'WHERE edge.cell_id=? AND owned.owner_nation_id=? AND owned.active=1 LIMIT 1',
                  (cell,nation['id']))
        if not c.fetchone():
            raise ValueError(tr('Możesz kupić tylko prowincję sąsiadującą z aktywną prowincją swojego państwa. '
                                'Jeśli mapa nie ma danych sąsiedztwa, poproś GM o ponowny import mapy.',
                                'You can only buy a province adjacent to an active province of your nation. '
                                'If map adjacency data is missing, ask a GM to reimport the map.'))
        c.execute('SELECT culture_id,religion_id FROM azgaar_cells WHERE cell_id=?',(cell,))
        target=c.fetchone()
        quote=_price(target,_nation_identity(c,nation))
        cost=quote['cost']
        if expected_cost is not None and cost!=expected_cost:
            raise ValueError(tr('Cena zmieniła się. Odśwież podgląd przed potwierdzeniem zakupu.',
                                'The price changed. Refresh the preview before confirming the purchase.'))
        if nation['treasury']<cost:
            raise ValueError(tr(f'Potrzeba {cost} złota w skarbcu.',f'The treasury needs {cost} gold.'))
        c.execute('UPDATE nations SET treasury=treasury-? WHERE id=? AND treasury>=?',(cost,nation['id'],cost))
        if c.rowcount!=1:
            raise ValueError(tr('Za mało złota.', 'Not enough gold.'))
        c.execute('UPDATE provinces SET owner_nation_id=? WHERE id=? AND owner_nation_id IS NULL',
                  (nation['id'],p['id']))
        if c.rowcount!=1:
            raise ValueError(tr('Prowincja została już zajęta.', 'The province is already taken.'))
        totals(c,[nation['id']])
        c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,'system',?)",
                  (nation['id'],f'Purchased province #{cell} for {cost} gold.'))
        activity(c,'expansion',nation['id'],f'province-purchase:{p["id"]}',{'cell':cell})
        return dict(nation=nation['name'],cell=cell,**quote)


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
