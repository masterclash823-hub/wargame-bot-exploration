import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock,patch

from test_regressions import interaction
import db
import i18n
import technology as tech
import military_service as military
import labor
from economy_engine import forecast,run_tick,military_cost
from test_technology import ResearchFixture


class AlgaeExtensionTests(ResearchFixture,unittest.TestCase):
    def test_era_uses_all_fields_and_discoveries_not_calendar(self):
        self.assertEqual(tech.historical_year(self.nation(),{})[0],1550)
        self.levels(land=10,economy=3,naval=3,colonial=3)
        year,fields=tech.historical_year(self.nation(),{})
        self.assertEqual(fields['land'],1800)
        self.assertLess(year,1700)
        with db.cursor() as c:c.execute("INSERT INTO game_config(key,value) VALUES('current_year','2200')")
        self.assertEqual(tech.historical_year(self.nation(),{})[0],year)
        self.levels(economy=3,land=3,naval=3,colonial=3)
        self.assertEqual(tech.historical_year(self.nation(),{'algae_economy':{'completions':1}})[0],1550)
        self.assertEqual(tech.historical_year(self.nation(),{'scientific_method':{'completions':1}})[1]['economy'],1700)

    def test_deposit_cap_validation_and_removal_preserve_inventory(self):
        with db.cursor() as c:
            for cell in range(11,16):c.execute('INSERT INTO provinces(azgaar_cell_id) VALUES(?)',(cell,))
            c.execute("INSERT INTO provinces(azgaar_cell_id,terrain) VALUES(20,'water')")
        for cell in range(10,15):tech.set_deposit(cell,True)
        before=self.nation()
        for cell in (10,15,20,999):
            with self.assertRaises(ValueError):tech.set_deposit(cell,True)
        tech.set_deposit(10,False)
        db.init_db()
        self.assertEqual(self.nation(),before)
        self.assertEqual(len(self.rows('SELECT * FROM algae_sites')),4)
        with self.assertRaises(ValueError):tech.set_deposit(10,False)
        tech.set_deposit(15,True)

    def test_trace_gathering_requires_own_active_deposit_and_tech(self):
        self.resources(wood=100)
        before=self.nation()
        with self.assertRaises(ValueError):tech.gather(1,1)
        self.assertEqual(self.nation(),before)
        tech.set_deposit(10,True);self.levels(economy=2)
        with self.assertRaises(ValueError):tech.gather(1,1)
        self.levels(economy=3)
        self.assertEqual(tech.gather(1,1),.05)
        self.assertEqual(self.nation()['treasury'],900)
        self.assertEqual(json.loads(self.nation()['resources_json'])['wood'],90)
        run_tick();tech.set_deposit(10,False)
        with self.assertRaises(ValueError):tech.gather(1,1)
        tech.set_deposit(10,True)
        with db.cursor() as c:c.execute('UPDATE provinces SET owner_nation_id=2 WHERE id=?',(self.pid,))
        with self.assertRaises(ValueError):tech.gather(1,1)

    def test_gathering_concurrency_cooldown_and_forecast_rollback(self):
        tech.set_deposit(10,True);self.resources(wood=100)
        def attempt(_):
            try:tech.gather(1,1);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(attempt,range(2))),1)
        before=self.nation();forecast(1)
        self.assertEqual(self.nation(),before)
        self.assertFalse(attempt(0))
        run_tick();self.assertTrue(attempt(0))
        self.assertEqual(json.loads(self.nation()['resources_json'])['algae'],.1)

    def test_failed_gather_never_charges_or_consumes_cooldown(self):
        tech.set_deposit(10,True);self.resources(wood=9)
        before=self.nation()
        with self.assertRaises(ValueError):tech.gather(1,1)
        self.assertEqual(self.nation(),before)
        self.assertFalse(self.rows('SELECT * FROM algae_gathering'))
        self.resources(wood=100)
        with self.assertRaises(ValueError):tech.gather(1,2)
        self.assertFalse(self.rows('SELECT * FROM algae_gathering'))

    def test_removing_site_stops_farm_and_traces_without_destroying_farm(self):
        tech.set_deposit(10,True);self.levels(economy=6)
        with db.cursor() as c:c.execute('UPDATE provinces SET buildings_json=? WHERE id=?',('["farm","algae_farm"]',self.pid))
        self.assertEqual(forecast(1)['production']['algae'],.5)
        tech.set_deposit(10,False)
        r=forecast(1)
        self.assertNotIn('algae',r['production'])
        self.assertEqual(next(s for s in r['staffing'] if s['building']=='algae_farm')['workers'],0)
        self.assertIn('algae_farm',self.rows('SELECT buildings_json FROM provinces WHERE id=?',(self.pid,))[0]['buildings_json'])


