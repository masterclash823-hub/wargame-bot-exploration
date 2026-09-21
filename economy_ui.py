"""Simple economy dashboard; advanced choices are optional."""
import asyncio
import io
import json
import logging
import math
from typing import Literal
import discord
from discord import app_commands
from discord.ext import commands
import db
import i18n
from flags import flagged_embed
from utils import gm_only,get_nation_by_owner
from economy_engine import forecast,set_policy,read_json
import economy_services as services


def tr(pl,en): return pl if i18n.current_language()=='pl' else en


def inventory_text(n):
    try: resources=read_json(n['resources_json'])
    except (ValueError,TypeError):
        return tr('Nie można odczytać magazynu. GM powinien sprawdzić dane państwa.',
                  'Cannot read the stockpile. Ask the GM to check this nation’s data.')
    if not isinstance(resources,dict):
        return tr('Nieprawidłowe dane magazynu. Zgłoś to GM-owi.','Invalid stockpile data. Please tell the GM.')
    lines=[]
    for key,value in sorted(resources.items()):
        try:
            amount=float(value)
            shown=f'{amount:g}' if math.isfinite(amount) else '?'
        except (ValueError,TypeError): shown='?'
        lines.append(f'{i18n.term(key)}: {shown}')
    return '\n'.join(lines) or '—'


def safe_forecast(nid):
    try: return forecast(nid)
    except Exception:
        logging.exception('Economy forecast failed for nation %s; displaying current stockpile',nid)
        return None


def current_nation(uid):
    n=get_nation_by_owner(str(uid))
    if n:
        with db.cursor() as c:
            c.execute('SELECT COALESCE(SUM(population),0) AS pop FROM provinces WHERE owner_nation_id=? AND active=1',(n['id'],))
            n['population']=c.fetchone()['pop']
    return n


