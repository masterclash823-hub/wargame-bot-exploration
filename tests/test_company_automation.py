import json
import unittest
from unittest.mock import patch, AsyncMock
import db
import i18n
import companies as co
from cogs.companies import budget, show
from cogs.economy import _seed_buildings
from economy_engine import run_month, forecast
from test_regressions import DatabaseFixture, interaction


class AutomationTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            c.execute("UPDATE nations SET treasury=5000,stability=100,tech_json=?,resources_json=?",
                      (json.dumps({'economy':4,'land':4,'naval':4,'colonial':4}),
                       json.dumps({'food':300,'wood':2000,'stone':2000,'iron':2000,'coal':20,'copper':20})))
            for cell,nid in ((10,1),(11,1),(20,2)):
                c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,terrain,population) VALUES(?,?,'plains',2000)",(cell,nid))

    def state(self):
        with db.cursor() as c:return co.state(c,1)

    def setup_company(self,amount=200,types=('farm',)):
        co.create(1,1,'Growers','Farm and develop our nation',list(types))
        co.configure(1,1,self.state()['version'],amount,'auto')

    def buildings(self,cell):
        with db.cursor() as c:
            c.execute('SELECT buildings_json FROM provinces WHERE azgaar_cell_id=?',(cell,))
            return json.loads(c.fetchone()['buildings_json'])

    async def test_domestic_auto_makes_multiple_investments_and_then_upgrades(self):
        self.setup_company()
        # Even an accepted foreign concession does not expand the automatic scope.
        grant=co.propose_concession(1,1,self.state()['version'],2,[20],['farm'],70,12,0)
        co.respond_concession(grant,2,'accept')
        before=self.balances()
        initial=self.state()
        preview=forecast(1)
        self.assertEqual(before,self.balances())
        self.assertEqual(initial,self.state())
        self.assertEqual(len(preview['company']['investments']),2)
        run_month()
        self.assertEqual(self.buildings(10),['farm'])
        self.assertEqual(self.buildings(11),['farm'])
        self.assertEqual(self.buildings(20),[])
        self.assertAlmostEqual(self.state()['spent'],180)
        with self.assertRaises(ValueError):co.manual_invest(1,1,self.state()['version'],10,'farm')
        run_month()
        investments=self.state()['report']['investments']
        self.assertEqual(len(investments),1)
        self.assertEqual(investments[0]['level'],2)
        self.assertAlmostEqual(self.state()['spent'],135)

    async def test_edit_toggle_pause_cannot_refill_budget_and_month_resets_it(self):
        self.setup_company(180)
        co.configure(1,1,self.state()['version'],180,'off')
        co.manual_invest(1,1,self.state()['version'],10,'farm')
        for mode in ('auto','off','auto'):
            co.configure(1,1,self.state()['version'],180,mode)
            self.assertEqual(self.state()['spent'],90)
        co.pause(1,1,self.state()['version'])
        co.pause(1,1,self.state()['version'])
        co.configure(1,1,self.state()['version'],80,'off')
        with self.assertRaises(ValueError):co.manual_invest(1,1,self.state()['version'],11,'farm')
        co.configure(1,1,self.state()['version'],180,'off')
        co.manual_invest(1,1,self.state()['version'],11,'farm')
        self.assertEqual(self.state()['spent'],180)
        with self.assertRaises(ValueError):co.manual_invest(1,1,self.state()['version'],10,'farm')
        run_month()
        self.assertEqual(self.state()['spent'],0)
        with db.cursor() as c:self.assertEqual(co.remaining_budget(self.state(),co.month_index(c)),180)
        co.manual_invest(1,1,self.state()['version'],10,'farm')
        self.assertEqual(self.state()['spent'],135)

    async def test_food_gets_priority_without_player_choosing_resource(self):
        self.setup_company(90,('lumber_camp','farm'))
        with db.cursor() as c:c.execute("UPDATE nations SET resources_json=? WHERE id=1",(json.dumps({'food':0,'wood':2000,'stone':2000}),))
        run_month()
        investment=self.state()['report']['investments'][0]
        self.assertEqual(investment['building'],'farm')
        self.assertEqual(investment['reason'],'food')

    async def test_no_materials_zero_budget_and_manual_mode_do_not_spend(self):
        self.setup_company(0)
        run_month()
        self.assertEqual(self.state()['spent'],0)
        co.configure(1,1,self.state()['version'],200,'off')
        run_month()
        self.assertEqual(self.buildings(10),[])
        co.configure(1,1,self.state()['version'],200,'auto')
        with db.cursor() as c:c.execute("UPDATE nations SET resources_json=? WHERE id=1",(json.dumps({'food':300}),))
        run_month()
        self.assertEqual(self.buildings(10),[])
        self.assertEqual(self.state()['spent'],0)

    async def test_manual_worker_assignments_can_prevent_wasteful_investment(self):
        self.setup_company(200)
        with db.cursor() as c:
            c.execute("UPDATE provinces SET active=0 WHERE azgaar_cell_id=11")
            c.execute("UPDATE provinces SET buildings_json='[\"farm\"]' WHERE azgaar_cell_id=10")
            c.execute('SELECT id FROM provinces WHERE azgaar_cell_id=10')
            pid=c.fetchone()['id']
            c.execute('INSERT INTO province_labor(province_id,nation_id,allocations_json) VALUES(?,1,?)',(pid,'{"farm":0}'))
        run_month()
        self.assertEqual(self.state()['spent'],0)
        with db.cursor() as c:
            c.execute('SELECT levels_json FROM province_development WHERE province_id=?',(pid,))
            self.assertIsNone(c.fetchone())

    async def test_auto_transaction_failure_rolls_back_spending_and_all_construction(self):
        self.setup_company()
        before=self.balances()
        initial=self.state()
        with patch('company_economy.finish',side_effect=RuntimeError('test rollback')):
            with self.assertRaises(RuntimeError):run_month()
        self.assertEqual(before,self.balances())
        self.assertEqual(initial,self.state())
        self.assertEqual(self.buildings(10),[])
        run_month()
        self.assertEqual(self.state()['spent'],180)

    async def test_legacy_settings_preserve_amount_state_and_current_spending(self):
        self.setup_company()
        with db.cursor() as c:
            current=co.month_index(c)
            legacy=dict(name='Old',description='Old company',types=['farm'],cells=[10],budget=180,
                        reserves={'gold':4900},mode='upgrade',resource='food',paused=True,
                        improvements=[],version=7,last_investment=current,
                        report={'investment':{'cell':10,'building':'farm','level':1,'cost':{'gold':90}}})
            c.execute('UPDATE companies SET state_json=? WHERE nation_id=1',(json.dumps(legacy),))
        migrated=self.state()
        self.assertEqual(migrated['monthly_budget'],180)
        self.assertEqual(migrated['spent'],90)
        self.assertEqual(migrated['mode'],'auto')
        self.assertTrue(migrated['paused'])
        self.assertNotIn('cells',migrated)
        self.assertNotIn('reserves',migrated)
        co.configure(1,1,7,200,'off')
        self.assertEqual(self.state()['spent'],90)
        self.assertEqual(self.state()['settings_version'],2)

    async def test_budget_ui_has_two_modes_and_one_amount_in_both_languages(self):
        self.setup_company()
        for lang in ('pl','en'):
            i18n.set_user_language(1,lang)
            with i18n.using_language(lang):
                i=interaction(1)
                await budget(i)
                menu=i.response.send_message.call_args.kwargs['view']
                self.assertEqual({o.value for o in menu.children[0].options},{'auto','off'})
                chosen=interaction(1)
                chosen.response.send_modal=AsyncMock()
                await menu.handler(chosen,'auto')
                modal=chosen.response.send_modal.call_args.args[0]
                self.assertEqual(len(modal.children),1)
                self.assertIn('miesiąc' if lang=='pl' else 'month',modal.children[0].label)
                shown=interaction(1)
                await show(shown)
                text=shown.response.send_message.call_args.kwargs['embed'].description
                self.assertIn('Budżet na miesiąc' if lang=='pl' else 'Monthly budget',text)
                menu.stop()
                shown.response.send_message.call_args.kwargs['view'].stop()


if __name__=='__main__':
    unittest.main()
