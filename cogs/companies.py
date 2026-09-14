"""Private company menus and GM approval, with live ownership checks."""
import asyncio
import json
import discord
from discord import app_commands
from discord.ext import commands
import db
import i18n
import companies as co
from utils import get_nation_by_owner, gm_only
from cogs.panel import OwnedView, ChoiceView, FieldsModal as InputModal
from world_service import tr


def nation(uid):
    n=get_nation_by_owner(str(uid))
    if not n:
        raise ValueError(tr('Nie masz państwa.', 'You do not have a nation.'))
    return n


def get_state(uid):
    n=nation(uid)
    with db.cursor() as c:
        return n,co.state(c,n['id'])


def values(text):
    return [int(v.strip()) for v in text.split(',') if v.strip()]


def reserves(text):
    result={}
    for entry in text.split(','):
        if not entry.strip():continue
        key,amount=entry.split('=',1)
        key=i18n.normalize_key(key.strip())
        if key in result:raise ValueError(tr('Powtórzony zasób.', 'Duplicate resource.'))
        result[key]=co.number(amount.strip())
    return result


def field(pl,en,default=None,required=True,paragraph=False):
    return dict(label=tr(pl,en),default=default,required=required,max_length=3000 if paragraph else 500,
                style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short)


async def execute(i,fn,*args):
    await i.response.defer(ephemeral=True)
    try:
        await asyncio.to_thread(fn,*args)
    except ValueError as exc:
        await i.followup.send(str(exc),ephemeral=True)
        return
    await i.followup.send(tr('Zapisano. Otwórz panel ponownie, aby zobaczyć aktualne dane.',
                            'Saved. Reopen the panel to see the updated data.'),ephemeral=True)


class Menu(OwnedView):
    async def on_error(self, interaction, error, item):
        message = str(error) if isinstance(error, ValueError) else i18n.t(i18n.current_language(), 'generic_error')
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    def add(self,label,callback,style=discord.ButtonStyle.secondary):
        button=discord.ui.Button(label=label,style=style)
        button.callback=i18n.localized(callback)
        self.add_item(button)


async def choose(i,title,options,handler,multiple=False):
    if not options:
        await i.response.send_message(tr('Brak pozycji.', 'No items.'),ephemeral=True)
        return
    # Keep every option reachable rather than silently dropping entries beyond 25.
    if len(options)>25:
        view=Menu(i.user.id)
        for start in range(0,len(options),25):
            async def page(interaction,start=start):
                await choose(interaction,title,options[start:start+25],handler,multiple)
            view.add(f'{start+1}–{min(start+25,len(options))}',page)
        await i.response.send_message(title,view=view,ephemeral=True)
        return
    view=ChoiceView(i.user.id,i18n.current_language(),options,i18n.localized(handler),multiple=multiple)
    await i.response.send_message(title,view=view,ephemeral=True)


def specialties(keys):
    return [discord.SelectOption(label=i18n.term(key)[:100],value=key) for key in keys]


