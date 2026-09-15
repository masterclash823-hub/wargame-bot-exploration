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
IMPROVEMENT_TEXT_LIMIT = 4000


def project_effects(project):
    """Signed changes: + increases output/cost, - decreases it; read old saves too."""
    if 'effects' in project:
        return project['effects']
    if project.get('kind') in KINDS:
        kind = project['kind']
        return {kind: project.get('amount', .05) * (100 if kind == 'production' else -100)}
    return {}


def validate_improvement_effects(key, effects):
    allowed = ('construction', 'maintenance') if key == 'algae_farm' else KINDS
    if not isinstance(effects, dict) or any(k not in allowed for k in effects):
        raise ValueError(tr('Algae dopuszcza tylko zmianę kosztu budowy i utrzymania.',
                            'Algae permits construction and upkeep cost changes only.'))
    result = {k: number(v, -50, 50) for k, v in effects.items()}
    result = {k: v for k, v in result.items() if v}
    if not result:
        raise ValueError(tr('Podaj co najmniej jeden skutek różny od zera lub odrzuć projekt.',
                            'Enter at least one non-zero effect or reject the project.'))
    return result


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
    if not row:
        return None
    value = read_json(row['state_json'])
    if value.get('settings_version', 1) < 2:
        # Preserve the amount and whether automation was enabled, not old filters.
        limit = value.pop('budget', 0)
        last = value.pop('last_investment', -1)
        investment = value.get('report', {}).get('investment', {})
        value.update(settings_version=2, monthly_budget=limit, budget_month=last,
                     spent=investment.get('cost', {}).get('gold', limit) if last >= 0 else 0,
                     mode='off' if value.get('mode') == 'off' else 'auto')
        for key in ('cells', 'reserves', 'resource'):
            value.pop(key, None)
        if investment:
            value['report'].setdefault('investments', [investment])
    return value


def remaining_budget(s, target):
    used = s.get('spent', 0) if s.get('budget_month') == target else 0
    return max(0, s['monthly_budget'] - used)


def begin_month(s, target):
    if s.get('budget_month') != target:
        s.update(budget_month=target, spent=0)
        s['report'] = dict(month=target, waiting='', received={}, costs={}, investments=[])
    for key, default in (('investments', []), ('received', {}), ('costs', {}), ('waiting', '')):
        s.setdefault('report', {}).setdefault(key, default)


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
                         settings_version=2, monthly_budget=0, budget_month=-1, spent=0, mode='off',
                         paused=False, improvements=[], report={}))


def configure(nid, uid, version, budget, mode):
    if mode not in ('off', 'auto'):
        raise ValueError(tr('Nieprawidłowy tryb.', 'Invalid mode.'))
    budget = number(budget)
    with db.atomic() as c:
        n, s = actor(c, nid, uid, version)
        require_tech(n)
        begin_month(s, month_index(c))
        # Editing the amount or toggling modes never replenishes this month's funds.
        s.update(monthly_budget=budget, mode=mode)
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
    penalties = {k: 0. for k in KINDS}
    for project in s['improvements']:
        if project['status'] == 'complete' and project['building'] == key:
            for kind, percent in project_effects(project).items():
                delta = percent / 100 * (1 if kind == 'production' else -1)
                if delta < 0:
                    penalties[kind] += delta
                else:
                    result[kind] += delta
    # Apply penalties after the positive technology cap so excess bonuses cannot
    # silently cancel an adverse GM decision. Multipliers remain above zero.
    result = {k: max(-.5, min(caps[k], value) + penalties[k]) for k, value in result.items()}
    if key == 'algae_farm':
        for k in ('production', 'inputs', 'workers'):
            result[k] = 0
    return result


def pay(c, nation, company, cost):
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
            raise ValueError(tr('Własne prowincje są dostępne bez koncesji.', 'Domestic provinces are available without a concession.'))
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
    if province['owner_nation_id'] == nid:
        return None
    for grant in grants(c,nid):
        terms = grant['terms']
        if (grant['company_nation_id'] == nid and grant['host_nation_id'] == province['owner_nation_id']
                and province['azgaar_cell_id'] in terms['cells'] and key in terms['types']
                and valid_grant(c,grant,target)):
            return grant['id']
    raise ValueError(tr('Brak zgody na tę inwestycję.', 'This investment is not authorized.'))


def investment_context(c, nid, s):
    """One snapshot per planning round, including this company's domestic bonuses."""
    from economy_engine import snapshot, project
    data = list(snapshot(c, nid))
    c.execute('SELECT * FROM company_plants WHERE host_nation_id=?', (nid,))
    plants = {(r['province_id'], r['building_key']): r for r in c.fetchall()}
    for p in data[1]:
        for key in read_json(p['buildings_json'], []):
            plant = plants.get((p['id'], key))
            if plant and plant['company_nation_id'] == nid and not plant['concession_id']:
                p['company_plants'][key] = dict(bonus=bonus(data[0], s, key), foreign=False)
    return dict(data=data, before=project(*data), plants=plants,
                provinces={p['azgaar_cell_id']: p for p in data[1]})


