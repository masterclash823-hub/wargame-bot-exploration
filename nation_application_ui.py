"""Application form, player status and a server-scoped GM review queue."""
import asyncio
import json
import discord
import i18n
import nation_applications as applications
from world_service import tr
from technology_ui import deliver,response_done
from workflow_ui import choose
from cogs.panel import FieldsModal
from utils import gm_only


def embed(a):
    labels={'pending':tr('Czeka na GM-a','Awaiting GM approval'),'approved':tr('Zaakceptowane','Approved'),
            'rejected':tr('Odrzucone','Rejected'),'withdrawn':tr('Wycofane','Withdrawn')}
    e=discord.Embed(title=tr('Zgłoszenie państwa','Nation application')+f" #{a['id']} · {a['name']}",color=discord.Color.gold())
    e.description=f"<@{a['player_id']}> · **{labels[a['status']]}**\n"+tr('Ustrój: ','Government: ')+(a['government'] or tr('Monarchia','Monarchy'))
    for start in range(0,len(a['history']),1000):
        e.add_field(name=tr('Historia','Lore'),value=a['history'][start:start+1000],inline=False)
    ids=json.loads(a['cells_json'])
    e.add_field(name=tr('Prowincje i stolica','Provinces and capital'),value=tr('Stolica: ','Capital: ')+f"#{ids[0]}\n"+', '.join(f'#{x}' for x in ids),inline=False)
    e.add_field(name=tr('Po akceptacji','On approval'),value=tr('Własność prowincji, stolica, projekty jednostek i jednorazowy pakiet: ','Province ownership, capital, unit blueprints and a one-time starter pack: ')+f'{applications.STARTER_GOLD}g · '+i18n.resource_list(applications.STARTER),inline=False)
    if a['reason']:e.add_field(name=tr('Powód decyzji','Decision reason'),value=a['reason'],inline=False)
    if a['flag']:e.add_field(name=tr('Flaga','Flag'),value=a['flag'][:512],inline=False)
    e.set_footer(text=tr('Do akceptacji zgłoszenie nie daje dostępu do państwa ani nie rezerwuje ziemi.','Until approval the application grants no nation access and reserves no land.'))
    return e


async def form(i,a=None):
    uid=i.user.id;guild_id=i.guild_id
    async def submit(j,name,history,government,flag,ids):
        if j.user.id!=uid or j.guild_id!=guild_id:
            await deliver(j,content=tr('To nie jest Twoje zgłoszenie.','This is not your application.'));return
        await j.response.defer(ephemeral=True)
        try:
            aid=await asyncio.to_thread(applications.submit,uid,guild_id,name,history,flag,government,ids,a['version'] if a else None)
            current=await asyncio.to_thread(applications.get,aid,guild_id,uid)
        except ValueError as exc:await deliver(j,content=str(exc));return
        await deliver(j,embed=embed(current),view=ApplicationView(uid,guild_id,current))
    fields=[dict(label=tr('Nazwa państwa','Nation name'),max_length=80,default=a['name'] if a else None),
        dict(label=tr('Historia państwa','Nation lore'),max_length=4000,style=discord.TextStyle.paragraph,default=a['history'] if a else None),
        dict(label=tr('Ustrój','Government'),required=False,max_length=80,default=a['government'] if a else None),
        dict(label=tr('Flaga: emoji lub URL','Flag: emoji or URL'),required=False,max_length=512,default=a['flag'] if a else None),
        dict(label=tr('ID prowincji (pierwsza = stolica)','Province IDs (first = capital)'),max_length=300,
             placeholder=tr('1–25 sąsiadujących wolnych pól lądu, np. 10,11','1–25 connected unclaimed land cells, e.g. 10,11'),default=','.join(str(x) for x in json.loads(a['cells_json'])) if a else None)]
    await i.response.send_modal(FieldsModal(tr('Zgłoś państwo','Apply for a nation'),fields,submit))