async def show(i):
    n,s=get_state(i.user.id)
    co.require_tech(n)
    view=Menu(i.user.id)
    if not s:
        async def create(interaction):
            async def picked(interaction,value):
                selected=[key for key in value.split(',') if key]
                if not 1<=len(selected)<=3:
                    await interaction.response.send_message(tr('Wybierz od 1 do 3 typów.',
                                                               'Choose 1 to 3 types.'),ephemeral=True)
                    return
                async def submitted(interaction,name,description):
                    await execute(interaction,co.create,n['id'],interaction.user.id,name,description,selected)
                await interaction.response.send_modal(InputModal(tr('Załóż kompanię','Create company'),
                    [field('Nazwa','Name'),field('Opis działalności','Business description',paragraph=True)],submitted))
            select=ChoiceView(interaction.user.id,i18n.current_language(),specialties(co.BUILDINGS),i18n.localized(picked),multiple=True)
            select.children[0].min_values=1
            select.children[0].max_values=3
            await interaction.response.send_message(tr('Wybierz do trzech specjalizacji.','Choose up to three specialties.'),view=select,ephemeral=True)
        view.add(tr('Załóż kompanię','Create company'),create,discord.ButtonStyle.primary)
        await i.response.send_message(tr('Jedna kompania na państwo. Specjalizacje ustalasz przy założeniu. Firma zaczyna z wyłączonym automatem.',
                                         'One company per nation. Specialties are fixed at creation. Automation starts disabled.'),view=view,ephemeral=True)
        return
    report=s.get('report',{})
    text=s['description']+'\n\n'+tr('Specjalizacje: ','Specialties: ')+', '.join(i18n.term(k) for k in s['types'])
    text+='\n'+tr('Limit złota na inwestycję: ','Gold limit per investment: ')+f"{s['budget']:g}"
    modes={'off':tr('ręcznie','manual'),'supply':tr('zaopatrzenie','supply'),
           'expand':tr('rozbudowa','expansion'),'upgrade':tr('modernizacja','upgrades')}
    text+='\n'+tr('Tryb: ','Mode: ')+modes[s['mode']]
    if s['paused']:text+='\n'+tr('Działalność wstrzymana.','Operations paused.')
    if report.get('investment'):
        investment=report['investment']
        text+='\n\n'+tr('Ostatnia inwestycja: ','Last investment: ')+f"{i18n.term(investment['building'])} · {investment['cell']} · {investment['level']}/3"
    if report.get('received'):
        text+='\n'+tr('Dostawy i zwroty: ','Deliveries and refunds: ')+i18n.resource_list(report['received'])
    if report.get('costs'):
        text+='\n'+tr('Przedpłaty zakładów zagranicznych: ','Foreign plant prepayments: ')+i18n.resource_list(report['costs'])
    if report.get('waiting'):text+='\n'+report['waiting']
    for label,callback in [(tr('Budżet i automat','Budget and automation'),budget),
                           (tr('Zakłady','Plants'),plants),(tr('Koncesje','Concessions'),offers),
                           (tr('Usprawnienia','Improvements'),improvements)]:
        view.add(label,callback)
    async def toggle(interaction):
        await execute(interaction,co.pause,n['id'],interaction.user.id,s['version'])
    view.add(tr('Wznów','Resume') if s['paused'] else tr('Wstrzymaj działalność','Pause operations'),toggle)
    view.add(tr('Odśwież','Refresh'),show)
    embed=discord.Embed(title=s['name'],description=text[:4000],color=discord.Color.gold())
    embed.set_footer(text=tr('Dostawy zagraniczne trafiają do zapasów na koniec miesiąca.',
                            'Foreign deliveries enter stocks at the end of the month.'))
    await i.response.send_message(embed=embed,view=view,ephemeral=True)


async def budget(interaction):
    n,s=get_state(interaction.user.id)
    async def mode(interaction,value):
        async def submitted(interaction,cells,limit,minimum,resource):
            await execute(interaction,co.configure,n['id'],interaction.user.id,s['version'],
                          values(cells),limit,reserves(minimum),value,i18n.normalize_key(resource or 'food'))
        await interaction.response.send_modal(InputModal(tr('Budżet firmy','Company budget'),[
            field('Własne prowincje: ID po przecinku','Domestic province IDs, comma-separated',','.join(map(str,s['cells'])),False),
            field('Limit złota na jedną inwestycję','Gold limit per investment',str(s['budget'])),
            field('Rezerwy: gold=100,wood=50','Reserves: gold=100,wood=50',','.join(f'{k}={v:g}' for k,v in s['reserves'].items()),False),
            field('Zasób dla zaopatrzenia, np. food','Supply resource, e.g. food',s['resource'],False)],submitted))
    options=[discord.SelectOption(label=label,value=key) for key,label in [
        ('off',tr('Ręczne inwestycje','Manual investments')),('supply',tr('Zaopatrzenie','Supply')),
        ('expand',tr('Rozbudowa','Expansion')),('upgrade',tr('Modernizacja','Upgrades'))]]
    await choose(interaction,tr('Wybierz priorytet. Automat wykonuje do jednej inwestycji na miesiąc.',
                                 'Choose a priority. Automation makes up to one investment per month.'),options,mode)


async def plants(interaction):
    n,s=get_state(interaction.user.id)
    with db.cursor() as c:
        c.execute('SELECT p.azgaar_cell_id,p.name,b.building_key FROM company_plants b '
                  'JOIN provinces p ON p.id=b.province_id WHERE b.company_nation_id=? ORDER BY p.azgaar_cell_id,b.building_key',(n['id'],))
        rows=c.fetchall()
    view=Menu(interaction.user.id)
    async def action(i,assign=False):
        async def picked(interaction,key):
            async def submitted(interaction,cell):
                fn=co.assign if assign else co.manual_invest
                await execute(interaction,fn,n['id'],interaction.user.id,s['version'],int(cell),key)
            await interaction.response.send_modal(InputModal(tr('Wybierz prowincję','Choose province'),[
                field('ID prowincji','Province ID')],submitted))
        await choose(i,tr('Wybierz typ budynku.','Choose a building type.'),specialties(s['types']),picked)
    async def build(interaction):await action(interaction)
    async def assign(interaction):await action(interaction,True)
    view.add(tr('Zbuduj lub ulepsz','Build or upgrade'),build)
    view.add(tr('Przypisz istniejący budynek','Assign existing building'),assign)
    lines=[f"{r['azgaar_cell_id']} · {i18n.term(r['building_key'])} · {r['name']}" for r in rows]
    text='\n'.join(lines) or tr('Brak zakładów. Najpierw ustaw prowincje i budżet.',
                               'No plants. Set provinces and budget first.')
    await interaction.response.send_message(text[:1900],view=view,ephemeral=True)
    for start in range(1900,len(text),1900):
        await interaction.followup.send(text[start:start+1900],ephemeral=True)


