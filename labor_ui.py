"""Private worker controls with province/building pagination and explicit numbers."""
import asyncio

import discord
import db
import i18n
import labor
from economy_engine import forecast
from technology import tr
from technology_ui import PrivateView,deliver,response_done


class WorkersModal(discord.ui.Modal):
    def __init__(self,nid,uid,cell,key,default):
        super().__init__(title=tr('Przydziel pracowników','Assign workers'),timeout=600)
        self.nid,self.uid,self.cell,self.key=nid,uid,cell,key
        self.number=discord.ui.TextInput(label=tr('Liczba ludzi; puste = automat','Workers; empty = automatic'),
                                        required=False,default=default,max_length=9,
                                        placeholder=tr('0 wyłącza obsadę budynku','0 leaves the building unstaffed'))
        self.add_item(self.number)

    @i18n.localized
    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            if interaction.user.id!=self.uid:raise ValueError(i18n.text('This is not your menu.'))
            text=self.number.value.strip()
            try:workers=int(text) if text else None
            except ValueError:raise ValueError(tr('Podaj całkowitą liczbę ludzi albo pozostaw puste pole.', 'Enter a whole number or leave the field empty.'))
            await asyncio.to_thread(labor.set_assignment,self.nid,interaction.user.id,self.cell,self.key,workers)
            await show(interaction,self.nid,self.cell,edit=True)
        except ValueError as exc:await deliver(interaction,content=str(exc))


class LaborView(PrivateView):
    def __init__(self,nid,uid,provinces,cell,staff,page):
        super().__init__(nid,uid)
        entries=provinces if cell is None else staff
        page=max(0,min(page,(len(entries)-1)//20))
        if entries:
            options=[]
            for row in entries[page*20:(page+1)*20]:
                if cell is None:
                    options.append(discord.SelectOption(label=f"#{row['azgaar_cell_id']} · {row['name']}"[:100],value=str(row['azgaar_cell_id'])))
                else:
                    options.append(discord.SelectOption(label=i18n.term(row['building'])[:100],value=row['building'],
                        description=tr('Obsada: ','Staffing: ')+f"{row['workers']:g}/{row['need']:g}"))
            select=discord.ui.Select(placeholder=tr('Wybierz prowincję' if cell is None else 'Wybierz budynek',
                                                  'Choose a province' if cell is None else 'Choose a building'),options=options,row=0)
            @i18n.localized
            async def selected(i):
                if not await self.interaction_check(i):return
                if cell is None:
                    await i.response.defer(ephemeral=True)
                    try:await show(i,nid,int(select.values[0]),edit=True)
                    except ValueError as exc:await deliver(i,content=str(exc))
                else:
                    item=next(s for s in staff if s['building']==select.values[0])
                    default=str(int(item['requested'])) if item.get('manual') else ''
                    await i.response.send_modal(WorkersModal(nid,i.user.id,cell,item['building'],default))
            select.callback=selected;self.add_item(select)
        if page:
            async def previous(i):await show(i,nid,cell,page-1,edit=True)
            self.button(tr('Poprzednia strona','Previous page'),previous,row=1)
        if (page+1)*20<len(entries):
            async def next_page(i):await show(i,nid,cell,page+1,edit=True)
            self.button(tr('Następna strona','Next page'),next_page,row=1)
        if cell is not None:
            async def reset(i):
                await asyncio.to_thread(labor.set_assignment,nid,i.user.id,cell)
                await show(i,nid,cell,edit=True)
            async def back(i):await show(i,nid,edit=True)
            self.button(tr('Automat w tej prowincji','Automatic for this province'),reset,row=2)
            self.button(tr('Inna prowincja','Other province'),back,row=2)
        async def refresh(i):await show(i,nid,cell,page,edit=True)
        self.button(tr('Odśwież','Refresh'),refresh,row=2)


async def show(interaction,nid,cell=None,page=0,*,edit=False):
    if not response_done(interaction):await interaction.response.defer(ephemeral=True)
    with db.cursor() as c:
        c.execute('SELECT owner_id FROM nations WHERE id=?',(nid,));n=c.fetchone()
        if not n or n['owner_id']!=str(interaction.user.id):raise ValueError(i18n.text('This is not your menu.'))
        c.execute('SELECT azgaar_cell_id,name,population FROM provinces WHERE owner_nation_id=? AND active=1 ORDER BY azgaar_cell_id',(nid,))
        provinces=c.fetchall()
    e=discord.Embed(title='👥 '+tr('Pracownicy','Workers'),color=discord.Color.gold())
    e.description=tr('Domyślnie działa automat. Ręcznie rezerwujesz ludzi dla wybranych budynków; pozostali trafiają automatycznie najpierw do żywności. 0 wyłącza obsadę, puste pole przywraca automat. Ręczne przydziały mogą spowodować brak żywności.',
                     'Automatic by default. Reserve workers for selected buildings; the rest are assigned automatically, food first. 0 leaves a building unstaffed; an empty field restores automatic staffing. Manual assignments may cause food shortages.')
    staff=[]
    if cell is not None:
        p=next((p for p in provinces if p['azgaar_cell_id']==cell),None)
        if not p:raise ValueError(tr('Prowincja nie należy do Ciebie.', 'This province is not yours.'))
        r=await asyncio.to_thread(forecast,nid)
        with db.cursor() as c:
            c.execute('SELECT owner_id FROM nations WHERE id=?',(nid,));current=c.fetchone()
            if not current or current['owner_id']!=str(interaction.user.id):raise ValueError(i18n.text('This is not your menu.'))
        staff=[s for s in r['staffing'] if s['cell']==cell]
        page=max(0,min(page,max(0,(len(staff)-1)//20)))
        available=max(0,p['population'])*.4;used=sum(s['workers'] for s in staff)
        e.add_field(name=f"#{cell} · {p['name']}"[:256],value=tr('Dostępni (40% mieszkańców): ','Available (40% of population): ')+f'{available:g}\n'+
                    tr('Przydzieleni: ','Assigned: ')+f'{used:g} · '+tr('Wolni: ','Idle: ')+f'{max(0,available-used):g}',inline=False)
        if any(s.get('scaled') for s in staff):
            e.description+='\n⚠️ '+tr('Populacja spadła: przydziały proporcjonalnie zmniejszono.', 'Population fell: reservations have been reduced proportionally.')
        if r['food_shortage']:e.description+='\n⚠️ '+tr('Prognoza państwa wskazuje niedobór żywności.', 'The national forecast shows a food shortage.')
        visible=staff[page*20:(page+1)*20]
        lines=[f"{i18n.term(s['building'])}: {s['workers']:g}/{s['need']:g} · "+
               (tr('nieaktywne','inactive') if s.get('blocked') else tr('ręcznie','manual') if s.get('manual') else tr('automat','automatic')) for s in visible]
        if lines:e.description+='\n\n'+'\n'.join(lines)
        else:e.description+='\n'+tr('Brak budynków w tej prowincji.', 'No buildings in this province.')
    elif not provinces:e.description+='\n'+tr('Brak własnych prowincji.', 'You have no provinces.')
    e.set_footer(text=tr('Prognoza używa tych samych przydziałów co miesięczne rozliczenie.', 'The forecast uses the same assignments as monthly settlement.'))
    await deliver(interaction,embed=e,view=LaborView(nid,interaction.user.id,provinces,cell,staff,page),edit=edit)
