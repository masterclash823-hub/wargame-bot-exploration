"""Company ownership, investment permissions and narrated improvements."""
import json
import math
import uuid
import db
from economy_engine import read_json, lock_nation, LEVEL_WORK, WORKERS
from world_service import world_lock, owned, month_index, tr
from economy_services import spend

MIN_TECH = 4
BUILDINGS = ('farm', 'pasture', 'fishing_wharf', 'plantation', 'lumber_camp',
             'mine', 'copper_mine', 'clay_pit', 'tar_works', 'powder_mill',
             'cannon_foundry', 'textile_mill', 'silk_workshop', 'algae_farm')
KINDS = ('production', 'inputs', 'workers', 'construction', 'maintenance')


def unlocked(nation):
    return bool(nation) and read_json(nation['tech_json']).get('economy', 3) >= MIN_TECH


def number(value, minimum=0, maximum=100000000):
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(tr('Nieprawidłowa wartość.', 'Invalid value.'))
    return value


def state(c, nid):
    c.execute('SELECT state_json FROM companies WHERE nation_id=?', (nid,))
    row = c.fetchone()
    return read_json(row['state_json']) if row else None


def save(c, nid, value):
    value['version'] = value.get('version', 0) + 1
    c.execute('INSERT INTO companies(nation_id,state_json) VALUES(?,?) '
              'ON CONFLICT(nation_id) DO UPDATE SET state_json=excluded.state_json',
              (nid, json.dumps(value)))


def actor(c, nid, uid, version=None):
    world_lock(c)
    nation = owned(c, nid, uid)
    company = state(c, nid)
    if not company:
        raise ValueError(tr('Najpierw załóż kompanię.', 'Create a company first.'))
    if version is not None and version != company['version']:
        raise ValueError(tr('Dane się zmieniły. Otwórz kompanię ponownie.',
                            'The data changed. Reopen the company.'))
    return nation, company


def require_tech(nation):
    if not unlocked(nation):
        raise ValueError(tr('Kompania wymaga gospodarki na poziomie 4.',
                            'A company requires economy level 4.'))


def create(nid, uid, name, description, types):
    types = list(dict.fromkeys(types))
    if not 1 <= len(types) <= 3 or any(t not in BUILDINGS for t in types):
        raise ValueError(tr('Wybierz od 1 do 3 typów budynków produkcyjnych.',
                            'Choose 1 to 3 production building types.'))
    if not 1 <= len(name.strip()) <= 80 or not 1 <= len(description.strip()) <= 1500:
        raise ValueError(tr('Nazwa: 1–80 znaków; opis: 1–1500.', 'Name: 1–80 characters; description: 1–1500.'))
    with db.atomic() as c:
        world_lock(c)
        nation = owned(c, nid, uid)
        require_tech(nation)
        if state(c, nid):
            raise ValueError(tr('Państwo ma już kompanię.', 'This nation already has a company.'))
        save(c, nid, dict(name=name.strip(), description=description.strip(), types=types,
                         cells=[], budget=0, reserves={}, mode='off', resource='food',
                         paused=False, improvements=[], report={}, last_investment=-1))


def configure(nid, uid, version, cells, budget, reserves, mode, resource='food'):
    if mode not in ('off', 'supply', 'expand', 'upgrade'):
        raise ValueError(tr('Nieprawidłowy tryb.', 'Invalid mode.'))
    cells = list(dict.fromkeys(int(cell) for cell in cells))
    if len(cells) > 200:
        raise ValueError(tr('Maksymalnie 200 prowincji.', 'At most 200 provinces.'))
    reserves = {str(k): number(v) for k, v in reserves.items()}
    budget = number(budget)
    with db.atomic() as c:
        n, s = actor(c, nid, uid, version)
        require_tech(n)
        for cell in cells:
            c.execute('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=? AND active=1', (cell,))
            p = c.fetchone()
            if not p or p['owner_nation_id'] != nid:
                raise ValueError(tr('Wybierz własne aktywne prowincje. Zagraniczne dodaje koncesja.',
                                    'Choose your active provinces. Concessions add foreign provinces.'))
        s.update(cells=cells, budget=budget, reserves=reserves, mode=mode, resource=resource)
        save(c, nid, s)