async def offers(interaction):
    n,s=get_state(interaction.user.id)
    with db.cursor() as c:rows=co.grants(c,n['id'])
    view=Menu(interaction.user.id)
    if s and co.unlocked(n):
        async def propose(interaction):
            async def picked(interaction,value):
                types=value.split(',')
                async def submitted(interaction,host,cells,share,months,fee):
                    with db.cursor() as c:
                        c.execute('SELECT id FROM nations WHERE LOWER(name)=LOWER(?)',(host,))
                        target=c.fetchone()
                    if not target:
                        await interaction.response.send_message(tr('Nie znaleziono państwa.','Nation not found.'),ephemeral=True)
                        return
                    await execute(interaction,co.propose_concession,n['id'],interaction.user.id,s['version'],
                                  target['id'],values(cells),types,share,months,fee)
                await interaction.response.send_modal(InputModal(tr('Propozycja koncesji','Concession proposal'),[
                    field('Nazwa państwa gospodarza','Host nation name'),field('ID prowincji po przecinku','Province IDs, comma-separated'),
                    field('Udział firmy w produkcji (%)','Company share of production (%)','70'),
                    field('Czas trwania w miesiącach gry','Duration in game months','12'),
                    field('Odszkodowanie za wcześniejsze zerwanie','Fee for ending early','0')],submitted))
            await choose(interaction,tr('Wybierz typy budynków objęte umową.',
                                        'Choose building types covered by the agreement.'),specialties(s['types']),picked,True)
        view.add(tr('Nowa koncesja','New concession'),propose)
    async def browse(interaction):
        async def selected(interaction,identifier):
            with db.cursor() as c:
                current=next((r for r in co.grants(c,nation(interaction.user.id)['id']) if r['id']==identifier),None)
                if current:
                    a=lock_name(c,current['company_nation_id'])
                    b=lock_name(c,current['host_nation_id'])
            if not current:
                await interaction.response.send_message(tr('Umowa nie jest dostępna.','Agreement unavailable.'),ephemeral=True)
                return
            terms=current['terms']
            text=f"{a} → {b}\n"+tr('Prowincje: ','Provinces: ')+', '.join(map(str,terms['cells']))
            text+='\n'+', '.join(i18n.term(k) for k in terms['types'])
            text+='\n'+tr('Udział firmy: ','Company share: ')+f"{terms['share']*100:g}%"
            text+='\n'+tr('Miesiące gry: ','Game months: ')+str(terms['months'])
            text+='\n'+tr('Opłata za wcześniejsze zerwanie: ','Early termination fee: ')+f"{terms['fee']:g}"
            text+='\n\n'+tr('Firma finansuje budowę, materiały i utrzymanie. Gospodarz zapewnia pracowników. Umowa obejmuje także przypisanie istniejących budynków. Po zakończeniu budynki pozostają gospodarzowi. Wojna zawiesza współpracę.',
                             'The company pays for construction, inputs and upkeep. The host provides workers. Existing buildings may also be assigned. Buildings remain with the host after the agreement ends. War suspends cooperation.')
            controls=Menu(interaction.user.id)
            async def accept(interaction):
                await execute(interaction,co.respond_concession,identifier,interaction.user.id,'accept')
            async def end(interaction):
                # Show a separate confirmation before any early termination payment.
                confirm=Menu(interaction.user.id)
                async def confirmed(interaction):
                    await execute(interaction,co.respond_concession,identifier,interaction.user.id,'end')
                confirm.add(tr('Potwierdź zakończenie','Confirm termination'),confirmed,discord.ButtonStyle.danger)
                await interaction.response.send_message(tr('Zakończyć umowę? Jeśli jest aktywna, opłata wyniesie: ',
                                                            'End the agreement? If active, the fee is: ')+f"{terms['fee']:g}",view=confirm,ephemeral=True)
            if current['status']=='proposed' and current['host_nation_id']==n['id']:
                controls.add(tr('Akceptuj','Accept'),accept,discord.ButtonStyle.success)
            if current['status'] in ('proposed','active'):
                controls.add(tr('Odrzuć lub zakończ','Reject or end'),end)
            chunks=[text[start:start+1900] for start in range(0,len(text),1900)]
            for index,chunk in enumerate(chunks):
                kwargs=dict(ephemeral=True)
                if index==len(chunks)-1:kwargs['view']=controls
                if index==0:await interaction.response.send_message(chunk,**kwargs)
                else:await interaction.followup.send(chunk,**kwargs)
        with db.cursor() as c:
            options=[discord.SelectOption(label=f"{lock_name(c,r['company_nation_id'])} → {lock_name(c,r['host_nation_id'])}"[:100],
                       value=r['id']) for r in rows if r['status'] in ('proposed','active')]
        await choose(interaction,tr('Wybierz koncesję.','Choose a concession.'),options,selected)
    view.add(tr('Oferty i umowy','Offers and agreements'),browse)
    await interaction.response.send_message(tr('Koncesje firmy i zagraniczne oferty dla twojego państwa.',
                                              'Company concessions and foreign offers for your nation.'),view=view,ephemeral=True)


