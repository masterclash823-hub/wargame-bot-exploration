"""Bounded, single-use victory claims; captives are transferred population, not new people."""
import json
import db
import i18n
from world_service import world_lock, owned, month_index, reward, tr


def add_opportunity(c,kind,source,winner,loser,capacity):
    if capacity<=0 or winner==loser:return
    c.execute('INSERT INTO captive_opportunities(source_type,source_id,winner_id,loser_id,capacity,created_month) VALUES(?,?,?,?,?,?) '
              'ON CONFLICT(source_type,source_id) DO NOTHING',(kind,source,winner,loser,capacity,month_index(c)))


def battle_pool(c,bid,result,a,b,land_ids):
    if result['winner']=='draw' or not result['casualties_applied']:return 0
    side='defender' if result['winner']=='attacker' else 'attacker'
    losses=sum(x['lost'] for x in result[side+'_losses'] if x['unit_id'] in land_ids)
    capacity=losses//4
    winner,loser=(a,b) if side=='defender' else (b,a)
    add_opportunity(c,'battle',bid,winner,loser,capacity)
    return capacity


def peace_pool(c,tid,terms,a,b):
    side=terms.get('war_winner','none')
    if side=='none':return
    winner,loser=(a,b) if side=='proposer' else (b,a)
    c.execute('SELECT COALESCE(SUM(population),0) AS pop FROM provinces WHERE owner_nation_id=? AND active=1',(loser,))
    add_opportunity(c,'peace',tid,winner,loser,min(500,max(0,c.fetchone()['pop'])//100))


def opportunity(c,oid,nid):
    c.execute('SELECT * FROM captive_opportunities WHERE id=? AND winner_id=?',(oid,nid))
    r=c.fetchone()
    if not r or r['used'] or month_index(c)>=r['created_month']+6:
        raise ValueError(tr('Uprawnienie do jeńców wygasło, zostało wykorzystane lub nie należy do Ciebie.',
                            'The captive claim expired, was used, or does not belong to you.'))
    return r


def take(nid,uid,oid,cell,quantity):
    if type(quantity) is not int or quantity<=0:raise ValueError(tr('Podaj dodatnią liczbę całkowitą.','Enter a positive whole number.'))
    with db.atomic() as c:
        world_lock(c);n=owned(c,nid,uid);claim=opportunity(c,oid,nid)
        from labor_regimes import state
        if state(c,nid)['mode']!='slavery':raise ValueError(tr('Zniewolenie jeńców wymaga polityki niewolnictwa.','Enslaving captives requires the slavery policy.'))
        if quantity>claim['capacity']:raise ValueError(tr('Przekroczono limit tego zwycięstwa.','This exceeds the victory limit.'))
        c.execute("SELECT p.* FROM provinces p LEFT JOIN colonies x ON x.province_id=p.id WHERE p.owner_nation_id=? AND p.active=1 AND p.azgaar_cell_id=? AND (x.id IS NULL OR x.status='province')",(nid,cell))
        dest=c.fetchone()
        if not dest:raise ValueError(tr('Wybierz własną, aktywną prowincję poza rozwijaną kolonią.','Choose your own active province, excluding developing colonies.'))
        c.execute('SELECT p.*,COALESCE(x.quantity,0) AS captive_count FROM provinces p LEFT JOIN province_captives x ON x.province_id=p.id WHERE p.owner_nation_id=? AND p.active=1 ORDER BY p.population DESC,p.id',(claim['loser_id'],))
        sources=c.fetchall()
        available=sum(max(0,p['population']-p['captive_count']) for p in sources)
        if quantity>available:raise ValueError(tr('Przegrane państwo nie ma wystarczającej ludności do przeniesienia.','The defeated nation lacks enough population to transfer.'))
        from economy_services import spend
        spend(c,n,{'gold':quantity*.5})
        left=quantity
        for p in sources:
            moved=min(left,max(0,p['population']-p['captive_count']))
            if moved:c.execute('UPDATE provinces SET population=population-? WHERE id=?',(moved,p['id']))
            left-=moved
            if not left:break
        c.execute('UPDATE provinces SET population=population+? WHERE id=?',(quantity,dest['id']))
        c.execute('INSERT INTO province_captives(province_id,quantity) VALUES(?,?) ON CONFLICT(province_id) DO UPDATE SET quantity=province_captives.quantity+excluded.quantity',(dest['id'],quantity))
        c.execute('UPDATE captive_opportunities SET used=1 WHERE id=?',(oid,))
        reward(c,nid,reputation=-5)
        for country in (nid,claim['loser_id']):
            c.execute('UPDATE nations SET population=(SELECT COALESCE(SUM(population),0) FROM provinces WHERE owner_nation_id=? AND active=1) WHERE id=?',(country,country))
            c.execute('SELECT owner_id FROM nations WHERE id=?',(country,));owner=c.fetchone()['owner_id']
            with i18n.using_language(i18n.get_user_language(owner)):
                entry=tr('Zniewoleni jeńcy: ','Enslaved captives: ')+f"{quantity}; {claim['source_type']} #{claim['source_id']}; {claim['loser_id']} → {nid}, #{cell}."
            c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)',(country,'system',entry))
        from world_service import activity
        activity(c,'captives',nid,f'captives:{oid}',{'quantity':quantity},claim['loser_id'])
        return quantity


def release(nid,uid,oid):
    with db.atomic() as c:
        world_lock(c);owned(c,nid,uid);opportunity(c,oid,nid)
        c.execute('UPDATE captive_opportunities SET used=1 WHERE id=?',(oid,))


def emancipate(c,nid):
    c.execute('DELETE FROM province_captives WHERE province_id IN (SELECT id FROM provinces WHERE owner_nation_id=?)',(nid,))


def reconcile(c,nid):
    # People remain in their provinces after abolition or transfer to a free-labor nation.
    from labor_regimes import state
    if state(c,nid)['mode']=='free':emancipate(c,nid)
    else:
        c.execute('SELECT x.province_id,x.quantity,p.population FROM province_captives x JOIN provinces p ON p.id=x.province_id WHERE p.owner_nation_id=?',(nid,))
        for r in c.fetchall():
            if r['quantity']>r['population']:c.execute('UPDATE province_captives SET quantity=? WHERE province_id=?',(max(0,r['population']),r['province_id']))
