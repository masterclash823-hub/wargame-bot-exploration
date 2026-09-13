import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch

from test_regressions import DatabaseFixture,interaction
import db
import i18n
import technology as tech
import technology_ui as ui
from cogs.economy import _seed_buildings,EconomyCog
from economy_engine import forecast,run_tick,run_month,snapshot,military_cost
from economy_services import build


class ResearchFixture(DatabaseFixture):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            c.execute("UPDATE nations SET stability=100,treasury=1000,resources_json='{}' WHERE id=1")
            c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,terrain,buildings_json) VALUES(10,1,2000,'plains','[\"farm\"]')")
            self.pid=c.lastrowid

    def rows(self,sql,params=()):
        with db.cursor() as c:c.execute(sql,params);return c.fetchall()

    def nation(self):return self.rows('SELECT * FROM nations WHERE id=1')[0]
    def active(self):return next(iter(self.rows('SELECT * FROM research_projects WHERE nation_id=1')),None)

    def resources(self,**values):
        with db.cursor() as c:c.execute('UPDATE nations SET resources_json=? WHERE id=1',(json.dumps(values),))

    def levels(self,**values):
        with db.cursor() as c:c.execute('UPDATE nations SET tech_json=? WHERE id=1',(json.dumps(values),))

    def unlock(self,*codes):
        with db.cursor() as c:
            for code in codes:
                c.execute('INSERT INTO research_discoveries(nation_id,code,completed_month) VALUES(1,?,12)',(code,))