def lock_name(c,nid):
    c.execute('SELECT name FROM nations WHERE id=?',(nid,))
    row=c.fetchone()
    return row['name'] if row else str(nid)



def effect_text(kind):
    labels={
        'production':('Premia produkcji: +5 punktów procentowych.','Production bonus: +5 percentage points.'),
        'inputs':('Oszczędność surowców: +5 punktów procentowych.','Input savings: +5 percentage points.'),
        'workers':('Oszczędność pracowników: +5 punktów procentowych.','Worker savings: +5 percentage points.'),
        'construction':('Zniżka budowy: +5 punktów procentowych.','Construction discount: +5 percentage points.'),
        'maintenance':('Zniżka utrzymania: +5 punktów procentowych.','Upkeep discount: +5 percentage points.'),
    }
    return tr(*labels[kind])

async def improvements(interaction):
    n,s=get_state(interaction.user.id)
    view=Menu(interaction.user.id)
    labels=dict(production=tr('Produkcja','Production'),inputs=tr('Zużycie surowców','Input use'),
                workers=tr('Pracownicy','Workers'),construction=tr('Koszt budowy','Construction cost'),
                maintenance=tr('Utrzymanie','Upkeep'))
    async def propose(interaction):
        async def building(interaction,key):
            async def kind(interaction,value):
                async def submitted(interaction,description):
                    await execute(interaction,co.improvement,n['id'],interaction.user.id,s['version'],key,value,description)
                await interaction.response.send_modal(InputModal(tr('Opisz usprawnienie','Describe improvement'),[
                    field('Co zmieniasz i dlaczego ma to pomóc?','What will change, and how will it help?',paragraph=True)],submitted))
            kinds=('construction','maintenance') if key=='algae_farm' else co.KINDS
            await choose(interaction,tr('Wybierz zamierzony efekt.','Choose the intended effect.'),
                         [discord.SelectOption(label=labels[k],value=k) for k in kinds],kind)
        await choose(interaction,tr('Którego typu budynków dotyczy pomysł?',
                                    'Which building type does the idea concern?'),specialties(s['types']),building)
    view.add(tr('Zaproponuj usprawnienie','Propose improvement'),propose)
    async def projects(interaction):
        async def selected(interaction,identifier):
            p=next(x for x in s['improvements'] if x['id']==identifier)
            controls=Menu(interaction.user.id)
            if p['status']=='approved':
                async def start(interaction):
                    await execute(interaction,co.start_improvement,n['id'],interaction.user.id,s['version'],identifier,True)
                controls.add(tr('Zapłać 100 złota i rozpocznij','Pay 100 gold and start'),start)
            if p['status'] in ('proposed','approved'):
                async def cancel(interaction):
                    await execute(interaction,co.start_improvement,n['id'],interaction.user.id,s['version'],identifier,False)
                controls.add(tr('Wycofaj projekt','Withdraw project'),cancel)
            status={'proposed':tr('Czeka na GM','Awaiting GM'),'approved':tr('Zatwierdzony','Approved'),
                    'running':tr('W realizacji','Running'),'complete':tr('Ukończony','Complete'),
                    'rejected':tr('Odrzucony','Rejected'),'cancelled':tr('Wycofany','Withdrawn')}[p['status']]
            text=f"{i18n.term(p['building'])} · {labels[p['kind']]} · {status}\n\n{p['description']}\n\n"
            text+=effect_text(p['kind'])+'\n'
            text+=tr('Koszt: 100 złota. Czas: 2 miesiące gry. Usprawnienie: 5 punktów procentowych, do limitu technologii.',
                     'Cost: 100 gold. Time: 2 game months. Improvement: 5 percentage points, within the technology cap.')
            await interaction.response.send_message(embed=discord.Embed(description=text),view=controls,ephemeral=True)
        await choose(interaction,tr('Wybierz projekt.','Choose a project.'),
                     [discord.SelectOption(label=(i18n.term(p['building'])+' · '+p['description'])[:100],value=p['id']) for p in s['improvements']],selected)
    view.add(tr('Projekty i decyzje GM','Projects and GM decisions'),projects)
    await interaction.response.send_message(tr('Opisz konkretny pomysł. GM ocenia, czy pasuje do wybranego efektu. Po zatwierdzeniu sam decydujesz, czy zapłacić i rozpocząć.',
                                              'Describe a concrete idea. The GM checks whether it supports the chosen effect. After approval, you choose whether to pay and start.'),view=view,ephemeral=True)


