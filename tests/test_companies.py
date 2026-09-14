import json
import unittest
from unittest.mock import patch
from test_regressions import DatabaseFixture, interaction
import db
import companies as co
from cogs.economy import _seed_buildings
from cogs.panel import PlayerPanel
from cogs.companies import CompanyCog
from economy_engine import run_month, forecast
from treaty_service import set_relation


class CompanyTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            c.execute("UPDATE nations SET treasury=5000,stability=100,tech_json=?",
                      (json.dumps(dict(economy=4,land=4,naval=4,colonial=4)),))
            for nid,cell in ((1,10),(2,20)):
                c.execute('UPDATE nations SET resources_json=? WHERE id=?',
                          (json.dumps(dict(food=100,wood=2000,stone=2000,iron=2000,coal=20,copper=20)),nid))
                c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,terrain) VALUES(?,?,?,?)',
                          (cell,nid,2000,'plains'))

    def state(self,nid=1):
        with db.cursor() as c:return co.state(c,nid)

    def create(self):
        co.create(1,1,'Company','A production company',['farm','powder_mill','lumber_camp'])
        co.configure(1,1,self.state()['version'],[10],1000,{},'off')

    def host_plant(self,key):
        with db.cursor() as c:
            c.execute('UPDATE provinces SET buildings_json=? WHERE azgaar_cell_id=20',(json.dumps([key]),))
        identifier=co.propose_concession(1,1,self.state()['version'],2,[20],[key],70,12,10)
        co.respond_concession(identifier,2,'accept')
        co.assign(1,1,self.state()['version'],20,key)
        return identifier

    async def test_button_threshold_and_direct_creation_guard(self):
        for level,visible in ((3,False),(3.99,False),(4,True),(4.5,True)):
            with db.cursor() as c:c.execute('UPDATE nations SET tech_json=? WHERE id=1',(json.dumps({'economy':level}),))
            view=PlayerPanel(None,1,'en','economy')
            self.assertEqual(any(getattr(item,'label',None)=='Company' for item in view.children),visible)
            view.stop()
            if not visible:
                with self.assertRaises(ValueError):co.create(1,1,'X','Example',['farm'])
                request=interaction(1)
                await CompanyCog.company.callback(None,request)
                self.assertIn('4',request.response.send_message.call_args.args[0])
        co.create(1,1,'X','Example',['farm'])
        with self.assertRaises(ValueError):co.create(1,1,'Y','Example',['mine'])

    async def test_specialties_ownership_and_stale_budget(self):
        with self.assertRaises(ValueError):co.create(1,1,'X','Example',['farm','mine','pasture','clay_pit'])
        self.create()
        old=self.state()['version']
        co.configure(1,1,old,[10],100,{'gold':100},'expand')
        with self.assertRaises(ValueError):co.configure(1,1,old,[10],900,{},'expand')
        with db.cursor() as c:c.execute("UPDATE nations SET owner_id='99' WHERE id=1")
        with self.assertRaises(ValueError):co.pause(1,1,self.state()['version'])

    async def test_discount_reserves_and_one_monthly_investment(self):
        self.create()
        cost=co.manual_invest(1,1,self.state()['version'],10,'farm')
        self.assertAlmostEqual(cost['gold'],90)
        self.assertAlmostEqual(cost['wood'],45)
        with self.assertRaises(ValueError):co.manual_invest(1,1,self.state()['version'],10,'farm')
        co.configure(1,1,self.state()['version'],[10],1000,{'gold':10000},'upgrade')
        before=self.balances()
        with self.assertRaises(ValueError):co.manual_invest(1,1,self.state()['version'],10,'farm')
        self.assertEqual(before,self.balances())

    async def test_automation_forecast_and_rollback(self):
        self.create()
        co.configure(1,1,self.state()['version'],[10],1000,{},'expand')
        before=self.balances()
        s=self.state()
        result=forecast(1)
        self.assertEqual(before,self.balances())
        self.assertEqual(s,self.state())
        self.assertAlmostEqual(result['production']['food'],21)
        run_month()
        with db.cursor() as c:
            c.execute('SELECT buildings_json FROM provinces WHERE azgaar_cell_id=10')
            self.assertEqual(json.loads(c.fetchone()['buildings_json']),['farm'])
        with self.assertRaises(ValueError):co.manual_invest(1,1,self.state()['version'],10,'farm')

    async def test_foreign_permission_split_and_upkeep(self):
        self.create()
        with self.assertRaises(ValueError):co.manual_invest(1,1,self.state()['version'],20,'farm')
        identifier=self.host_plant('farm')
        with self.assertRaises(ValueError):co.respond_concession(identifier,2,'accept')
        run_month()
        with db.cursor() as c:
            c.execute('SELECT report_json FROM economy_months')
            r=json.loads(c.fetchone()['report_json'])
        self.assertAlmostEqual(r['2']['production']['food'],6.3)
        self.assertAlmostEqual(r['1']['company']['received']['food'],14.7)
        self.assertAlmostEqual(r['2']['upkeep'],0)
        self.assertAlmostEqual(r['1']['company']['costs']['gold'],2)

    async def test_foreign_inputs_are_not_taken_from_host(self):
        self.create()
        self.host_plant('powder_mill')
        with db.cursor() as c:
            c.execute('UPDATE nations SET resources_json=? WHERE id=1',(json.dumps(dict(food=100,coal=1,copper=20)),))
        run_month()
        with db.cursor() as c:
            c.execute('SELECT resources_json FROM nations WHERE id=2')
            resources=json.loads(c.fetchone()['resources_json'])
        self.assertEqual(resources['coal'],20)
        self.assertEqual(resources.get('gunpowder',0),0)
        self.assertEqual(self.state()['report']['costs'],{})

    async def test_prepaid_inputs_are_refunded_when_unstaffed(self):
        self.create()
        self.host_plant('powder_mill')
        with db.cursor() as c:
            c.execute('SELECT id FROM provinces WHERE azgaar_cell_id=20')
            pid=c.fetchone()['id']
            c.execute('INSERT INTO province_labor(province_id,nation_id,allocations_json) VALUES(?,?,?)',
                      (pid,2,'{"powder_mill":0}'))
        run_month()
        received=self.state()['report']['received']
        self.assertAlmostEqual(received['coal'],2)
        self.assertAlmostEqual(received['copper'],1)
        self.assertAlmostEqual(received['gold'],3.75)
        self.assertEqual(received.get('gunpowder',0),0)

    async def test_war_suspends_concessions_and_termination_is_once(self):
        self.create()
        identifier=self.host_plant('farm')
        with db.cursor() as c:set_relation(c,1,2,'war')
        run_month()
        self.assertEqual(self.state()['report']['received'],{})
        with db.cursor() as c:set_relation(c,1,2,'neutral')
        before=self.balances()
        co.respond_concession(identifier,2,'end')
        after=self.balances()
        self.assertAlmostEqual(after[0]['treasury']-before[0]['treasury'],10)
        self.assertAlmostEqual(after[1]['treasury']-before[1]['treasury'],-10)
        with self.assertRaises(ValueError):co.respond_concession(identifier,2,'end')

    async def test_improvement_requires_gm_then_player_and_time(self):
        self.create()
        co.improvement(1,1,self.state()['version'],'farm','production','We train supervisors to reduce harvesting delays.')
        identifier=self.state()['improvements'][0]['id']
        with self.assertRaises(ValueError):co.start_improvement(1,1,self.state()['version'],identifier,True)
        request=interaction(1)
        with patch('cogs.companies.gm_only',return_value=False):
            await CompanyCog.company_review.callback(None,request)
        self.assertEqual(self.state()['improvements'][0]['status'],'proposed')
        co.review(1,identifier,True,99)
        co.start_improvement(1,1,self.state()['version'],identifier,True)
        run_month()
        self.assertEqual(self.state()['improvements'][0]['status'],'running')
        run_month()
        self.assertEqual(self.state()['improvements'][0]['status'],'complete')
        with db.cursor() as c:
            c.execute('SELECT * FROM nations WHERE id=1')
            self.assertAlmostEqual(co.bonus(c.fetchone(),self.state(),'farm')['production'],.10)

    async def test_algae_efficiency_and_technology_caps(self):
        self.create()
        s=self.state()
        s['improvements']=[dict(status='complete',building='farm',kind=k,amount=.5) for k in co.KINDS]
        for tech,production,construction,savings in ((4,.15,.15,.10),(6,.25,.20,.15),(8,.35,.25,.20)):
            n={'tech_json':json.dumps({'economy':tech})}
            b=co.bonus(n,s,'farm')
            self.assertAlmostEqual(b['production'],production)
            self.assertAlmostEqual(b['construction'],construction)
            self.assertAlmostEqual(b['inputs'],savings)
            self.assertAlmostEqual(b['workers'],savings)
            algae=co.bonus(n,s,'algae_farm')
            self.assertEqual((algae['production'],algae['inputs'],algae['workers']),(0,0,0))
