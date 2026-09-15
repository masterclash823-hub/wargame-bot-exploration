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



def field(pl,en,default=None,required=True,paragraph=False):
    return dict(label=tr(pl,en),default=default,required=required,max_length=3000 if paragraph else 500,
                style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short)


def responded(i):
    check=getattr(i.response,'is_done',None)
    return bool(check and check())


async def send_private(i,**kwargs):
    sender=i.followup.send if responded(i) else i.response.send_message
    await sender(**kwargs,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


async def execute(i,fn,*args):
    if not responded(i):await i.response.defer(ephemeral=True)
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
        await send_private(i,content=tr('Brak pozycji.', 'No items.'))
        return
    # Keep every option reachable rather than silently dropping entries beyond 25.
    if len(options)>25:
        view=Menu(i.user.id)
        for start in range(0,len(options),25):
            async def page(interaction,start=start):
                await choose(interaction,title,options[start:start+25],handler,multiple)
            view.add(f'{start+1}–{min(start+25,len(options))}',page)
        await send_private(i,content=title,view=view)
        return
    view=ChoiceView(i.user.id,i18n.current_language(),options,i18n.localized(handler),multiple=multiple)
    await send_private(i,content=title,view=view)


def specialties(keys):
    from cogs.economy import _building_label, _building_effect_summary
    with db.cursor() as c:
        c.execute('SELECT * FROM building_defs')
        definitions={row['key']:row for row in c.fetchall()}
    lang=i18n.current_language()
    return [discord.SelectOption(label=_building_label(definitions[key],lang)[:100],value=key,
                                 description=_building_effect_summary(definitions[key],lang))
            for key in keys if key in definitions]


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
    with db.cursor() as c:
        current=co.month_index(c)
    remaining=co.remaining_budget(s,current)
    spent=s['spent'] if s['budget_month']==current else 0
    text+='\n'+tr('Budżet na miesiąc: ','Monthly budget: ')+f"{s['monthly_budget']:g} "+tr('złota','gold')
    text+='\n'+tr('Wydano / zostało w tym miesiącu: ','Spent / remaining this month: ')+f"{spent:g} / {remaining:g}"
    modes={'off':tr('ręczny','manual'),'auto':tr('automatyczny — całe własne państwo','automatic — all domestic provinces')}
    text+='\n'+tr('Tryb: ','Mode: ')+modes[s['mode']]
    if s['paused']:text+='\n'+tr('Działalność wstrzymana.','Operations paused.')
    investments=report.get('investments',[])
    if investments:
        reasons={'food':tr('żywność','food'),'supplies':tr('surowce','supplies'),'growth':tr('rozwój','growth')}
        text+='\n\n'+tr('Inwestycje w ostatnim raporcie: ','Investments in the latest report: ')+str(len(investments))
        for item in investments[-5:]:
            reason=reasons.get(item.get('reason'),tr('ręcznie','manual'))
            text+=f"\n{i18n.term(item['building'])} · #{item['cell']} · {item['level']}/3 · {item['cost'].get('gold',0):g}g · {reason}"
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
        async def submitted(interaction,amount):
            await execute(interaction,co.configure,n['id'],interaction.user.id,s['version'],amount,value)
        await interaction.response.send_modal(InputModal(tr('Budżet miesięczny','Monthly budget'),[
            field('Złoto na miesiąc gry','Gold per game month',str(s['monthly_budget']))],submitted))
    options=[discord.SelectOption(label=label,value=key,description=description) for key,label,description in [
        ('auto',tr('Automatyczny','Automatic'),
         tr('Wszystkie własne prowincje; sam wybiera budowę i modernizację.',
            'All domestic provinces; chooses construction and upgrades.')),
        ('off',tr('Ręczny','Manual'),tr('Ty wybierasz każdą inwestycję.','You choose every investment.'))]]
    await choose(interaction,tr(
        'Ustaw miesięczną kwotę złota na budowę i modernizację. Materiały pochodzą z zapasów państwa. '
        'Automat najpierw uzupełnia braki, potem rozwija produkcję. Może wykonać kilka inwestycji. '
        'Ręczne inwestycje firmy korzystają z tej samej kwoty; jej zmiana nie zeruje wydatków. '
        'Niewydane złoto zostaje w skarbcu, a budżet odnawia się co miesiąc bez kumulowania. '
        'Utrzymanie zakładów i zatwierdzone usprawnienia opłacasz osobno. Za granicą inwestujesz ręcznie przez koncesje.',
        'Set a monthly gold amount for construction and upgrades. Materials come from national stocks. '
        'Automation addresses shortages first, then grows production. It can make several investments. '
        'Manual company investments use the same amount; editing it does not reset spending. '
        'Unspent gold stays in the treasury; the budget renews monthly without accumulating. '
        'Plant upkeep and approved improvements are paid separately. Foreign investment is manual through concessions.'),
        options,mode)


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
    text='\n'.join(lines) or tr('Brak zakładów. Ustaw budżet miesięczny i włącz automat.',
                               'No plants. Set a monthly budget and enable automation.')
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



def effect_labels():
    return dict(production=tr('Produkcja','Production'),inputs=tr('Zużycie surowców','Input use'),
                workers=tr('Zapotrzebowanie na pracowników','Workers required'),
                construction=tr('Koszt budowy','Construction cost'),maintenance=tr('Utrzymanie','Upkeep'))


def effect_text(project):
    values=co.project_effects(project)
    labels=effect_labels()
    if not values:
        return tr('Skutki ustali GM.','The GM will decide the effects.')
    return '\n'.join(f"{labels[k]}: {v:+g}%" for k,v in values.items())


def project_embed(s,p):
    status={'proposed':tr('Czeka na GM','Awaiting GM'),'approved':tr('Zatwierdzony','Approved'),
            'running':tr('W realizacji','Running'),'complete':tr('Ukończony','Complete'),
            'rejected':tr('Odrzucony','Rejected'),'cancelled':tr('Wycofany','Withdrawn')}[p['status']]
    embed=discord.Embed(title=(s['name']+' · '+i18n.term(p['building']))[:256],description=p['description'])
    embed.add_field(name=status,value=effect_text(p),inline=False)
    embed.add_field(name=tr('Realizacja','Implementation'),value=i18n.resource_list(p['cost'])+' · '+str(p['months'])+' '+tr('miesiące gry','game months'),inline=False)
    source=p.get('source')
    if source:
        embed.add_field(name=tr('Wiadomość z pomysłem','Idea message'),value=f"[Discord]({source['url']})",inline=False)
    embed.set_footer(text=tr('Plus zwiększa daną wartość, minus ją zmniejsza. Premie mają limit technologii; kary obniżają wynik.',
                            'Plus increases a value; minus decreases it. Bonuses have technology caps; penalties reduce the result.'))
    return embed


async def propose_improvement(interaction,building='',description=None,source=None):
    n,s=get_state(interaction.user.id)
    co.require_tech(n)
    if not s:
        raise ValueError(tr('Najpierw załóż kompanię.','Create a company first.'))
    async def picked(interaction,key):
        if key not in s['types']:
            raise ValueError(tr('Wybierz specjalizację swojej kompanii.','Choose a company specialty.'))
        if description is not None:
            await execute(interaction,co.improvement,n['id'],interaction.user.id,s['version'],key,description,source)
            return
        async def submitted(interaction,text):
            await execute(interaction,co.improvement,n['id'],interaction.user.id,s['version'],key,text)
        item=field('Co zmieniasz i jak to ma działać?','What changes and how should it work?',paragraph=True)
        item['max_length']=co.IMPROVEMENT_TEXT_LIMIT
        await interaction.response.send_modal(InputModal(tr('Opisz usprawnienie','Describe improvement'),[item],submitted))
    if building:
        await picked(interaction,i18n.normalize_key(building))
    else:
        await choose(interaction,tr('Której specjalizacji dotyczy pomysł? GM ustali premie i straty.',
                                    'Which specialty does the idea concern? The GM decides gains and losses.'),specialties(s['types']),picked)


async def improvements(interaction):
    n,s=get_state(interaction.user.id)
    if not s:
        raise ValueError(tr('Najpierw załóż kompanię.','Create a company first.'))
    view=Menu(interaction.user.id)
    async def propose(interaction):
        await propose_improvement(interaction)
    view.add(tr('Zaproponuj usprawnienie','Propose improvement'),propose)
    async def projects(interaction):
        async def selected(interaction,identifier):
            current_n,current_s=get_state(interaction.user.id)
            if current_n['id']!=n['id']:
                raise ValueError(tr('Państwo zmieniło właściciela.','Nation ownership changed.'))
            p=next(x for x in current_s['improvements'] if x['id']==identifier)
            controls=Menu(interaction.user.id)
            if p['status']=='approved':
                async def start(interaction):
                    await execute(interaction,co.start_improvement,n['id'],interaction.user.id,current_s['version'],identifier,True)
                controls.add(tr('Zapłać i rozpocznij','Pay and start'),start)
            if p['status'] in ('proposed','approved'):
                async def cancel(interaction):
                    await execute(interaction,co.start_improvement,n['id'],interaction.user.id,current_s['version'],identifier,False)
                controls.add(tr('Wycofaj projekt','Withdraw project'),cancel)
            await interaction.response.send_message(embed=project_embed(current_s,p),view=controls,ephemeral=True)
        await choose(interaction,tr('Wybierz projekt.','Choose a project.'),
                     [discord.SelectOption(label=(i18n.term(p['building'])+' · '+p['description'])[:100],value=p['id']) for p in s['improvements']],selected)
    view.add(tr('Projekty i decyzje GM','Projects and GM decisions'),projects)
    await interaction.response.send_message(tr(
        '1. Opisz pomysł i wybierz budynek. Możesz użyć formularza lub menu swojej wiadomości: Aplikacje → company_improve.\n'
        '2. GM ustala skutki — premie, straty lub oba naraz. Nic nie jest przyznawane automatycznie.\n'
        '3. Przeczytaj decyzję w „Projekty i decyzje GM”. Jeśli ją przyjmiesz, zapłać 100 złota. Po 2 miesiącach gry skutki zaczną działać.',
        '1. Describe an idea and choose a building. Use the form or your message menu: Apps → company_improve.\n'
        '2. The GM decides bonuses, penalties or both. No effect is awarded automatically.\n'
        '3. Read Projects and GM decisions. If you accept, pay 100 gold. The effects start after 2 game months.'),view=view,ephemeral=True)


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
        if not gm_only(interaction):
            await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True)
            return
        nid,identifier=value.split(':',1)
        with db.cursor() as c:s=co.state(c,int(nid))
        p=next((x for x in (s or {}).get('improvements',[]) if x['id']==identifier),None)
        if not p or p['status']!='proposed':
            raise ValueError(tr('Projekt nie czeka na ocenę.','The project is not awaiting review.'))
        view=Menu(interaction.user.id)
        async def accept(interaction):
            if not gm_only(interaction):
                await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True)
                return
            keys=('construction','maintenance') if p['building']=='algae_farm' else co.KINDS
            labels=effect_labels()
            async def submitted(interaction,*amounts):
                if not gm_only(interaction):
                    await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True)
                    return
                effects={key:amount.replace(',','.') for key,amount in zip(keys,amounts)}
                await execute(interaction,co.review,int(nid),identifier,True,interaction.user.id,effects,s['version'])
            fields=[dict(label=labels[key]+' (%)',default='0',required=True,max_length=12) for key in keys]
            await interaction.response.send_modal(InputModal(tr('Skutki: + zwiększa, − zmniejsza','Effects: + increases, − decreases'),fields,submitted))
        async def reject(interaction):
            if not gm_only(interaction):
                await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True)
                return
            await execute(interaction,co.review,int(nid),identifier,False,interaction.user.id,None,s['version'])
        view.add(tr('Ustal skutki i zatwierdź','Set effects and approve'),accept,discord.ButtonStyle.success)
        view.add(tr('Odrzuć','Reject'),reject,discord.ButtonStyle.danger)
        async def history(interaction):
            if not gm_only(interaction):return
            async def old_project(interaction,old_id):
                if not gm_only(interaction):return
                old=next(x for x in s['improvements'] if x['id']==old_id)
                await interaction.response.send_message(embed=project_embed(s,old),ephemeral=True)
            await choose(interaction,tr('Wcześniejsze pomysły.','Earlier ideas.'),
                         [discord.SelectOption(label=x['description'][:100],value=x['id']) for x in s['improvements'] if x['id']!=identifier],old_project)
        view.add(tr('Historia usprawnień','Improvement history'),history)
        await interaction.response.send_message(content=tr(
            'GM wybiera każdą zmianę od −50 do +50%. Np. produkcja +10%, surowce +5% daje większą produkcję kosztem większego zużycia. '
            'Minus przy kosztach oznacza oszczędność. Zostaw 0 przy braku zmiany. Premie nadal podlegają limitom technologii; kary działają po ich ograniczeniu.',
            'The GM chooses each change from −50 to +50%. E.g. production +10%, inputs +5% increases output at the cost of more inputs. '
            'Minus on a cost means savings. Leave 0 for no change. Bonuses retain technology caps; penalties apply after those caps.'),
            embed=project_embed(s,p),view=view,ephemeral=True)
    await choose(interaction,tr('Projekty czekające na ocenę GM.','Projects awaiting GM review.'),options,selected)


