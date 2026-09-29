"""Explain existing social mechanics using saved settlement data and the real forecast."""
import asyncio
import json

import discord

import db
import i18n
from economy_engine import happiness, policy
from flags import flagged_embed
from nation_access import can_manage, find_nation
from utils import gm_only


def tr(pl, en, lang=None):
    return pl if (lang or i18n.current_language()) == 'pl' else en


def number(value, *, signed=False, lang=None):
    # Avoid displaying a negative zero after floating point arithmetic.
    value = 0 if abs(value) < .005 else value
    text = f'{value:+.2f}' if signed else f'{value:.2f}'
    return text.replace('.', ',') if (lang or i18n.current_language()) == 'pl' else text


def stability_value(value, lang=None):
    labels = [('✅', 'Stabilnie', 'Stable'), ('🟡', 'Napięcie', 'Tense'),
              ('🟠', 'Niestabilnie', 'Unstable'), ('🔴', 'Kryzys', 'Crisis')]
    icon, pl, en = labels[0 if value >= 80 else 1 if value >= 60 else 2 if value >= 40 else 3]
    return f'{icon} {number(value, lang=lang)}/100 · {tr(pl, en, lang)}'


def current_policy(nid):
    with db.cursor() as c:
        return policy(c, nid)


def add_current_fields(embed, nation, *, include_stability=True, lang=None):
    p = current_policy(nation['id'])
    if include_stability:
        embed.add_field(name=tr('Stabilność', 'Stability', lang), value=stability_value(nation['stability'], lang))
    embed.add_field(name=tr('Zadowolenie', 'Happiness', lang),
                    value=number(happiness(p['unrest']), lang=lang)+'/100\n'+
                    tr('Podatki i polityka pracy', 'Taxes and labor policy', lang))
    return p


def change_text(report, *, lang=None):
    labels = {'taxes': tr('Podatki', 'Taxes', lang), 'slavery': tr('Niewolnictwo', 'Slavery', lang),
              'dynasty': tr('Mariaże', 'Dynastic marriages', lang), 'hunger': tr('Głód', 'Hunger', lang),
              'luxuries': tr('Zużyte luksusy', 'Consumed luxuries', lang),
              'treaties': tr('Skutki traktatów', 'Treaty consequences', lang),
              'projects': tr('Ukończone projekty', 'Completed projects', lang),
              'prestige_rank': tr('Top 3 prestiżu', 'Prestige top 3', lang),
              'other': tr('Pozostałe skutki rozliczenia', 'Other settlement effects', lang)}
    points = tr('pkt', 'points', lang)
    lines = [f"**{number(report['before'], lang=lang)} → {number(report['after'], lang=lang)}/100** "
             f"({number(report['change'], signed=True, lang=lang)} {points})"]
    for key, value in report['effects'].items():
        if abs(value) > 1e-9:
            lines.append(f"{labels.get(key, key)}: {number(value, signed=True, lang=lang)} {points}")
    adjustment = report.get('limit_adjustment', 0)
    if abs(adjustment) > 1e-9:
        lines.append(tr('Limit 0–100: ', '0–100 limit: ', lang)+number(adjustment, signed=True, lang=lang)+f' {points}')
    if len(lines) == 1:
        lines.append(tr('Brak zmiany w tym miesiącu.', 'No change this month.', lang))
    return '\n'.join(lines)


def rules_text(p):
    tax = p['tax']
    rules = {
        'low': tr('Niskie podatki: do +3 zadowolenia i +0,25 stabilności/mies.',
                  'Low taxes: up to +3 happiness and +0.25 stability/month.'),
        'normal': tr('Normalne podatki: do +1 zadowolenia/mies.; bez bezpośredniej zmiany stabilności.',
                     'Normal taxes: up to +1 happiness/month; no direct stability change.'),
        'high': tr('Wysokie podatki: do −3 zadowolenia/mies. Kara do stabilności = (100 − zadowolenie po rozliczeniu) / 100.',
                   'High taxes: up to −3 happiness/month. Stability penalty = (100 − happiness after settlement) / 100.')}
    return rules[tax]


def add_forecast_fields(embed, report):
    if report.get('nation_ruins'):
        return
    sr, hr = report.get('stability_report'), report.get('happiness_report')
    if sr:
        embed.add_field(name=tr('Stabilność — następny miesiąc', 'Stability — next month'), value=change_text(sr), inline=False)
    if hr:
        embed.add_field(name=tr('Zadowolenie — następny miesiąc', 'Happiness — next month'), value=change_text(hr), inline=False)


def load_current(nid):
    with db.cursor() as c:
        c.execute('SELECT * FROM nations WHERE id=?', (nid,))
        n = c.fetchone()
        if not n:
            return None, None, None
        p = policy(c, nid)
        c.execute('SELECT month_index,report_json FROM economy_months ORDER BY month_index DESC LIMIT 1')
        row = c.fetchone()
        latest = None
        if row:
            recorded = json.loads(row['report_json']).get(str(nid))
            if recorded:
                latest = dict(month_index=row['month_index'], report=recorded)
        return n, p, latest


