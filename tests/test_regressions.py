import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DISCORD_TOKEN", "test-not-a-token")
os.environ.setdefault("GEMINI_API_KEY", "test-not-a-key")
os.environ.setdefault("DEFAULT_LANGUAGE", "en")  # Legacy English assertions; Polish has its own coverage.

import config
import db
import utils
from trade_service import accept_trade, parse_resources, validate_gold
from cogs.economy import EconomyCog, HelpView
from cogs.military import MilitaryCog


def interaction(uid=2, roles=()):
    return NS(user=NS(id=uid, roles=roles), guild=NS(get_member=lambda uid: None),
              locale=None, response=NS(send_message=AsyncMock(), defer=AsyncMock(),
                                       edit_message=AsyncMock()),
              followup=NS(send=AsyncMock()))


class DatabaseFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"DB_PATH": self.tmp.name + "/test.db"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.pg = patch.object(db, "USE_POSTGRES", False)
        self.pg.start()
        self.addCleanup(self.pg.stop)
        db.init_db()
        with db.cursor() as c:
            for uid, name, resources in ((1, "A", {"wood":100}), (2, "B", {"iron":50})):
                c.execute("INSERT INTO nations(owner_id,name,treasury,resources_json) VALUES(?,?,?,?)",
                          (str(uid), name, 100, json.dumps(resources)))
        self.trade_id = db.insert_returning_id(
            "INSERT INTO trades(from_nation_id,to_nation_id,offer_resources_json,"
            "receive_resources_json,offer_gold,receive_gold) VALUES(?,?,?,?,?,?)",
            (1, 2, '{"wood":10}', '{"iron":5}', 20, 3))

    def balances(self):
        with db.cursor() as c:
            c.execute("SELECT * FROM nations ORDER BY id")
            return c.fetchall()


class TradeTests(DatabaseFixture, unittest.TestCase):
    def test_exchange_and_duplicate(self):
        accept_trade(self.trade_id, 2)
        a, b = self.balances()
        self.assertEqual((a["treasury"], b["treasury"]), (83, 117))
        self.assertEqual(json.loads(a["resources_json"]), {"wood":90, "iron":5})
        self.assertEqual(json.loads(b["resources_json"]), {"wood":10, "iron":45})
        with self.assertRaisesRegex(ValueError, "already accepted"):
            accept_trade(self.trade_id, 2)
        self.assertEqual([a, b], self.balances())

    def test_insufficient_resources_rollback(self):
        with db.cursor() as c:
            c.execute("UPDATE nations SET treasury=0 WHERE id=2")
        before = self.balances()
        with self.assertRaisesRegex(ValueError, "gold"):
            accept_trade(self.trade_id, 2)
        self.assertEqual(before, self.balances())
        with db.cursor() as c:
            c.execute("SELECT status FROM trades WHERE id=?", (self.trade_id,))
            self.assertEqual(c.fetchone()["status"], "pending")

    def test_unauthorized_and_gm_without_nation(self):
        for uid in (1, 999):
            with self.assertRaisesRegex(ValueError, "not addressed"):
                accept_trade(self.trade_id, uid)
        accept_trade(self.trade_id, 999, is_gm=True)

    def test_invalid_legacy_offer(self):
        with db.cursor() as c:
            c.execute("UPDATE trades SET offer_gold=-10 WHERE id=?", (self.trade_id,))
        before = self.balances()
        with self.assertRaises(ValueError):
            accept_trade(self.trade_id, 2)
        self.assertEqual(before, self.balances())

    def test_concurrent_acceptance(self):
        def attempt(_):
            try:
                accept_trade(self.trade_id, 2)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, range(2))), [False, True])
        self.assertEqual(self.balances()[0]["treasury"], 83)

    def test_invalid_amounts(self):
        for raw in ('[]', 'null', '{"wood":-2}', '{"wood":"10"}',
                    '{"wood":true}', '{"wood":NaN}', '{"wood":Infinity}', '{"gold":1}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_resources(raw)
        for value in (-1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                validate_gold(value)


class UITests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def test_gm_tab_and_role_id(self):
        with patch.object(config, "GM_ROLE_ID", ""), patch.object(config, "GM_ROLE_NAME", "Game Master"):
            gm = interaction(roles=[NS(id=10, name=" game MASTER ")])
            self.assertTrue(utils.gm_only(gm))
            self.assertIn("🔐 GM", [b.label for b in HelpView(utils.gm_only(gm)).children])
            self.assertNotIn("🔐 GM", [b.label for b in HelpView(False).children])
            gm.guild = None
            self.assertFalse(utils.gm_only(gm))
        with patch.object(config, "GM_ROLE_ID", "10"):
            self.assertTrue(utils.gm_only(interaction(roles=[NS(id=10, name="GM")])) )
            self.assertFalse(utils.gm_only(interaction(roles=[NS(id=11, name="Game Master")])))

    async def test_navy_stats_and_pagination(self):
        bid = db.insert_returning_id(
            "INSERT INTO blueprints(nation_id,type,name,hull,stats_json) VALUES(?,?,?,?,?)",
            (2, "ship", "Sloop", "sloop", '{"cargo":2}'))
        with db.cursor() as c:
            for _ in range(45):
                c.execute("INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(?,?,?)",
                          (2, bid, 3))
            c.execute("INSERT INTO military_units(nation_id,unit_type) VALUES(?,?)", (2, "orphan"))
        inter = interaction()
        await MilitaryCog.mil_list.callback(None, inter)
        kwargs = inter.followup.send.call_args.kwargs
        pages = kwargs["view"].pages
        self.assertGreater(len(pages), 1)
        self.assertIn("Fleet cargo: 270", pages[0].fields[0].value)
        self.assertEqual(sum(len(p.fields) - 1 for p in pages), 46)
        for p in pages:
            self.assertLessEqual(len(p), 6000)
            self.assertLessEqual(len(p.fields), 25)
            self.assertTrue(all(len(f.value) <= 1024 for f in p.fields))
        await kwargs["view"].next_page.callback(inter)
        self.assertEqual(kwargs["view"].index, 1)

    async def test_private_military(self):
        inter = interaction()
        with patch.object(config, "GM_ROLE_ID", ""), patch.object(config, "GM_ROLE_NAME", "Game Master"):
            await MilitaryCog.mil_list.callback(None, inter, "A")
        self.assertIn("private", inter.response.send_message.call_args.args[0])
        inter.followup.send.assert_not_called()

    async def test_offer_id_and_accept_callback(self):
        sender = interaction(1)
        await EconomyCog.trade_offer.callback(None, sender, "B", '{"wood":5}', 1)
        self.assertIn("#2", sender.response.send_message.call_args.kwargs["embed"].title)
        receiver = interaction(2)
        await EconomyCog.trade_accept.callback(None, receiver, 2)
        receiver.response.defer.assert_awaited_once()
        self.assertIn("Accepted", receiver.followup.send.call_args.kwargs["embed"].title)

    async def test_cancel_authorization_and_timestamp(self):
        inter = interaction(999)
        await EconomyCog.trade_cancel.callback(None, inter, self.trade_id)
        self.assertIn("not party", inter.response.send_message.call_args.args[0])
        await EconomyCog.trade_cancel.callback(None, interaction(1), self.trade_id)
        with db.cursor() as c:
            c.execute("SELECT status,resolved_at FROM trades WHERE id=?", (self.trade_id,))
            trade = c.fetchone()
        self.assertEqual(trade["status"], "cancelled")
        self.assertIsNotNone(trade["resolved_at"])


if __name__ == "__main__":
    unittest.main()