def pause(nid, uid, version):
    with db.atomic() as c:
        n, s = actor(c, nid, uid, version)
        s['paused'] = not s['paused']
        save(c, nid, s)


def bonus(nation, s, key):
    tech = read_json(nation['tech_json']).get('economy', 3)
    if tech < 4 or s['paused']:
        return {k: 0 for k in KINDS}
    tier = 0 if tech < 6 else 1 if tech < 8 else 2
    caps = dict(production=(.15,.25,.35)[tier], construction=(.15,.20,.25)[tier],
                inputs=(.10,.15,.20)[tier], workers=(.10,.15,.20)[tier],
                maintenance=(.15,.20,.25)[tier])
    result = dict(production=.05, construction=.10, inputs=0., workers=0., maintenance=0.)
    for project in s['improvements']:
        if project['status'] == 'complete' and project['building'] == key:
            result[project['kind']] += project['amount']
    result = {k: min(caps[k], value) for k, value in result.items()}
    if key == 'algae_farm':
        for k in ('production', 'inputs', 'workers'):
            result[k] = 0
    return result


def pay(c, nation, company, cost):
    stock = dict(read_json(nation['resources_json']), gold=nation['treasury'])
    for key, value in cost.items():
        number(value)
        if stock.get(key, 0) - value < company['reserves'].get(key, 0) - .000001:
            raise ValueError(tr('Brak środków ponad ustalone rezerwy: ', 'Insufficient funds above reserves: ') + key)
    spend(c, nation, cost)


def grants(c, nid):
    c.execute('SELECT * FROM company_concessions WHERE company_nation_id=? OR host_nation_id=? ORDER BY id',
              (nid, nid))
    return [dict(r, terms=read_json(r['terms_json'])) for r in c.fetchall()]


def valid_grant(c, grant, target=None):
    if grant['status'] != 'active':
        return False
    terms = read_json(grant['terms_json'])
    target = month_index(c) if target is None else target
    if target > terms['expires']:
        return False
    from treaty_service import relation
    if relation(c, grant['company_nation_id'], grant['host_nation_id']) == 'war':
        return False
    for key, owner in (('company_nation_id', 'company_owner'), ('host_nation_id', 'host_owner')):
        c.execute('SELECT owner_id FROM nations WHERE id=?', (grant[key],))
        row = c.fetchone()
        if not row or row['owner_id'] != terms[owner]:
            return False
    return True


def propose_concession(nid, uid, version, host, cells, types, share, months, fee):
    share, fee = number(share, 1, 99), number(fee)
    months = int(number(months, 1, 120))
    cells, types = list(dict.fromkeys(map(int, cells))), list(dict.fromkeys(types))
    if not cells or len(cells) > 200 or not types:
        raise ValueError(tr('Wskaż prowincje i typy budynków.', 'Choose provinces and building types.'))
    with db.atomic() as c:
        n, s = actor(c, nid, uid, version)
        require_tech(n)
        if any(key not in s['types'] for key in types):
            raise ValueError(tr('Koncesja musi dotyczyć specjalizacji firmy.', 'Use the company specialties.'))
        h = lock_nation(c, int(host))
        if h['id'] == nid:
            raise ValueError(tr('Własne prowincje dodaj w budżecie.', 'Add domestic provinces in the budget settings.'))
        for cell in cells:
            c.execute('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=? AND active=1', (cell,))
            p = c.fetchone()
            if not p or p['owner_nation_id'] != h['id']:
                raise ValueError(tr('Prowincja nie należy do gospodarza.', 'The host does not own this province.'))
        terms = dict(cells=cells, types=types, share=share/100, months=months, fee=fee,
                     company_owner=n['owner_id'], host_owner=h['owner_id'], created=month_index(c))
        identifier = uuid.uuid4().hex
        c.execute('INSERT INTO company_concessions(id,company_nation_id,host_nation_id,status,terms_json) '
                  'VALUES(?,?,?,?,?)', (identifier, nid, h['id'], 'proposed', json.dumps(terms)))
        return identifier


