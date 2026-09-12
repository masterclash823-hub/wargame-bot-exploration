"""Optional per-building worker reservations; all other staffing stays automatic."""
import json
import math

import db
from economy_engine import WORKERS,LEVEL_WORK,read_json
from technology import tr


def set_assignment(nid,uid,cell,building=None,workers=None):
    """None/None resets the province; workers=None resets just one building."""
    from world_service import world_lock,owned
    if workers is not None and (type(workers) is not int or workers<0):
        raise ValueError(tr('Podaj nieujemną, całkowitą liczbę pracowników.', 'Enter a non-negative whole number of workers.'))
    with db.atomic() as c:
        world_lock(c);owned(c,nid,uid)
        c.execute('SELECT p.*,d.levels_json FROM provinces p LEFT JOIN province_development d ON d.province_id=p.id '
                  'WHERE p.azgaar_cell_id=? AND p.owner_nation_id=? AND p.active=1',(cell,nid))
        p=c.fetchone()
        if not p:raise ValueError(tr('Prowincja nie istnieje albo nie należy do Ciebie.', 'Province missing or not yours.'))
        c.execute('SELECT allocations_json FROM province_labor WHERE province_id=? AND nation_id=?',(p['id'],nid));row=c.fetchone()
        buildings=read_json(p['buildings_json'],[]);levels=read_json(p['levels_json'])
        values={k:v for k,v in read_json(row['allocations_json'] if row else None).items() if k in buildings}
        if building is None:values={}
        else:
            if building not in buildings:raise ValueError(tr('W tej prowincji nie ma tego budynku.', 'This building is not in the province.'))
            need=WORKERS.get(building,200)*LEVEL_WORK[min(3,max(1,int(levels.get(building,1))))]
            if workers is None:values.pop(building,None)
            else:
                if workers>need:raise ValueError(tr(f'Ten budynek potrzebuje najwyżej {need:g} pracowników.', f'This building needs at most {need:g} workers.'))
                values[building]=workers
                if sum(values.values())>math.floor(max(0,p['population'])*.4):
                    raise ValueError(tr('Brakuje ludzi. Pracować może 40% mieszkańców prowincji. Zmniejsz inne przydziały.',
                                        'Not enough workers. 40% of the province population can work. Reduce other assignments.'))
        c.execute('INSERT INTO province_labor(province_id,nation_id,allocations_json) VALUES(?,?,?) '
                  'ON CONFLICT(province_id) DO UPDATE SET nation_id=excluded.nation_id,allocations_json=excluded.allocations_json',
                  (p['id'],nid,json.dumps(values)))
        return values
