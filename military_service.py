"""Atomic recruitment, including the scarce algae elite and its support limit."""
import json

import db
import i18n
from economy_services import spend
from technology import tr,discoveries,name

REGULARS_PER_ELITE = 4


def validate_discovery(c,n,data,category):
    if json.loads(n['tech_json']).get(category,3)<data.get('requires_tech',0):
        raise ValueError(tr('Za niski poziom technologii tej dziedziny.', 'This field’s technology level is too low.'))
    code=data.get('requires_discovery')
    if code and code not in discoveries(c,n['id']):
        raise ValueError(tr('Najpierw ukończ badanie: ', 'Complete this research first: ')+name(code))


def force_counts(c,nid,kind):
    from cogs.military import HULLS,LAND_UNITS
    catalog=HULLS if kind=='ship' else LAND_UNITS
    c.execute('SELECT u.quantity,u.unit_type,b.hull,b.type FROM military_units u '
              'LEFT JOIN blueprints b ON b.id=u.blueprint_id WHERE u.nation_id=?',(nid,))
    regular=elite=0
    for row in c.fetchall():
        key=row['hull'] or row['unit_type']
        # Unknown/orphaned designs cannot act as cheap support for elites.
        if key not in catalog or (row['type'] and row['type']!=kind):continue
        if catalog[key].get('elite'):elite+=max(0,row['quantity'])
        else:regular+=max(0,row['quantity'])
    return regular,elite


def limit_error():
    return tr('Elita wymaga 4 zwykłych jednostek na każdą elitarną, osobno dla armii i floty. Uzupełnij zwykłe siły lub rozwiąż część elity.',
              'Each elite needs 4 ordinary units, counted separately for the army and navy. Recruit ordinary forces or disband some elites.')


def recruit(nid,uid,blueprint_id,quantity,cell=0):
    from cogs.military import HULLS,LAND_UNITS,MODULES
    from world_service import world_lock,owned
    if type(quantity) is not int or not 1<=quantity<=100000:
        raise ValueError(tr('Liczba jednostek musi wynosić od 1 do 100000.', 'Quantity must be between 1 and 100000.'))
    with db.atomic() as c:
        world_lock(c);n=owned(c,nid,uid)
        c.execute('SELECT * FROM blueprints WHERE id=? AND nation_id=?',(blueprint_id,nid));bp=c.fetchone()
        if not bp:raise ValueError(tr('Projekt nie istnieje albo nie należy do Ciebie.', 'Blueprint missing or not yours.'))
        is_ship=bp['type']=='ship';catalog=HULLS if is_ship else LAND_UNITS
        data=catalog.get(bp['hull'])
        if not data:raise ValueError(tr('Nieznany typ jednostki.', 'Unknown unit type.'))
        validate_discovery(c,n,data,'naval' if is_ship else 'land')
        if data.get('elite'):
            regular,elite=force_counts(c,nid,bp['type'])
            if (elite+quantity)*REGULARS_PER_ELITE>regular:raise ValueError(limit_error())
        p=None
        if cell:
            c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1 AND owner_nation_id=?',(cell,nid));p=c.fetchone()
            if not p:raise ValueError(tr('Wybierz własną prowincję albo pozostaw jednostki bez przydziału.', 'Choose your own province or leave the units unassigned.'))
        base=dict(data['cost'])
        if is_ship:
            modules=json.loads(bp['components_json'])
            if len(modules)>data['slots'] or any(m not in MODULES for m in modules):
                raise ValueError(tr('Nieprawidłowy projekt okrętu.', 'Invalid ship blueprint.'))
            for module in modules:
                for key,value in MODULES[module]['cost'].items():base[key]=base.get(key,0)+value
        cost={k:v*quantity for k,v in base.items()}
        if not is_ship:cost['cloth']=cost.get('cloth',0)+(quantity+4)//5
        spend(c,n,cost)
        unit_id=db.insert_returning_id('INSERT INTO military_units(nation_id,blueprint_id,unit_type,quantity,province_id) VALUES(?,?,?,?,?)',
                                      (nid,blueprint_id,bp['hull'],quantity,p['id'] if p else None))
        return dict(id=unit_id,blueprint=bp,province=p,cost=cost,upkeep=data['peace_upkeep'])


def disband(nid,uid,unit_id):
    from cogs.military import HULLS,LAND_UNITS
    from world_service import world_lock,owned
    with db.atomic() as c:
        world_lock(c);owned(c,nid,uid)
        c.execute('SELECT u.*,b.type,b.hull,b.name AS bname FROM military_units u LEFT JOIN blueprints b ON b.id=u.blueprint_id '
                  'WHERE u.id=? AND u.nation_id=?',(unit_id,nid));u=c.fetchone()
        if not u:raise ValueError(i18n.text('Unit not found or not yours.'))
        c.execute("SELECT forces_json FROM battle_plans WHERE nation_id=? AND status IN ('unmatched','matched')",(nid,))
        if any(any(int(f.get('unit_id',0))==unit_id for f in json.loads(p['forces_json'])) for p in c.fetchall()):
            raise ValueError(i18n.text('This unit is assigned to a pending battle plan.'))
        key=u['hull'] or u['unit_type'];kind=u['type'] or ('ship' if key in HULLS else 'unit')
        data=(HULLS if kind=='ship' else LAND_UNITS).get(key,{})
        if data and not data.get('elite'):
            regular,elite=force_counts(c,nid,kind)
            if elite and regular-u['quantity']<REGULARS_PER_ELITE*elite:raise ValueError(limit_error())
        c.execute('DELETE FROM military_units WHERE id=?',(unit_id,))
        return u


def delete_blueprint(nid,uid,bpid):
    from world_service import world_lock,owned
    with db.atomic() as c:
        world_lock(c);owned(c,nid,uid)
        c.execute('SELECT * FROM blueprints WHERE id=? AND nation_id=?',(bpid,nid));bp=c.fetchone()
        if not bp:raise ValueError(tr('Projekt nie istnieje albo nie należy do Ciebie.', 'Blueprint missing or not yours.'))
        c.execute('SELECT id FROM military_units WHERE blueprint_id=? LIMIT 1',(bpid,))
        if c.fetchone():raise ValueError(tr('Najpierw rozwiąż jednostki korzystające z tego projektu.', 'Disband the units using this blueprint first.'))
        c.execute('DELETE FROM blueprints WHERE id=?',(bpid,))
        return bp