class LaborTests(ResearchFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        with db.cursor() as c:c.execute('UPDATE provinces SET population=500,buildings_json=? WHERE id=?',('["farm","university"]',self.pid))

    def test_manual_assignments_change_real_production_and_food(self):
        auto=forecast(1)
        labor.set_assignment(1,1,10,'university',200)
        before=self.nation();manual=forecast(1)
        self.assertEqual(self.nation(),before)
        self.assertEqual(auto['production']['universal_knowledge'],0)
        self.assertGreater(manual['production']['universal_knowledge'],0)
        self.assertGreater(manual['food_shortage'],0)
        self.assertEqual(sum(s['workers'] for s in manual['staffing']),200)
        run_tick()
        settled=json.loads(self.rows('SELECT report_json FROM economy_months')[0]['report_json'])['1']
        self.assertEqual(settled['staffing'],manual['staffing'])
        self.assertEqual(settled['production'],manual['production'])

    def test_manual_zero_and_reset_restore_food_first(self):
        labor.set_assignment(1,1,10,'farm',0)
        manual=forecast(1)
        self.assertEqual(next(s for s in manual['staffing'] if s['building']=='farm')['workers'],0)
        self.assertGreater(manual['production']['universal_knowledge'],0)
        labor.set_assignment(1,1,10,'farm',None)
        self.assertEqual(forecast(1)['production']['universal_knowledge'],0)
        labor.set_assignment(1,1,10,'university',200)
        labor.set_assignment(1,1,10)
        self.assertEqual(forecast(1)['production']['universal_knowledge'],0)

    def test_population_decline_scales_reservations_and_ownership_resets_them(self):
        labor.set_assignment(1,1,10,'university',200)
        with db.cursor() as c:c.execute('UPDATE provinces SET population=250 WHERE id=?',(self.pid,))
        r=forecast(1);s=next(s for s in r['staffing'] if s['building']=='university')
        self.assertTrue(s['scaled']);self.assertEqual(s['workers'],100)
        with db.cursor() as c:c.execute('UPDATE provinces SET owner_nation_id=2 WHERE id=?',(self.pid,))
        self.assertFalse(any(s.get('manual') for s in forecast(2)['staffing']))
        with self.assertRaises(ValueError):labor.set_assignment(1,1,10,'farm',0)

    def test_invalid_allocation_and_parallel_overbooking_are_rejected(self):
        for value in (-1,1.5,True,401):
            with self.assertRaises(ValueError):labor.set_assignment(1,1,10,'university',value)
        with self.assertRaises(ValueError):labor.set_assignment(1,2,10,'farm',0)
        with self.assertRaises(ValueError):labor.set_assignment(1,1,10,'mine',100)
        def allocate(key):
            try:labor.set_assignment(1,1,10,key,200);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(allocate,['farm','university'])),1)
        self.assertEqual(sum(f['workers'] for f in forecast(1)['staffing']),200)