def respond_concession(identifier, uid, action):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM company_concessions WHERE id=?', (identifier,))
        grant = c.fetchone()
        if not grant:
            raise ValueError(tr('Nie znaleziono koncesji.', 'Concession not found.'))
        terms = read_json(grant['terms_json'])
        a, b = lock_nation(c, grant['company_nation_id']), lock_nation(c, grant['host_nation_id'])
        me = next((n for n in (a,b) if n['owner_id'] == str(uid)), None)
        if me is None:
            raise ValueError(tr('To nie jest twoja umowa.', 'This is not your agreement.'))
        if action == 'accept':
            if me['id'] != b['id'] or grant['status'] != 'proposed':
                raise ValueError(tr('Tylko odbiorca może przyjąć oczekującą ofertę.', 'Only the recipient may accept a pending offer.'))
            if a['owner_id'] != terms['company_owner'] or b['owner_id'] != terms['host_owner']:
                raise ValueError(tr('Zmienił się właściciel państwa. Potrzebna jest nowa oferta.',
                                    'Nation ownership changed. A new offer is required.'))
            require_tech(a)
            from treaty_service import relation
            if relation(c,a['id'],b['id']) == 'war':
                raise ValueError(tr('Najpierw zakończcie wojnę.', 'End the war first.'))
            for cell in terms['cells']:
                c.execute('SELECT owner_nation_id FROM provinces WHERE azgaar_cell_id=? AND active=1', (cell,))
                p = c.fetchone()
                if not p or p['owner_nation_id'] != b['id']:
                    raise ValueError(tr('Zmienił się właściciel prowincji.', 'Province ownership changed.'))
            terms['expires'] = month_index(c) + terms['months']
            status = 'active'
        elif action == 'end' and grant['status'] in ('active', 'proposed'):
            other = b if me['id'] == a['id'] else a
            current = (grant['status']=='active' and month_index(c)<=terms.get('expires',-1)
                       and a['owner_id']==terms['company_owner'] and b['owner_id']==terms['host_owner'])
            fee = terms['fee'] if current else 0
            if fee:
                spend(c, me, {'gold':fee})
                c.execute('UPDATE nations SET treasury=treasury+? WHERE id=?', (fee,other['id']))
            status = 'ended'
            c.execute('DELETE FROM company_plants WHERE concession_id=?', (identifier,))
        else:
            raise ValueError(tr('Ta oferta została już rozpatrzona.', 'This offer has already been handled.'))
        c.execute('UPDATE company_concessions SET status=?,terms_json=? WHERE id=?',
                  (status,json.dumps(terms),identifier))


def permission(c, nid, s, province, key, target=None):
    if province['owner_nation_id'] == nid and province['azgaar_cell_id'] in s['cells']:
        return None
    for grant in grants(c,nid):
        terms = grant['terms']
        if (grant['company_nation_id'] == nid and grant['host_nation_id'] == province['owner_nation_id']
                and province['azgaar_cell_id'] in terms['cells'] and key in terms['types']
                and valid_grant(c,grant,target)):
            return grant['id']
    raise ValueError(tr('Brak zgody na tę inwestycję.', 'This investment is not authorized.'))


