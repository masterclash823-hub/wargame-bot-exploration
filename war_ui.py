"""War inbox and explicit player confirmation of automatic battles."""
import asyncio
import json
import discord
import i18n
import war_service as wars
from world_service import tr
from technology_ui import deliver
from workflow_ui import choose
from cogs.panel import FieldsModal,invoke


RULES=lambda:tr(
    'Po potwierdzeniu przez obrońcę AI ocenia plany obu stron w każdej bitwie, także 1 na 1: mnożniki ×0,7–1,4 dla całej strony oraz taktyczny podział strat. Następnie bot rozlicza wynik i straty. Przy błędzie AI bitwa czeka na ponowienie. Statystyki, technologie, morale i fortyfikacje działają normalnie; los ataku 0,85–1,15. Granice zmienia zaakceptowany traktat. Wyzwanie wygasa po 2 miesiącach gry. Nietypową bitwę może rozegrać GM.',
    'After the defender confirms, AI assesses both plans in every battle, including one-on-one battles: modifiers ×0.7–1.4 for each whole side and tactical loss allocation. The bot then settles the result and losses. An AI error leaves the battle pending for retry. Stats, technology, morale and forts apply; attack roll 0.85–1.15. Borders change through an accepted treaty. Challenges expire after 2 game months. A GM can handle special battles.')


def inbox_embed(n,enemies,engagements):
    e=discord.Embed(title=tr('⚔️ Wojny — ','⚔️ Wars — ')+n['name'],color=discord.Color.red())
    e.description=tr('Przeciwnicy: ','Enemies: ')+(', '.join(x['name'] for x in enemies)[:1000] or '—')
    pending=[x for x in engagements if x['status']=='pending']
    incoming=sum(x['defender_id']==n['id'] for x in pending)
    e.add_field(name=tr('Co wymaga odpowiedzi','Awaiting action'),value=tr(
        f'{incoming} wyzwań do przyjęcia · {len(pending)-incoming} wysłanych wyzwań.\nPrzygotuj plan → wyślij wyzwanie → obrońca wybiera plan → potwierdzenie i wynik.',
        f'{incoming} incoming · {len(pending)-incoming} outgoing challenges.\nPrepare plan → challenge → defender chooses plan → confirm and resolve.'),inline=False)
    for x in pending[:6]:
        deadline=f"{x['expires_month']%12+1}/{x['expires_month']//12}"
        e.add_field(name=f"#{x['id']} {x['attacker']} → {x['defender']}"[:256],
            value=f"📍 #{x['cell_id']} {x['place'] or ''}\n"+tr('Termin: ','Deadline: ')+deadline+' · '+tr('czeka na obrońcę','waiting for defender'),inline=False)
    latest=[x for x in engagements if x['status']!='pending'][:5]
    statuses={'resolved':tr('rozstrzygnięta','resolved'),'declined':tr('odrzucona','declined'),
              'cancelled':tr('anulowana','cancelled'),'expired':tr('wygasła','expired')}
    if latest:e.add_field(name=tr('Ostatnie działania','Recent activity'),value='\n'.join(
        f"#{x['id']} {x['attacker']} → {x['defender']} · {statuses.get(x['status'],x['status'])}"+
        (f" · /battle view {x['battle_id']}" if x['battle_id'] else '') for x in latest)[:1024],inline=False)
    e.add_field(name=tr('Zasady automatycznej bitwy','Automatic battle rules'),value=RULES(),inline=False)
    return e


class WarView(discord.ui.View):
    def __init__(self,uid,bot,n,enemies,engagements,plans):
        super().__init__(timeout=600);self.uid=uid
        async def new_plan(i):await self.plan_menu(i,bot)
        async def challenge(i):
            async def target(j,nid):
                async def plan(k,pid):
                    async def submit(l,cell):
                        await l.response.defer(ephemeral=True)
                        try:eid=await asyncio.to_thread(wars.challenge,l.user.id,int(nid),int(pid),int(cell))
                        except ValueError as exc:await deliver(l,content=str(exc));return
                        await deliver(l,content=tr(f'Wysłano wyzwanie #{eid}. Obrońca znajdzie je w /war status.',f'Challenge #{eid} sent. The defender can find it in /war status.'))
                    await k.response.send_modal(FieldsModal(tr('Wyzwanie do bitwy','Battle challenge'),[dict(label=tr('ID pola bitwy','Battlefield cell ID'),max_length=10)],submit))
                await choose(j,plan_options(plans),plan,content=RULES())
            await choose(i,[discord.SelectOption(label=x['name'][:100],value=str(x['id'])) for x in enemies],target)
        async def respond(i):
            async def engagement(j,eid):
                async def plan(k,pid):await preview(k,int(eid),int(pid))
                await choose(j,plan_options(plans),plan,content=tr('Wybierz własny plan obrony. Potem zobaczysz potwierdzenie.','Choose your defense plan, then review confirmation.'))
            await choose(i,engagement_options([x for x in engagements if x['status']=='pending' and x['defender_id']==n['id']]),engagement)
        async def close(i):
            async def selected(j,eid):
                await j.response.defer(ephemeral=True)
                try:await asyncio.to_thread(wars.close,j.user.id,int(eid))
                except ValueError as exc:await deliver(j,content=str(exc));return
                await show(j,bot)
            await choose(i,engagement_options([x for x in engagements if x['status']=='pending']),selected)
        async def peace(i):
            async def target(j,nid):
                name=next(x['name'] for x in enemies if str(x['id'])==nid)
                await invoke(bot.get_cog('CombatCog'),'make_peace',j,name)
            await choose(i,[discord.SelectOption(label=x['name'][:100],value=str(x['id'])) for x in enemies],target)
        async def treaties(i):await invoke(bot.get_cog('TreatiesCog'),'list_treaties',i)
        async def reports(i):
            from cogs.panel import PlayerPanel
            await PlayerPanel(bot,i.user.id,i18n.current_language()).choose_battle(i)
        async def refresh(i):await show(i,bot)
        for label,callback in ((tr('Przygotuj plan','Prepare plan'),new_plan),(tr('Wyślij wyzwanie','Send challenge'),challenge),
            (tr('Odpowiedz na wyzwanie','Respond to challenge'),respond),(tr('Anuluj / odrzuć','Cancel / decline'),close),
            (tr('Zaproponuj pokój','Propose peace'),peace),(tr('Traktaty i propozycje','Treaties & proposals'),treaties),
            (tr('Raporty bitew','Battle reports'),reports),(tr('Odśwież','Refresh'),refresh)):
            b=discord.ui.Button(label=label)
            @i18n.localized
            async def clicked(i,callback=callback):
                if await self.interaction_check(i):await callback(i)
            b.callback=clicked;self.add_item(b)

    async def plan_menu(self,i,bot):
        from cogs.panel import PlayerPanel
        await PlayerPanel(bot,i.user.id,i18n.current_language()).choose_battle_units(i)

    async def interaction_check(self,i):
        if i.user.id==self.uid:return True
        await deliver(i,content=tr('To nie jest Twój panel.','This is not your panel.'));return False


