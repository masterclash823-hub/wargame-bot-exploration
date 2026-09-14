"""Company monthly accounting. Foreign supplies are prepaid, with exact refunds."""
import json
import math
import i18n
import db
import companies as co
from economy_engine import read_json, LEVEL_OUTPUT, LEVEL_WORK, building_level, lock_nation
from world_service import tr


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
    c.execute('SELECT nation_id FROM companies ORDER BY nation_id')
    companies=[r['nation_id'] for r in c.fetchall()]
    for nid in companies:
        n=lock_nation(c,nid)
        with i18n.using_language(i18n.get_user_language(n['owner_id'])):
            s=co.state(c,nid)
            s['report']={'month':target,'waiting':'','received':{},'costs':{}}
            if not co.unlocked(n) or s['paused']:
                s['report']['waiting']=tr('Firma jest wstrzymana lub wymaga gospodarki 4.',
                                         'The company is paused or requires economy 4.')
                co.save(c,nid,s)
                continue
            for p in s['improvements']:
                if p['status']=='running' and target-p['started']>=p['months']:
                    p['status']='complete'
            co.save(c,nid,s)
            if s['mode']=='off':
                continue
            targets=[(cell,key) for cell in s['cells'] for key in s['types']]
            for grant in co.grants(c,nid):
                if grant['company_nation_id']==nid and co.valid_grant(c,grant,target):
                    targets += [(cell,key) for cell in grant['terms']['cells'] for key in grant['terms']['types']]
            for cell,key in dict.fromkeys(targets):
                if s['mode']=='supply':
                    c.execute('SELECT effect_json FROM building_defs WHERE key=?',(key,))
                    definition=c.fetchone()
                    if not definition or read_json(definition['effect_json']).get(s['resource'],0)<=0:
                        continue
                try:
                    co.invest(c,nid,s,cell,key,target,automatic=True)
                except ValueError as exc:
                    s['report']['waiting']=str(exc)
                else:
                    s['report']['waiting']=''
                    break
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
        reports[str(row['nation_id'])]['company']=s['report']