def investment_quote(c, nid, s, cell, key, target, context=None):
    from cogs.economy import _terrain_ok, _tech_ok
    n = context['data'][0] if context else lock_nation(c, nid)
    require_tech(n)
    if s['paused'] or key not in s['types']:
        raise ValueError(tr('Firma jest wstrzymana lub budynek nie jest jej specjalizacją.',
                            'The company is paused or this building is not a specialty.'))
    if context:
        p = context['provinces'].get(int(cell))
    else:
        c.execute('SELECT * FROM provinces WHERE azgaar_cell_id=? AND active=1', (int(cell),))
        p = c.fetchone()
    if not p:
        raise ValueError(tr('Nie znaleziono prowincji.', 'Province not found.'))
    grant_id = permission(c, nid, s, p, key, target)
    if context:
        plant = context['plants'].get((p['id'], key))
        levels = dict(read_json(p['levels_json']))
        definition = context['data'][2].get(key)
    else:
        c.execute('SELECT * FROM company_plants WHERE province_id=? AND building_key=?', (p['id'], key))
        plant = c.fetchone()
        c.execute('SELECT levels_json FROM province_development WHERE province_id=?', (p['id'],))
        row = c.fetchone()
        levels = read_json(row['levels_json']) if row else {}
        c.execute('SELECT * FROM building_defs WHERE key=?', (key,))
        definition = c.fetchone()
    if plant and plant['company_nation_id'] != nid:
        raise ValueError(tr('Zakład prowadzi inna firma.', 'Another company manages this building.'))
    buildings = list(read_json(p['buildings_json'], []))
    old = int(levels.get(key, 1)) if key in buildings else 0
    if old >= 3:
        raise ValueError(tr('Budynek ma już poziom 3.', 'The building is already level 3.'))
    if not definition:
        raise ValueError(tr('Nie znaleziono typu budynku.', 'Building type not found.'))
    host = n if p['owner_nation_id'] == nid else lock_nation(c, p['owner_nation_id'])
    required = max(6 if old else 3, definition['requires_tech']) if key == 'algae_farm' else definition['requires_tech']
    if not _tech_ok(n, required, key) or not _tech_ok(host, required, key):
        raise ValueError(tr('Firma lub gospodarz nie spełnia wymagań technologii.', 'Company or host technology is insufficient.'))
    if key == 'algae_farm':
        if context:
            site = p['algae_site']
        else:
            c.execute('SELECT province_id FROM algae_sites WHERE province_id=?', (p['id'],))
            site = c.fetchone()
        if not site:
            raise ValueError(tr('Farma algae wymaga złoża.', 'An algae farm requires a deposit.'))
    elif not _terrain_ok(p['terrain'], definition['requires_terrain']):
        raise ValueError(tr('Nieodpowiedni teren.', 'Unsuitable terrain.'))
    extra = WORKERS[key] * (LEVEL_WORK[old+1] - (LEVEL_WORK[old] if old else 0))
    used = sum(WORKERS.get(k, 200)*LEVEL_WORK[min(3, max(1, int(levels.get(k, 1))))] for k in set(buildings))
    if p['population']*.4 - used < extra:
        raise ValueError(tr('Za mało wolnych pracowników.', 'Not enough available workers.'))
    discount = bonus(n, s, key)['construction']
    cost = {k: v*{0:1, 1:1.5, 2:2}[old]*(1-discount) for k, v in read_json(definition['cost_json']).items()}
    if cost.get('gold', 0) > remaining_budget(s, target) + .000001:
        raise ValueError(tr('Za mało złota w pozostałym budżecie miesięcznym.',
                            'Not enough gold in the remaining monthly budget.'))
    stock = dict(read_json(n['resources_json']), gold=n['treasury'])
    for resource, amount in cost.items():
        number(amount)
        if stock.get(resource, 0) < amount:
            raise ValueError(tr('Brak środków: ', 'Insufficient funds: ') + resource)
    return dict(nation=n, province=p, levels=levels, buildings=buildings, old=old,
                key=key, cost=cost, grant=grant_id, definition=definition)


def preview_investment(context, s, quote):
    """Preview paid construction before accepting an automatic investment."""
    from economy_engine import project
    data = list(context['data'])
    key, p, old = quote['key'], quote['province'], quote['old']
    n = dict(data[0])
    stock = dict(read_json(n['resources_json']))
    for resource, value in quote['cost'].items():
        if resource == 'gold':
            n['treasury'] -= value
        else:
            stock[resource] = stock.get(resource, 0) - value
    n['resources_json'] = json.dumps(stock)
    data[0] = n
    changed = []
    for original in data[1]:
        prov = dict(original)
        if prov['id'] == p['id']:
            prov['buildings_json'] = json.dumps(list(dict.fromkeys(quote['buildings'] + [key])))
            prov['levels_json'] = json.dumps(dict(quote['levels'], **{key: old+1}))
            prov['company_plants'] = dict(prov['company_plants'])
            prov['company_plants'][key] = dict(bonus=bonus(n, s, key), foreign=False)
        changed.append(prov)
    data[1] = changed
    after = project(*data)
    before = context['before']
    gain = any(v > before['production'].get(k, 0) + .000001 for k, v in after['production'].items())
    if (not gain or after['food_shortage'] > before['food_shortage'] + .000001
            or after['balance'] < 0 or after['policy']['arrears'] > before['policy']['arrears'] + .000001):
        raise ValueError(tr('Brak użytecznego przyrostu produkcji lub środków na utrzymanie.',
                            'No useful production gain or insufficient funds for upkeep.'))
    return after


