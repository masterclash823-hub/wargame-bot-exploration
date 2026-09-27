import asyncio
import json
import unittest
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace as NS

import db
import i18n
import config
from test_regressions import DatabaseFixture,interaction
from nation_access import find_nation,can_manage,set_coop
from world_service import owned,transfer_nation,create_nation
from economy_engine import forecast,run_tick
from economy_services import build
from cogs.economy import _seed_buildings
from province_admin import edit,claim


class ProvinceTests(DatabaseFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();_seed_buildings()
        with db.cursor() as c:
            c.execute("UPDATE nations SET treasury=10000,stability=100,resources_json=? WHERE id=1", ('{"wood":2000,"food":100}',))
            c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,biome,terrain,base_resources_json) VALUES(10,1,2000,'Temperate Grassland','plains',?)",('{"horses":4,"cloth":2,"iron":2}',))
            self.pid=c.lastrowid
            c.execute('INSERT INTO province_coasts VALUES(?,1)',(self.pid,))

    def test_fishing_upgrade_changes_real_output_on_tick(self):
        for level in (1,2,3):
            build(1,10,'fishing_wharf',level>1,uid=1)
            report=forecast(1)
            self.assertAlmostEqual(report['production']['food'],16*{1:1,2:1.7,3:2.4}[level])
            self.assertEqual(report['staffing'][0]['workers'],{1:200,2:300,3:400}[level])
        run_tick()
        with db.cursor() as c:
            c.execute('SELECT report_json FROM economy_months');actual=json.loads(c.fetchone()['report_json'])['1']
        self.assertAlmostEqual(actual['production']['food'],38.4)

    def test_manual_staffing_and_migration_preserve_player_and_gm_choices(self):
        build(1,10,'fishing_wharf',uid=1)
        from labor import set_assignment
        set_assignment(1,1,10,'fishing_wharf',200)
        build(1,10,'fishing_wharf',True,uid=1)
        self.assertAlmostEqual(forecast(1)['production']['food'],16*1.7*2/3)
        with db.cursor() as c:
            c.execute("DELETE FROM economy_meta WHERE key='food_taiga_v4'")
            c.execute("UPDATE building_defs SET effect_json=? WHERE key='farm'",('{"food":20}',))
            c.execute("UPDATE building_defs SET effect_json=? WHERE key='pasture'",('{"food":50}',))
            c.execute("UPDATE building_defs SET requires_terrain='forest' WHERE key='lumber_camp'")
        _seed_buildings();_seed_buildings()
        with db.cursor() as c:
            c.execute("SELECT key,effect_json FROM building_defs WHERE key IN ('farm','pasture')")
            effects={r['key']:json.loads(r['effect_json']) for r in c.fetchall()}
        self.assertEqual(effects,{'farm':{'food':21},'pasture':{'food':50}})
        edit(10,biome='Taiga')
        with db.cursor() as c:c.execute("UPDATE provinces SET terrain='hills' WHERE id=?",(self.pid,))
        build(1,10,'lumber_camp',uid=1)
        self.assertEqual(forecast(1)['staffing'][1]['building'],'lumber_camp')

    def test_province_edits_update_totals_resources_and_claim_normalization(self):
        p=edit(10,population=1200,biome='Taiga')
        self.assertEqual(p['terrain'],'taiga')
        self.assertEqual(json.loads(p['base_resources_json']),{'iron':2,'wood':7,'tar':3})
        self.assertEqual(self.balances()[0]['population'],1200)
        with db.cursor() as c:
            c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population) VALUES(11,2,100)")
            c.execute('UPDATE nations SET capital_province_id=? WHERE id=2',(self.pid,))
        changed,missing,normalized=claim(1,'10-11',True)
        self.assertEqual((changed,missing,normalized),(2,0,2))
        a,b=self.balances()
        self.assertEqual((a['population'],b['population']),(4000,0))
        self.assertIsNone(b['capital_province_id'])
        with self.assertRaises(ValueError):edit(10,population=-1)
        with self.assertRaises(ValueError):edit(10,biome='made up biome')
        claim(None,'10-11')
        self.assertEqual(self.balances()[0]['population'],0)


class CoopTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp();set_coop(1,1,3)

    async def test_lookup_services_revoke_and_transfer(self):
        self.assertEqual(find_nation(3)['id'],1)
        self.assertEqual(find_nation(3)['owner_id'],'1')
        with db.cursor() as c:self.assertEqual(owned(c,1,3)['id'],1)
        with self.assertRaises(ValueError):set_coop(1,3,4)
        with self.assertRaises(ValueError):set_coop(2,2,3)
        with self.assertRaises(ValueError):create_nation(3,'C','Lore','','',999)
        with self.assertRaises(ValueError):transfer_nation(1,1,'1',999)
        self.assertTrue(can_manage(1,3))
        from technology_ui import PrivateView
        old=PrivateView(1,3)
        self.assertTrue(await old.interaction_check(interaction(3)))
        set_coop(1,1,3,remove=True)
        self.assertFalse(await old.interaction_check(interaction(3)))
        self.assertIsNone(find_nation(3))
        with db.cursor() as c:
            with self.assertRaises(ValueError):owned(c,1,3)
        set_coop(1,1,3)
        transfer_nation(1,3,'1',999)
        self.assertEqual(find_nation(3)['owner_id'],'3')
        self.assertFalse(can_manage(1,1))

    async def test_coop_can_accept_trade_and_treaty_as_recipient(self):
        set_coop(2,2,4)
        from trade_service import accept_trade
        accept_trade(self.trade_id,4)
        import treaty_service as treaties
        from cogs.treaties import TreatyView
        tid=treaties.propose(1,3,2,'alliance')
        treaty=treaties.get_treaty(tid,4)
        view=TreatyView(4,treaty)
        self.assertFalse(view.accept.disabled)
        treaties.accept(tid,4,treaty['version'])
        self.assertEqual(treaties.get_treaty(tid,3)['status'],'active')

    async def test_coop_event_has_shared_version_and_revocation_during_generation(self):
        import event_adventure as flow
        eid=db.insert_returning_id("INSERT INTO events(nation_id,gm_final_text,effects_json) VALUES(1,'Story',?)",('{"treasury":30}',))
        with db.cursor() as c:
            c.execute('SELECT * FROM events WHERE id=?',(eid,));event=c.fetchone()
        with patch.object(flow,'scene',AsyncMock(return_value=('Scene',['A','B','C']))):
            state=flow.start_run(await flow.prepare_run(event,find_nation(3)))
            async def baseline(state,action,choice):return flow.fallback_consequence(state,choice),'Fine',False
            with patch.object(flow,'assess_consequence',side_effect=baseline):
                await flow.decide(eid,0,3,choice=1)
                with self.assertRaises(ValueError):await flow.decide(eid,0,1,choice=1)
            async def revoke(state,action,choice):
                set_coop(1,1,3,remove=True)
                return flow.fallback_consequence(state,choice),'Fine',False
            with patch.object(flow,'assess_consequence',side_effect=revoke):
                with self.assertRaises(ValueError):await flow.decide(eid,1,3,choice=1)
        self.assertEqual(flow.load_run(eid)['version'],1)
        self.assertEqual(self.balances()[0]['treasury'],100)

    async def test_coop_panels_and_slash_lookups(self):
        from cogs.economy import _nation_owner
        from cogs.military import _nat_owner
        from cogs.colonialism import _nat_owner as colonial_owner
        from cogs.panel import PlayerPanel
        for lookup in (_nation_owner,_nat_owner,colonial_owner):self.assertEqual(lookup(3)['id'],1)
        self.assertIn('A',PlayerPanel(None,3,'en').embed().description)


class AdminTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    async def test_admin_forms_dispatch_and_validate_at_submission(self):
        from admin_panel import AdminPanel
        from cogs.provinces import ProvincesCog
        with db.cursor() as c:c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population) VALUES(10,1,100)')
        bot=NS(get_cog=lambda name:ProvincesCog(None))
        panel=AdminPanel(bot,999,1,10)
        gm=interaction(999,[NS(id=10,name=config.GM_ROLE_NAME)])
        gm.response.send_modal=AsyncMock()
        await panel.action(gm,'population')
        form=gm.response.send_modal.call_args.args[0]
        await form.submitter(gm,'2500')
        self.assertEqual(self.balances()[0]['population'],2500)
        await form.submitter(interaction(999),'100')
        self.assertEqual(self.balances()[0]['population'],2500)
        await panel.action(gm,'tick')
        form=gm.response.send_modal.call_args.args[0]
        with patch.object(panel,'call',AsyncMock()) as advance:
            for months in ('0','121'):await form.submitter(gm,months)
            advance.assert_not_called()
            await form.submitter(gm,'1')
            advance.assert_awaited_once_with(gm,'EconomyCog','admin_tick',1)

    async def test_all_admin_sections_fit_and_recheck_role(self):
        from admin_panel import AdminPanel,AdminPicker
        gm=interaction(999,[NS(id=10,name=config.GM_ROLE_NAME)])
        for lang in ('pl','en'):
            with i18n.using_language(lang):
                for category in ('provinces','nations','events','world'):
                    view=AdminPanel(None,999,1,10,category)
                    self.assertLessEqual(len(view.children),25)
                    self.assertLess(len(view.embed()),6000)
                    self.assertTrue(await view.interaction_check(gm))
                    self.assertFalse(await view.interaction_check(interaction(999)))
        rows=[(str(i),'Nation '+str(i)) for i in range(40)]
        view=AdminPicker(AdminPanel(None,999),rows,AsyncMock())
        self.assertEqual(len(view.children[0].options),25)
        self.assertEqual(len(AdminPicker(AdminPanel(None,999),rows,AsyncMock(),1).children[0].options),15)

        from cogs.panel import PlayerPanel,ACTIONS
        panel=PlayerPanel(None,999,'en',admin=True)
        for section in ACTIONS:
            panel.section=section;panel.rebuild()
            button=next(child for child in panel.children if getattr(child,'label',None)=='Admin panel')
            self.assertLessEqual(len(panel.children),25)
        blocked=interaction(999)
        await button.callback(blocked)
        self.assertNotIn('view',blocked.response.send_message.call_args.kwargs)

    async def test_province_commands_reject_players_and_accept_server_admin(self):
        from cogs.provinces import ProvincesCog
        cog=ProvincesCog(None)
        with db.cursor() as c:c.execute('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population) VALUES(10,1,100)')
        await cog.population.callback(cog,interaction(1),10,2000)
        self.assertEqual(self.balances()[0]['population'],0)
        admin=interaction(999);admin.user.guild_permissions=NS(administrator=True)
        await cog.population.callback(cog,admin,10,2000)
        self.assertEqual(self.balances()[0]['population'],2000)
        await cog.claim.callback(cog,admin,'B','10',True)
        self.assertEqual([n['population'] for n in self.balances()],[0,2000])