def plan_options(plans):
    return [discord.SelectOption(label=f"Plan #{p['id']}",value=str(p['id']),
        description=(', '.join(str(x) for x in json.loads(p['provinces_json'] or '[]')) or tr('Bez lokalizacji','No location'))[:100]) for p in plans]


def engagement_options(rows):
    return [discord.SelectOption(label=f"#{x['id']} {x['attacker']} → {x['defender']}"[:100],value=str(x['id']),description=f"#{x['cell_id']} {x['place'] or ''}"[:100]) for x in rows]


async def preview(i,eid,pid):
    await i.response.defer(ephemeral=True)
    try:
        n,_,rows,plans=await asyncio.to_thread(wars.dashboard,i.user.id)
        e=next((x for x in rows if x['id']==eid and x['defender_id']==n['id'] and x['status']=='pending'),None)
        if not e or pid not in {p['id'] for p in plans}:raise ValueError(tr('Wyzwanie lub plan zmieniły się. Odśwież panel.','The challenge or plan changed. Refresh the panel.'))
    except ValueError as exc:await deliver(i,content=str(exc));return
    embed=discord.Embed(title=tr('Potwierdź obronę i rozliczenie','Confirm defense and settlement'),
        description=f"{e['attacker']} → {e['defender']}\n📍 #{e['cell_id']} {e['place'] or ''} · {i18n.term(e['terrain'])}\n"+
        tr('Fortyfikacje: ','Fortifications: ')+f"{e['fortification_level']} (×{1+.1*(e['fortification_level'] or 0):.1f})\n"+
        tr('Twój plan: ','Your plan: ')+f'#{pid}\n\n'+RULES())
    await deliver(i,embed=embed,view=DefenseConfirmation(i.user.id,eid,pid))


class DefenseConfirmation(discord.ui.View):
    def __init__(self,uid,eid,pid):
        super().__init__(timeout=600);self.uid,self.eid,self.pid=uid,eid,pid
        self.confirm.label=tr('Potwierdź i rozegraj bitwę','Confirm and fight')
    @discord.ui.button(label='Confirm',style=discord.ButtonStyle.danger)
    @i18n.localized
    async def confirm(self,i,button):
        if i.user.id!=self.uid:await deliver(i,content=tr('To nie jest Twoje potwierdzenie.','This is not your confirmation.'));return
        await i.response.defer(ephemeral=True)
        try:bid,report=await wars.defend_with_ai(i.user.id,self.eid,self.pid)
        except ValueError as exc:await deliver(i,content=str(exc));return
        names=lambda side:' + '.join(x['name'] for x in report[side+'_nations'])
        winner=tr('Remis','Draw') if report['winner']=='draw' else names(report['winner'])
        e=discord.Embed(title=tr(f'Bitwa #{bid} rozstrzygnięta',f'Battle #{bid} resolved'),description=f"{names('attacker')} ⚔️ {names('defender')}\n🏆 {winner}",color=discord.Color.gold())
        from cogs.combat import add_loss_fields
        add_loss_fields(e,report,report['attacker_forces'],report['defender_forces'])
        e.set_footer(text=f'/battle view {bid}')
        await deliver(i,embed=e)


async def show(i,bot):
    if not getattr(i.response,'is_done',lambda:False)():await i.response.defer(ephemeral=True)
    try:n,enemies,rows,plans=await asyncio.to_thread(wars.dashboard,i.user.id)
    except ValueError as exc:await deliver(i,content=str(exc));return
    await deliver(i,embed=inbox_embed(n,enemies,rows),view=WarView(i.user.id,bot,n,enemies,rows,plans))
