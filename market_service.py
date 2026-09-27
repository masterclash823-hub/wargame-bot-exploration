"""Persistent player asks, escrow and atomic price/time-priority settlement."""
import json
import re
import math
from decimal import Decimal,InvalidOperation

import db
import i18n
from nation_access import find_nation,can_manage
from world_service import world_lock,tr
from nation_decay import require_playable

RESOURCES='food wood stone iron copper coal clay cloth tar gunpowder horses spices silk algae universal_knowledge'.split()


def shifted(value,delta):
    old=Decimal(str(value));new=float(old+delta)
    if not math.isfinite(new) or (delta and new==float(old)):
        raise ValueError(tr('Kwota jest zbyt mała względem salda albo saldo jest nieprawidłowe. Zwiększ ilość.',
                            'The amount is too small relative to the balance, or the balance is invalid. Increase quantity.'))
    return new


def amount(value,scale):
    try:
        number=Decimal(str(value).replace(',','.'))
        if not number.is_finite() or not 0<number<=1_000_000 or number*scale!=(number*scale).to_integral_value():raise ValueError
        return int(number*scale)
    except (InvalidOperation,ValueError,TypeError):
        raise ValueError(tr('Podaj dodatnią liczbę do 1 000 000; ilość do 3 miejsc po przecinku, cena do 2.',
                            'Enter a positive number up to 1,000,000; quantity up to 3 decimal places, price up to 2.')) from None


def resource_key(value):
    key=i18n.normalize_key(value)
    if key=='gold' or not re.fullmatch('[a-z_]{1,40}',key):
        raise ValueError(tr('Wybierz surowiec. Cenę podaje się osobno w złocie.','Choose a resource. The price is entered separately in gold.'))
    return key


def actor(c,uid):
    n=find_nation(uid,c)
    if not n or not can_manage(n['id'],uid,c):raise ValueError(tr('Nie masz dostępu do grywalnego państwa.','No playable nation access.'))
    return n


def _book(c,nid=None,resource=None,mine=False):
    sql="SELECT o.*,n.name AS seller FROM market_offers o JOIN nations n ON n.id=o.nation_id WHERE o.status='open' AND o.remaining>0 "
    sql+="AND NOT EXISTS (SELECT 1 FROM nation_decay d WHERE d.nation_id=n.id AND d.status='ruins')"
    args=[]
    if nid is not None:sql+=' AND o.nation_id'+('=?' if mine else '<>?');args.append(nid)
    if resource:sql+=' AND o.resource=?';args.append(resource)
    c.execute(sql+' ORDER BY o.resource,o.price_cents,o.id',tuple(args))
    rows=c.fetchall()
    if mine:return rows
    best={}
    for row in rows:best.setdefault(row['resource'],row)
    return list(best.values())


def book(uid,mine=False):
    with db.cursor() as c:
        n=actor(c,uid)
        return n,_book(c,n['id'],mine=mine)


def sell(uid,resource,quantity,price,nation_id=None):
    key=resource_key(resource);units=amount(quantity,1000);cents=amount(price,100)
    with db.atomic() as c:
        world_lock(c);n=actor(c,uid)
        if nation_id is not None and n['id']!=nation_id:raise ValueError(tr('Państwo zmieniło się. Otwórz rynek ponownie.','Your nation changed. Reopen the market.'))
        # Existing trade services lock nations in id order; use the same order.
        c.execute('SELECT * FROM nations WHERE id=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(n['id'],));n=c.fetchone()
        stock=json.loads(n['resources_json']);qty=Decimal(units)/1000
        if Decimal(str(stock.get(key,0)))<qty:raise ValueError(tr('Brak wystarczającej ilości surowca.','Not enough resource stock.'))
        stock[key]=shifted(stock.get(key,0),-qty)
        c.execute('UPDATE nations SET resources_json=? WHERE id=?',(json.dumps(stock),n['id']))
        oid=db.insert_returning_id('INSERT INTO market_offers(nation_id,resource,remaining,price_cents) VALUES(?,?,?,?)',(n['id'],key,units,cents))
        c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,'trade',?)",(n['id'],f'Market #{oid}: {float(qty):g} {key} reserved at {cents/100:g} gold/unit.'))
        return oid


def quote(uid,resource,quantity):
    key=resource_key(resource);units=amount(quantity,1000)
    with db.cursor() as c:
        n=actor(c,uid);rows=_book(c,n['id'],key)
    if not rows:raise ValueError(tr('Brak ofert tego surowca od innych państw.','No offers for this resource from other nations.'))
    row=rows[0]
    if units>row['remaining']:raise ValueError(tr('Najtańsza oferta ma tylko ','The cheapest offer only has ')+f"{row['remaining']/1000:g}. "+tr('Kup mniejszą ilość; droższa oferta nie jest dobierana automatycznie.','Buy a smaller quantity; a more expensive offer is not selected automatically.'))
    return dict(row,buyer_id=n['id'],units=units,cost=units*row['price_cents']/100000)