class CompanyCog(commands.Cog):
    def __init__(self,bot=None):
        self.bot=bot
        self.message_command=app_commands.ContextMenu(name='company_improve',callback=self.improve_message)
        self.message_command.guild_only=True

    async def cog_load(self):
        if self.bot:self.bot.tree.add_command(self.message_command)

    async def cog_unload(self):
        if self.bot:
            self.bot.tree.remove_command(self.message_command.name,type=discord.AppCommandType.message)

    @i18n.localized
    async def improve_message(self,interaction:discord.Interaction,message:discord.Message):
        from company_message import message_proposal
        try:
            description,source=message_proposal(message,interaction.user.id,interaction.guild_id)
            await interaction.response.defer(ephemeral=True)
            await propose_improvement(interaction,description=description,source=source)
        except ValueError as exc:
            await send_private(interaction,content=str(exc))

    @app_commands.command(name='company_improve',description='Propose an improvement or use a message link / Zgłoś pomysł lub link do wiadomości')
    @app_commands.guild_only()
    @app_commands.describe(building='Company specialty / Specjalizacja kompanii',message_link='Your Discord message link; optional / Link do własnej wiadomości; opcjonalnie')
    @i18n.localized
    async def company_improve(self,interaction:discord.Interaction,building:str='',message_link:str=''):
        from company_message import from_link
        try:
            description=source=None
            if message_link:
                await interaction.response.defer(ephemeral=True)
                description,source=await from_link(interaction,message_link)
            await propose_improvement(interaction,building,description,source)
        except ValueError as exc:
            await send_private(interaction,content=str(exc))

    @company_improve.autocomplete('building')
    @i18n.localized
    async def improvement_buildings(self,interaction:discord.Interaction,current:str):
        try:n,s=get_state(interaction.user.id)
        except ValueError:return []
        if not s:return []
        return [app_commands.Choice(name=i18n.term(k)[:100],value=k) for k in s['types']
                if current.casefold() in (k+' '+i18n.term(k)).casefold()][:25]

    @commands.command(name='company_improve')
    async def improvement_reply(self,ctx,building:str=''):
        from company_message import from_reply
        with i18n.using_language(i18n.get_user_language(ctx.author.id)):
            try:
                if not ctx.guild:
                    raise ValueError(tr('Użyj tej komendy na serwerze gry.','Use this command on the game server.'))
                with db.cursor() as c:
                    c.execute('SELECT value FROM game_config WHERE key=?',(f'guild_active_{ctx.guild.id}',))
                    active=c.fetchone()
                if not active or active['value']!='1':
                    raise ValueError(tr('Bot nie jest aktywowany na tym serwerze.','The bot is not activated on this server.'))
                if not building:
                    raise ValueError(tr('Podaj specjalizację, np. !company_improve farm.','Choose a specialty, e.g. !company_improve farm.'))
                description,source=await from_reply(ctx)
                n,s=get_state(ctx.author.id)
                if not s:raise ValueError(tr('Najpierw załóż kompanię.','Create a company first.'))
                await asyncio.to_thread(co.improvement,n['id'],ctx.author.id,s['version'],i18n.normalize_key(building),description,source)
            except ValueError as exc:
                await ctx.reply(str(exc),mention_author=False,allowed_mentions=discord.AllowedMentions.none())
                return
            await ctx.reply(tr('Pomysł zapisany. GM ustali skutki w /company_review. Decyzję zobaczysz w panelu kompanii → Usprawnienia.',
                               'Idea saved. The GM chooses effects in /company_review. Read the decision in the company panel → Improvements.'),
                            mention_author=False,allowed_mentions=discord.AllowedMentions.none())

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
    await bot.add_cog(CompanyCog(bot))