class ResearchTests(ResearchFixture,unittest.TestCase):
    def test_free_science_preserves_legacy_levels_and_preview_does_not_write(self):
        self.levels(economy=7.4,land=10,naval=4.8,colonial=6)
        self.resources(algae=12,universal_knowledge=7,wood=500)
        before=self.nation();report=forecast(1)
        self.assertEqual(self.nation(),before)
        self.assertEqual(report['resources']['universal_knowledge'],8)
        run_tick()
        self.assertEqual(json.loads(self.nation()['resources_json'])['universal_knowledge'],8)
        self.assertEqual(self.nation()['tech_json'],before['tech_json'])
        self.assertEqual(json.loads(self.nation()['resources_json'])['algae'],12)

    def test_first_discovery_requires_no_gold_or_university_and_completes_goal(self):
        from world_service import choose_goal
        choose_goal(1,1,'scholarship')
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=0 WHERE id=1')
        tech.start(1,1,'crop_rotation')
        run_tick(2)
        self.assertEqual(self.active()['knowledge'],2)
        self.assertFalse(self.rows('SELECT * FROM research_discoveries'))
        run_tick()
        self.assertIsNone(self.active())
        self.assertEqual(json.loads(self.nation()['tech_json'])['economy'],3.5)
        self.assertEqual(self.rows('SELECT prestige FROM nation_profiles WHERE nation_id=1')[0]['prestige'],10)
        from cogs.world import goal_state
        self.assertIn('1/1',goal_state(1)[0].fields[3].value)
        report=forecast(1)
        self.assertEqual(report['production']['food'],22)
        self.assertEqual(self.rows("SELECT source FROM nation_history WHERE source='research_private'")[0]['source'],'research_private')

    def test_stockpile_cannot_bypass_duration_pause_keeps_progress(self):
        self.levels(economy=5);self.resources(universal_knowledge=100)
        tech.start(1,1,'scientific_method');run_tick()
        self.assertEqual(self.active()['knowledge'],16)
        token=self.active()['token']
        tech.manage(1,1,token,'pause');run_tick(2)
        self.assertEqual(self.active()['months'],1)
        tech.manage(1,1,token,'resume');run_tick(4)
        self.assertIsNotNone(self.active())
        run_tick();self.assertIsNone(self.active())
        self.assertEqual(json.loads(self.nation()['tech_json'])['economy'],6)

    def test_concurrent_start_charges_algae_once_and_rejects_unauthorized_owner(self):
        self.levels(economy=6,land=6);self.resources(algae=4)
        with self.assertRaises(ValueError):tech.start(1,2,'algae_economy')
        def attempt(code):
            try:tech.start(1,1,code);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(attempt,['algae_economy','algae_land'])),[False,True])
        self.assertEqual(json.loads(self.nation()['resources_json'])['algae'],0)
        self.assertEqual(len(self.rows('SELECT * FROM research_projects')),1)

    def test_failed_tick_and_forecast_roll_back_completion_and_notifications(self):
        tech.start(1,1,'army_logistics');run_tick(2)
        before=self.nation();active=self.active()
        self.assertEqual(forecast(1)['research_completed'],'army_logistics')
        self.assertEqual(self.active(),active)
        with patch('cogs.colonialism.tick_colonies',side_effect=RuntimeError('stop')):
            with self.assertRaises(RuntimeError):run_tick()
        self.assertEqual(self.nation(),before)
        self.assertFalse(self.rows('SELECT * FROM research_discoveries'))
        self.assertEqual(self.active(),active)
        run_tick();self.assertEqual(len(self.rows('SELECT * FROM research_discoveries')),1)

    def test_concurrent_month_funds_and_progresses_once(self):
        self.unlock('algae_economy');self.resources(algae=2)
        tech.set_program(1,1,'economy',True)
        tech.start(1,1,'crop_rotation')
        with ThreadPoolExecutor(2) as pool:
            result=list(pool.map(lambda _:run_month(expected_month=12),range(2)))
        self.assertEqual(sum(r is not None for r in result),1)
        self.assertEqual(self.active()['months'],1)
        self.assertEqual(json.loads(self.nation()['resources_json'])['algae'],1)

    def test_stale_abandon_cannot_delete_restarted_project(self):
        tech.start(1,1,'crop_rotation');token=self.active()['token']
        tech.manage(1,1,token,'abandon');tech.start(1,1,'crop_rotation')
        with self.assertRaises(ValueError):tech.manage(1,1,token,'abandon')
        self.assertIsNotNone(self.active())

    def test_repeatable_research_reaches_cap_without_duplicate_bonuses(self):
        self.levels(land=9.5);self.resources(universal_knowledge=100)
        tech.start(1,1,'mastery_land');run_tick(6)
        self.assertEqual(json.loads(self.nation()['tech_json'])['land'],10)
        with self.assertRaises(ValueError):tech.start(1,1,'mastery_land')
        with db.cursor() as c:self.assertEqual(tech.bonuses(c,1),{})

    def test_university_output_speeds_actual_completion(self):
        self.levels(economy=4)
        with db.cursor() as c:
            c.execute('UPDATE provinces SET buildings_json=? WHERE id=?',('["farm","university"]',self.pid))
        tech.start(1,1,'public_accounts');run_tick(4)
        self.assertIsNone(self.active())
        self.assertEqual(self.rows('SELECT code FROM research_discoveries')[0]['code'],'public_accounts')


