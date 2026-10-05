"""Six-category builder with a shared budget and live, private preview."""
import asyncio
import json
import discord
import i18n
import starting_bonuses as bonuses
import nation_applications as applications
from game_setup import budget
from world_service import tr
from utils import gm_only
from technology_ui import deliver,response_done


class StartingView(discord.ui.View):
    def __init__(self,uid,a,*,review=False,points=None,category='resources'):
        super().__init__(timeout=900)
        self.uid,self.a,self.review=uid,a,review
        self.points=dict(points if points is not None else bonuses.choices(a))
        self.total=budget(a['guild_id']) if a['status']=='pending' else sum(self.points.values());self.category=category;self.busy=False
        readonly=review or a['status']!='pending'
        if not readonly:
            select=discord.ui.Select(row=0,placeholder=tr('Kategoria bonusu','Bonus category'),options=[
                discord.SelectOption(label=tr(*label)+f' {self.points[k]}/10',value=k,default=k==category) for k,label in bonuses.FIELDS.items()])
            @i18n.localized
            async def selected(i):await self.redraw(i,category=select.values[0])
            select.callback=selected;self.add_item(select)
            values=discord.ui.Select(row=1,placeholder=tr('Liczba punktów','Points'),options=[
                discord.SelectOption(label=f'{value}/10',value=str(value),default=value==self.points[category]) for value in range(1 if category=='territory' else 0,11)])
            @i18n.localized
            async def changed(i):
                p=dict(self.points);p[category]=int(values.values[0]);await self.redraw(i,points=p)
            values.callback=changed;self.add_item(values)
            save=discord.ui.Button(label=tr('Zapisz wybór','Save choices'),style=discord.ButtonStyle.success,row=2,
                disabled=sum(self.points.values())!=self.total)
            @i18n.localized
            async def saved(i):
                if not await self.interaction_check(i):return
                if self.busy:await i.response.defer();return
                self.busy=True
                await i.response.defer()
                try:
                    await asyncio.to_thread(bonuses.save,self.uid,a['guild_id'],a['id'],a['version'],self.points)
                    fresh=await asyncio.to_thread(applications.get,a['id'],a['guild_id'],self.uid)
                    view=StartingView(self.uid,fresh)
                    await deliver(i,content=tr('Zapisano.','Saved.'),embed=view.embed(),view=view,edit=True)
                    self.stop()
                except ValueError as exc:await deliver(i,content=str(exc))
                finally:self.busy=False
            save.callback=saved;self.add_item(save)

    async def interaction_check(self,i):
        if i.user.id==self.uid and str(i.guild_id)==self.a['guild_id'] and (not self.review or gm_only(i)):return True
        await deliver(i,content=tr('Brak dostępu do kreatora.','Builder access unavailable.'));return False

    async def redraw(self,i,**kwargs):
        if not await self.interaction_check(i):return
        view=StartingView(self.uid,self.a,review=self.review,points=kwargs.get('points',self.points),category=kwargs.get('category',self.category))
        await deliver(i,embed=view.embed(),view=view,edit=True);self.stop()

    def embed(self):
        p=self.points;e=bonuses.effects(p);spent=sum(p.values())
        embed=discord.Embed(title=tr('Bonusy początkowe — ','Starting bonuses — ')+self.a['name'],
            description=tr(f'Wydano **{spent}/{self.total}** · pozostało **{self.total-spent}**. Zmiany działają dopiero po zapisaniu i akceptacji państwa przez GM-a.',
                           f'Spent **{spent}/{self.total}** · remaining **{self.total-spent}**. Changes apply only after saving and GM approval.'))
        details={'resources':i18n.resource_list(e['resources']),'gold':f"{e['gold']}g",
            'territory':tr(f"{e['territory']} prowincji; w formularzu: {len(json.loads(self.a['cells_json']))}. Pierwsza = stolica.",
                           f"{e['territory']} provinces; selected: {len(json.loads(self.a['cells_json']))}. First = capital."),
            'culture':tr('Ekspansja własnej kultury: ','Own culture expansion: ')+str(e['culture']),
            'religion':tr('Ekspansja własnej religii: ','Own religion expansion: ')+str(e['religion']),
            'technology':tr('Wszystkie cztery dziedziny: ','All four branches: ')+str(e['technology'])}
        for k,label in bonuses.FIELDS.items():embed.add_field(name=tr(*label)+f" {p[k]}/10",value=details[k],inline=False)
        embed.add_field(name=tr('Zasady balansu','Balance rules'),value=tr(
            '0 pkt daje podstawowe zaopatrzenie. Gotówka 200–800; surowce 50–150% pakietu; technologia 2,5–3,5; ekspansja 0,5–2. Wielkość: dokładnie 1–10 prowincji, po 1 pkt. Ziemię edytuj w /nation application. Siła to parametr mapy, nie premia do armii.',
            '0 points keeps basic supplies. Cash 200–800; resources 50–150% of the pack; technology 2.5–3.5; expansion 0.5–2. Size: exactly 1–10 provinces at 1 point each. Edit land in /nation application. Strength is a map parameter, not an army bonus.'),inline=False)
        try:bonuses.validate(p,self.total,json.loads(self.a['cells_json']))
        except ValueError as exc:embed.add_field(name=tr('Przed akceptacją','Before approval'),value=str(exc),inline=False)
        return embed


async def show(i,aid=None,review=False):
    if not response_done(i):await i.response.defer(ephemeral=True)
    if review and not gm_only(i):await deliver(i,content=tr('Tylko GM.','GM only.'));return
    a=await asyncio.to_thread(applications.get,aid,i.guild_id,None if review else i.user.id) if aid else await asyncio.to_thread(applications.latest,i.user.id,i.guild_id)
    if not a:await deliver(i,content=tr('Najpierw złóż formularz /nation found.','Submit /nation found first.'));return
    if bonuses.legacy(a):
        await deliver(i,content=tr('To państwo utworzono przed kreatorem punktów. Bonusów nie przyznaje się ponownie.',
                                  'This nation predates the points builder. Starting bonuses cannot be granted again.'));return
    view=StartingView(i.user.id,a,review=review)
    await deliver(i,embed=view.embed(),view=view)