class EliteTests(ResearchFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.levels(land=6,naval=6)
        self.resources(**dict.fromkeys(('wood','algae','iron','horses','gunpowder','tar','cloth'),10000))
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=100000 WHERE id=1')

    def blueprint(self,key):
        from cogs.military import HULLS,_ship_stats,_unit_stats
        ship=key in HULLS;modules=['light_cannon'] if ship else []
        stats=_ship_stats(key,modules) if ship else _unit_stats(key)
        return db.insert_returning_id('INSERT INTO blueprints(nation_id,type,name,hull,components_json,stats_json) VALUES(?,?,?,?,?,?)',
                                      (1,'ship' if ship else 'unit',key,key,json.dumps(modules),json.dumps(stats)))

    def test_elite_requires_research_support_and_exact_batch_costs(self):
        guard=self.blueprint('algae_guard');ordinary=self.blueprint('militia')
        with self.assertRaises(ValueError):military.recruit(1,1,guard,1)
        self.unlock('algae_land')
        with self.assertRaises(ValueError):military.recruit(1,1,guard,1)
        military.recruit(1,1,ordinary,20)
        before=self.nation();built=military.recruit(1,1,guard,5)
        self.assertEqual(built['cost'],dict(gold=1750,iron=100,gunpowder=50,algae=15,cloth=1))
        self.assertEqual(self.nation()['treasury'],before['treasury']-1750)
        with self.assertRaises(ValueError):military.recruit(1,1,guard,1)
        with db.cursor() as c:self.assertEqual(military_cost(c,1)[0],20*1+5*18)

    def test_split_groups_and_different_elite_designs_share_the_limit(self):
        self.unlock('algae_land')
        ordinary=self.blueprint('militia');guard=self.blueprint('algae_guard');riders=self.blueprint('algae_riders')
        for _ in range(4):military.recruit(1,1,ordinary,1)
        military.recruit(1,1,guard,1)
        with self.assertRaises(ValueError):military.recruit(1,1,riders,1)
        with self.assertRaises(ValueError):military.recruit(1,1,self.blueprint('algae_guard'),1)

    def test_fleet_has_its_own_support_and_module_costs(self):
        self.unlock('algae_naval')
        military.recruit(1,1,self.blueprint('militia'),100)
        elite=self.blueprint('algae_frigate')
        with self.assertRaises(ValueError):military.recruit(1,1,elite,1)
        military.recruit(1,1,self.blueprint('sloop'),4)
        result=military.recruit(1,1,elite,1)
        self.assertEqual(result['cost']['algae'],6)
        self.assertEqual(result['cost']['gold'],1880)  # Hull plus cannon.
        self.assertEqual(result['cost']['iron'],140)

    def test_support_cannot_be_disbanded_or_removed_via_blueprint(self):
        self.unlock('algae_land');normal=self.blueprint('militia');elite=self.blueprint('algae_guard')
        regular=military.recruit(1,1,normal,4);guard=military.recruit(1,1,elite,1)
        with self.assertRaises(ValueError):military.disband(1,1,regular['id'])
        with self.assertRaises(ValueError):military.delete_blueprint(1,1,normal)
        # Combat losses can exceed the ratio; surviving elites are never erased.
        with db.cursor() as c:c.execute('UPDATE military_units SET quantity=1 WHERE id=?',(regular['id'],))
        with self.assertRaises(ValueError):military.recruit(1,1,elite,1)
        self.assertEqual(self.rows('SELECT quantity FROM military_units WHERE id=?',(guard['id'],))[0]['quantity'],1)
        military.disband(1,1,guard['id']);military.disband(1,1,regular['id']);military.delete_blueprint(1,1,normal)

    def test_concurrent_recruitment_spends_for_one_elite_only(self):
        self.unlock('algae_land');military.recruit(1,1,self.blueprint('militia'),4)
        elite=self.blueprint('algae_guard');before=self.nation()
        def recruit(_):
            try:military.recruit(1,1,elite,1);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(recruit,range(2))),1)
        self.assertEqual(self.nation()['treasury'],before['treasury']-350)

    def test_invalid_and_foreign_recruitment_never_spends(self):
        ordinary=self.blueprint('militia');before=self.nation()
        for quantity in (-1,0,True,100001):
            with self.assertRaises(ValueError):military.recruit(1,1,ordinary,quantity)
        with self.assertRaises(ValueError):military.recruit(1,2,ordinary,1)
        with self.assertRaises(ValueError):military.recruit(1,1,ordinary,1,999)
        self.assertEqual(self.nation(),before)