def dashboard(n,r):
    embed=flagged_embed(discord.Embed(title=tr('💰 Gospodarka — ','💰 Economy — ')+n['name'],color=discord.Color.gold()),(n['flag'],n['name']))
    inventory=inventory_text(n)
    embed.add_field(name=tr('Skarbiec teraz','Treasury now'),value=f"{n['treasury']:.1f}g")
    embed.add_field(name=tr('Populacja','Population'),value=f"{n['population']:,}")
    embed.add_field(name=tr('Magazyn','Stockpile'),value=inventory[:900]+(tr('\nPełna lista w załączniku.','\nFull inventory in the attachment.') if len(inventory)>900 else ''),inline=False)
    if r is not None and r.get('stockpile_only'):
        embed.description=tr('Aktualne zasoby państwa. „Prognoza” oblicza bilans następnego miesiąca; ustawienia gospodarki są poniżej.',
                             'Current nation resources. Forecast calculates the next monthly balance; economy settings are below.')
        return embed
    if r is None:
        embed.description=tr('Pokazuję aktualne zasoby. Prognoza miesiąca jest chwilowo niedostępna; zgłoś to GM-owi. Żadne zasoby ani czas gry nie zostały zmienione przez ten podgląd.',
                             'Showing current resources. The monthly forecast is temporarily unavailable; please tell the GM. This preview did not change resources or game time.')
        return embed
    p=r['policy']
    if r.get('nation_ruins'):
        embed.description=tr('Przy następnym rozliczeniu państwo będzie już ruinami. Gospodarka zostanie zatrzymana, a gracz straci kontrolę. Przed upadkiem GM może zatrzymać rozpad przez /nation decay_stop.',
                             'At the next settlement this nation will be ruins. Its economy will stop and the player will lose control. Before collapse the GM can cancel decay with /nation decay_stop.')
        return embed
    embed.description=tr('Prognoza następnego miesiąca. Domyślnie pracowników przydziela automat. W „Pracownikach” możesz ustawić ręczne przydziały.',
                         'Forecast for the next month. Workers are assigned automatically by default. Open Workers for manual assignments.')
    embed.add_field(name=tr('Złoto / miesiąc','Gold / month'),value=f"{r['balance']:+.1f}g\n"+tr('Dochód','Income')+f": {r['income']:.1f}g | "+tr('Utrzymanie','Upkeep')+f": {r['upkeep']:.1f}g")
    from labor_regimes import label
    regime=r.get('labor',{'mode':'free','transition_months':0})
    embed.add_field(name=tr('Polityka pracy','Labor policy'),value=label(regime['mode'])+
                    tr(' · nadzór: ',' · supervision: ')+f"{r.get('labor_upkeep',0):g}g/"+tr('mies.','month')+
                    (tr(' · okres przejściowy: ',' · transition: ')+str(regime['transition_months']) if regime['transition_months'] else ''),inline=False)
    if r.get('dynasty_stability'):
        embed.add_field(name=tr('Mariaże dynastyczne','Dynastic marriages'),value=f"+{r['dynasty_stability']:g} "+tr('stabilności/miesiąc','stability/month'))
    needed=r['food_needed'];stock=read_json(n['resources_json']).get('food',0)
    cover=f'{stock/needed:.1f}' if needed else '∞'
    embed.add_field(name=tr('Żywność','Food'),value=tr('Zapas na ','Stock for ')+cover+tr(' mies.',' months')+f"\n{r['food_change']:+.1f}/"+tr('mies.','month'))
    tips=[]
    if r['balance']<0:tips.append(tr('⚠️ Wydatki przewyższają dochody. Sprawdź rezerwy wojskowe albo zwiększ sprzedaż.',
                                    '⚠️ Spending exceeds income. Consider military reserves or increase sales.'))
    if r['food_shortage']:tips.append(tr('⚠️ Brakuje żywności: zbuduj lub ulepsz farmę albo zawrzyj umowę na dostawy.',
                                         '⚠️ Food shortage: build/upgrade a farm or arrange regular food imports.'))
    if p['arrears']:tips.append(tr('⚠️ Brak złota na rachunki. Zmniejsz armię aktywną lub przenieś ją do rezerwy.',
                                  '⚠️ Bills exceed available gold. Reduce active forces or put units in reserve.'))
    if r.get('maintenance_factor',1)<1:
        factor=r['maintenance_factor']
        tips.append(tr(f'⚠️ Zaległości ograniczają wydajność płatnych budynków do {factor:.0%}. Spłata rachunków przywraca pełne działanie.',
                       f'⚠️ Arrears limit maintained buildings to {factor:.0%} efficiency. Paying the bills restores full operation.'))
    elif p['arrears']:
        tips.append(tr('Dwa pierwsze miesiące zaległości są ochronne. Od trzeciego spada wydajność budynków.',
                       'The first two unpaid months are protected. Building efficiency falls from the third.'))
    if any(s.get('blocked')=='inland' for s in r['staffing']):
        tips.append(tr('⚠️ Port lub przystań rybacka leży poza wybrzeżem i jest nieaktywna.',
                       '⚠️ An inland port or fishing wharf is inactive.'))
    blocked=sum(s.get('blocked')=='algae_site_or_tech' for s in r['staffing'])
    if blocked:tips.append(tr('🧪 Nieaktywne farmy algae: potrzebują własnego złoża i gospodarki 3. Sprawdź /algae locations.',
                              '🧪 Dormant algae farms need your own deposit and economy 3. See /algae locations.'))
    for program in r.get('algae_programs',[]):
        if program['enabled'] and not program['funded']:
            tips.append('🧪 '+i18n.term(program['category'])+': '+tr('brak algae na program w następnym miesiącu.','not enough algae for next month’s program.'))
    understaffed=sum(s['staff']<.99 and not s.get('blocked') for s in r['staffing'])
    if understaffed:tips.append(tr(f'ℹ️ {understaffed} budynków ma za mało pracowników. Sprawdź ręczne przydziały; automat obsadza żywność najpierw.',
                                   f'ℹ️ {understaffed} buildings are understaffed. Check manual assignments; automatic staffing prioritizes food.'))
    if not tips:tips.append(tr('✅ Podstawowe potrzeby są zabezpieczone. Możesz rozwijać prowincje.',
                               '✅ Basic needs are covered. You can develop your provinces.'))
    chunk=[]
    for tip in tips:
        if chunk and len('\n'.join(chunk+[tip]))>1024:
            embed.add_field(name=tr('Co teraz?','What next?'),value='\n'.join(chunk),inline=False)
            chunk=[]
        chunk.append(tip)
    embed.add_field(name=tr('Co teraz?','What next?'),value='\n'.join(chunk),inline=False)
    labels={'low':tr('niskie','low'),'normal':tr('normalne','normal'),'high':tr('wysokie','high'),
            'balanced':tr('zrównoważony','balanced'),'food':tr('żywność','food'),'industry':tr('przemysł','industry'),
            'trade':tr('handel','trade'),'science':tr('nauka','science')}
    embed.set_footer(text=tr('Podatki: ','Taxes: ')+labels[p['tax']]+tr(' · Priorytet: ',' · Priority: ')+labels[p['priority']])
    return embed


