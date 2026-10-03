"""Private terraforming quotes; opening and browsing never spend resources."""
import asyncio
import json
import logging

import discord
import i18n
import terraforming as service
from world_service import tr

PAGE_SIZE=25
log=logging.getLogger(__name__)


def safe(value,limit=150):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))[:limit]


def resources(values):
    return ', '.join(f'{i18n.term(key)}: {value:g}' for key,value in values.items()) or '—'


def preview_embed(data,page):
    embed=discord.Embed(title=tr('Terraformacja — ','Terraforming — ')+safe(data['nation']['name']),
                        color=discord.Color.green())
    p,q=data['selected'],data['quote']
    embed.description=tr('Wybierz własną prowincję, aby obejrzeć projekt i koszty przed zatwierdzeniem.',
                         'Choose your own province to review the project and costs before confirming.')
    if not data['rows']:
        embed.description=tr('Nie masz aktywnych prowincji lądowych.','You have no active land provinces.')
    if p:
        embed.description=f"**#{p['azgaar_cell_id']} · {safe(p['name'] or tr('Prowincja','Province'))}**"
        embed.add_field(name=tr('Obecny biom / teren','Current biome / terrain'),
                        value=f"{i18n.term(p['biome'])} / {i18n.term(p['terrain'])}")
        embed.add_field(name=tr('Ludność','Population'),value=str(p['population']))
        if not data['choices']:
            embed.add_field(name=tr('Brak projektu','No project'),inline=False,
                            value=tr('Dla tego biomu lub terenu nie ma bezpiecznej terraformacji. Nie zmieniamy wody, gór ani lodowców.',
                                     'No terraforming project is available for this biome or terrain. Water, mountains and glaciers cannot be changed.'))
    if q:
        embed.add_field(name=service.name(q['key']),value=tr('Docelowy biom: ','Target biome: ')+i18n.term(q['target']['biome']),inline=False)
        embed.add_field(name=tr('Koszt jednorazowy','One-time cost'),value=resources(q['cost']),inline=False)
        embed.add_field(name=tr('Czas prac','Construction time'),
                        value=f"{q['months']} "+tr('mies. gry; koniec: ','game months; completes: ')+service.date(q['due']))
        embed.add_field(name=tr('Wymagania','Requirements'),
                        value=tr('Technologia gospodarcza','Economy technology')+f" {q['tech']} · "+
                        tr('stabilność ≥40 · ludność ≥1000','stability ≥40 · population ≥1000'),inline=False)
        embed.add_field(name=tr('Zasoby bazowe: teraz → po ukończeniu','Base resources: now → after completion'),
                        value=(resources(json.loads(p['base_resources_json']))+'\n→ '+
                               resources(json.loads(q['target']['base_resources_json'])))[:1000],inline=False)
        if q['reasons']:
            embed.add_field(name=tr('Nie można rozpocząć','Cannot start'),value='\n'.join(q['reasons'])[:1000],inline=False)
    embed.add_field(name=tr('Zasady','Rules'),inline=False,value=tr(
        'Jeden projekt naraz na państwo. Po ukończeniu: 12 mies. przerwy w tej prowincji. '
        'Zapłata z góry; nowy biom działa od kolejnego miesiąca po ukończeniu. Populacja nie wzrasta od terraformacji. '
        'Utrata prowincji, upadek państwa lub niezgodna zmiana mapy/budynków przerywa prace bez zwrotu kosztów.',
        'One project at a time per nation. After completion: a 12-month cooldown in this province. '
        'Paid upfront; the new biome produces from the month after completion. Terraforming does not increase population. '
        'Losing the province, nation collapse or incompatible map/building changes cancel work without a refund.'))
    if data['projects']:
        lines=[]
        for project in data['projects']:
            if project['status']=='building':
                remaining=max(0,project['due_month']-data['month'])
                status=tr('w toku, pozostało ','underway, remaining ')+str(remaining)+tr(' mies.',' months')
            elif project['status']=='complete':
                status=tr('ukończono ','completed ')+service.date(project['finished_month'])
            else:
                status=tr('przerwano: ','cancelled: ')+service.reason_text(project['reason'])
            lines.append(f"#{project['azgaar_cell_id']} · {service.name(project['project_key'])}: {status}")
        embed.add_field(name=tr('Ostatnie projekty','Recent projects'),value='\n'.join(lines)[:1000],inline=False)
    embed.set_footer(text=tr('Strona','Page')+f" {page+1}/{max(1,(len(data['rows'])+PAGE_SIZE-1)//PAGE_SIZE)}")
    return embed