def buy(uid,offer_id,quantity,request_id,buyer_id):
    units=amount(quantity,1000)
    if not isinstance(request_id,str) or not 1<=len(request_id)<=80:raise ValueError('Invalid request ID')
    with db.atomic() as c:
        world_lock(c);n=actor(c,uid)
        if n['id']!=buyer_id:raise ValueError(tr('Państwo zmieniło się. Otwórz rynek ponownie.','Your nation changed. Reopen the market.'))
        c.execute('SELECT request_id FROM market_fills WHERE request_id=?',(request_id,))
        if c.fetchone():raise ValueError(tr('Ten zakup został już rozliczony.','This purchase was already settled.'))
        c.execute("SELECT * FROM market_offers WHERE id=? AND status='open'",(offer_id,));offer=c.fetchone()
        if not offer:raise ValueError(tr('Oferta została wykupiona lub wycofana. Odśwież rynek.','The offer was filled or cancelled. Refresh the market.'))
        best=_book(c,n['id'],offer['resource'])
        if not best or best[0]['id']!=offer_id:
            raise ValueError(tr('Najtańsza oferta zmieniła się. Odśwież rynek i potwierdź nową cenę.','The cheapest offer changed. Refresh and confirm the new price.'))
        if units>offer['remaining']:raise ValueError(tr('W ofercie zostało mniej surowca. Odśwież rynek.','Less stock remains. Refresh the market.'))
        c.execute('SELECT * FROM nations WHERE id IN (?,?) ORDER BY id'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(n['id'],offer['nation_id']))
        nations={row['id']:row for row in c.fetchall()};buyer=nations[n['id']];seller=nations[offer['nation_id']]
        require_playable(c,seller['id'])
        cost=Decimal(units)*offer['price_cents']/100000
        if cost>1_000_000_000:raise ValueError(tr('Maksymalnie miliard złota na zakup.','Maximum one billion gold per purchase.'))
        if Decimal(str(buyer['treasury']))<cost:raise ValueError(tr('Brakuje złota na zakup.','Not enough gold.'))
        stock=json.loads(buyer['resources_json']);key=offer['resource']
        stock[key]=shifted(stock.get(key,0),Decimal(units)/1000)
        buyer_gold=shifted(buyer['treasury'],-cost);seller_gold=shifted(seller['treasury'],cost)
        c.execute('UPDATE nations SET resources_json=?,treasury=? WHERE id=?',(json.dumps(stock),buyer_gold,buyer['id']))
        c.execute('UPDATE nations SET treasury=? WHERE id=?',(seller_gold,seller['id']))
        left=offer['remaining']-units
        c.execute('UPDATE market_offers SET remaining=?,status=? WHERE id=?',(left,'open' if left else 'filled',offer_id))
        c.execute('INSERT INTO market_fills(request_id,offer_id,buyer_id,quantity,gold) VALUES(?,?,?,?,?)',(request_id,offer_id,buyer['id'],units,float(cost)))
        for nid in (buyer['id'],seller['id']):
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,'trade',?)",
                      (nid,f"Market #{offer_id}: {seller['name']} → {buyer['name']}: {units/1000:g} {key}, {float(cost):g} gold."))
        return dict(offer,units=units,cost=float(cost))


def refund(c,nid,offer_id=None):
    """Caller holds world lock; also used for nation transfer and collapse."""
    c.execute('SELECT * FROM nations WHERE id=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(nid,));n=c.fetchone()
    if not n:return 0
    sql="SELECT * FROM market_offers WHERE nation_id=? AND status='open'";args=[nid]
    if offer_id is not None:sql+=' AND id=?';args.append(offer_id)
    c.execute(sql,tuple(args));rows=c.fetchall();stock=json.loads(n['resources_json'])
    for row in rows:
        key=row['resource'];stock[key]=float(Decimal(str(stock.get(key,0)))+Decimal(row['remaining'])/1000)
        c.execute("UPDATE market_offers SET status='cancelled',remaining=0 WHERE id=?",(row['id'],))
    if rows:c.execute('UPDATE nations SET resources_json=? WHERE id=?',(json.dumps(stock),nid))
    return len(rows)


def cancel(uid,offer_id):
    with db.atomic() as c:
        world_lock(c);n=actor(c,uid)
        if not refund(c,n['id'],offer_id):raise ValueError(tr('Brak aktywnej oferty twojego państwa o tym ID.','No open offer with this ID belongs to your nation.'))