class EconomyView(i18n.LocalizedView):
    def __init__(self,owner,nid):
        super().__init__(timeout=600)
        self.owner,self.nid=owner,nid
        self.built.label=tr('Zbudowane budynki','Built buildings')
        self.income.label=tr('Bilans surowców','Resource balance')
        self.labor.label=tr('Polityka pracy','Labor policy')
        self.preview.label=tr('Prognoza','Forecast')
        options=[('tax:low',tr('Podatki niskie','Low taxes')),('tax:normal',tr('Podatki normalne — domyślne','Normal taxes — default')),
                 ('tax:high',tr('Podatki wysokie','High taxes')),('priority:balanced',tr('Rozwój zrównoważony — domyślny','Balanced growth — default')),
                 ('priority:food',tr('Priorytet: żywność','Priority: food')),('priority:industry',tr('Priorytet: przemysł','Priority: industry')),
                 ('priority:trade',tr('Priorytet: handel','Priority: trade')),('priority:science',tr('Priorytet: nauka','Priority: science')),
                 ('luxury:auto',tr('Luksusy: automatycznie — domyślne','Luxuries: automatic — default')),
                 ('luxury:stockpile',tr('Luksusy: magazynuj','Luxuries: stockpile')),('luxury:consume',tr('Luksusy: konsumuj','Luxuries: consume')),
                 ('luxury:sell',tr('Luksusy: sprzedawaj','Luxuries: sell'))]
        self.settings.options=[discord.SelectOption(label=label,value=value) for value,label in options]
        self.settings.placeholder=tr('Opcjonalne ustawienia gospodarki','Optional economy settings')

    async def interaction_check(self,interaction):
        nation=get_nation_by_owner(str(interaction.user.id))
        if interaction.user.id==self.owner and nation and nation['id']==self.nid:return True
        await interaction.response.send_message(tr('To nie jest Twój panel.','This is not your panel.'),ephemeral=True)
        return False

    @discord.ui.button(label='Built buildings',row=2)
    @i18n.localized
    async def built(self,interaction,button):
        from economy_reports import show
        await show(interaction,'buildings')

    @discord.ui.button(label='Resource balance',row=2)
    @i18n.localized
    async def income(self,interaction,button):
        from economy_reports import show
        await show(interaction,'income')

    @discord.ui.select(row=0)
    @i18n.localized
    async def settings(self,interaction,select):
        await interaction.response.defer()
        key,value=select.values[0].split(':')
        try:await asyncio.to_thread(set_policy,self.nid,key,value,interaction.user.id)
        except ValueError as exc:
            await interaction.followup.send(str(exc),ephemeral=True);return
        n=await asyncio.to_thread(current_nation,interaction.user.id)
        if not n or n['id']!=self.nid:
            await interaction.followup.send(tr('Państwo zmieniło właściciela.','Nation ownership changed.'),ephemeral=True);return
        embed=dashboard(n,{'stockpile_only':True})
        embed.set_footer(text=tr('Ustawienia zapisane.','Settings saved.'))
        await interaction.edit_original_response(embed=embed,view=EconomyView(self.owner,self.nid))

    @discord.ui.button(label='Forecast',row=1)
    @i18n.localized
    async def preview(self,interaction,button):
        await interaction.response.defer()
        r=await asyncio.to_thread(safe_forecast,self.nid)
        n=await asyncio.to_thread(current_nation,interaction.user.id)
        if not n or n['id']!=self.nid:
            await interaction.followup.send(tr('Państwo zmieniło właściciela.','Nation ownership changed.'),ephemeral=True);return
        await interaction.edit_original_response(embed=dashboard(n,r),view=self)

    @discord.ui.button(label='Details',row=1)
    @i18n.localized
    async def details(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        r=await asyncio.to_thread(safe_forecast,self.nid)
        n=await asyncio.to_thread(current_nation,interaction.user.id)
        if not n or n['id']!=self.nid:
            await interaction.followup.send(tr('Państwo zmieniło właściciela.','Nation ownership changed.'),ephemeral=True);return
        if r is None:
            await interaction.followup.send(tr('Prognoza niedostępna. Poniżej pełny aktualny magazyn.','Forecast unavailable. The complete current stockpile is attached.'),file=discord.File(io.BytesIO(inventory_text(n).encode()),filename='resources.txt'),ephemeral=True);return
        lines=[tr('Obsada budynków:','Building staffing:')]
        lines += [f"#{s['cell']} {i18n.term(s['building'])} L{s['level']}: {s['staff']:.0%}" for s in r['staffing']]
        lines += [tr('Zaległości po miesiącu: ','Arrears after this month: ')+f"{r['policy']['arrears']:.1f}g",'',inventory_text(n)]
        await interaction.followup.send(file=discord.File(io.BytesIO('\n'.join(lines).encode()),filename='economy.txt'),ephemeral=True)

    @discord.ui.button(label='Workers',row=1)
    @i18n.localized
    async def workers(self,interaction,button):
        from labor_ui import show
        await show(interaction,self.nid)

    @discord.ui.button(label='Labor policy',row=1)
    @i18n.localized
    async def labor(self,interaction,button):
        from labor_policy_ui import show
        await show(interaction)


async def show_dashboard(interaction):
    await interaction.response.defer(ephemeral=True)
    n=await asyncio.to_thread(current_nation,interaction.user.id)
    if not n:
        await interaction.followup.send(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
    files={}
    inventory=inventory_text(n)
    if len(inventory)>900:
        files['file']=discord.File(io.BytesIO(inventory.encode()),filename='resources.txt')
    await interaction.followup.send(embed=dashboard(n,{'stockpile_only':True}),view=EconomyView(interaction.user.id,n['id']),ephemeral=True,**files)


class PopulationConfirm(i18n.LocalizedView):
    def __init__(self,owner,nid):super().__init__(timeout=120);self.owner,self.nid=owner,nid

    @discord.ui.button(label='Apply',style=discord.ButtonStyle.danger)
    @i18n.localized
    async def apply(self,interaction,button):
        if interaction.user.id!=self.owner or not gm_only(interaction):return
        await interaction.response.defer(ephemeral=True)
        changes=await asyncio.to_thread(services.normalize_population,self.nid,True)
        self.stop()
        await interaction.edit_original_response(content=tr('Zaktualizowano populację.','Population updated.'),view=None)


class EconomyControlCog(commands.Cog):
    economy=app_commands.Group(name='economy',description='Economy dashboard / Panel gospodarki')

    @economy.command(name='income',description='Monthly balance of every resource / Miesięczny bilans wszystkich surowców')
    @i18n.localized
    async def income(self,interaction:discord.Interaction):
        from economy_reports import show
        await show(interaction,'income')

    @economy.command(name='labor',description='Labor policy, slavery and emancipation / Polityka pracy')
    @i18n.localized
    async def labor(self,interaction:discord.Interaction):
        from labor_policy_ui import show
        await show(interaction)

    @economy.command(name='status',description='Simple balance and settings / Bilans i ustawienia')
    @i18n.localized
    async def status(self,interaction:discord.Interaction):await show_dashboard(interaction)

    @economy.command(name='workers',description='View and assign workers in your provinces')
    @i18n.localized
    async def workers(self,interaction:discord.Interaction,cell_id:int=0):
        from labor_ui import show
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:
            await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        try:await show(interaction,n['id'],cell_id or None)
        except ValueError as exc:
            from technology_ui import deliver
            await deliver(interaction,content=str(exc))

    @economy.command(name='upgrade',description='Upgrade a building / Ulepsz budynek')
    @i18n.localized
    async def upgrade(self,interaction:discord.Interaction,cell_id:int,building:str):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        await interaction.response.defer(ephemeral=True)
        try:
            level,cost=await asyncio.to_thread(services.build,n['id'],cell_id,i18n.normalize_key(building),True)
            await interaction.followup.send(tr('Poziom budynku: ','Building level: ')+str(level)+' · '+i18n.resource_list(cost),ephemeral=True)
        except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)

    @economy.command(name='posture',description='Reserve or mobilize units / Rezerwa i mobilizacja')
    @i18n.localized
    async def posture(self,interaction:discord.Interaction,unit_id:int,mode: Literal['reserve','active','deployed']='active'):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        await interaction.response.defer(ephemeral=True)
        try:
            result=await asyncio.to_thread(services.set_posture,n['id'],unit_id,mode)
            message=i18n.term(result)
            if result=='mobilizing':message+=tr(' — gotowość po następnym miesiącu gry.',' — ready after the next game month.')
            await interaction.followup.send(message,ephemeral=True)
        except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)

    @economy.command(name='population',description='[GM] Preview population scale / Podgląd skali populacji')
    @i18n.localized
    async def population(self,interaction:discord.Interaction,nation:str):
        if not gm_only(interaction):await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        from utils import get_nation_by_name
        n=get_nation_by_name(nation)
        if not n:await interaction.response.send_message(i18n.text('Nation not found.'),ephemeral=True);return
        await interaction.response.defer(ephemeral=True)
        changes=await asyncio.to_thread(services.normalize_population,n['id'])
        lines=[f"#{r['cell']}: {r['old']} → {r['new']}" for r in changes]
        await interaction.followup.send(tr('Docelowa średnia: 2000 mieszkańców. Zastosowanie zmieni populację tego państwa.',
                                           'Target average: 2000 inhabitants. Applying changes this nation’s population.'),
            file=discord.File(io.BytesIO('\n'.join(lines).encode()),filename='population-preview.txt'),
            view=PopulationConfirm(interaction.user.id,n['id']),ephemeral=True)

    @economy.command(name='settlers',description='Send 300 settlers / Wyślij 300 osadników')
    @i18n.localized
    async def settlers(self,interaction:discord.Interaction,cell_id:int):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        try:
            with db.atomic() as c:
                from economy_engine import lock_nation
                current=lock_nation(c,n['id'])
                c.execute('SELECT p.id FROM provinces p JOIN colonies x ON x.province_id=p.id WHERE p.azgaar_cell_id=? AND p.owner_nation_id=? AND p.active=1',(cell_id,n['id']))
                target=c.fetchone()
                if not target:raise ValueError(tr('Wskaż własną kolonię.','Choose your colony.'))
                services.spend(c,current,{'gold':50,'food':3})
                services.move_settlers(c,n['id'],target['id'])
            await interaction.response.send_message(tr('Wysłano 300 osadników. Koszt: 50 złota i 3 żywności.',
                                                       'Sent 300 settlers. Cost: 50 gold and 3 food.'),ephemeral=True)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True)

    @economy.command(name='recurring',description='Make a pending offer monthly / Oferta co miesiąc')
    @i18n.localized
    async def recurring(self,interaction:discord.Interaction,trade_id:int):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        try:
            services.set_recurring(trade_id,n['id'])
            await interaction.response.send_message(tr('Oferta oznaczona jako miesięczna. Druga strona musi ją zaakceptować.',
                                                       'Offer marked monthly. The other party must accept it.'),ephemeral=True)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True)

    @economy.command(name='contract_stop',description='Stop a monthly contract / Zatrzymaj umowę')
    @i18n.localized
    async def contract_stop(self,interaction:discord.Interaction,trade_id:int):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        try:
            services.cancel_contract(trade_id,n['id'])
            await interaction.response.send_message(tr('Umowa zatrzymana.','Contract stopped.'),ephemeral=True)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True)
