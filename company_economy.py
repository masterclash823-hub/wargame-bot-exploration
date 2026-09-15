"""Company monthly accounting. Foreign supplies are prepaid, with exact refunds."""
import json
import math
import i18n
import db
import companies as co
from economy_engine import read_json, LEVEL_OUTPUT, LEVEL_WORK, building_level, lock_nation
from world_service import tr


def candidate_priority(context, quote, needs):
    """Food first, then missing supplies; compare added base output per gold."""
    from economy_engine import LEVEL_OUTPUT
    raw = read_json(quote['definition']['effect_json'])
    outputs = {k:v for k,v in raw.items() if isinstance(v, (int,float)) and math.isfinite(v) and v > 0}
    old = quote['old']
    growth = LEVEL_OUTPUT[old+1] - (LEVEL_OUTPUT[old] if old else 0)
    priority = 2
    if outputs.get('food', 0) > 0 and needs.get('food', 0) > 0:
        priority = 0
    elif any(needs.get(k, 0) > 0 for k in outputs if k != 'food'):
        priority = 1
    useful = sum(min(v*growth, needs[k]) if needs.get(k, 0) > 0 else v*growth
                 for k,v in outputs.items())
    efficiency = useful / max(1, quote['cost'].get('gold', 0))
    return (priority, -efficiency, quote['key'], quote['province']['azgaar_cell_id'])


def supply_needs(context, s):
    from economy_engine import LEVEL_OUTPUT, building_level
    before = context['before']
    needs = {}
    inputs = {}
    n, provinces, definitions = context['data'][:3]
    economy = read_json(n['tech_json']).get('economy', 3)
    for p in provinces:
        for key in read_json(p['buildings_json'], []):
            definition = definitions.get(key)
            if not definition:
                continue
            level = building_level(key, read_json(p['levels_json']), economy)
            for resource, value in read_json(definition['effect_json']).items():
                if isinstance(value, (int,float)) and math.isfinite(value) and value < 0 and resource != 'gold':
                    inputs[resource] = inputs.get(resource, 0) - value*LEVEL_OUTPUT[level]
    for resource, demand in inputs.items():
        needs[resource] = max(0, 3*demand - before['resources'].get(resource, 0))
    # Also replenish materials needed for the company's own construction.
    stock = read_json(n['resources_json'])
    for key in s['types']:
        definition = definitions.get(key)
        if definition:
            for resource, value in read_json(definition['cost_json']).items():
                if resource != 'gold':
                    needs[resource] = max(needs.get(resource, 0), 2*value - stock.get(resource, 0), 0)
    needs['food'] = max(0, before['food_needed'] - before['production'].get('food', 0),
                        3*before['food_needed'] - before['resources'].get('food', 0))
    return needs


def automate(c, nid, s, target):
    # Each success raises a building by one level (maximum 3), so this terminates
    # even for zero-gold definitions. There is no arbitrary investments-per-month cap.
    while True:
        if co.remaining_budget(s, target) <= .000001:
            s['report']['waiting'] = tr('Budżet na ten miesiąc jest wykorzystany lub wynosi 0.',
                                        'This month’s budget is spent or set to zero.')
            return
        context = co.investment_context(c, nid, s)
        needs = supply_needs(context, s)
        candidates = []
        reasons = {}
        for cell in context['provinces']:
            for key in s['types']:
                try:
                    quote = co.investment_quote(c, nid, s, cell, key, target, context)
                except ValueError as exc:
                    reasons[str(exc)] = reasons.get(str(exc), 0) + 1
                else:
                    candidates.append((candidate_priority(context, quote, needs), quote))
        for rank, quote in sorted(candidates, key=lambda item: item[0]):
            try:
                co.preview_investment(context, s, quote)
            except ValueError as exc:
                reasons[str(exc)] = reasons.get(str(exc), 0) + 1
                continue
            co.invest(c, nid, s, quote['province']['azgaar_cell_id'], quote['key'], target, automatic=True)
            s['report']['investment']['reason'] = ('food', 'supplies', 'growth')[rank[0]]
            break
        else:
            reason = max(reasons, key=reasons.get) if reasons else tr('Brak własnych aktywnych prowincji.',
                                                                     'No active domestic provinces.')
            s['report']['waiting'] = tr('Automat czeka: ', 'Automation is waiting: ') + reason
            return


