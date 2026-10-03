"""Purchase geography from the stored Azgaar map and current game ownership."""
import json
import math


def load(c):
    c.execute('SELECT data_json FROM azgaar_world WHERE id=1')
    row=c.fetchone()
    if not row:return {},{}
    data=json.loads(row['data_json'])
    pack=data.get('pack',data)
    from azgaar_format import cells
    return {cell['i']:cell for cell in cells(data)}, {
        burg['i']:burg for burg in pack.get('burgs',[])
        if isinstance(burg,dict) and burg.get('i') and not burg.get('removed')}


def point(cell):
    xy=cell.get('p')
    if (isinstance(xy,(list,tuple)) and len(xy)==2
            and all(type(value) in (int,float) and math.isfinite(value) for value in xy)):
        return tuple(xy)


def is_water(province,cells):
    terrain=str(province['terrain']).strip().casefold()
    biome=str(province['biome']).strip().casefold()
    height=cells.get(province['azgaar_cell_id'],{}).get('h')
    return (terrain in ('water','sea','ocean','lake','lakes') or biome=='marine'
            or (type(height) in (int,float) and height<20))


def reference(c,nation,owned,cells,burgs):
    located={p['azgaar_cell_id']:(p,point(cells.get(p['azgaar_cell_id'],{}))) for p in owned}
    located={cell:value for cell,value in located.items() if value[1] is not None}
    for cell,(province,xy) in located.items():
        if province['id']==nation['capital_province_id']:
            return dict(kind='capital',cell=cell,point=xy)
    # Imports can link a map state before a game capital has been selected.
    c.execute('SELECT data_json FROM azgaar_states WHERE nation_id=? ORDER BY state_id',(nation['id'],))
    for row in c.fetchall():
        state=json.loads(row['data_json'])
        if state.get('removed'):continue
        cell=burgs.get(state.get('capital'),{}).get('cell')
        if cell in located:return dict(kind='map_capital',cell=cell,point=located[cell][1])
    if located:
        xy=tuple(sum(value[1][axis] for value in located.values())/len(located) for axis in (0,1))
        return dict(kind='center',cell=None,point=xy)


def location(province,cells,origin):
    xy=point(cells.get(province['azgaar_cell_id'],{}))
    if xy is None:return None
    result=dict(point=xy,reference=origin,direction=None,distance=None)
    if origin:
        dx,dy=xy[0]-origin['point'][0],xy[1]-origin['point'][1]
        distance=math.hypot(dx,dy)
        # Azgaar screen coordinates grow downwards: north is negative y.
        bearing=math.degrees(math.atan2(dx,-dy))%360
        direction=('N','NE','E','SE','S','SW','W','NW')[int((bearing+22.5)//45)%8]
        result.update(direction=direction if distance>1e-9 else 'center',distance=distance)
    return result