def invest(c, nid, s, cell, key, target, automatic=False):
    context = investment_context(c, nid, s) if automatic else None
    if automatic and s['mode'] != 'auto':
        raise ValueError(tr('Automat jest wyłączony.', 'Automation is disabled.'))
    quote = investment_quote(c, nid, s, cell, key, target, context)
    if automatic:
        preview_investment(context, s, quote)
    n, p = quote['nation'], quote['province']
    old, levels, buildings, cost = quote['old'], quote['levels'], quote['buildings'], quote['cost']
    # No writes occur until all checks succeed; the caller holds the world lock.
    pay(c, n, s, cost)
    levels[key] = old+1
    if not old:
        buildings.append(key)
    c.execute('UPDATE provinces SET buildings_json=? WHERE id=?', (json.dumps(buildings), p['id']))
    c.execute('INSERT INTO province_development(province_id,levels_json) VALUES(?,?) '
              'ON CONFLICT(province_id) DO UPDATE SET levels_json=excluded.levels_json', (p['id'], json.dumps(levels)))
    c.execute('INSERT INTO company_plants(province_id,building_key,company_nation_id,host_nation_id,concession_id) '
              'VALUES(?,?,?,?,?) ON CONFLICT(province_id,building_key) DO UPDATE SET concession_id=excluded.concession_id,host_nation_id=excluded.host_nation_id',
              (p['id'], key, nid, p['owner_nation_id'], quote['grant']))
    begin_month(s, target)
    s['spent'] += cost.get('gold', 0)
    item = dict(cell=cell, building=key, level=old+1, cost=cost, automatic=automatic)
    s['report']['investments'].append(item)
    s['report']['investment'] = item
    s['report']['spent'] = s['spent']
    s['report']['budget'] = s['monthly_budget']
    s['report']['waiting'] = ''
    save(c, nid, s)
    from world_service import activity
    activity(c, 'building', p['owner_nation_id'], f"building:{p['id']}:{key}:{old+1}",
             {'building':key, 'level':old+1, 'cell':cell})
    return cost


def manual_invest(nid,uid,version,cell,key):
    with db.atomic() as c:
        n,s=actor(c,nid,uid,version)
        return invest(c,nid,s,cell,key,month_index(c))


def improvement(nid,uid,version,key,description,source=None):
    text=description.strip()
    if not 20<=len(text)<=IMPROVEMENT_TEXT_LIMIT:
        raise ValueError(tr('Opisz usprawnienie w 20–4000 znakach.', 'Describe the improvement in 20–4000 characters.'))
    with db.atomic() as c:
        n,s=actor(c,nid,uid,version)
        require_tech(n)
        if key not in s['types']:
            raise ValueError(tr('To usprawnienie nie pasuje do specjalizacji.', 'This improvement does not fit the specialty.'))
        normalized=' '.join(text.split()).casefold()
        if any(' '.join(x['description'].split()).casefold()==normalized for x in s['improvements']):
            raise ValueError(tr('Ten pomysł został już zapisany.', 'This idea is already recorded.'))
        if any(x['status'] in ('proposed','approved','running') for x in s['improvements']):
            raise ValueError(tr('Najpierw zakończ lub odrzuć bieżący projekt.', 'Finish or reject the current project first.'))
        identifier=uuid.uuid4().hex
        s['improvements'].append(dict(id=identifier,building=key,description=text,source=source,
                                      proposer_id=str(uid),status='proposed',effects={},cost={'gold':100},months=2))
        save(c,nid,s)
        return identifier


def review(nid,identifier,approved,gm_id,effects=None,version=None):
    """Called only by the GM-checked Discord review callback."""
    with db.atomic() as c:
        world_lock(c)
        s=state(c,nid)
        if s and version is not None and version != s['version']:
            raise ValueError(tr('Dane się zmieniły. Otwórz ocenę ponownie.', 'The data changed. Reopen the review.'))
        p=next((x for x in (s or {}).get('improvements',[]) if x['id']==identifier),None)
        if not p or p['status']!='proposed':
            raise ValueError(tr('Projekt nie czeka na ocenę.', 'The project is not awaiting review.'))
        if approved:
            p['effects']=validate_improvement_effects(p['building'],effects)
        p.update(status='approved' if approved else 'rejected',gm_id=str(gm_id),reviewed_month=month_index(c))
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
