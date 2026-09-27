import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import db
import i18n
import market_service as market
from nation_access import set_coop
from world_service import transfer_nation
from test_regressions import DatabaseFixture,interaction


class MarketTests(DatabaseFixture,unittest.TestCase):
    def test_price_time_priority_partial_fills_persistence_and_refund(self):
        expensive=market.sell(1,'wood',20,4)
        cheap=market.sell(1,'wood',10,2)
        tie=market.sell(1,'wood',5,2)
        self.assertEqual(json.loads(self.balances()[0]['resources_json'])['wood'],65)
        db.init_db()
        self.assertEqual([r['id'] for r in market.book(2)[1]],[cheap])
        self.assertEqual(len(market.book(1,True)[1]),3)
        q=market.quote(2,'wood',4);self.assertEqual(q['cost'],8)
        market.buy(2,cheap,4,'first',2)
        self.assertEqual(market.book(2)[1][0]['remaining'],6000)
        before=self.balances()
        with self.assertRaises(ValueError):market.buy(2,cheap,4,'first',2)
        self.assertEqual(before,self.balances())
        with self.assertRaises(ValueError):market.buy(2,expensive,1,'skip',2)
        market.buy(2,cheap,6,'second',2)
        self.assertEqual(market.book(2)[1][0]['id'],tie)
        market.cancel(1,tie)
        self.assertEqual(market.book(2)[1][0]['id'],expensive)
        market.cancel(1,expensive)
        a,b=self.balances()
        self.assertEqual(json.loads(a['resources_json'])['wood'],90)
        self.assertEqual(json.loads(b['resources_json'])['wood'],10)
        self.assertEqual((a['treasury'],b['treasury']),(120,80))
        with self.assertRaises(ValueError):market.cancel(1,expensive)

    def test_invalid_amounts_insufficient_balances_and_no_self_purchase(self):
        before=self.balances()
        for value in (0,-1,float('nan'),float('inf'),True,0.0001,1_000_001):
            with self.assertRaises(ValueError):market.sell(1,'wood',value,1)
        for price in (0,-1,0.001,float('nan'),True):
            with self.assertRaises(ValueError):market.sell(1,'wood',1,price)
        with self.assertRaises(ValueError):market.sell(1,'gold',1,1)
        with self.assertRaises(ValueError):market.sell(1,'wood',101,1)
        self.assertEqual(before,self.balances())
        oid=market.sell(1,'wood',10,20);before=self.balances()
        with self.assertRaises(ValueError):market.buy(2,oid,10,'poor',2)
        with self.assertRaises(ValueError):market.quote(2,'wood',11)
        with self.assertRaises(ValueError):market.buy(1,oid,1,'self',1)
        with self.assertRaises(ValueError):market.cancel(2,oid)
        self.assertEqual(before,self.balances())

    def test_changed_cheapest_requires_new_confirmation(self):
        old=market.sell(1,'wood',5,5);q=market.quote(2,'wood',5)
        new=market.sell(1,'wood',5,2)
        with self.assertRaises(ValueError):market.buy(2,q['id'],5,'old-price',2)
        self.assertEqual(market.quote(2,'wood',5)['id'],new)
        self.assertEqual(len(market.book(1,True)[1]),2)

    def test_tiny_payment_cannot_round_to_free_purchase(self):
        oid=market.sell(1,'wood',1,.01)
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=? WHERE id=2',(1e15,))
        before=self.balances()
        with self.assertRaises(ValueError):market.buy(2,oid,.001,'too-small',2)
        self.assertEqual(before,self.balances())

    def test_coop_revoke_and_transfer_refund_remaining_stock(self):
        set_coop(1,1,3);oid=market.sell(3,'wood',10,1)
        set_coop(1,1,3,remove=True)
        with self.assertRaises(ValueError):market.cancel(3,oid)
        market.buy(2,oid,4,'coop-order',2)
        transfer_nation(1,4,'1',999)
        self.assertEqual(json.loads(self.balances()[0]['resources_json'])['wood'],96)
        self.assertFalse(market.book(2)[1])

    def test_fractional_algae_and_collapse(self):
        with db.cursor() as c:c.execute('UPDATE nations SET resources_json=? WHERE id=1',('{"algae":0.05}',))
        oid=market.sell(1,'algae',.05,10)
        market.buy(2,oid,.001,'algae',2)
        self.assertAlmostEqual(self.balances()[0]['treasury'],100.01)
        from nation_decay import set_decay,tick
        state=set_decay(1,999)
        with db.atomic() as c:tick(c,state['due_month'])
        self.assertFalse(market.book(2)[1])
        self.assertAlmostEqual(json.loads(self.balances()[0]['resources_json'])['algae'],.049)

    def test_concurrent_buyers_cannot_double_spend_escrow(self):
        oid=market.sell(1,'wood',10,1)
        def buy(token):
            try:market.buy(2,oid,10,token,2);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(buy,['one','two']))
        self.assertEqual(sum(results),1)
        self.assertEqual(json.loads(self.balances()[1]['resources_json'])['wood'],10)

    def test_failed_receipt_rolls_back_balances_and_offer(self):
        oid=market.sell(1,'wood',10,1);before=self.balances()
        original=db._UnifiedCursor.execute
        def fail(c,sql,params=()):
            if sql.startswith('INSERT INTO market_fills'):raise RuntimeError('write failed')
            return original(c,sql,params)
        with patch.object(db._UnifiedCursor,'execute',fail),self.assertRaises(RuntimeError):market.buy(2,oid,5,'failed',2)
        self.assertEqual(before,self.balances())
        self.assertEqual(market.book(2)[1][0]['remaining'],10000)


class MarketUITests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    async def test_ui_pages_and_revoked_access(self):
        from cogs.market import MarketView,ConfirmView,MarketCog
        for _ in range(23):market.sell(1,'wood',1,1)
        n,rows=market.book(1,True)
        for lang in ('pl','en'):
            with i18n.using_language(lang):
                for page in (0,1):
                    view=MarketView(1,1,rows,True,page)
                    self.assertLessEqual(len(view.children[0].options),20)
                    self.assertLess(len(view.embed),6000)
        self.assertEqual(len(MarketCog.market_group.commands),5)
        set_coop(2,2,3)
        q=market.quote(3,'wood',1);view=ConfirmView(3,2,quote=q)
        set_coop(2,2,3,remove=True)
        self.assertFalse(await view.interaction_check(interaction(3)))
        with self.assertRaises(ValueError):market.buy(3,q['id'],1,'removed',2)

    async def test_market_button_fits_with_company_and_admin(self):
        from cogs.panel import PlayerPanel
        with patch('companies.unlocked',return_value=True):
            view=PlayerPanel(None,1,'pl','economy',admin=True)
        self.assertTrue(any(getattr(b,'label',None)=='Wolny rynek' for b in view.children))
        self.assertLessEqual(len(view.children),25)

    async def test_slash_quote_confirms_once_without_silently_buying(self):
        from cogs.market import MarketCog,number
        from unittest.mock import AsyncMock
        oid=market.sell(1,'wood',5,1.23)
        i=interaction(2);i.response.is_done=lambda:True;i.edit_original_response=AsyncMock()
        await MarketCog.buy.callback(MarketCog(),i,'wood',2)
        self.assertEqual(self.balances()[1]['treasury'],100)
        view=i.followup.send.call_args.kwargs['view']
        await view.confirm(i)
        self.assertAlmostEqual(self.balances()[1]['treasury'],97.54)
        await view.confirm(i)
        self.assertAlmostEqual(self.balances()[1]['treasury'],97.54)
        self.assertEqual(number(999999.99),'999999.99')
