"""Nation rankings from current game data, with no rewards or persistent changes."""
import io
import json
import math
from datetime import datetime, timezone

import discord
import db
import i18n
from economy_engine import forecast, read_json
from technology import CATEGORIES as TECH_CATEGORIES
from world_service import month_index, tr


# Labels are also Discord choices, translated by the command translator.
CATEGORIES = {
    'prestige': ('Prestige', 'Prestiż'),
    'population': ('Population', 'Ludność'),
    'provinces': ('Provinces', 'Prowincje'),
    'treasury': ('Treasury', 'Skarbiec'),
    'income': ('Monthly gold balance', 'Miesięczny bilans złota'),
    'technology': ('Technology', 'Technologia'),
    'army': ('Army strength', 'Siła armii'),
    'navy': ('Navy strength', 'Siła floty'),
}


def explanation(category):
    notes = {
        'prestige': ('Punkty prestiżu zapisane przy państwie.', 'Prestige points recorded for the nation.'),
        'population': ('Mieszkańcy aktywnych prowincji, łącznie z koloniami.',
                       'Inhabitants of active provinces, including colonies.'),
        'provinces': ('Liczba aktywnych prowincji, łącznie z koloniami.',
                      'Number of active provinces, including colonies.'),
        'treasury': ('Aktualne złoto w skarbcu; bez wyceny surowców.',
                     'Current gold in the treasury; resource stocks are not valued.'),
        'income': ('Prognoza zmiany skarbca netto w następnym miesiącu, po wydatkach i spłatach. Nie przesuwa czasu.',
                   'Forecast net treasury change next month, after expenses and repayments. Does not advance time.'),
        'technology': ('Średnia poziomów gospodarki, wojsk lądowych, marynarki i kolonii.',
                       'Average technology level across economy, land, naval and colonial fields.'),
    }
    if category in ('army', 'navy'):
        return tr('Atak + obrona gotowych sił według zasad bitew: technologia, badania i morale. '
                  'Bez rezerw, mobilizacji i okrętów na szlakach. Bez terenu, planów i losowości; to nie prognoza zwycięstwa.',
                  'Attack + defense of ready forces under battle rules: technology, research and morale. '
                  'Excludes reserves, mobilizing units and trade-route ships. No terrain, plans or rolls; not a victory forecast.')
    return tr(*notes[category])


def rank_rows(rows):
    # Compare the displayed precision so equal visible scores share a place.
    for row in rows:
        value=float(row['value'])
        row['value']=round(value,2) if math.isfinite(value) else 0
    rows.sort(key=lambda r:(-r['value'],r['name'].casefold(),r['id']))
    previous=None
    for index,row in enumerate(rows,1):
        if row['value']!=previous:place=index
        row['place']=place
        previous=row['value']
    return rows


def load(category):
    if category not in CATEGORIES:raise ValueError('Unknown ranking category')
    rows=[]
    with db.cursor() as c:
        month=month_index(c)
        c.execute("SELECT n.*,COALESCE(p.people,0) AS people,COALESCE(p.cells,0) AS cells,"
                  "COALESCE(profile.prestige,0) AS prestige FROM nations n "
                  "LEFT JOIN (SELECT owner_nation_id,SUM(population) AS people,COUNT(*) AS cells "
                  "FROM provinces WHERE active=1 GROUP BY owner_nation_id) p ON p.owner_nation_id=n.id "
                  "LEFT JOIN nation_profiles profile ON profile.nation_id=n.id "
                  "WHERE NOT EXISTS (SELECT 1 FROM nation_decay d WHERE d.nation_id=n.id AND d.status='ruins') "
                  "ORDER BY n.id")
        nations=c.fetchall()
        for n in nations:
            if category=='technology':
                levels=read_json(n['tech_json'])
                value=sum(float(levels.get(k,3)) for k in TECH_CATEGORIES)/len(TECH_CATEGORIES)
            elif category in ('army','navy'):
                from battle_resolution import _power
                c.execute("SELECT u.id,u.quantity FROM military_units u JOIN blueprints b ON b.id=u.blueprint_id "
                          "LEFT JOIN military_posture m ON m.unit_id=u.id WHERE u.nation_id=? AND b.type=? "
                          "AND u.quantity>0 AND (m.mode IS NULL OR m.mode IN ('active','deployed')) "
                          "AND NOT EXISTS (SELECT 1 FROM route_assignments a WHERE a.ship_id=u.id) ORDER BY u.id",
                          (n['id'],'ship' if category=='navy' else 'unit'))
                forces=[{'unit_id':u['id'],'qty':u['quantity']} for u in c.fetchall()]
                # Rankings must not take battle row locks while reading countries
                # in a different order from the monthly settlement.
                attack,defense,_=_power(c,{'forces_json':json.dumps(forces)},n,lock_units=False)
                value=attack+defense
            elif category=='income':value=0  # One shared world forecast below.
            else:value=n[{'population':'people','provinces':'cells'}.get(category,category)]
            rows.append(dict(id=n['id'],name=n['name'],value=value))
    if category=='income' and rows:
        reports=forecast()
        for row in rows:
            report=reports.get(str(row['id']))
            # A nation that collapses next month has no operating economy then.
            row['value']=report['treasury']-report['opening_treasury'] if report else 0
    return dict(category=category,month=month,rows=rank_rows(rows))


def number(value):
    if abs(value)>=1e12:return f'{value:.4g}'
    text=f'{value:,.2f}'.rstrip('0').rstrip('.').replace(',',' ')
    return text.replace('.',',') if i18n.current_language()=='pl' else text


def render(result,limit):
    category=result['category'];rows=result['rows'];month=result['month']
    label=CATEGORIES[category][i18n.current_language()=='pl']
    title=tr('Ranking państw — ','Nation ranking — ')+label
    note=explanation(category)
    date=f'{month%12+1:02}/{month//12}'
    lines=[];full=[]
    for row in rows:
        name=' '.join(row['name'].split())
        full.append(f"{row['place']}. {name} — {number(row['value'])}")
        if len(lines)<limit:
            shortened=name if len(name)<=60 else name[:59]+'…'
            safe=discord.utils.escape_mentions(discord.utils.escape_markdown(shortened))
            lines.append(f"**{row['place']}.** {safe} — **{number(row['value'])}**")
    embed=discord.Embed(title=title,description=note+'\n\n'+'\n'.join(lines),
                        color=discord.Color.gold(),timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=tr('Miesiąc gry: ','Game month: ')+date+f' · {len(lines)}/{len(rows)} · '+
                     tr('Pełny ranking w pliku TXT · bez państw upadłych',
                        'Full ranking in TXT · fallen nations excluded'))
    text=title+'\n'+date+'\n'+note+'\n\n'+'\n'.join(full)
    file=discord.File(io.BytesIO(text.encode('utf-8')),filename=f'ranking-{category}.txt')
    return embed,file