async def review(interaction):
    if not gm_only(interaction):
        await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True)
        return
    with db.cursor() as c:
        c.execute('SELECT nation_id,state_json FROM companies ORDER BY nation_id')
        options=[]
        for row in c.fetchall():
            for p in json.loads(row['state_json'])['improvements']:
                if p['status']=='proposed':
                    options.append(discord.SelectOption(label=(lock_name(c,row['nation_id'])+' · '+p['description'])[:100],
                                                         value=f"{row['nation_id']}:{p['id']}"))
    async def selected(interaction,value):
        if not gm_only(interaction):return
        nid,identifier=value.split(':',1)
        with db.cursor() as c:s=co.state(c,int(nid))
        p=next(x for x in s['improvements'] if x['id']==identifier)
        view=Menu(interaction.user.id)
        async def decision(interaction,approve):
            if not gm_only(interaction):
                await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True)
                return
            await execute(interaction,co.review,int(nid),identifier,approve,interaction.user.id)
        async def accept(interaction):await decision(interaction,True)
        async def reject(interaction):await decision(interaction,False)
        view.add(tr('Zatwierdź','Approve'),accept,discord.ButtonStyle.success)
        view.add(tr('Odrzuć','Reject'),reject,discord.ButtonStyle.danger)
        text=f"{s['name']} · {i18n.term(p['building'])}\n\n{p['description']}\n\n"
        text+=effect_text(p['kind'])+'\n'+tr('100 złota, 2 miesiące. Sprawdź wcześniejsze usprawnienia, aby nie premiować tego samego pomysłu ponownie.', '100 gold, 2 months. Check previous improvements to avoid rewarding the same idea twice.')
        async def history(interaction):
            if not gm_only(interaction):return
            async def old_project(interaction,old_id):
                if not gm_only(interaction):return
                old=next(x for x in s['improvements'] if x['id']==old_id)
                await interaction.response.send_message(embed=discord.Embed(description=old['description']+'\n\n'+effect_text(old['kind'])),ephemeral=True)
            await choose(interaction,tr('Wcześniejsze pomysły.','Earlier ideas.'),
                         [discord.SelectOption(label=x['description'][:100],value=x['id']) for x in s['improvements'] if x['id']!=identifier],old_project)
        view.add(tr('Historia usprawnień','Improvement history'),history)
        await interaction.response.send_message(embed=discord.Embed(description=text),view=view,ephemeral=True)
    await choose(interaction,tr('Projekty czekające na ocenę GM.','Projects awaiting GM review.'),options,selected)


class CompanyCog(commands.Cog):
    @app_commands.command(name='company',description='Company management / Zarządzanie kompanią')
    @i18n.localized
    async def company(self,interaction:discord.Interaction):
        try:await show(interaction)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True)

    @app_commands.command(name='company_offers',description='Investment offers / Oferty inwestycji')
    @i18n.localized
    async def company_offers(self,interaction:discord.Interaction):
        try:await offers(interaction)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True)

    @app_commands.command(name='company_review',description='GM: review company improvements / GM: oceń usprawnienia kompanii')
    @i18n.localized
    async def company_review(self,interaction:discord.Interaction):
        await review(interaction)


async def setup(bot):
    await bot.add_cog(CompanyCog())
