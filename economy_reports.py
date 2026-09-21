"""Private building register and an exact, read-only monthly resource forecast."""
import asyncio
import io
import logging

import discord
import db
import i18n
from economy_engine import forecast, read_json
from utils import get_nation_by_owner
from world_service import tr


def building_inventory(nid):
    with db.cursor() as c:
        c.execute('SELECT p.*,d.levels_json FROM provinces p LEFT JOIN province_development d '
                  'ON d.province_id=p.id WHERE p.owner_nation_id=? ORDER BY p.azgaar_cell_id',(nid,))
        provinces=c.fetchall()
        c.execute('SELECT * FROM building_defs');definitions={r['key']:r for r in c.fetchall()}
    from cogs.economy import _building_label
    rows=[]
    for p in provinces:
        levels=read_json(p['levels_json'])
        for key in dict.fromkeys(read_json(p['buildings_json'],[])):
            rows.append(dict(key=key,name=_building_label(definitions[key]) if key in definitions else key,
                             cell=p['azgaar_cell_id'],province=p['name'],active=p['active'],
                             level=min(3,max(1,int(levels.get(key,1))))))
    return sorted(rows,key=lambda r:(r['name'].casefold(),r['cell']))


def resource_rows(report):
    start=report['opening_resources'];end=report['resources'];production=report['production']
    keys=set(start)|set(end)|set(production)
    # Show the catalogue's zero-output resources as well as custom resources.
    with db.cursor() as c:
        c.execute('SELECT effect_json,cost_json,upkeep_json FROM building_defs')
        for definition in c.fetchall():
            for value in definition.values():keys.update(read_json(value))
    rows=[dict(key='gold',production=None,change=report['treasury']-report['opening_treasury'],stock=report['treasury'])]
    rows.extend(dict(key=k,production=production.get(k,0),change=end.get(k,0)-start.get(k,0),stock=end.get(k,0))
                for k in sorted(keys-{'gold'},key=lambda k:i18n.term(k).casefold()))
    return rows


def pages(title,description,lines):
    result=[];chunk=[];length=0
    for line in lines:
        # Full names and rows remain in the downloadable register.
        shown=line[:700]
        if chunk and length+len(shown)+1>3000:
            result.append(discord.Embed(title=title[:256],description=description+'\n\n'+'\n'.join(chunk),color=discord.Color.gold()))
            chunk=[];length=0
        chunk.append(shown);length+=len(shown)+1
    result.append(discord.Embed(title=title[:256],description=description+'\n\n'+('\n'.join(chunk) or '—'),color=discord.Color.gold()))
    for index,embed in enumerate(result,1):embed.set_footer(text=f'{index}/{len(result)}')
    return result


class ReportView(i18n.LocalizedView):
    def __init__(self,owner,nid,embeds):
        super().__init__(timeout=600)
        self.owner,self.nid,self.pages,self.page=owner,nid,embeds,0
        self.refresh_buttons()

    @i18n.localized
    async def interaction_check(self,interaction):
        n=get_nation_by_owner(str(interaction.user.id))
        if interaction.user.id==self.owner and n and n['id']==self.nid:return True
        await interaction.response.send_message(tr('Nie masz dostępu do tego raportu.','You cannot access this report.'),ephemeral=True)
        return False

    def refresh_buttons(self):
        self.previous.disabled=self.page==0
        self.next.disabled=self.page==len(self.pages)-1

    async def move(self,interaction,offset):
        self.page=max(0,min(len(self.pages)-1,self.page+offset));self.refresh_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page],view=self,allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='◀',row=0)
    @i18n.localized
    async def previous(self,interaction,button):await self.move(interaction,-1)

    @discord.ui.button(label='▶',row=0)
    @i18n.localized
    async def next(self,interaction,button):await self.move(interaction,1)


async def show(interaction,kind):
    await interaction.response.defer(ephemeral=True)
    n=get_nation_by_owner(str(interaction.user.id))
    if not n:
        await interaction.followup.send(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
    try:
        if kind=='buildings':
            rows=await asyncio.to_thread(building_inventory,n['id'])
            title=tr('Zbudowane budynki — ','Built buildings — ')+n['name']
            description=tr('Budynki w prowincjach państwa, także w koloniach. Łącznie: ',
                           'Buildings in this nation’s provinces, including colonies. Total: ')+str(len(rows))
            lines=[f"{r['name']} · {tr('poz.','level')} {r['level']}/3 — #{r['cell']} {r['province'] or '—'}"+
                   (tr(' [prowincja nieaktywna]',' [inactive province]') if not r['active'] else '') for r in rows]
            if not lines:lines=[tr('Państwo nie ma zbudowanych budynków.','This nation has no buildings.')]
        else:
            report=await asyncio.to_thread(forecast,n['id'])
            rows=resource_rows(report)
            title=tr('Bilans surowców — ','Resource balance — ')+n['name']
            description=tr('Prognoza jednego miesiąca. Produkcja uwzględnia zużycie materiałów. Bilans to zmiana zapasu po wszystkich rozliczeniach: wyżywieniu, handlu, kompaniach, badaniach i projektach. Złoto uwzględnia też rachunki i zaległości.',
                           'One-month forecast. Production includes industrial input use. Balance is the stock change after all settlement: food, trade, companies, research and projects. Gold also includes bills and arrears.')
            if report.get('nation_ruins'):
                description+='\n'+tr('Państwo jest lub stanie się ruinami: gospodarka zatrzymana.','This nation is or will become ruins: economy halted.')
            def number(value,signed=False):
                text=format(0 if abs(value)<.000005 else value,'+.5f' if signed else '.5f').rstrip('0').rstrip('.')
                return text.replace('.',',') if i18n.current_language()=='pl' else text
            lines=[]
            for row in rows:
                production=(tr('produkcja','production')+f" {number(row['production'],True)} · ") if row['production'] is not None else ''
                lines.append(f"{i18n.term(row['key'])}: {production}{tr('bilans','balance')} {number(row['change'],True)} · "+
                             tr('po miesiącu','closing stock')+f" {number(row['stock'])}")
        current=get_nation_by_owner(str(interaction.user.id))
        if not current or current['id']!=n['id']:
            await interaction.followup.send(tr('Państwo zmieniło właściciela. Otwórz raport ponownie.',
                                              'Nation ownership changed. Open the report again.'),ephemeral=True);return
        embeds=pages(title,description,[discord.utils.escape_markdown(line) for line in lines])
        content='\n'.join([title,description,'',*lines])
        await interaction.followup.send(embed=embeds[0],view=ReportView(interaction.user.id,n['id'],embeds),
                                        file=discord.File(io.BytesIO(content.encode('utf-8')),filename=f'{kind}.txt'),
                                        ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logging.exception('Could not prepare %s report for nation %s',kind,n['id'])
        await interaction.followup.send(tr('Nie udało się przygotować raportu. Spróbuj ponownie lub zgłoś to GM-owi.',
                                          'Could not prepare the report. Try again or tell the GM.'),ephemeral=True)
