"""Resource market commands and a private, button-driven order book."""
import asyncio
import json
import uuid

import discord
from discord import app_commands
from discord.ext import commands

import i18n
import market_service as market
from nation_access import find_nation,can_manage
from world_service import tr
from technology_ui import deliver
from cogs.panel import FieldsModal


def number(value):
    return f'{value:.5f}'.rstrip('0').rstrip('.')


class MarketView(i18n.LocalizedView):
    def __init__(self,uid,nid,rows,mine=False,page=0):
        super().__init__(timeout=600)
        self.uid,self.nid,self.mine=uid,nid,mine
        page=min(page,max(0,(len(rows)-1)//20));self.page=page
        visible=rows[page*20:(page+1)*20]
        self.embed=discord.Embed(title=tr('Moje oferty' if mine else 'Wolny rynek','My offers' if mine else 'Open market'),color=discord.Color.gold())
        self.embed.description=tr('Wybierz ofertę, aby zwrócić niesprzedany towar.','Choose an offer to reclaim unsold stock.') if mine else tr(
            'Najtańsza oferta każdego surowca od innych państw. Cena za 1 jednostkę w złocie. Droższe oferty czekają w kolejce. Wybierz surowiec, wpisz ilość i potwierdź zakup.',
            'Cheapest offer per resource from other nations. Prices are gold per unit. More expensive offers stay queued. Choose a resource, enter quantity and confirm.')
        for row in visible:
            self.embed.add_field(name=f"#{row['id']} · {i18n.term(row['resource'])}",value=f"{number(row['remaining']/1000)} × {number(row['price_cents']/100)} 🪙 · {row['seller'][:80]}",inline=False)
        if not visible:self.embed.description+='\n'+tr('Brak aktywnych ofert.','No open offers.')
        self.embed.set_footer(text=f'{page+1}/{max(1,(len(rows)+19)//20)}')
        if visible:
            select=discord.ui.Select(row=0,placeholder=tr('Wybierz ofertę','Choose an offer'),options=[
                discord.SelectOption(label=f"{i18n.term(r['resource'])} · {number(r['price_cents']/100)} 🪙"[:100],
                    value=str(r['id']),description=f"#{r['id']} · {number(r['remaining']/1000)} · {r['seller']}"[:100]) for r in visible])
            async def selected(i):
                row=next(r for r in visible if r['id']==int(select.values[0]))
                if mine:
                    view=ConfirmView(self.uid,self.nid,offer_id=row['id'])
                    await deliver(i,content=tr('Wycofać ofertę i zwrócić pozostały towar?','Cancel this offer and return remaining stock?'),view=view)
                else:
                    async def submit(j,quantity):
                        if await self.interaction_check(j):await prepare_purchase(j,row['resource'],quantity)
                    await i.response.send_modal(FieldsModal(tr('Kup surowiec','Buy resource'),[
                        dict(label=tr('Ilość (do 3 miejsc po przecinku)','Quantity (up to 3 decimal places)'),default=f"{number(row['remaining']/1000)}",max_length=20)],submit))
            select.callback=i18n.localized(selected);self.add_item(select)
        buttons=[('sell',tr('Sprzedaj','Sell')),('switch',tr('Rynek' if mine else 'Moje oferty','Market' if mine else 'My offers')),
                 ('refresh',tr('Odśwież','Refresh'))]
        if page>0:buttons.append(('prev','◀'))
        if (page+1)*20<len(rows):buttons.append(('next','▶'))
        for key,label in buttons:
            button=discord.ui.Button(label=label,row=1)
            async def clicked(i,key=key):
                if key=='sell':
                    async def submit(j,resource,quantity,price):
                        if not await self.interaction_check(j):return
                        await j.response.defer(ephemeral=True)
                        try:
                            oid=await asyncio.to_thread(market.sell,self.uid,resource,quantity,price,self.nid)
                            await deliver(j,content=tr('Oferta zapisana, surowiec zarezerwowany: #','Offer saved, stock reserved: #')+str(oid))
                        except ValueError as exc:await deliver(j,content=str(exc))
                    await i.response.send_modal(FieldsModal(tr('Sprzedaj na rynku','Sell on the market'),[
                        dict(label=tr('Surowiec, np. drewno','Resource, e.g. wood'),max_length=40),
                        dict(label=tr('Ilość (do 3 miejsc po przecinku)','Quantity (up to 3 decimal places)'),max_length=20),
                        dict(label=tr('Złoto za 1 jednostkę (do 2 miejsc)','Gold per unit (up to 2 decimals)'),max_length=20)],submit))
                else:await show(i,mine=not mine if key=='switch' else mine,page=page+({'prev':-1,'next':1}.get(key,0)),edit=True)
            button.callback=i18n.localized(clicked);self.add_item(button)

    async def interaction_check(self,i):
        if i.user.id==self.uid and can_manage(self.nid,self.uid):return True
        await deliver(i,content=tr('To nie jest twój panel lub utracono dostęp do państwa.','This is not your panel or nation access was revoked.'));return False


class ConfirmView(i18n.LocalizedView):
    def __init__(self,uid,nid,quote=None,offer_id=None):
        super().__init__(timeout=180)
        self.uid,self.nid,self.quote,self.offer_id=uid,nid,quote,offer_id
        self.request_id=uuid.uuid4().hex
        button=discord.ui.Button(label=tr('Potwierdź zakup' if quote else 'Wycofaj ofertę','Confirm purchase' if quote else 'Cancel offer'),style=discord.ButtonStyle.success if quote else discord.ButtonStyle.danger)
        button.callback=i18n.localized(self.confirm);self.add_item(button)

    async def interaction_check(self,i):
        if i.user.id==self.uid and can_manage(self.nid,self.uid):return True
        await deliver(i,content=tr('Brak dostępu do tej operacji.','You cannot perform this action.'));return False

    async def confirm(self,i):
        if not await self.interaction_check(i):return
        await i.response.defer(ephemeral=True)
        try:
            if self.quote:
                q=self.quote
                result=await asyncio.to_thread(market.buy,self.uid,q['id'],q['units']/1000,self.request_id,self.nid)
                text=tr('Zakup rozliczony: ','Purchase settled: ')+f"{number(result['units']/1000)} {i18n.term(result['resource'])} · {number(result['cost'])} 🪙"
            else:
                await asyncio.to_thread(market.cancel,self.uid,self.offer_id)
                text=tr('Oferta wycofana. Niesprzedany surowiec wrócił do państwa.','Offer cancelled. Unsold stock returned to the nation.')
            for child in self.children:child.disabled=True
            await i.edit_original_response(content=text,embed=None,view=self)
        except ValueError as exc:await deliver(i,content=str(exc))


async def show(i,mine=False,page=0,edit=False):
    await i.response.defer(ephemeral=True)
    try:
        n,rows=await asyncio.to_thread(market.book,i.user.id,mine)
        view=MarketView(i.user.id,n['id'],rows,mine,page)
        await deliver(i,embed=view.embed,view=view,edit=edit)
    except ValueError as exc:await deliver(i,content=str(exc))


async def prepare_purchase(i,resource,quantity):
    await i.response.defer(ephemeral=True)
    try:
        q=await asyncio.to_thread(market.quote,i.user.id,resource,quantity)
        text=f"{number(q['units']/1000)} {i18n.term(q['resource'])} × {number(q['price_cents']/100)} 🪙 = **{number(q['cost'])} 🪙**\n{q['seller']} · #{q['id']}"
        await deliver(i,content=text,view=ConfirmView(i.user.id,q['buyer_id'],quote=q))
    except ValueError as exc:await deliver(i,content=str(exc))


@i18n.localized
async def resource_choices(i,current):
    n=find_nation(i.user.id);keys=set(market.RESOURCES)
    if n:keys.update(json.loads(n['resources_json']))
    return [app_commands.Choice(name=i18n.term(k),value=k) for k in sorted(keys)
            if current.casefold() in (k+' '+i18n.term(k)).casefold()][:25]


class MarketCog(commands.Cog):
    market_group=app_commands.Group(name='market',description='Player resource market / Wolny rynek surowców')

    @market_group.command(name='list',description='Cheapest resource offers / Najtańsze oferty surowców')
    @i18n.localized
    async def market_list(self,i:discord.Interaction):await show(i)

    @market_group.command(name='mine',description='Your active offers, including queued ones / Twoje aktywne oferty')
    @i18n.localized
    async def mine(self,i:discord.Interaction):await show(i,mine=True)

    @market_group.command(name='sell',description='Reserve resources for sale / Wystaw surowce na sprzedaż')
    @app_commands.describe(resource='Resource to sell / Surowiec na sprzedaż',quantity='Quantity, up to 3 decimals / Ilość, do 3 miejsc po przecinku',price='Gold per unit, up to 2 decimals / Złoto za jednostkę, do 2 miejsc')
    @app_commands.autocomplete(resource=resource_choices)
    @i18n.localized
    async def sell(self,i:discord.Interaction,resource:str,quantity:float,price:float):
        await i.response.defer(ephemeral=True)
        try:
            oid=await asyncio.to_thread(market.sell,i.user.id,resource,quantity,price)
            await deliver(i,content=tr('Oferta zapisana, surowiec zarezerwowany: #','Offer saved, stock reserved: #')+str(oid))
        except ValueError as exc:await deliver(i,content=str(exc))

    @market_group.command(name='buy',description='Preview purchase at the cheapest price / Kup po najniższej cenie')
    @app_commands.autocomplete(resource=resource_choices)
    @i18n.localized
    async def buy(self,i:discord.Interaction,resource:str,quantity:float):await prepare_purchase(i,resource,quantity)

    @market_group.command(name='cancel',description='Withdraw your unsold offer / Wycofaj swoją ofertę')
    @i18n.localized
    async def cancel(self,i:discord.Interaction,offer_id:int):
        await i.response.defer(ephemeral=True)
        try:
            n,rows=await asyncio.to_thread(market.book,i.user.id,True)
            if not any(r['id']==offer_id for r in rows):raise ValueError(tr('Nie znaleziono twojej aktywnej oferty.','Your open offer was not found.'))
            await deliver(i,content=tr('Wycofać ofertę?','Cancel this offer?'),view=ConfirmView(i.user.id,n['id'],offer_id=offer_id))
        except ValueError as exc:await deliver(i,content=str(exc))


async def setup(bot):await bot.add_cog(MarketCog())
