import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import db
import i18n
import economy_reports as reports
from cogs.economy import _seed_buildings,EconomyCog
from cogs.panel import PlayerPanel
from economy_ui import EconomyControlCog,EconomyView
from economy_engine import forecast,run_tick
from economy_services import set_recurring
from trade_service import accept_trade
from test_regressions import DatabaseFixture,interaction


class ReportTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp();_seed_buildings()
        with db.cursor() as c:
            c.execute('UPDATE nations SET stability=100,tech_json=?,resources_json=? WHERE id=1',
                      ('{"economy":4}',json.dumps(dict(food=100,wood=100,coal=10,copper=10,algae=.05))))
            c.execute('INSERT INTO provinces(azgaar_cell_id,name,owner_nation_id,population,buildings_json) VALUES(10,?,1,2000,?)',
                      ('Capital','["farm","powder_mill"]'))
            self.pid=c.lastrowid

    async def test_register_retains_levels_unknown_buildings_and_inactive_provinces(self):
        with db.cursor() as c:
            c.execute('INSERT INTO province_development(province_id,levels_json) VALUES(?,?)',(self.pid,'{"farm":3}'))
            c.execute('INSERT INTO provinces(azgaar_cell_id,name,owner_nation_id,active,buildings_json) VALUES(20,?,1,0,?)',
                      ('Old colony','["legacy_building","farm","farm"]'))
            c.execute('INSERT INTO provinces(azgaar_cell_id,name,owner_nation_id,buildings_json) VALUES(30,?,2,?)',('Private B','["farm"]'))
        with i18n.using_language('pl'):rows=reports.building_inventory(1)
        self.assertEqual(len(rows),4)
        self.assertEqual(next(r for r in rows if r['cell']==10 and r['key']=='farm')['level'],3)
        self.assertTrue(any(r['key']=='legacy_building' and not r['active'] for r in rows))
        self.assertFalse(any(r['cell']==30 for r in rows))

    async def test_forecast_balance_matches_actual_tick_including_trade_projects_and_food(self):
        set_recurring(self.trade_id,1);accept_trade(self.trade_id,2,monthly=True)
        with db.cursor() as c:
            c.execute("INSERT INTO megaprojects(nation_id,name,status,effect_json) VALUES(1,'Grant','complete',?)",
                      ('{"resources_per_tick":{"wood":7,"gold":5}}',))
        before=self.balances()
        result=forecast(1);rows={r['key']:r for r in reports.resource_rows(result)}
        self.assertEqual(self.balances(),before)
        self.assertEqual(result['opening_resources'],json.loads(before[0]['resources_json']))
        self.assertEqual(result['opening_treasury'],before[0]['treasury'])
        self.assertEqual(rows['wood']['change'],-3)  # Project +7, monthly contract -10.
        self.assertEqual(rows['food']['production'],20)
        self.assertLess(rows['food']['change'],rows['food']['production'])
        self.assertEqual(rows['coal']['change'],-2)
        self.assertEqual(rows['algae']['stock'],.05)
        self.assertIn('silk',rows)  # Catalogue resource with zero stock and production.
        run_tick();after=self.balances()[0]
        resources=json.loads(after['resources_json'])
        for key,row in rows.items():
            opening=before[0]['treasury'] if key=='gold' else json.loads(before[0]['resources_json']).get(key,0)
            closing=after['treasury'] if key=='gold' else resources.get(key,0)
            self.assertAlmostEqual(row['stock'],closing)
            self.assertAlmostEqual(row['change'],closing-opening)

    async def test_large_register_paginates_and_attachment_contains_every_province(self):
        with db.cursor() as c:
            for index in range(100,220):
                c.execute('INSERT INTO provinces(azgaar_cell_id,name,owner_nation_id,buildings_json) VALUES(?,?,1,?)',
                          (index,'Province '+str(index)+'x'*50,'["farm"]'))
        request=interaction(1)
        await EconomyCog.buildings_owned.callback(None,request)
        message=request.followup.send.call_args.kwargs
        self.assertTrue(message['ephemeral'])
        view=message['view'];self.assertGreater(len(view.pages),1)
        self.assertTrue(all(len(p.description)<=4096 and len(p)<=6000 for p in view.pages))
        text=message['file'].fp.read().decode()
        for index in range(100,220):self.assertIn('#'+str(index)+' ',text)
        self.assertIn('Total: 122',text)
        await view.next.callback(request)
        self.assertEqual(view.page,1)
        self.assertTrue(await view.interaction_check(request))
        self.assertFalse(await view.interaction_check(interaction(2)))
        with db.cursor() as c:c.execute("UPDATE nations SET owner_id='99' WHERE id=1")
        self.assertFalse(await view.interaction_check(request))

    async def test_income_command_and_both_panel_buttons_are_localized_and_private(self):
        for lang in ('en','pl'):
            i18n.set_user_language(1,lang)
            cog=EconomyControlCog()
            panel=PlayerPanel(NS(get_cog=lambda key:cog if key=='EconomyControlCog' else EconomyCog(NS())),1,lang,'economy')
            self.assertLessEqual(max(item.row for item in panel.children),4)
            with i18n.using_language(lang):
                for action in ('owned_buildings','income'):
                    request=interaction(1)
                    await panel.dispatch(request,action)
                    self.assertTrue(request.followup.send.call_args.kwargs['ephemeral'])
                request=interaction(1)
                await cog.income.callback(cog,request)
                message=request.followup.send.call_args.kwargs
                text=message['file'].fp.read().decode()
                self.assertIn('0,05' if lang=='pl' else '0.05',text)
                self.assertIn('Bilans surowców' if lang=='pl' else 'Resource balance',message['embed'].title)
                dashboard=EconomyView(1,1)
                self.assertEqual(dashboard.built.row,2)
                self.assertEqual(dashboard.income.row,2)

    async def test_no_nation_and_forecast_failure_send_actionable_private_messages(self):
        request=interaction(999)
        await reports.show(request,'income')
        self.assertNotIn('file',request.followup.send.call_args.kwargs)
        request=interaction(1)
        with patch.object(reports,'forecast',side_effect=RuntimeError('secret')),self.assertLogs('root',level='ERROR'):
            await reports.show(request,'income')
        message=request.followup.send.call_args
        self.assertTrue(message.kwargs['ephemeral'])
        self.assertNotIn('secret',message.args[0])

    async def test_ownership_change_during_forecast_prevents_delivery(self):
        original=reports.forecast
        def transfer(nid):
            result=original(nid)
            with db.cursor() as c:c.execute("UPDATE nations SET owner_id='99' WHERE id=?",(nid,))
            return result
        request=interaction(1)
        with patch.object(reports,'forecast',side_effect=transfer):await reports.show(request,'income')
        self.assertNotIn('file',request.followup.send.call_args.kwargs)
        self.assertIn('ownership',request.followup.send.call_args.args[0])
