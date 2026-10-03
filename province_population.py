"""Normalize unclaimed land without resetting a player's population."""
import db
import province_geography as geography

AVERAGE=2000
MINIMUM=500
MAXIMUM=4000
MIGRATION='unclaimed_population_2k_v1'


def distribution(populations):
    """Keep settlement differences, bounded to 500–4000, with an exact mean."""
    values=[max(MINIMUM,min(MAXIMUM,int(pop))) if pop and pop>0 else AVERAGE for pop in populations]
    remaining=AVERAGE*len(values)-sum(values)
    while remaining:
        direction=1 if remaining>0 else -1
        available=[i for i,value in enumerate(values) if value<MAXIMUM] if direction==1 else [
            i for i,value in enumerate(values) if value>MINIMUM]
        step=max(1,abs(remaining)//len(available))
        for index in available:
            capacity=MAXIMUM-values[index] if direction==1 else values[index]-MINIMUM
            change=min(step,capacity,abs(remaining))*direction
            values[index]+=change
            remaining-=change
            if not remaining:break
    return values


def normalize(c,cells=None):
    if cells is None:cells,_=geography.load(c)
    c.execute('SELECT * FROM provinces WHERE owner_nation_id IS NULL AND active=1 ORDER BY azgaar_cell_id')
    land=[];updates=[]
    for province in c.fetchall():
        if geography.is_water(province,cells):
            if province['population']!=0:updates.append((0,province['id']))
        else:land.append(province)
    values=distribution([p['population'] for p in land])
    updates.extend((value,p['id']) for p,value in zip(land,values) if p['population']!=value)
    c.executemany('UPDATE provinces SET population=? WHERE id=? AND owner_nation_id IS NULL AND active=1',updates)
    return dict(land=len(land),changed=len(updates),population=sum(values))


def migrate():
    from world_service import world_lock
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT value FROM economy_meta WHERE key=?',(MIGRATION,))
        if c.fetchone():return
        normalize(c)
        c.execute('INSERT INTO economy_meta(key,value) VALUES(?,?)',(MIGRATION,'1'))
