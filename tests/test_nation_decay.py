import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import config
import db
import i18n
import nation_decay as decay
from economy_engine import forecast, run_month, run_tick, lock_nation
from world_service import month_index, owned, transfer_nation
from test_regressions import DatabaseFixture, interaction
from cogs.economy import _seed_buildings
from cogs.nations import NationCog
from cogs.events import EventsCog
import event_adventure


class DecayTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        with db.cursor() as c:
            for cell,nid in ((10,1),(20,2)):
                c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,terrain,population,buildings_json) VALUES(?,?,'plains',2000,'[\"farm\"]')",(cell,nid))
            c.execute('INSERT INTO province_neighbors(cell_id,neighbor_cell_id) VALUES(20,10)')
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(1,'lore','Old stone cities')")
            c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(1,'event','Secret military decision')")

    def query(self,sql,args=()):
        with db.cursor() as c:
            c.execute(sql,args)
            return c.fetchall()

    def status(self):
        with db.cursor() as c:return decay.state(c,1)

    def collapse(self):
        decay.set_decay(1,999,'Abandoned')
        run_tick(3)

    async def test_three_month_deadline_freezes_archive_and_releases_account(self):
        with db.cursor() as c:start=month_index(c)
        s=decay.set_decay(1,999)
        self.assertEqual(s['due_month'],start+3)
        with self.assertRaises(ValueError):decay.set_decay(1,999)
        run_tick(2)
        with db.cursor() as c:self.assertEqual(owned(c,1,1)['owner_id'],'1')
        before=self.balances()[0]
        run_month()
        self.assertEqual(self.status()['status'],'ruins')
        archived=self.balances()[0]
        self.assertEqual(archived,dict(before,owner_id='ruins:1'))
        self.assertEqual(self.status()['former_owner'],'1')
        history=self.query('SELECT * FROM nation_history WHERE nation_id=1')
        run_tick(2)
        self.assertEqual(archived,self.balances()[0])
        self.assertEqual(history,self.query('SELECT * FROM nation_history WHERE nation_id=1'))
        self.assertEqual(self.query('SELECT cell_id FROM ruin_sites')[0]['cell_id'],10)
        with self.assertRaises(ValueError):
            with db.cursor() as c:lock_nation(c,1)
        with self.assertRaises(ValueError):transfer_nation(1,'3','ruins:1',999)
        with db.cursor() as c:
            c.execute("INSERT INTO nations(owner_id,name) VALUES('1','Replacement')")

    async def test_forecast_and_failed_tick_rollback_collapse(self):
        decay.set_decay(1,999)
        run_tick(2)
        before=self.balances()
        report=forecast(1)
        self.assertTrue(report['nation_ruins'])
        self.assertEqual(report['income'],0)
        self.assertEqual(report['treasury'],before[0]['treasury'])
        self.assertEqual(self.balances(),before)
        self.assertEqual(self.status()['status'],'decaying')
        self.assertFalse(self.query('SELECT * FROM ruin_sites'))
        with patch('cogs.colonialism.tick_colonies',side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):run_month()
        self.assertEqual(self.balances(),before)
        self.assertEqual(self.status()['status'],'decaying')
        run_month()
        self.assertEqual(self.status()['status'],'ruins')

    async def test_cancel_only_before_collapse_and_restart_new_countdown(self):
        decay.set_decay(1,999)
        run_month()
        decay.set_decay(1,999,cancel=True)
        run_tick(3)
        self.assertEqual(self.status()['status'],'cancelled')
        self.assertEqual(self.balances()[0]['owner_id'],'1')
        self.collapse()
        for cancel in (True,False):
            with self.assertRaises(ValueError):decay.set_decay(1,999,cancel=cancel)

    async def test_only_gm_may_start_and_stop(self):
        cog=NationCog(None)
        player=interaction(1)
        await cog.decay.callback(cog,player,'A')
        self.assertIsNone(self.status())
        gm=interaction(999,[NS(id=12,name=config.GM_ROLE_NAME)])
        await cog.decay.callback(cog,gm,'A')
        self.assertEqual(self.status()['status'],'decaying')
        await cog.decay_stop.callback(cog,player,'A')
        self.assertEqual(self.status()['status'],'decaying')
        await cog.decay_stop.callback(cog,gm,'A')
        self.assertEqual(self.status()['status'],'cancelled')

    async def test_pending_battle_and_recurring_trade_no_longer_settle(self):
        with db.cursor() as c:
            c.execute("INSERT INTO trade_contracts(trade_id,status) VALUES(?,'active')",(self.trade_id,))
        a=db.insert_returning_id("INSERT INTO battle_plans(nation_id,status) VALUES(1,'matched')",())
        b=db.insert_returning_id("INSERT INTO battle_plans(nation_id,status) VALUES(2,'matched')",())
        db.insert_returning_id('INSERT INTO battles(plan_a_id,plan_b_id) VALUES(?,?)',(a,b))
        self.collapse()
        self.assertEqual(self.query('SELECT status FROM battles')[0]['status'],'cancelled')
        self.assertEqual([r['status'] for r in self.query('SELECT status FROM battle_plans ORDER BY id')],['cancelled','unmatched'])
        self.assertEqual(self.query('SELECT status FROM trade_contracts')[0]['status'],'ended')
        run_month()

    async def test_neighbors_both_directions_active_cells_and_safe_history(self):
        with db.cursor() as c:self.assertFalse(decay.nearby(c,2))
        self.collapse()
        for edge in ((10,20),(20,10)):
            with db.cursor() as c:
                c.execute('DELETE FROM province_neighbors')
                c.execute('INSERT INTO province_neighbors(cell_id,neighbor_cell_id) VALUES(?,?)',edge)
                ctx=decay.context(c,2,1)
            self.assertEqual(ctx['lore'],'Old stone cities')
            self.assertNotIn('Secret',json.dumps(ctx))
        with db.cursor() as c:
            c.execute('UPDATE provinces SET active=0 WHERE azgaar_cell_id=20')
            self.assertFalse(decay.nearby(c,2))
            with self.assertRaises(ValueError):decay.context(c,2,1)

    async def test_companies_foreign_concessions_and_old_captive_claims_end(self):
        import companies as co
        import captivity
        with db.cursor() as c:
            c.execute("UPDATE nations SET treasury=5000,tech_json='{\"economy\":4}',resources_json='{\"food\":300,\"wood\":1000,\"stone\":1000}'")
        co.create(1,1,'Ruined company','Farms',['farm'])
        co.create(2,2,'Surviving company','Farms',['farm'])
        def version(nid):
            with db.cursor() as c:return co.state(c,nid)['version']
        for a,b,cell in ((1,2,20),(2,1,10)):
            grant=co.propose_concession(a,a,version(a),b,[cell],['farm'],70,12,0)
            co.respond_concession(grant,b,'accept')
            co.assign(a,a,version(a),cell,'farm')
        with db.atomic() as c:captivity.add_opportunity(c,'battle',1,2,1,10)
        self.collapse()
        self.assertEqual([r['status'] for r in self.query('SELECT status FROM company_concessions')],['ended','ended'])
        self.assertFalse(self.query('SELECT * FROM company_plants'))
        with db.cursor() as c:
            self.assertTrue(co.state(c,1)['paused'])
            with self.assertRaises(ValueError):captivity.opportunity(c,1,2)
        frozen=self.balances()[0]
        run_month()
        self.assertEqual(self.balances()[0],frozen)

    async def test_player_discovery_is_localized_deduplicated_and_has_no_rewards(self):
        self.collapse()
        before=self.balances()
        with self.assertRaises(ValueError):decay.request_event(2,1,1)
        with patch('i18n.get_user_language',return_value='pl'):
            event=decay.request_event(2,2,1)
        self.assertEqual(decay.request_event(2,2,1),event)
        row=self.query('SELECT * FROM events WHERE id=?',(event,))[0]
        self.assertIn('Zwiadowcy',row['gm_final_text'])
        self.assertEqual(row['status'],'draft')
        self.assertEqual(json.loads(row['effects_json']),{})
        self.assertEqual(self.balances(),before)
        with patch.object(event_adventure,'scene',AsyncMock(return_value=('Ruins',['A','B','C']))):
            state=await event_adventure.prepare_run(row,self.balances()[1])
        self.assertEqual(state['ruins']['name'],'A')
        event_adventure.start_run(state)
        self.assertEqual(self.query('SELECT status FROM events WHERE id=?',(event,))[0]['status'],'active')

    async def test_event_generated_with_ruins_rechecks_target_and_borders(self):
        self.collapse()
        cog=EventsCog(None)
        gm=interaction(999,[NS(id=12,name=config.GM_ROLE_NAME)])
        with patch('cogs.events._generate_event',AsyncMock(return_value=('Old ruins','{}'))) as ai:
            await cog.event_generate.callback(cog,gm,'B',1)
        self.assertEqual(ai.call_args.args[1]['name'],'A')
        self.assertEqual(len(self.query('SELECT * FROM ruin_event_links')),1)
        with db.cursor() as c:c.execute('DELETE FROM province_neighbors')
        with patch('cogs.events._generate_event',AsyncMock()) as ai:
            await cog.event_generate.callback(cog,gm,'B',1)
            ai.assert_not_awaited()
            await cog.event_generate.callback(cog,gm,'A')
            ai.assert_not_awaited()

    async def test_prepared_event_cannot_start_after_recipient_collapse(self):
        eid=db.insert_returning_id("INSERT INTO events(nation_id,gm_final_text,effects_json,status) VALUES(1,'Old event','{}','draft')",())
        event=self.query('SELECT * FROM events WHERE id=?',(eid,))[0]
        with patch.object(event_adventure,'scene',AsyncMock(return_value=('Old event',['A','B','C']))):
            state=await event_adventure.prepare_run(event,self.balances()[0])
        self.collapse()
        with self.assertRaises(ValueError):event_adventure.start_run(state)
        self.assertFalse(self.query('SELECT * FROM event_runs'))