def social_embed(n, p, forecast, latest):
    embed = flagged_embed(discord.Embed(title=tr('Stabilność i zadowolenie — ', 'Stability and happiness — ')+n['name'],
                                        color=discord.Color.blue()), (n['flag'], n['name']))
    embed.description = tr('Stabilność wpływa na produkcję i podatki. Zadowolenie opisuje podatki i politykę pracy; '
                           'żywność i luksusy wpływają bezpośrednio na stabilność.',
                           'Stability affects production and taxes. Happiness reflects taxes and labor policy; '
                           'food and luxuries affect stability directly.')
    embed.add_field(name=tr('Stabilność teraz', 'Stability now'), value=stability_value(n['stability']))
    embed.add_field(name=tr('Zadowolenie teraz', 'Happiness now'), value=number(happiness(p['unrest']))+'/100')
    tax_factor = max(.5, 1-p['unrest']/200)
    embed.add_field(name=tr('Wpływ zadowolenia na podatki', 'Happiness effect on taxes'),
                    value=number(tax_factor*100)+'%\n'+tr('100 = pełne wpływy; 0 = połowa.', '100 = full tax revenue; 0 = half.'))
    if forecast is None:
        embed.add_field(name=tr('Prognoza', 'Forecast'), value=tr('Prognoza chwilowo niedostępna. Pokazuję bieżące dane i zapisany raport.',
                                                               'Forecast temporarily unavailable. Showing current data and the saved report.'), inline=False)
    elif forecast.get('nation_ruins'):
        embed.add_field(name=tr('Prognoza', 'Forecast'), value=tr('Państwo będzie ruinami. Miesięczne zmiany stabilności i zadowolenia są zatrzymane.',
                                                               'The nation will be ruins. Monthly stability and happiness changes are stopped.'), inline=False)
    else:
        add_forecast_fields(embed, forecast)
        if forecast['food_shortage'] and forecast['policy']['hunger_months'] == 1:
            embed.add_field(name=tr('Pierwszy miesiąc niedoboru', 'First shortage month'), value=tr(
                'Bez kary do stabilności. Kara zacznie się od drugiego kolejnego miesiąca niedoboru; dostawa żywności zeruje serię.',
                'No stability penalty yet. It starts in the second consecutive shortage month; adequate food resets the streak.'), inline=False)
    if latest:
        saved = latest['report']
        title = tr('Ostatnie rozliczenie — ', 'Last settlement — ')+f"{latest['month_index']%12+1}/{latest['month_index']//12}"
        sr = saved.get('stability_report')
        if sr:
            embed.add_field(name=title, value=change_text(sr), inline=False)
            if saved.get('happiness_report'):
                embed.add_field(name=tr('Zadowolenie — ostatnie rozliczenie', 'Happiness — last settlement'),
                                value=change_text(saved['happiness_report']), inline=False)
            since = n['stability'] - sr['after']
            if abs(since) >= .005:
                embed.add_field(name=tr('Zmiany po rozliczeniu', 'Changes since settlement'), value=
                    number(since, signed=True)+' '+tr('pkt stabilności. Są już w bieżącej wartości. Szczegóły decyzji znajdziesz w historii państwa.',
                                                      'stability points. Already included in the current value. See nation history for decisions.'), inline=False)
        else:
            embed.add_field(name=title, value=tr('Starszy raport nie zapisał przyczyn zmiany stabilności. Nowe rozliczenia będą je pokazywać.',
                                                'This older report did not record stability causes. New settlements will include them.'), inline=False)
    else:
        embed.add_field(name=tr('Ostatnie rozliczenie', 'Last settlement'), value=tr('Brak zapisanego raportu dla tego państwa.',
                                                                               'No saved report for this nation.'), inline=False)
    embed.add_field(name=tr('Jak poprawić sytuację?', 'How to improve things?'), value=rules_text(p)+'\n'+tr(
        'Zapewnij żywność. Zużywanie jedwabiu i przypraw daje łącznie do +1 stabilności/mies.; samo magazynowanie nie daje premii.',
        'Keep food supplied. Consuming silk and spices grants up to +1 stability/month in total; storing them gives no bonus.'), inline=False)
    embed.set_footer(text=tr('Zmiana jest w punktach, nie w procentach. Prognoza nie przesuwa czasu gry.',
                            'Changes are in points, not percent. Forecasting does not advance game time.'))
    return embed


async def show(interaction, nation=''):
    await interaction.response.defer(ephemeral=True)
    with db.cursor() as c:
        if nation:
            c.execute('SELECT * FROM nations WHERE LOWER(name)=LOWER(?)', (nation,))
            n = c.fetchone()
        else:
            n = find_nation(interaction.user.id, c)
    if not n:
        await interaction.followup.send(tr('Nie znaleziono państwa.', 'Nation not found.'), ephemeral=True)
        return
    nid = n['id']
    if not gm_only(interaction) and not can_manage(nid, interaction.user.id):
        await interaction.followup.send(tr('Szczegóły są dostępne dla graczy tego państwa i GM.',
                                          'Details are available to this nation’s players and the GM.'), ephemeral=True)
        return
    from economy_ui import safe_forecast
    forecast = await asyncio.to_thread(safe_forecast, nid)
    n, p, latest = await asyncio.to_thread(load_current, nid)
    if not n or (not gm_only(interaction) and not can_manage(nid, interaction.user.id)):
        await interaction.followup.send(tr('Zmienił się dostęp do państwa. Otwórz panel ponownie.',
                                          'Nation access changed. Open the panel again.'), ephemeral=True)
        return
    await interaction.followup.send(embed=social_embed(n, p, forecast, latest), ephemeral=True)