def invest(c, nid, s, cell, key, target, automatic=False):
    from cogs.economy import _terrain_ok, _tech_ok
    from economy_engine import project, snapshot
    n = lock_nation(c,nid)
    require_tech(n)
    if s['paused'] or key not in s['types'] or s['last_investment'] == target:
        raise ValueError(tr('Firma jest wstrzymana albo wykorzystała inwestycję w tym miesiącu.',
                            'The company is paused or has used this month’s investment.'))
    c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1', (int(cell),))
    p = c.fetchone()
    if not p:
        raise ValueError(tr('Nie znaleziono prowincji.', 'Province not found.'))
    grant_id = permission(c,nid,s,p,key,target)
    c.execute('SELECT * FROM company_plants WHERE province_id=? AND building_key=?',(p['id'],key))
    plant = c.fetchone()
    if plant and plant['company_nation_id'] != nid:
        raise ValueError(tr('Zakład prowadzi inna firma.', 'Another company manages this building.'))
    buildings = read_json(p['buildings_json'],[])
    c.execute('SELECT levels_json FROM province_development WHERE province_id=?',(p['id'],))
    row = c.fetchone()
    levels = read_json(row['levels_json']) if row else {}
    old = int(levels.get(key,1)) if key in buildings else 0
    if old >= 3:
        raise ValueError(tr('Budynek ma już poziom 3.', 'The building is already level 3.'))
    if automatic and ((s['mode']=='upgrade' and not old) or (s['mode']=='expand' and old)):
        raise ValueError(tr('Inwestycja nie pasuje do trybu.', 'The investment does not match the selected mode.'))
    c.execute('SELECT * FROM building_defs WHERE key=?',(key,))
    definition = c.fetchone()
    if not definition:
        raise ValueError(tr('Nie znaleziono typu budynku.', 'Building type not found.'))
    host = lock_nation(c,p['owner_nation_id'])
    required = max(6 if old else 3,definition['requires_tech']) if key=='algae_farm' else definition['requires_tech']
    if not _tech_ok(n,required,key) or not _tech_ok(host,required,key):
        raise ValueError(tr('Firma lub gospodarz nie spełnia wymagań technologii.', 'Company or host technology is insufficient.'))
    if key=='algae_farm':
        c.execute('SELECT province_id FROM algae_sites WHERE province_id=?',(p['id'],))
        if not c.fetchone():
            raise ValueError(tr('Farma algae wymaga złoża.', 'An algae farm requires a deposit.'))
    elif not _terrain_ok(p['terrain'],definition['requires_terrain']):
        raise ValueError(tr('Nieodpowiedni teren.', 'Unsuitable terrain.'))
    extra = WORKERS[key] * (LEVEL_WORK[old+1] - (LEVEL_WORK[old] if old else 0))
    used = sum(WORKERS.get(k,200)*LEVEL_WORK[min(3,max(1,int(levels.get(k,1))))] for k in set(buildings))
    if p['population']*.4-used < extra:
        raise ValueError(tr('Za mało wolnych pracowników.', 'Not enough available workers.'))
    discount = bonus(n,s,key)['construction']
    cost = {k:v*{0:1,1:1.5,2:2}[old]*(1-discount) for k,v in read_json(definition['cost_json']).items()}
    if cost.get('gold',0) > s['budget']:
        raise ValueError(tr('Inwestycja przekracza budżet.', 'The investment exceeds the budget.'))
    # Validate all conditions before writing: nested atomic() does not create savepoints.
    stock = dict(read_json(n['resources_json']),gold=n['treasury'])
    for resource,amount in cost.items():
        number(amount)
        if stock.get(resource,0)-amount < s['reserves'].get(resource,0):
            raise ValueError(tr('Brak środków ponad rezerwy: ', 'Insufficient funds above reserves: ')+resource)
    data = list(snapshot(c,host['id']))
    before = project(*data)
    changed = [dict(prov) for prov in data[1]]
    for prov in changed:
        if prov['id']==p['id']:
            projected_levels = dict(levels,**{key:old+1})
            prov['buildings_json'] = json.dumps(list(dict.fromkeys(buildings+[key])))
            prov['levels_json'] = json.dumps(projected_levels)
    data[1] = changed
    after = project(*data)
    if automatic and (after['food_shortage'] > before['food_shortage'] or after['balance'] < 0):
        raise ValueError(tr('Prognoza nie pozwala na bezpieczne utrzymanie inwestycji.',
                            'The forecast cannot sustain this investment.'))
    pay(c,n,s,cost)
    levels[key] = old+1
    if not old: buildings.append(key)
    c.execute('UPDATE provinces SET buildings_json=? WHERE id=?',(json.dumps(buildings),p['id']))
    c.execute('INSERT INTO province_development(province_id,levels_json) VALUES(?,?) '
              'ON CONFLICT(province_id) DO UPDATE SET levels_json=excluded.levels_json',(p['id'],json.dumps(levels)))
    c.execute('INSERT INTO company_plants(province_id,building_key,company_nation_id,host_nation_id,concession_id) '
              'VALUES(?,?,?,?,?) ON CONFLICT(province_id,building_key) DO UPDATE SET concession_id=excluded.concession_id,host_nation_id=excluded.host_nation_id',
              (p['id'],key,nid,p['owner_nation_id'],grant_id))
    s['last_investment'] = target
    s['report']['investment'] = dict(cell=cell,building=key,level=old+1,cost=cost)
    save(c,nid,s)
    from world_service import activity
    activity(c,'building',host['id'],f"building:{p['id']}:{key}:{old+1}",
             {'building':key,'level':old+1,'cell':cell})
    return cost