class ApplicationView(discord.ui.View):
    def __init__(self,uid,guild_id,a,review=False):
        super().__init__(timeout=900);self.uid,self.guild_id,self.a,self.review=uid,str(guild_id),a,review
        async def approve(i):
            await self.decision(i,'approve')
        async def reject(i):
            async def submit(j,reason):
                if await self.interaction_check(j):await self.decision(j,'reject',reason)
            await i.response.send_modal(FieldsModal(tr('Odrzuć zgłoszenie','Reject application'),[dict(label=tr('Powód dla gracza','Reason for player'),max_length=500)],submit))
        async def edit(i):await form(i,a)
        async def withdraw(i):await self.decision(i,'withdraw')
        async def refresh(i):
            await i.response.defer(ephemeral=True)
            current=await asyncio.to_thread(applications.get,a['id'],self.guild_id,None if review else uid)
            await deliver(i,embed=embed(current),view=ApplicationView(uid,guild_id,current,review))
        actions=[(tr('Odśwież','Refresh'),refresh)]
        if a['status']=='pending':actions=([(tr('Akceptuj i utwórz','Approve and create'),approve),(tr('Odrzuć z powodem','Reject with reason'),reject)] if review else
            [(tr('Edytuj zgłoszenie','Edit application'),edit),(tr('Wycofaj','Withdraw'),withdraw)])+actions
        for label,callback in actions:
            b=discord.ui.Button(label=label)
            @i18n.localized
            async def clicked(i,callback=callback):
                if not await self.interaction_check(i):return
                try:await callback(i)
                except ValueError as exc:await deliver(i,content=str(exc))
            b.callback=clicked;self.add_item(b)

    async def interaction_check(self,i):
        if i.user.id==self.uid and str(i.guild_id)==self.guild_id and (not self.review or gm_only(i)):return True
        await deliver(i,content=tr('Brak dostępu do tego zgłoszenia lub uprawnień GM.','Application access or GM permission unavailable.'));return False

    async def decision(self,i,action,reason=''):
        if not await self.interaction_check(i):return
        if action in ('approve','reject') and not (self.review and gm_only(i)):
            await deliver(i,content=i18n.t(i18n.current_language(),'gm_only'));return
        await i.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(applications.decide,self.a['id'],self.guild_id,i.user.id,self.a['version'],action,reason)
            a=await asyncio.to_thread(applications.get,self.a['id'],self.guild_id)
        except ValueError as exc:await deliver(i,content=str(exc));return
        await deliver(i,embed=embed(a),view=ApplicationView(self.uid,self.guild_id,a,self.review))


async def show(i):
    if not response_done(i):await i.response.defer(ephemeral=True)
    a=await asyncio.to_thread(applications.latest,i.user.id,i.guild_id)
    if not a:await deliver(i,content=tr('Zgłoś państwo przez /nation found lub przycisk „Zgłoś państwo” w /panel.','Apply using /nation found or the “Apply for a nation” button in /panel.'));return
    await deliver(i,embed=embed(a),view=ApplicationView(i.user.id,i.guild_id,a))


async def review_queue(i):
    if not gm_only(i):await deliver(i,content=i18n.t(i18n.current_language(),'gm_only'));return
    await i.response.defer(ephemeral=True)
    rows=await asyncio.to_thread(applications.pending,i.guild_id)
    async def selected(j,aid):
        if not gm_only(j):await deliver(j,content=i18n.t(i18n.current_language(),'gm_only'));return
        await j.response.defer(ephemeral=True)
        a=await asyncio.to_thread(applications.get,int(aid),j.guild_id)
        await deliver(j,embed=embed(a),view=ApplicationView(j.user.id,j.guild_id,a,True))
    await choose(i,[discord.SelectOption(label=f"#{a['id']} {a['name']}"[:100],value=str(a['id'])) for a in rows],selected)
