import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import sys

import db
import config
import battle_plan_text as text
from cogs import combat
from test_regressions import DatabaseFixture, interaction


class LongPlanTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def submit(self, orders='', attachment=None):
        inter = interaction(2)
        await combat.CombatCog.battle_plan.callback(None, inter, 'Harbor', orders, orders_file=attachment)
        return inter

    async def test_full_file_saved_downloaded_and_sent_to_ai(self):
        orders = 'ż' * 99000 + ' | Location: trap | Forces: trap\nFINAL ORDERS'
        attachment = NS(filename='plan.txt', size=len(orders.encode()), read=AsyncMock(return_value=orders.encode()))
        inter = await self.submit(attachment=attachment)
        with db.cursor() as c:
            c.execute('SELECT * FROM battle_plans')
            plan = c.fetchone()
        data = text.unpack(plan)
        self.assertEqual(data['orders_text'], orders)
        sent = inter.followup.send.call_args.kwargs
        self.assertIn(orders, sent['file'].fp.getvalue().decode())
        self.assertLessEqual(len(sent['embed'].fields[1].value), 1024)
        generate = Mock(return_value=NS(text='{}'))
        google = NS(genai=NS(Client=Mock(return_value=NS(models=NS(generate_content=generate)))))
        nation = dict(name='A', tech_json='{}')
        with patch.dict(sys.modules, {'google': google}):
            await combat._get_ai_modifier(data, data, nation, nation)
        self.assertIn(orders, generate.call_args.kwargs['contents'])
        for user, roles, allowed in [(2, [], True), (1, [], False), (999, [NS(id=10, name=config.GM_ROLE_NAME)], True)]:
            viewer = interaction(user, roles)
            await combat.CombatCog.plan_show.callback(None, viewer, plan['id'])
            args = viewer.response.send_message.call_args.kwargs
            self.assertEqual('file' in args, allowed)
            self.assertTrue(args['ephemeral'])

    async def test_oversized_and_invalid_utf8_do_not_save(self):
        for content in [b'x' * 100001, b'\xff', b'']:
            await self.submit(attachment=NS(filename='plan.txt', size=len(content), read=AsyncMock(return_value=content)))
        with db.cursor() as c:
            c.execute('SELECT COUNT(*) AS n FROM battle_plans')
            self.assertEqual(c.fetchone()['n'], 0)

    async def test_legacy_internal_separator_preserved(self):
        orders = 'First | Location: decoy | Forces: decoy\nLast'
        old = dict(orders_text=orders+' | Location: Harbor | Forces: Infantry', provinces_json='["Harbor"]')
        self.assertEqual(text.unpack(old)['orders_text'], orders)

    async def test_inline_long_orders_fit_embed(self):
        inter = await self.submit('x' * 6000)
        sent = inter.response.send_message.call_args.kwargs
        self.assertTrue(all(len(f.value) <= 1024 for f in sent['embed'].fields))
        self.assertIn('x' * 6000, sent['file'].fp.getvalue().decode())
