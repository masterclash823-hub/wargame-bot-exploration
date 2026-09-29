import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import config
import db
import i18n
import labor_regimes as labor
import nation_decay
import treaty_service as treaties
from cogs.nations import NationCog
from cogs.panel import PlayerPanel
from economy_engine import forecast, policy, run_tick, save_policy, set_policy
from economy_ui import EconomyControlCog, EconomyView, dashboard
from nation_access import set_coop
from stability_ui import change_text, load_current, social_embed
from test_dynasty_labor import Fixture
from test_regressions import interaction


class SocialFixture(Fixture):
    def prefs(self, **changes):
        with db.cursor() as c:
            p = policy(c, 1)
            p.update(changes)
            save_policy(c, 1, p)

    def saved(self, nid=1):
        row = self.query('SELECT report_json FROM economy_months ORDER BY month_index DESC LIMIT 1')[0]
        return json.loads(row['report_json'])[str(nid)]

    def assert_reconciles(self, report):
        for name in ('stability_report', 'happiness_report'):
            r = report[name]
            self.assertAlmostEqual(r['after'] - r['before'], r['change'])
            self.assertAlmostEqual(sum(r['effects'].values()) + r['limit_adjustment'], r['change'])


class StabilityTests(SocialFixture, unittest.TestCase):
    def test_tax_rates_and_opening_happiness_revenue(self):
        for tax, closing_happiness, closing_stability, tax_income in (
            ('high', 71, 50.71, 24.36),
            ('normal', 75, 51, 18.27),
            ('low', 77, 51.25, 12.18),
        ):
            with self.subTest(tax=tax):
                self.prefs(tax=tax, unrest=26)
                r = forecast(1)
                self.assertEqual(r['happiness_report']['before'], 74)
                self.assertEqual(r['happiness_report']['after'], closing_happiness)
                self.assertAlmostEqual(r['stability'], closing_stability)
                self.assertEqual(r['stability_report']['effects']['prestige_rank'], 1)
                self.assertAlmostEqual(r['taxes'], tax_income)
                self.assert_reconciles(r)

    def test_tax_and_labor_limits_apply_in_existing_order(self):
        labor.change(1, 1, 'slavery', 0, 50)
        for unrest, tax, tax_change, labor_change, final in (
            (0, 'normal', 0, -2, 98),
            (1, 'normal', 1, -2, 98),
            (99, 'high', -1, 0, 0),
        ):
            with self.subTest(unrest=unrest, tax=tax):
                self.prefs(unrest=unrest, tax=tax)
                r = forecast(1)
                h = r['happiness_report']
                self.assertEqual(h['after'], final)
                self.assertEqual(h['effects'], dict(taxes=tax_change, slavery=labor_change))
                self.assertEqual(r['stability_report']['effects']['slavery'], -.5)
                self.assert_reconciles(r)

    def test_food_grace_second_month_penalty_and_recovery(self):
        with db.cursor() as c:
            c.execute("UPDATE provinces SET buildings_json='[]' WHERE owner_nation_id=1")
            c.execute("UPDATE nations SET resources_json='{}' WHERE id=1")
        first = forecast(1)
        self.assertEqual(first['stability_report']['effects']['hunger'], 0)
        run_tick()
        second = forecast(1)
        self.assertEqual(second['stability_report']['effects']['hunger'], -8)
        # One prestige point in each month; hunger still removes eight points.
        self.assertEqual(first['stability'], 51)
        self.assertEqual(second['stability'], 44)
        with db.cursor() as c:
            c.execute('UPDATE nations SET resources_json=? WHERE id=1', ('{"food":10}',))
        self.assertEqual(forecast(1)['stability_report']['effects']['hunger'], -4)
        with db.cursor() as c:
            c.execute('UPDATE nations SET resources_json=? WHERE id=1', ('{"food":100}',))
        recovered = forecast(1)
        self.assertEqual(recovered['stability_report']['effects']['hunger'], 0)
        self.assertEqual(recovered['policy']['hunger_months'], 0)
        self.assertEqual(recovered['happiness_report']['after'], 100)

    def test_luxury_bonus_requires_consumption_and_marriage_is_separate(self):
        self.marry()
        with db.cursor() as c:
            c.execute('UPDATE nations SET resources_json=? WHERE id=1',
                      (json.dumps(dict(food=1000, silk=10, spices=10)),))
        set_policy(1, 'luxury', 'stockpile')
        stored = forecast(1)
        self.assertEqual(stored['stability_report']['effects']['luxuries'], 0)
        self.assertEqual(stored['stability_report']['effects']['dynasty'], .25)
        set_policy(1, 'luxury', 'consume')
        consumed = forecast(1)
        self.assertEqual(consumed['stability_report']['effects']['luxuries'], 1)
        self.assertEqual(consumed['stability_report']['effects']['prestige_rank'], 1)
        self.assertEqual(consumed['stability'], 52.25)
        self.assert_reconciles(consumed)

    def test_limit_explains_actual_gain_and_loss(self):
        self.prefs(tax='low')
        with db.cursor() as c:
            c.execute('UPDATE nations SET stability=99.9 WHERE id=1')
        high = forecast(1)
        r = high['stability_report']
        self.assertEqual(r['after'], 100)
        self.assertAlmostEqual(r['change'], .1)
        self.assertAlmostEqual(r['limit_adjustment'], -1.15)
        with db.cursor() as c:
            c.execute("UPDATE provinces SET buildings_json='[]' WHERE owner_nation_id=1")
            c.execute("UPDATE nations SET stability=2,resources_json='{}' WHERE id=1")
        self.prefs(tax='normal', hunger_months=1)
        low = forecast(1)
        # Prestige is awarded after the ordinary monthly effects reach zero.
        self.assertEqual(low['stability_report']['after'], 1)
        self.assertEqual(low['stability_report']['change'], -1)
        self.assertEqual(low['stability_report']['limit_adjustment'], 6)
        self.assert_reconciles(high)
        self.assert_reconciles(low)

    def test_project_prediction_matches_saved_tick_without_writes(self):
        self.prefs(tax='high', unrest=26)
        with db.cursor() as c:
            c.execute("INSERT INTO megaprojects(nation_id,name,status,duration_months,effect_json) "
                      "VALUES(1,'Civic project','building',1,?)", ('{"stability":5}',))
        before = self.balances()
        projected = forecast(1)
        self.assertEqual(self.balances(), before)
        self.assertFalse(self.query('SELECT * FROM economy_months'))
        self.assertFalse(self.query('SELECT * FROM game_config'))
        self.assertFalse(self.query('SELECT * FROM nation_history'))
        self.assertEqual(self.query('SELECT months_spent FROM megaprojects')[0]['months_spent'], 0)
        self.assertEqual(projected['stability_report']['before'], 50)
        self.assertEqual(projected['stability_report']['effects']['projects'], 5)
        self.assertAlmostEqual(projected['stability'], 55.71)
        run_tick()
        saved = self.saved()
        self.assertEqual(saved['stability_report'], projected['stability_report'])
        self.assertEqual(saved['happiness_report'], projected['happiness_report'])
        self.assertEqual(self.balances()[0]['stability'], saved['stability_report']['after'])
        self.assert_reconciles(saved)

    def test_automatic_married_treaty_breach_appears_in_settlement(self):
        tid = treaties.propose(1, 1, 2, 'alliance')
        treaties.amend_tribute(tid, 1, 100, 3, 'recipient')
        treaties.accept(tid, 2, 1)
        self.marry(tid)
        with db.cursor() as c:
            c.execute('UPDATE nations SET treasury=0 WHERE id=2')
        run_tick()
        preview = forecast(2)
        self.assertEqual(preview['stability_report']['effects']['treaties'], -5)
        self.assertEqual(preview['stability_report']['effects']['dynasty'], 0)
        self.assertEqual(treaties.get_treaty(tid, 1)['status'], 'active')
        run_tick()
        self.assertEqual(self.saved(2)['stability_report'], preview['stability_report'])
        self.assertEqual(treaties.get_treaty(tid, 1)['status'], 'broken')

    def test_ruins_forecast_has_no_social_change(self):
        nation_decay.set_decay(1, 999)
        run_tick(2)
        self.prefs(tax='high', unrest=26)
        preview = forecast(1)
        self.assertTrue(preview['nation_ruins'])
        for name in ('stability_report', 'happiness_report'):
            self.assertEqual(preview[name]['before'], preview[name]['after'])
            self.assertEqual(preview[name]['effects'], {})


