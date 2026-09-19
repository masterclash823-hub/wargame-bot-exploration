import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from test_regressions import DatabaseFixture, interaction
import db
import calendar_service as calendar
from economy_engine import _set, forecast, run_month
from cogs.economy import EconomyCog, _seed_buildings
from economy_ui import show_dashboard, EconomyView


class CalendarTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        _seed_buildings()
        self.now=datetime(2026,9,19,12,tzinfo=timezone.utc)

    def config(self,key):
        with db.cursor() as c:
            c.execute('SELECT value FROM game_config WHERE key=?',(key,));r=c.fetchone()
            return r['value'] if r else None

    async def test_repeated_start_pause_resume_preserve_elapsed_time(self):
        calendar.configure(24,99,now=self.now)
        self.assertTrue(calendar.start(self.now))
        self.assertFalse(calendar.start(self.now+timedelta(hours=8)))
        self.assertEqual(calendar.utc(self.config('last_tick_ts')),self.now)
        calendar.stop(self.now+timedelta(hours=8))
        calendar.stop(self.now+timedelta(hours=10))
        calendar.start(self.now+timedelta(hours=32))
        self.assertIsNone(run_month(scheduled_at=self.now+timedelta(hours=47)))
        self.assertEqual(run_month(scheduled_at=self.now+timedelta(hours=48))[:2],(2,1))

    async def test_configure_speed_channel_keeps_date_and_fraction(self):
        calendar.configure(24,99,4,5,now=self.now)
        calendar.start(self.now)
        calendar.configure(12,100,now=self.now+timedelta(hours=12))
        self.assertEqual((self.config('current_month'),self.config('current_year')),('4','5'))
        self.assertIsNone(run_month(scheduled_at=self.now+timedelta(hours=17)))
        self.assertEqual(run_month(scheduled_at=self.now+timedelta(hours=18))[:2],(5,5))
        before=self.balances()
        with self.assertRaises(ValueError):calendar.configure(24,99,1,1)
        self.assertEqual(self.balances(),before)
        self.assertEqual(self.config('hours_per_month'),'12.0')

    async def test_invalid_configuration_is_atomic(self):
        for hours in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):calendar.configure(hours,99)
        for month in (0,13):
            with self.assertRaises(ValueError):calendar.configure(24,99,month)
        self.assertIsNone(self.config('hours_per_month'))

    async def test_legacy_clock_recovery_does_not_pay_a_month_twice(self):
        calendar.start(self.now)
        run_month(scheduled_at=self.now+timedelta(hours=24))
        after=self.balances()
        with db.cursor() as c:_set(c,'current_month',1)
        self.assertIsNone(run_month(scheduled_at=self.now+timedelta(hours=25)))
        self.assertEqual(self.config('current_month'),'2')
        self.assertEqual(self.balances(),after)
        preview=forecast(1)
        self.assertEqual(self.balances(),after)
        run_month(scheduled_at=self.now+timedelta(hours=48))
        self.assertEqual(self.balances()[0]['treasury'],preview['treasury'])
        with db.cursor() as c:
            c.execute('SELECT COUNT(*) AS n FROM economy_months');self.assertEqual(c.fetchone()['n'],2)

    async def test_naive_or_missing_checkpoint_no_longer_stalls_forever(self):
        for stamp in ('','invalid'):
            with db.cursor() as c:
                _set(c,'calendar_running','1');_set(c,'last_tick_ts',stamp)
            self.assertIsNone(run_month(scheduled_at=self.now))
            self.assertEqual(calendar.utc(self.config('last_tick_ts')),self.now)
        with db.cursor() as c:_set(c,'last_tick_ts',self.now.replace(tzinfo=None).isoformat())
        self.assertIsNotNone(run_month(scheduled_at=self.now+timedelta(hours=24)))

    async def test_manual_tick_while_paused_does_not_move_resume_checkpoint_to_future(self):
        calendar.start(self.now)
        calendar.stop(self.now+timedelta(hours=12))
        later=self.now+timedelta(days=3)
        with patch('economy_engine.datetime') as clock:
            clock.now.return_value=later
            run_month()
        calendar.start(later+timedelta(hours=2))
        due=calendar.status()['due']
        self.assertEqual(due,later+timedelta(hours=26))
        self.assertIsNotNone(run_month(scheduled_at=due))

    async def test_catchup_continues_when_channel_is_missing(self):
        calendar.configure(24,99,now=self.now)
        calendar.start(self.now-timedelta(hours=100))
        bot=NS(get_channel=Mock(return_value=None),fetch_channel=AsyncMock(side_effect=RuntimeError('missing channel')))
        with patch('cogs.economy.datetime') as clock:
            clock.now.return_value=self.now
            await EconomyCog.calendar_loop.coro(NS(bot=bot))
        self.assertEqual(self.config('current_month'),'4')
        self.assertEqual(calendar.utc(self.config('last_tick_ts')),self.now-timedelta(hours=28))

    async def test_failed_tick_rolls_back_and_status_reports_retry(self):
        calendar.start(self.now-timedelta(hours=48))
        before=self.balances()
        with patch('cogs.economy.datetime') as clock, patch('cogs.colonialism.tick_colonies',side_effect=RuntimeError('broken')):
            clock.now.return_value=self.now
            with self.assertLogs(level='ERROR'):
                await EconomyCog.calendar_loop.coro(NS(bot=NS()))
        self.assertEqual(self.balances(),before)
        self.assertEqual(calendar.status()['error'],'RuntimeError')
        inter=interaction(1)
        await EconomyCog.calendar_status.callback(None,inter)
        self.assertIn('RuntimeError',str(inter.followup.send.call_args.kwargs['embed'].to_dict()))
        run_month(scheduled_at=self.now)
        self.assertEqual(calendar.status()['error'],'')


class ResourcesTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    async def test_forecast_failure_still_shows_real_stockpile_and_does_not_mutate(self):
        before=self.balances()
        request=interaction(1)
        with patch('economy_ui.forecast',side_effect=RuntimeError('another nation failed')) as preview:
            await show_dashboard(request)
            preview.assert_not_called()
        payload=request.followup.send.call_args.kwargs
        self.assertTrue(payload['ephemeral'])
        text=str(payload['embed'].to_dict())
        self.assertIn('wood: 100',text)
        self.assertIn('100.0g',text)
        self.assertNotIn('another nation failed',text)
        self.assertEqual(self.balances(),before)
        request.edit_original_response=AsyncMock()
        with patch('economy_ui.forecast',side_effect=RuntimeError('another nation failed')),self.assertLogs(level='ERROR'):
            await payload['view'].preview.callback(request)
        text=str(request.edit_original_response.call_args.kwargs['embed'].to_dict())
        self.assertIn('unavailable',text)
        self.assertIn('wood: 100',text)
        self.assertEqual(self.balances(),before)

    async def test_full_inventory_is_not_lost_to_embed_limit(self):
        _seed_buildings()
        values={f'custom_resource_{i}':i/10 for i in range(100)}
        values['algae']=.05
        with db.cursor() as c:c.execute('UPDATE nations SET resources_json=? WHERE id=1',(json.dumps(values),))
        request=interaction(1)
        await show_dashboard(request)
        sent=request.followup.send.call_args.kwargs
        contents=sent['file'].fp.read().decode()
        self.assertIn('custom resource 99: 9.9',contents)
        self.assertIn('0.05',contents)
        self.assertTrue(all(len(f.value)<=1024 for f in sent['embed'].fields))

    async def test_resources_command_is_same_panel_entry_point(self):
        request=interaction(1)
        with patch('economy_ui.show_dashboard',AsyncMock()) as show:
            await EconomyCog.resources.callback(None,request)
            show.assert_awaited_once_with(request)