def prepare(c,target):
    c.execute("SELECT * FROM company_concessions WHERE status IN ('active','proposed')")
    for grant in c.fetchall():
        terms=read_json(grant['terms_json'])
        a=lock_nation(c,grant['company_nation_id'])
        b=lock_nation(c,grant['host_nation_id'])
        expired=grant['status']=='active' and target>terms['expires']
        changed=a['owner_id']!=terms['company_owner'] or b['owner_id']!=terms['host_owner']
        if expired or changed:
            c.execute('UPDATE company_concessions SET status=? WHERE id=?',
                      ('expired' if expired else 'withdrawn',grant['id']))
            c.execute('DELETE FROM company_plants WHERE concession_id=?',(grant['id'],))
    c.execute("SELECT nation_id FROM companies WHERE nation_id NOT IN (SELECT nation_id FROM nation_decay WHERE status='ruins') ORDER BY nation_id")
    companies=[r['nation_id'] for r in c.fetchall()]
    for nid in companies:
        n=lock_nation(c,nid)
        with i18n.using_language(i18n.get_user_language(n['owner_id'])):
            s=co.state(c,nid)
            co.begin_month(s,target)
            s['report'].update(budget=s['monthly_budget'], spent=s['spent'])
            if not co.unlocked(n) or s['paused']:
                s['report']['waiting']=tr('Firma jest wstrzymana lub wymaga gospodarki 4.',
                                         'The company is paused or requires economy 4.')
                co.save(c,nid,s)
                continue
            for p in s['improvements']:
                if p['status']=='running' and target-p['started']>=p['months']:
                    p['status']='complete'
            co.save(c,nid,s)
            if s['mode']=='auto':
                automate(c,nid,s,target)
            co.save(c,nid,s)

    # Expired or transferred permissions cease to manage the host's buildings.
    c.execute('SELECT * FROM company_plants ORDER BY company_nation_id,province_id,building_key')
    plants=c.fetchall()
    result={}
    for plant in plants:
        nid=plant['company_nation_id']
        n=lock_nation(c,nid)
        s=co.state(c,nid)
        c.execute('SELECT p.*,d.levels_json,x.status AS colony_status FROM provinces p '
                  'LEFT JOIN province_development d ON d.province_id=p.id '
                  'LEFT JOIN colonies x ON x.province_id=p.id WHERE p.id=?',(plant['province_id'],))
        p=c.fetchone()
        if not p or not p['active'] or p['owner_nation_id']!=plant['host_nation_id'] or plant['building_key'] not in read_json(p['buildings_json'],[]):
            c.execute('DELETE FROM company_plants WHERE province_id=? AND building_key=?',
                      (plant['province_id'],plant['building_key']))
            continue
        if not co.unlocked(n) or s['paused']:
            continue
        key=plant['building_key']
        grant=None
        if plant['concession_id']:
            c.execute('SELECT * FROM company_concessions WHERE id=?',(plant['concession_id'],))
            grant=c.fetchone()
            if not grant or not co.valid_grant(c,grant,target):
                terms=read_json(grant['terms_json']) if grant else {}
                retired=not grant or grant['status']!='active' or target>terms.get('expires',-1)
                if grant and not retired:
                    for nation_key,owner_key in (('company_nation_id','company_owner'),('host_nation_id','host_owner')):
                        current=lock_nation(c,grant[nation_key])
                        retired=retired or current['owner_id']!=terms[owner_key]
                if retired:
                    c.execute('DELETE FROM company_plants WHERE province_id=? AND building_key=?',
                              (plant['province_id'],plant['building_key']))
                continue
        elif p['owner_nation_id']!=nid:
            continue
        item=dict(nation_id=nid,foreign=p['owner_nation_id']!=nid,
                  bonus=co.bonus(n,s,key),inputs={},upkeep=0.,share=0.,halted=False)
        result.setdefault(p['id'],{})[key]=item
        if not item['foreign']:
            continue
        item['share']=read_json(grant['terms_json'])['share']
        host=lock_nation(c,p['owner_nation_id'])
        economy=read_json(host['tech_json']).get('economy',3)
        level=building_level(key,read_json(p['levels_json']),economy)
        if key=='algae_farm':
            c.execute('SELECT province_id FROM algae_sites WHERE province_id=?',(p['id'],))
            if economy<3 or not c.fetchone():
                item['halted']=True
                continue
        c.execute('SELECT * FROM building_defs WHERE key=?',(key,))
        definition=c.fetchone()
        if not definition:
            item['halted']=True
            continue
        raw=read_json(definition['effect_json'])
        inputs={k:-v*LEVEL_OUTPUT[level]*(1-item['bonus']['inputs'])
                for k,v in raw.items() if isinstance(v,(int,float)) and math.isfinite(v) and v<0}
        upkeep=max(0,read_json(definition['upkeep_json']).get('gold',0))*LEVEL_WORK[level]*(1-item['bonus']['maintenance'])
        cost=dict(inputs)
        cost['gold']=cost.get('gold',0)+upkeep
        try:
            with i18n.using_language(i18n.get_user_language(n['owner_id'])):
                co.pay(c,n,s,cost)
        except ValueError as exc:
            item['halted']=True
            s['report']['waiting']=str(exc)
        else:
            item.update(inputs=inputs,upkeep=upkeep)
            for resource,value in cost.items():
                s['report']['costs'][resource]=s['report']['costs'].get(resource,0)+value
        co.save(c,nid,s)
    return result


def finish(c,reports):
    """Deliver company shares and refund unused escrow after every host is settled.

    Deliveries enter closing stocks and are available for consumption next month;
    results do not depend on the order in which nation rows are processed.
    """
    credits={}
    for report in reports.values():
        for transfer in report.get('company_transfers',[]):
            pool=credits.setdefault(transfer['nation_id'],{})
            for resource,value in transfer['resources'].items():
                pool[resource]=pool.get(resource,0)+value
    for nid,pool in credits.items():
        n=lock_nation(c,nid)
        stock=read_json(n['resources_json'])
        for resource,value in pool.items():
            if resource!='gold':
                stock[resource]=stock.get(resource,0)+value
        c.execute('UPDATE nations SET resources_json=?,treasury=treasury+? WHERE id=?',
                  (json.dumps(stock),pool.get('gold',0),nid))
        r=reports[str(nid)]
        r['resources']=stock
        r['treasury']+=pool.get('gold',0)
        r['income']+=pool.get('gold',0)
        r['balance']+=pool.get('gold',0)
        r['food_change']+=pool.get('food',0)
        r['food_months']=stock.get('food',0)/r['food_needed'] if r['food_needed'] else None
        s=co.state(c,nid)
        s['report']['received']=pool
        co.save(c,nid,s)
    c.execute('SELECT nation_id,state_json FROM companies ORDER BY nation_id')
    for row in c.fetchall():
        s=read_json(row['state_json'])
        if str(row['nation_id']) in reports:
            reports[str(row['nation_id'])]['company']=s['report']