class StabilityUITests(SocialFixture, unittest.IsolatedAsyncioTestCase):
    async def test_current_values_available_without_forecast_and_translated(self):
        self.prefs(unrest=26)
        with db.cursor() as c:
            c.execute('UPDATE nations SET stability=49.71 WHERE id=1')
        i18n.set_user_language(1, 'pl')
        n = self.balances()[0]
        with i18n.using_language('pl'), patch('economy_ui.forecast', side_effect=AssertionError('No preview needed')):
            e = dashboard(n, dict(stockpile_only=True))
            values = {f.name: f.value for f in e.fields}
            self.assertIn('49,71/100', values['Stabilność'])
            self.assertIn('74,00/100', values['Zadowolenie'])
            panel = PlayerPanel(NS(get_cog=lambda _: None), 1, 'pl')
            self.assertTrue(any(getattr(b, 'label', '') == 'Stabilność i zadowolenie' for b in panel.children))
            self.assertIn('49,71/100', str(panel.embed().to_dict()))
        inter = interaction(1)
        await NationCog.stats.callback(NationCog(None), inter)
        self.assertIn('49,71/100', str(inter.response.send_message.call_args.kwargs['embed'].to_dict()))

    async def test_report_is_localized_and_handles_later_changes_and_legacy_reports(self):
        self.prefs(tax='high', unrest=26)
        run_tick()
        with db.cursor() as c:
            c.execute('UPDATE nations SET stability=stability+5 WHERE id=1')
        preview = forecast(1)
        n, p, latest = load_current(1)
        for lang, label, change, happiness_label in (
            ('pl', 'Zmiany po rozliczeniu', '+5,00', 'Zadowolenie — ostatnie rozliczenie'),
            ('en', 'Changes since settlement', '+5.00', 'Happiness — last settlement'),
        ):
            with self.subTest(lang=lang), i18n.using_language(lang):
                e = social_embed(n, p, preview, latest)
                self.assertIn(change, next(f.value for f in e.fields if f.name == label))
                self.assertTrue(any(f.name == happiness_label for f in e.fields))
                self.assertLessEqual(len(e), 6000)
                self.assertTrue(all(len(f.value) <= 1024 for f in e.fields))
        with i18n.using_language('pl'):
            legacy = social_embed(n, p, None, dict(month_index=13, report={}))
            text = str(legacy.to_dict())
            self.assertIn('Starszy raport', text)
            self.assertIn('chwilowo niedostępna', text)
            self.assertNotIn('Forecast', text)

    async def test_private_report_access_and_coop(self):
        denied = interaction(2)
        with patch('economy_ui.safe_forecast') as preview:
            await EconomyControlCog.stability.callback(None, denied, 'A')
            preview.assert_not_called()
        self.assertIn('Details are available', denied.followup.send.call_args.args[0])
        self.assertTrue(denied.followup.send.call_args.kwargs['ephemeral'])
        set_coop(1, 1, 3)
        coop = interaction(3)
        await EconomyControlCog.stability.callback(None, coop)
        self.assertIn('Stability and happiness — A', coop.followup.send.call_args.kwargs['embed'].title)
        self.assertFalse(self.query('SELECT * FROM economy_months'))
        gm = interaction(999, [NS(id=12, name=config.GM_ROLE_NAME)])
        with patch.object(config, 'GM_ROLE_ID', ''):
            await EconomyControlCog.stability.callback(None, gm, 'B')
        self.assertIn('— B', gm.followup.send.call_args.kwargs['embed'].title)

    async def test_access_revoked_while_forecast_runs(self):
        set_coop(1, 1, 3)
        preview = forecast(1)
        def revoke(nid):
            set_coop(1, 1, 3, remove=True)
            return preview
        inter = interaction(3)
        with patch('economy_ui.safe_forecast', side_effect=revoke):
            await EconomyControlCog.stability.callback(None, inter)
        self.assertNotIn('embed', inter.followup.send.call_args.kwargs)
        self.assertIn('access changed', inter.followup.send.call_args.args[0])

    async def test_economy_button_localization_and_tax_descriptions(self):
        i18n.set_user_language(1, 'pl')
        with i18n.using_language('pl'):
            view = EconomyView(1, 1)
            self.assertEqual(view.social.label, 'Stabilność i zadowolenie')
            for option in view.settings.options:
                if option.value.startswith('tax:'):
                    self.assertIn('zadowolenia', option.description)
                    self.assertLessEqual(len(option.description), 100)
        inter = interaction(1)
        with patch('economy_ui.safe_forecast', return_value=None):
            await view.social.callback(inter)
        self.assertIn('Stabilność i zadowolenie', inter.followup.send.call_args.kwargs['embed'].title)
        self.assertFalse(self.query('SELECT * FROM economy_months'))

    async def test_food_grace_and_limit_are_explained_in_points(self):
        with db.cursor() as c:
            c.execute("UPDATE provinces SET buildings_json='[]' WHERE owner_nation_id=1")
            c.execute("UPDATE nations SET stability=99.9,resources_json='{}' WHERE id=1")
        self.prefs(tax='low')
        preview = forecast(1)
        n, p, latest = load_current(1)
        with i18n.using_language('pl'):
            text = str(social_embed(n, p, preview, latest).to_dict())
            self.assertIn('Pierwszy miesiąc niedoboru', text)
            self.assertIn('Limit 0–100: -1,15', text)
            self.assertIn('Top 3 prestiżu: +1,00 pkt', text)
            self.assertNotIn('prestige_rank', text)
            self.assertIn('+0,10 pkt', text)
            self.assertNotIn('-0,00', change_text(preview['happiness_report']))