def manual_invest(nid,uid,version,cell,key):
    with db.atomic() as c:
        n,s=actor(c,nid,uid,version)
        return invest(c,nid,s,cell,key,month_index(c))


def improvement(nid,uid,version,key,kind,description):
    text=' '.join(description.split())
    if not 20<=len(text)<=3000 or kind not in KINDS:
        raise ValueError(tr('Opisz usprawnienie w 20–3000 znakach.', 'Describe the improvement in 20–3000 characters.'))
    with db.atomic() as c:
        n,s=actor(c,nid,uid,version)
        require_tech(n)
        if key not in s['types'] or (key=='algae_farm' and kind not in ('construction','maintenance')):
            raise ValueError(tr('To usprawnienie nie pasuje do specjalizacji.', 'This improvement does not fit the specialty.'))
        if any(x['description'].casefold()==text.casefold() for x in s['improvements']):
            raise ValueError(tr('Ten pomysł został już zapisany.', 'This idea is already recorded.'))
        if any(x['status'] in ('proposed','approved','running') for x in s['improvements']):
            raise ValueError(tr('Najpierw zakończ lub odrzuć bieżący projekt.', 'Finish or reject the current project first.'))
        s['improvements'].append(dict(id=uuid.uuid4().hex,building=key,kind=kind,description=text,
                                      status='proposed',amount=.05,cost={'gold':100},months=2))
        save(c,nid,s)


def review(nid,identifier,approved,gm_id):
    """Called only by the GM-checked Discord review callback."""
    with db.atomic() as c:
        world_lock(c)
        s=state(c,nid)
        p=next((x for x in (s or {}).get('improvements',[]) if x['id']==identifier),None)
        if not p or p['status']!='proposed':
            raise ValueError(tr('Projekt nie czeka na ocenę.', 'The project is not awaiting review.'))
        p.update(status='approved' if approved else 'rejected',gm_id=str(gm_id))
        save(c,nid,s)


def start_improvement(nid,uid,version,identifier,accept):
    with db.atomic() as c:
        n,s=actor(c,nid,uid,version)
        require_tech(n)
        p=next((x for x in s['improvements'] if x['id']==identifier),None)
        if not p or p['status'] not in ('proposed','approved'):
            raise ValueError(tr('Projekt nie jest dostępny.', 'The project is not available.'))
        if accept:
            if p['status']!='approved':
                raise ValueError(tr('Potrzebna jest zgoda GM.', 'GM approval is required.'))
            pay(c,n,s,p['cost'])
            p.update(status='running',started=month_index(c))
        else:
            p['status']='cancelled'
        save(c,nid,s)


def assign(nid,uid,version,cell,key):
    with db.atomic() as c:
        n,s=actor(c,nid,uid,version)
        require_tech(n)
        if key not in s['types']:
            raise ValueError(tr('To nie jest specjalizacja firmy.', 'This is not a company specialty.'))
        c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1',(int(cell),))
        p=c.fetchone()
        if not p or key not in read_json(p['buildings_json'],[]):
            raise ValueError(tr('W prowincji nie ma takiego budynku.', 'The province has no such building.'))
        grant=permission(c,nid,s,p,key)
        c.execute('SELECT company_nation_id FROM company_plants WHERE province_id=? AND building_key=?',(p['id'],key))
        existing=c.fetchone()
        if existing:
            raise ValueError(tr('Budynek jest już przypisany do firmy.', 'The building already belongs to a company portfolio.'))
        c.execute('INSERT INTO company_plants(province_id,building_key,company_nation_id,host_nation_id,concession_id) '
                  'VALUES(?,?,?,?,?)',(p['id'],key,nid,p['owner_nation_id'],grant))
        save(c,nid,s)