class TerraformView(discord.ui.View):
    def __init__(self,uid,data,page=0):
        super().__init__(timeout=600)
        self.uid,self.nid,self.lang=uid,data['nation']['id'],i18n.current_language()
        self.selected,self.quote=data['selected'],data['quote']
        rows=data['rows']
        if self.selected:page=rows.index(self.selected)//PAGE_SIZE
        self.page=min(max(0,page),max(0,(len(rows)-1)//PAGE_SIZE))
        self.busy=False
        self.embed=preview_embed(data,self.page)
        visible=rows[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
        if visible:
            select=discord.ui.Select(placeholder=tr('Wybierz prowincję','Choose a province'),row=0,
                options=[discord.SelectOption(label=f"#{p['azgaar_cell_id']} · {p['name'] or tr('Prowincja','Province')}"[:100],
                    description=f"{i18n.term(p['biome'])} · {p['population']}"[:100],value=str(p['azgaar_cell_id']),
                    default=self.selected is not None and p['id']==self.selected['id']) for p in visible])
            async def selected(i):await self.browse(i,cell=int(select.values[0]))
            select.callback=selected;self.add_item(select)
        # Each current source biome has one balanced conversion; retain a picker
        # for future alternatives instead of accepting arbitrary target biomes.
        if len(data['choices'])>1:
            choice=discord.ui.Select(placeholder=tr('Wybierz projekt','Choose a project'),row=1,
                options=[discord.SelectOption(label=service.name(key),value=key,
                    default=bool(self.quote and self.quote['key']==key)) for key in data['choices']])
            async def chosen(i):await self.browse(i,cell=self.selected['azgaar_cell_id'],key=choice.values[0])
            choice.callback=chosen;self.add_item(choice)
        self.confirm_button=discord.ui.Button(label=tr('Zapłać i rozpocznij','Pay and start'),row=2,
            style=discord.ButtonStyle.success,disabled=not self.quote or bool(self.quote['reasons']))
        self.confirm_button.callback=self.confirm;self.add_item(self.confirm_button)
        for label,number in ((tr('Poprzednia','Previous'),self.page-1),(tr('Następna','Next'),self.page+1)):
            button=discord.ui.Button(label=label,row=2,disabled=number<0 or number*PAGE_SIZE>=len(rows))
            async def clicked(i,number=number):await self.browse(i,page=number)
            button.callback=clicked;self.add_item(button)
        refresh=discord.ui.Button(label=tr('Odśwież','Refresh'),row=2)
        async def refreshed(i):
            await self.browse(i,page=self.page,cell=self.selected['azgaar_cell_id'] if self.selected else None)
        refresh.callback=refreshed;self.add_item(refresh)
        close=discord.ui.Button(label=tr('Zamknij','Close'),row=2)
        close.callback=self.close;self.add_item(close)

    async def interaction_check(self,i):
        if i.user.id==self.uid:return True
        with i18n.using_language(self.lang):
            await i.response.send_message(tr('To nie jest twój panel.','This is not your panel.'),ephemeral=True)
        return False

    async def _ack(self,i):
        if not await self.interaction_check(i):return False
        await i.response.defer()
        with i18n.using_language(self.lang):
            if self.is_finished():
                await i.followup.send(tr('Panel wygasł. Otwórz /province terraform.',
                                         'Panel expired. Open /province terraform.'),ephemeral=True)
                return False
            if self.busy:
                await i.followup.send(tr('Poczekaj na wynik poprzedniego kliknięcia.',
                                         'Wait for the previous click to finish.'),ephemeral=True)
                return False
        self.busy=True
        return True

    async def browse(self,i,*,page=0,cell=None,key=None):
        if not await self._ack(i):return
        with i18n.using_language(self.lang):
            try:
                data=await asyncio.to_thread(service.preview,self.uid,cell,key,nation_id=self.nid)
                view=TerraformView(self.uid,data,page)
                await i.edit_original_response(content=None,embed=view.embed,view=view)
                self.stop()
            except ValueError as exc:await i.followup.send(str(exc),ephemeral=True)
            finally:self.busy=False

    async def confirm(self,i):
        if not await self._ack(i):return
        with i18n.using_language(self.lang):
            try:
                if not self.selected or not self.quote:return
                result=await asyncio.to_thread(service.start,self.uid,self.selected['azgaar_cell_id'],self.quote['key'],
                    nation_id=self.nid,expected_biome=self.selected['biome'],expected_terrain=self.selected['terrain'])
            except ValueError as exc:
                await i.followup.send(str(exc),ephemeral=True)
                return
            except Exception:
                log.exception('Terraforming start failed for nation %s',self.nid)
                await i.followup.send(tr('Nie udało się potwierdzić rozpoczęcia prac. Otwórz ponownie terraformację, aby sprawdzić projekty.',
                                         'Could not confirm project creation. Reopen terraforming to check your projects.'),ephemeral=True)
                return
            finally:self.busy=False
            self.stop()
            await i.edit_original_response(content=tr('Rozpoczęto terraformację','Terraforming started')+
                f" #{result['cell']} · {service.name(result['key'])}\n"+
                tr('Pobrano: ','Paid: ')+resources(result['cost'])+'\n'+
                tr('Planowane ukończenie: ','Expected completion: ')+service.date(result['due']),embed=None,view=None)

    async def close(self,i):
        if not await self._ack(i):return
        self.stop()
        with i18n.using_language(self.lang):
            await i.edit_original_response(content=tr('Zamknięto podgląd.','Preview closed.'),embed=None,view=None)


async def show(interaction,cell=None):
    await interaction.response.defer(ephemeral=True)
    try:
        data=await asyncio.to_thread(service.preview,interaction.user.id,cell)
        view=TerraformView(interaction.user.id,data)
        await interaction.followup.send(embed=view.embed,view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)