class AlgaeTests(ResearchFixture,unittest.TestCase):
    def test_deposits_are_few_stable_and_do_not_reroll_on_resync(self):
        from cogs.provinces import _upsert_provinces
        incoming=[dict(cell_id=cid,name='',biome='Wetland',terrain='wetland',resources={},pop=2000,neighbors=[]) for cid in range(100,1101)]
        _upsert_provinces(incoming,resync=True)
        self.assertEqual(self.rows('SELECT * FROM algae_sites'),[])
        for cid in range(100,105):tech.set_deposit(cid,True)
        first=self.rows('SELECT * FROM algae_sites ORDER BY province_id')
        self.assertEqual(len(first),5)
        db.init_db();_upsert_provinces(list(reversed(incoming)),resync=True)
        self.assertEqual(self.rows('SELECT * FROM algae_sites ORDER BY province_id'),first)
        removed=self.rows('SELECT azgaar_cell_id FROM provinces WHERE id=?',(first[0]['province_id'],))[0]['azgaar_cell_id']
        _upsert_provinces([p for p in incoming if p['cell_id']!=removed],resync=True)
        self.assertEqual(len(self.rows('SELECT * FROM algae_sites')),5)
        self.assertEqual(len(self.rows('SELECT * FROM algae_sites a JOIN provinces p ON p.id=a.province_id WHERE p.active=1')),4)

    def test_farm_requires_deposit_and_technology_then_produces_slowly(self):
        self.resources(wood=1000);self.levels(economy=6)
        before=self.nation()
        with self.assertRaisesRegex(ValueError,'rare deposit'):build(1,10,'algae_farm')
        self.assertEqual(self.nation(),before)
        with db.cursor() as c:c.execute('INSERT INTO algae_sites(province_id) VALUES(?)',(self.pid,))
        self.levels(economy=2.99)
        with self.assertRaises(ValueError):build(1,10,'algae_farm')
        self.levels(economy=6);build(1,10,'algae_farm')
        r=forecast(1)
        self.assertEqual(r['production']['algae'],.5)
        self.assertEqual(r['upkeep'],10)  # Farm 2 + extractor 8.
        with db.cursor() as c:c.execute('UPDATE provinces SET population=500 WHERE id=?',(self.pid,))
        self.assertEqual(forecast(1)['production']['algae'],0)

    def test_legacy_farms_and_base_yields_cannot_bypass_sites(self):
        self.levels(economy=6);self.resources(algae=5)
        with db.cursor() as c:
            c.execute('UPDATE provinces SET buildings_json=?,base_resources_json=? WHERE id=?',('["farm","algae_farm"]','{"algae":99}',self.pid))
            c.execute("INSERT INTO megaprojects(nation_id,name,status,effect_json) VALUES(1,'Legacy','complete',?)",('{"resources_per_tick":{"algae":50}}',))
        r=forecast(1)
        self.assertEqual(r['resources']['algae'],5)
        self.assertEqual(r['upkeep'],2)
        self.assertTrue(any(s.get('blocked') for s in r['staffing']))
        run_tick()
        self.assertIn('algae_farm',self.rows('SELECT buildings_json FROM provinces WHERE id=?',(self.pid,))[0]['buildings_json'])

    def test_all_programs_need_opt_in_supplies_and_recover_after_shortage(self):
        self.unlock(*('algae_'+k for k in tech.CATEGORIES))
        self.resources(algae=4)
        run_tick()
        with db.cursor() as c:self.assertEqual(tech.bonuses(c,1),{})
        for k in tech.CATEGORIES:tech.set_program(1,1,k,True)
        r=forecast(1)
        self.assertEqual(sum(p['funded'] for p in r['algae_programs']),4)
        self.assertEqual(r['production']['food'],28)
        self.assertEqual(json.loads(self.nation()['resources_json'])['algae'],4)
        run_tick()
        with db.cursor() as c:
            effects=tech.bonuses(c,1)
            self.assertEqual(effects['land_attack'],.20)
            self.assertEqual(effects['naval_defense'],.20)
            self.assertEqual(effects['cargo'],.25)
            self.assertEqual(effects['colony_cost'],-.30)
        run_tick()
        with db.cursor() as c:self.assertEqual(tech.bonuses(c,1),{})
        self.resources(algae=1);run_tick()
        funded=self.rows('SELECT category FROM algae_programs WHERE funded=1')
        self.assertEqual(funded,[{'category':'economy'}])
        self.assertEqual(len(self.rows('SELECT * FROM research_discoveries')),4)

    def test_research_algae_payment_is_upfront_and_not_refunded_on_abandonment(self):
        self.levels(economy=6);self.resources(algae=3)
        with self.assertRaises(ValueError):tech.start(1,1,'algae_economy')
        self.resources(algae=4,universal_knowledge=10)
        tech.start(1,1,'algae_economy');run_tick()
        tech.manage(1,1,self.active()['token'],'abandon')
        self.assertEqual(json.loads(self.nation()['resources_json'])['algae'],0)
        self.assertEqual(json.loads(self.nation()['resources_json'])['universal_knowledge'],0)
        self.assertFalse(self.rows('SELECT * FROM research_discoveries'))

    def test_land_and_naval_power_use_their_own_level_and_funded_bonuses(self):
        from battle_resolution import _power,force_snapshot
        self.levels(land=3,naval=9)
        self.unlock('army_logistics','algae_land','algae_naval','cargo_design')
        self.resources(algae=2)
        for k in ('land','naval'):tech.set_program(1,1,k,True)
        with db.atomic() as c:
            tech.fund_programs(c,1)
            for kind,hull in [('unit','militia'),('ship','sloop')]:
                c.execute('INSERT INTO blueprints(nation_id,type,name,hull,stats_json) VALUES(1,?,?,?,?)',(kind,kind,hull,'{"attack":100,"defense":100,"hp":1000,"cargo":10}'))
                bp=c.lastrowid
                c.execute('INSERT INTO military_units(nation_id,blueprint_id,quantity) VALUES(1,?,1)',(bp,))
                uid=c.lastrowid
                plan={'forces_json':json.dumps([{'unit_id':uid,'qty':1}])}
                n=self.nation();attack,defense,_=_power(c,plan,n)
                self.assertAlmostEqual(attack,100*(1+(9 if kind=='ship' else 3)/20)*1.2)
                self.assertAlmostEqual(defense,attack)
                force=force_snapshot(plan,n)[0]
                self.assertIn(('naval' if kind=='ship' else 'land')+'_attack',force['research_bonuses'])
            self.assertAlmostEqual(military_cost(c,1)[0],1*.95+3)
        from cogs.colonialism import _total_cargo
        self.assertAlmostEqual(_total_cargo(1),13.5)

    def test_colony_requirements_investment_and_auto_advance_use_same_bonuses(self):
        from cogs.colonialism import invest_in_colony
        self.levels(colonial=6);self.resources(algae=1,food=100)
        self.unlock('colonial_admin','colonial_bureau','surveyors','settler_medicine','algae_colonial')
        tech.set_program(1,1,'colonial',True)
        with db.atomic() as c:
            tech.fund_programs(c,1)
            c.execute("INSERT INTO colonies(nation_id,province_id,name,status,months_in_status) VALUES(1,?,'Colony','outpost',4)",(self.pid,))
        self.assertEqual(tech.colony_requirements(1,'outpost'),(150,4))
        result=invest_in_colony(1,10,500)
        self.assertEqual(result['applied'],150)
        self.assertEqual(result['advanced_to'],'settlement')
        self.assertEqual(self.nation()['treasury'],850)


