"""Persist geographic evidence; an old port is not proof of a coastline."""
from world_service import tr

COASTAL_BUILDINGS={'port','fishing_wharf'}


def require_coast(c, province, key):
    if key not in COASTAL_BUILDINGS:return
    c.execute('SELECT coastal FROM province_coasts WHERE province_id=?',(province['id'],))
    row=c.fetchone()
    if row and row['coastal']:return
    if row is None:
        raise ValueError(tr('Brak danych o wybrzeżu. Poproś GM o ponowny import mapy przez /admin map_resync.',
                            'Coastline data is missing. Ask the GM to reimport the map with /admin map_resync.'))
    raise ValueError(tr('Port i przystań rybacka wymagają prowincji lądowej na wybrzeżu.',
                        'Ports and fishing wharves require a coastal land province.'))


def coast_cells(cells):
    """Azgaar land h>=20 borders water h<20; t=1 is its coastal-land marker.

    Validate haven against water heights (zero can be a real cell ID, and a
    missing/default zero must not make every province coastal).
    """
    if isinstance(cells,list):
        rows={int(c['i']):dict(c) for c in cells if isinstance(c,dict) and c.get('i') is not None}
    else:
        rows={}
        for j,cid in enumerate(cells.get('i',[])):
            rows[int(cid)]={k:v[j] for k,v in cells.items() if isinstance(v,list) and j<len(v)}
    # Match the importer's numeric-string support without assuming that absent
    # heights describe water or that an absent haven describes water cell zero.
    for c in rows.values():
        for key in ('h','t','haven'):
            if c.get(key) is not None:c[key]=int(c[key])
    water={cid for cid,c in rows.items() if c.get('h') is not None and c['h']<20}
    result={}
    for cid,c in rows.items():
        if c.get('h') is None:continue  # Unknown geography must stay unknown.
        neighbors=[int(n) for n in c.get('c') or []]
        # t=1 also supports land-only exports. haven=0 alone is ambiguous.
        coastal=c.get('t')==1 or any(n in water for n in neighbors)
        if 'c' not in c and 't' not in c and (c.get('haven') or 0)>0:
            coastal=c['haven'] in water
        result[cid]=bool(c['h']>=20 and coastal)
    return result