class ExtensionUITests(ResearchFixture,unittest.IsolatedAsyncioTestCase):
    async def test_elite_blueprint_and_recruitment_callbacks(self):
        from cogs.military import MilitaryCog
        from discord import app_commands,Locale
        self.levels(land=6);self.unlock('algae_land')
        self.resources(algae=10,iron=100,gunpowder=100,cloth=100)
        i18n.set_user_language(1,'pl')
        for key in ('militia','algae_guard'):
            inter=interaction(1);inter.locale=Locale.american_english
            await MilitaryCog.create_unit.callback(None,inter,key,app_commands.Choice(name=key,value=key))
            self.assertNotIn('None',str(inter.response.send_message.call_args.kwargs['embed'].to_dict()))
        rows=self.rows('SELECT * FROM blueprints WHERE nation_id=1 ORDER BY id')
        self.assertEqual(len(rows),2)
        for row,qty in zip(rows,(4,1)):
            inter=interaction(1)
            await MilitaryCog.mil_build.callback(None,inter,row['id'],qty)
            inter.response.defer.assert_awaited_once()
            self.assertIn('embed',inter.followup.send.call_args.kwargs)
        self.assertEqual(self.nation()['treasury'],610)
        self.assertEqual(json.loads(self.nation()['resources_json'])['algae'],7)

    async def test_worker_modal_uses_edit_after_real_defer(self):
        import labor_ui as ui
        inter=interaction(1);inter.done=False
        inter.response.is_done=lambda:inter.done
        async def defer(**kwargs):inter.done=True
        inter.response.defer=AsyncMock(side_effect=defer)
        inter.edit_original_response=AsyncMock()
        await ui.show(inter,1,10)
        inter.response.send_message.assert_not_called()
        self.assertIn('embed',inter.followup.send.call_args.kwargs)
        inter.done=False
        modal=ui.WorkersModal(1,1,10,'farm','0');modal.number._value='0'
        await modal.on_submit(inter)
        inter.edit_original_response.assert_awaited_once()
        view=inter.edit_original_response.call_args.kwargs['view']
        with db.cursor() as c:c.execute("UPDATE nations SET owner_id='3' WHERE id=1")
        self.assertFalse(await view.interaction_check(inter))

    async def test_deposits_require_gm_role_and_keep_english_commands(self):
        from cogs.tech import TechCog
        cog=object.__new__(TechCog)
        with patch('cogs.tech.gm_only',return_value=False):
            await TechCog.deposit_add.callback(cog,interaction(1),10)
        self.assertFalse(self.rows('SELECT * FROM algae_sites'))
        with patch('cogs.tech.gm_only',return_value=True):
            await TechCog.deposit_add.callback(cog,interaction(1),10)
        self.assertEqual(len(self.rows('SELECT * FROM algae_sites')),1)
        self.assertEqual(TechCog.deposit_add.name,'deposit_add')
        self.assertEqual(TechCog.algae_gather.name,'gather')

    async def test_gather_panel_is_confirmation_and_checks_live_ownership(self):
        import technology_ui as ui
        tech.set_deposit(10,True);self.resources(wood=100)
        inter=interaction(1);before=self.nation()
        await ui.show_gather(inter,1)
        view=inter.response.send_message.call_args.kwargs['view']
        self.assertEqual(self.nation(),before)
        with db.cursor() as c:c.execute("UPDATE nations SET owner_id='3' WHERE id=1")
        await view.children[0].callback(inter)
        self.assertEqual(self.nation()['treasury'],before['treasury'])
        self.assertFalse(self.rows('SELECT * FROM algae_gathering'))

    async def test_worker_views_paginate_and_modal_changes_real_assignment(self):
        import labor_ui as ui
        with db.cursor() as c:
            for cell in range(11,41):c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id) VALUES(?,1)',(cell,))
        for lang in ('pl','en'):
            with i18n.using_language(lang):
                inter=interaction(1);await ui.show(inter,1)
                data=inter.response.send_message.call_args.kwargs
                self.assertLessEqual(len(data['embed']),6000)
                select=data['view'].children[0];self.assertEqual(len(select.options),20)
                self.assertTrue(any('Next' in (getattr(b,'label','') or '') or 'Następna' in (getattr(b,'label','') or '') for b in data['view'].children))
        modal=ui.WorkersModal(1,1,10,'farm','0');modal.number._value='0'
        inter=interaction(1);inter.edit_original_response=AsyncMock()
        await modal.on_submit(inter)
        self.assertEqual(next(s for s in forecast(1)['staffing'] if s['cell']==10)['workers'],0)

    async def test_nation_stats_show_approximate_year_in_both_languages(self):
        from cogs.nations import NationCog
        for lang in ('pl','en'):
            i18n.set_user_language(1,lang)
            inter=interaction(1);await NationCog.stats.callback(None,inter)
            embed=inter.response.send_message.call_args.kwargs['embed']
            self.assertTrue(any('1550' in f.value and 'IRL' in f.name for f in embed.fields))
            self.assertLessEqual(len(embed),6000)