class ResearchUITests(ResearchFixture,unittest.IsolatedAsyncioTestCase):
    async def test_deferred_command_and_component_use_followup_and_original_edit(self):
        from cogs.tech import TechCog
        def deferred_request():
            request=interaction(1);done=[False]
            async def defer(**kwargs):done[0]=True
            request.response.defer=AsyncMock(side_effect=defer)
            request.response.is_done=lambda:done[0]
            request.edit_original_response=AsyncMock()
            return request
        request=deferred_request()
        await TechCog.tech_research.callback(None,request,'crop_rotation')
        request.response.send_message.assert_not_awaited()
        view=request.followup.send.call_args.kwargs['view']
        click=deferred_request();await view.confirm.callback(click)
        click.response.edit_message.assert_not_awaited()
        click.edit_original_response.assert_awaited_once()
        self.assertEqual(self.active()['code'],'crop_rotation')

    async def test_algae_marker_export_uses_deposits_instead_of_raw_yield(self):
        from cogs.provinces import ProvincesCog
        import config
        with db.cursor() as c:
            c.execute('INSERT INTO algae_sites(province_id) VALUES(?)',(self.pid,))
            c.execute('INSERT INTO provinces(azgaar_cell_id,base_resources_json) VALUES(11,?)',('{"algae":99}',))
        request=interaction(999,[NS(id=99,name=config.GM_ROLE_NAME)])
        await ProvincesCog.map_export_markers.callback(ProvincesCog(None),request)
        file=request.followup.send.call_args.kwargs['file']
        script=file.fp.read().decode()
        self.assertIn('cell:10,type:"algae"',script)
        self.assertNotIn('cell:11,type:"algae"',script)
        file.close()

    async def test_views_fit_both_languages_and_open_without_spending(self):
        before=self.nation()
        for lang in ('pl','en'):
            i18n.set_user_language(1,lang)
            with i18n.using_language(lang):
                for code in [None,*tech.PROJECTS]:
                    view=ui.ResearchView(1,1,code)
                    e=ui.detail(1,code) if code else ui.overview(1)
                    self.assertLessEqual(len(e),6000)
                    self.assertTrue(all(len(f.value)<=1024 for f in e.fields))
                    self.assertTrue(all(len(b.label)<=80 for b in view.children))
                view=ui.ResearchView(1,1,catalogue=True)
                self.assertLessEqual(len(view.children[0].options),25)
                request=interaction(1);await ui.show_programs(request,1)
                self.assertTrue(request.response.send_message.call_args.kwargs['ephemeral'])
        self.assertEqual(self.nation(),before)
        self.assertFalse(self.rows('SELECT * FROM research_projects'))

    async def test_project_confirmation_rechecks_owner_and_duplicate(self):
        from cogs.tech import TechCog
        i18n.set_user_language(1,'pl')
        request=interaction(1)
        await TechCog.tech_research.callback(None,request,'crop_rotation')
        view=request.response.send_message.call_args.kwargs['view']
        await view.confirm.callback(interaction(2))
        self.assertIsNone(self.active())
        i18n.set_user_language(1,'en')
        confirmed=interaction(1);await view.confirm.callback(confirmed)
        self.assertIn('Research',confirmed.response.edit_message.call_args.kwargs['embed'].title)
        await view.confirm.callback(interaction(1))
        self.assertEqual(len(self.rows('SELECT * FROM research_projects')),1)

    async def test_algae_locations_available_without_nation_and_include_owner_and_id(self):
        from cogs.tech import TechCog
        with db.cursor() as c:c.execute('INSERT INTO algae_sites(province_id) VALUES(?)',(self.pid,))
        i18n.set_user_language(99,'pl');request=interaction(99)
        await TechCog.algae_locations.callback(None,request)
        e=request.response.send_message.call_args.kwargs['embed']
        self.assertIn('#10',e.fields[0].name)
        self.assertIn('A',e.fields[0].value)
        self.assertIn('Stanowiska',e.title)

    async def test_notification_uses_current_owner_language_and_sends_once(self):
        from cogs.tech import TechCog
        self.unlock('crop_rotation');i18n.set_user_language(1,'pl')
        user=NS(send=AsyncMock());cog=NS(bot=NS(get_user=lambda _:user))
        await TechCog.notifications.coro(cog)
        await TechCog.notifications.coro(cog)
        self.assertEqual(user.send.await_count,1)
        self.assertIn('Trójpolówka',user.send.call_args.kwargs['embed'].description)

    async def test_private_history_and_project_command_rename(self):
        from cogs.nations import NationCog
        tech.start(1,1,'crop_rotation');run_tick(3)
        request=interaction(2)
        await NationCog.history.callback(None,request,'A',1)
        e=request.response.send_message.call_args.kwargs['embed']
        self.assertNotIn('Crop rotation',str(e.to_dict()))
        own=interaction(1);await NationCog.history.callback(None,own,'A',1)
        self.assertIn('Crop rotation',str(own.response.send_message.call_args.kwargs['embed'].to_dict()))
        self.assertEqual(EconomyCog.mp_grp.name,'project')
        self.assertEqual(EconomyCog.mp_approve.name,'project_approve')
        self.assertEqual(EconomyCog.mp_advance.name,'project_advance')
