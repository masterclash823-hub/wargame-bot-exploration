"""Private, button-driven research, recommendations and algae supply controls."""
import math
import json
import asyncio

import discord

import db
import i18n
import technology as tech
from flags import flagged_embed


def response_done(interaction):
    check=getattr(interaction.response,'is_done',None)
    return bool(check and check())


async def deliver(interaction,*,content=None,embed=None,view=None,edit=False):
    payload=dict(content=content,embed=embed,view=view)
    if edit:
        if response_done(interaction):await interaction.edit_original_response(**payload)
        else:await interaction.response.edit_message(**payload)
    elif response_done(interaction):await interaction.followup.send(**payload,ephemeral=True)
    else:await interaction.response.send_message(**payload,ephemeral=True)


def state(nid):
    with db.cursor() as c:
        c.execute('SELECT * FROM nations WHERE id=?',(nid,));n=c.fetchone()
        c.execute('SELECT * FROM research_projects WHERE nation_id=?',(nid,));active=c.fetchone()
        return n,active,tech.discoveries(c,nid)


def estimate(nid,p,active=None):
    from economy_engine import snapshot,project
    with db.cursor() as c:data=snapshot(c,nid)
    output=project(*data)['production'].get('universal_knowledge',0)+tech.BASE_KNOWLEDGE
    stock=json.loads(data[0]['resources_json']).get('universal_knowledge',0)
    remaining=max(0,p['knowledge']-(active['knowledge'] if active else 0)-stock)
    return max(1,p['duration']-(active['months'] if active else 0),math.ceil(remaining/max(.01,output)))


def overview(nid):
    n,active,known=state(nid)
    levels=json.loads(n['tech_json']);resources=json.loads(n['resources_json'])
    e=flagged_embed(discord.Embed(title='🔬 '+tech.tr('Badania — ','Research — ')+n['name'],color=discord.Color.teal()),(n['flag'],n['name']))
    e.description=tech.tr('Wybierz projekt i pozwól uczonym pracować. Każdy miesiąc gry daje państwu 1 darmowy punkt wiedzy. Uniwersytety zwiększają produkcję.','Choose a project and let scholars work. Each game month grants your nation 1 free knowledge point. Universities increase production.')
    e.add_field(name=tech.tr('Dziedziny','Fields'),value='\n'.join(f'{i18n.term(k)}: **{levels.get(k,3):g}/10**' for k in tech.CATEGORIES),inline=True)
    e.add_field(name=tech.tr('Zapasy','Stockpile'),value=f"📚 {resources.get('universal_knowledge',0):g} · 🧪 {resources.get('algae',0):g} algae",inline=True)
    year,years=tech.historical_year(n,known)
    e.add_field(name=tech.tr('Orientacyjny rok technologiczny (IRL)','Approximate technology year (IRL)'),
                value=f'≈ {year}\n'+' · '.join(f'{i18n.term(k)} ≈ {v}' for k,v in years.items())+'\n'+
                tech.tr('Umowne porównanie; nie zmienia kalendarza ani premii.', 'A rough analogy; does not change the calendar or bonuses.'),inline=False)
    if active:
        p=tech.PROJECTS[active['code']]
        when=tech.tr('Wstrzymane','Paused') if active['paused'] else tech.tr('Szacunkowo jeszcze','Estimated remaining')+f' {estimate(nid,p,active)} '+tech.tr('mies. gry','game months')
        e.add_field(name=tech.name(active['code']),value=f"📚 {active['knowledge']:g}/{p['knowledge']} · 📅 {active['months']}/{p['duration']}\n{when}\n"+tech.effect_text(p['effects']),inline=False)
    else:
        e.add_field(name=tech.tr('Następny krok','Next move'),value=tech.tr('Wybierz jedną z trzech rekomendacji lub otwórz cały katalog.','Choose one of the three recommendations or open the full catalogue.'),inline=False)
    if known:
        e.add_field(name=tech.tr('Ostatnie odkrycia','Recent discoveries'),value='\n'.join('✓ '+tech.name(k) for k in list(known)[:5] if k in tech.PROJECTS),inline=False)
    with db.cursor() as c:effects=tech.bonuses(c,nid)
    if effects:e.add_field(name=tech.tr('Obowiązujące premie','Current bonuses'),value=tech.effect_text(effects)[:1024],inline=False)
    e.set_footer(text=tech.tr('Wiedza jest przekazywana stopniowo. Czas to minimum; prognoza zależy od zatrudnienia i dostaw.','Knowledge is contributed gradually. Duration is a minimum; estimates depend on staffing and supplies.'))
    return e


def detail(nid,code):
    n,active,known=state(nid);p=tech.PROJECTS[code]
    level=json.loads(n['tech_json']).get(p['category'],3)
    e=discord.Embed(title='🔬 '+tech.name(code),color=discord.Color.teal())
    e.description=tech.effect_text(p['effects'])
    e.add_field(name=tech.tr('Koszt','Cost'),value=f"{p['knowledge']} "+tech.tr('wiedzy stopniowo','knowledge over time')+f"\n{p['algae']} algae "+tech.tr('przy rozpoczęciu','on starting'),inline=True)
    e.add_field(name=tech.tr('Czas','Time'),value=tech.tr('Minimum','Minimum')+f" {p['duration']} "+tech.tr('mies. gry','game months')+'\n'+tech.tr('Szacunek','Estimate')+f' {estimate(nid,p)}',inline=True)
    e.add_field(name=i18n.term(p['category']),value=f"{level:g} → {min(10,level+p['gain']):g}; "+tech.tr('wymagany poziom','required level')+f" {p['level']}",inline=False)
    if p['algae']:
        e.add_field(name='🧪 Algae',value=tech.tr('Odblokowuje program, który włączasz osobno. Premie działają po opłaceniu 1 algae na miesiąc. Brak dostaw zawiesza premie; odkrycie i poziom pozostają.','Unlocks a program you enable separately. Bonuses work after paying 1 algae per month. Missing supplies suspend bonuses; the discovery and level remain.'),inline=False)
    if code in known and not p['repeat']:e.add_field(name='✓',value=tech.tr('Już ukończone.','Already completed.'),inline=False)
    elif code not in tech.available(n,known):e.add_field(name='🔒',value=tech.tr('Najpierw podnieś poziom tej dziedziny.','Raise this field’s level first.'),inline=False)
    if active:e.set_footer(text=tech.tr('Najpierw ukończ lub porzuć aktualny projekt.','Complete or abandon the current project first.'))
    return e


class PrivateView(discord.ui.View):
    def __init__(self,nid,uid):
        super().__init__(timeout=600)
        self.nid,self.uid=nid,str(uid)

    async def interaction_check(self,interaction):
        with db.cursor() as c:
            c.execute('SELECT owner_id FROM nations WHERE id=?',(self.nid,));n=c.fetchone()
        if str(interaction.user.id)!=self.uid or not n or n['owner_id']!=self.uid:
            lang=i18n.get_user_language(interaction.user.id)
            await interaction.response.send_message(i18n.text('This is not your menu.',lang=lang),ephemeral=True)
            return False
        return True

    def button(self,label,handler,*,style=discord.ButtonStyle.secondary,disabled=False,row=None):
        b=discord.ui.Button(label=label[:80],style=style,disabled=disabled,row=row)
        @i18n.localized
        async def clicked(interaction):
            # Recheck even for callbacks invoked by other menus or test clients.
            if not await self.interaction_check(interaction):return
            await interaction.response.defer(ephemeral=True)
            try:await handler(interaction)
            except ValueError as exc:await deliver(interaction,content=str(exc))
        b.callback=clicked
        self.add_item(b)
        return b


class ResearchView(PrivateView):
    def __init__(self,nid,uid,code=None,catalogue=False):
        super().__init__(nid,uid)
        n,active,known=state(nid)
        async def back(i):await show(i,nid,edit=True)
        if code:
            async def confirm(i):
                tech.start(nid,i.user.id,code)
                await show(i,nid,edit=True)
            self.confirm=self.button(tech.tr('Rozpocznij badanie','Start research'),confirm,style=discord.ButtonStyle.success,
                                     disabled=bool(active) or code not in tech.available(n,known))
            self.button(tech.tr('Wróć','Back'),back)
            return
        if catalogue:
            options=[discord.SelectOption(label=tech.name(k)[:100],value=k,
                       description=f"{i18n.term(p['category'])} · {p['knowledge']} 📚 · {p['duration']} 📅 · {p['algae']} algae"[:100]) for k,p in tech.PROJECTS.items()]
            select=discord.ui.Select(placeholder=tech.tr('Wybierz odkrycie — także zablokowane','Choose a discovery — including locked ones'),options=options)
            @i18n.localized
            async def selected(interaction):
                if await self.interaction_check(interaction):
                    await interaction.response.defer(ephemeral=True)
                    await show(interaction,nid,code=select.values[0],edit=True)
            select.callback=selected;self.add_item(select)
            self.button(tech.tr('Wróć','Back'),back)
            return
        if active:
            action='resume' if active['paused'] else 'pause'
            async def toggle(i):
                tech.manage(nid,i.user.id,active['token'],action);await show(i,nid,edit=True)
            self.button(tech.tr('Wznów' if active['paused'] else 'Wstrzymaj','Resume' if active['paused'] else 'Pause'),toggle)
            async def abandon(i):
                view=PrivateView(nid,uid)
                async def confirmed(j):
                    tech.manage(nid,j.user.id,active['token'],'abandon');await show(j,nid,edit=True)
                view.button(tech.tr('Potwierdź porzucenie','Confirm abandonment'),confirmed,style=discord.ButtonStyle.danger)
                view.button(tech.tr('Wróć','Back'),back)
                await deliver(i,embed=discord.Embed(description=tech.tr('Porzucenie usuwa postęp. Zużyta wiedza i algae nie wracają. Kontynuować?','Abandoning discards progress. Spent knowledge and algae are not refunded. Continue?')),view=view,edit=True)
            self.button(tech.tr('Porzuć projekt','Abandon project'),abandon)
        else:
            with db.cursor() as c:recommended=tech.recommendations(c,n)
            for key in recommended:
                async def preview(i,k=key):await show(i,nid,code=k,edit=True)
                self.button(tech.name(key),preview,style=discord.ButtonStyle.primary,row=0)
        async def all_projects(i):await show(i,nid,catalogue=True,edit=True)
        async def programs(i):await show_programs(i,nid,edit=True)
        self.button(tech.tr('Wszystkie badania','All research'),all_projects,row=1)
        self.button(tech.tr('Programy algae','Algae programs'),programs,row=1)
        self.button(tech.tr('Odśwież','Refresh'),back,row=1)


async def show(interaction,nid,*,code=None,catalogue=False,edit=False):
    n,_,_=state(nid)
    if not n or n['owner_id']!=str(interaction.user.id):
        raise ValueError(i18n.text('This is not your menu.'))
    e=detail(nid,code) if code else overview(nid)
    view=ResearchView(nid,interaction.user.id,code,catalogue)
    await deliver(interaction,embed=e,view=view,edit=edit)


def locations_embed():
    with db.cursor() as c:
        c.execute('SELECT p.*,n.name AS owner_name FROM algae_sites a JOIN provinces p ON p.id=a.province_id '
                  'LEFT JOIN nations n ON n.id=p.owner_nation_id WHERE p.active=1 ORDER BY p.azgaar_cell_id')
        sites=c.fetchall()
    e=discord.Embed(title='🧪 '+tech.tr('Stanowiska algae','Algae deposits'),color=discord.Color.dark_green())
    e.description=tech.tr('Złoża wskazuje GM: /algae deposit_add i deposit_remove. Limit: 5. Farma poziomu 1 wymaga własnego złoża, gospodarki 3 i 250 pracowników. Produkuje automatycznie: gospodarka 3 → 0,05; 4 → 0,1; 5 → 0,2; 6+ → 0,5 algae/miesiąc. Przy gospodarce 6 można ulepszyć farmę: poziom 2 → 0,85, poziom 3 → 1,2. To wartości bazowe przed obsadą, stabilnością i etapem kolonii.',
                         'The GM places deposits: /algae deposit_add and deposit_remove. Limit: 5. A level-one farm needs your own deposit, economy 3 and 250 workers. Automatic output: economy 3 → 0.05; 4 → 0.1; 5 → 0.2; 6+ → 0.5 algae/month. At economy 6, upgrade the farm: level 2 → 0.85, level 3 → 1.2. These are base values before staffing, stability and colony-stage factors.')
    for p in sites:
        title=f"#{p['azgaar_cell_id']} · {p['name'] or i18n.term(p['terrain'])}"
        value=(p['owner_name'] or tech.tr('Niczyje','Unclaimed'))+' · '+i18n.term(p['terrain'])
        if 'algae_farm' in json.loads(p['buildings_json']):value+=' · '+tech.tr('farma istnieje','farm exists')
        e.add_field(name=discord.utils.escape_mentions(title)[:256],value=discord.utils.escape_mentions(value)[:1024],inline=False)
    if not sites:e.add_field(name='—',value=tech.tr('Brak aktywnych złóż. GM wskazuje je przez /algae deposit_add cell_id. Import ani restart nie tworzą złóż.', 'No active deposits. The GM places them with /algae deposit_add cell_id. Importing or restarting creates no deposits.'))
    with db.cursor() as c:
        c.execute('SELECT p.azgaar_cell_id FROM algae_sites a JOIN provinces p ON p.id=a.province_id WHERE p.active=0 ORDER BY p.azgaar_cell_id')
        inactive=[str(p['azgaar_cell_id']) for p in c.fetchall()]
    if inactive:e.add_field(name=tech.tr('Nieaktywne — zajmują limit','Inactive — count towards the limit'),value=', '.join(inactive),inline=False)
    e.set_footer(text=tech.tr('Algae można też kupować od graczy. Starsze farmy poza stanowiskami są nieaktywne i nie kosztują utrzymania.','Algae can also be traded between players. Older farms outside deposits are dormant and have no upkeep.'))
    return e


async def show_production(interaction,nid,*,edit=False,notice=None):
    from economy_engine import forecast,read_json,building_level,LEVEL_OUTPUT
    from economy_services import build
    n,_,_=state(nid)
    if not n or n['owner_id']!=str(interaction.user.id):raise ValueError(i18n.text('This is not your menu.'))
    if not response_done(interaction):await interaction.response.defer(ephemeral=True)
    with db.cursor() as c:
        c.execute('SELECT p.*,d.levels_json FROM provinces p JOIN algae_sites a ON a.province_id=p.id '
                  'LEFT JOIN province_development d ON d.province_id=p.id WHERE p.owner_nation_id=? AND p.active=1 ORDER BY p.azgaar_cell_id',(nid,))
        sites=c.fetchall()
        c.execute("SELECT * FROM building_defs WHERE key='algae_farm'");definition=c.fetchone()
    result=await asyncio.to_thread(forecast,nid)
    n,_,_=state(nid)
    if not n or n['owner_id']!=str(interaction.user.id):raise ValueError(i18n.text('This is not your menu.'))
    economy=read_json(n['tech_json']).get('economy',3)
    e=flagged_embed(discord.Embed(title='🧪 '+tech.tr('Wydobycie algae','Algae production'),color=discord.Color.dark_green()),(n['flag'],n['name']))
    e.description=tech.tr('Zbuduj farmę na własnym złożu. Od następnego rozliczenia produkuje co miesiąc automatycznie. Poziom 1: gospodarka 3 → 0,05; 4 → 0,1; 5 → 0,2; 6+ → 0,5 algae/miesiąc. Ulepszenia do poziomu 2 (0,85) i 3 (1,2) wymagają gospodarki 6. Obsada, stabilność i etap kolonii wpływają na wynik.',
                         'Build a farm on your own deposit. It produces automatically every month from the next settlement. Level one: economy 3 → 0.05; 4 → 0.1; 5 → 0.2; 6+ → 0.5 algae/month. Upgrades to level 2 (0.85) and 3 (1.2) require economy 6. Staffing, stability and colony stage affect the result.')
    e.add_field(name=tech.tr('Gospodarka / zapas algae','Economy / algae stock'),value=f"{economy:g} / {read_json(n['resources_json']).get('algae',0):g}")
    e.add_field(name=tech.tr('Prognoza wydobycia / miesiąc','Extraction forecast / month'),value=f"{result['production'].get('algae',0):.3f}")
    if definition:
        e.add_field(name=tech.tr('Budowa poziomu 1','Build level one'),value=i18n.resource_list(read_json(definition['cost_json']))+'\n'+tech.tr('Bazowe utrzymanie przy pełnej obsadzie: ','Base upkeep when fully staffed: ')+i18n.resource_list(read_json(definition['upkeep_json'])),inline=False)
    view=PrivateView(nid,interaction.user.id)
    for p in sites:
        exists='algae_farm' in read_json(p['buildings_json'],[])
        levels=read_json(p['levels_json']);stored=levels.get('algae_farm',1) if exists else 0
        active=building_level('algae_farm',levels,economy) if exists else 0
        info=tech.tr('Brak farmy.','No farm.') if not exists else tech.tr('Poziom budynku: ','Building level: ')+str(stored)
        if exists and active!=stored:info+=' · '+tech.tr('działa jako poziom 1 do gospodarki 6','operates at level one until economy 6')
        e.add_field(name=f"#{p['azgaar_cell_id']} · {p['name']}"[:256],value=info,inline=False)
        if stored>=3 or not definition:continue
        cost={k:v*({0:1,1:1.5,2:2}[stored]) for k,v in read_json(definition['cost_json']).items()}
        required=max(6 if exists else 3,definition['requires_tech'])
        async def inspect(i,cell=p['azgaar_cell_id'],upgrade=exists,price=cost):
            confirm=PrivateView(nid,i.user.id)
            async def pay(j):
                level,paid=await asyncio.to_thread(build,nid,cell,'algae_farm',upgrade,uid=j.user.id)
                await show_production(j,nid,edit=True,notice=tech.tr('Farma ma poziom ','Farm level is now ')+str(level)+'. '+i18n.resource_list(paid))
            async def cancel(j):await show_production(j,nid,edit=True)
            confirm.button(tech.tr('Potwierdź budowę','Confirm construction'),pay,style=discord.ButtonStyle.success)
            confirm.button(tech.tr('Anuluj','Cancel'),cancel)
            preview=discord.Embed(description=tech.tr('Prowincja ','Province ')+f'#{cell} · '+i18n.resource_list(price))
            await deliver(i,embed=preview,view=confirm,edit=True)
        label=tech.tr('Ulepsz','Upgrade') if exists else tech.tr('Zbuduj farmę','Build farm')
        view.button(f"{label} #{p['azgaar_cell_id']}",inspect,disabled=economy<required)
    if not sites:e.add_field(name='—',value=tech.tr('Nie masz aktywnego złoża. Sprawdź /algae locations lub kup algae od graczy.', 'You have no active deposit. See /algae locations or trade for algae.'))
    async def workers(i):
        from labor_ui import show as show_workers
        await show_workers(i,nid,edit=True)
    async def refresh(i):await show_production(i,nid,edit=True)
    view.button(tech.tr('Pracownicy','Workers'),workers)
    view.button(tech.tr('Odśwież','Refresh'),refresh)
    await deliver(interaction,content=notice,embed=e,view=view,edit=edit)


async def show_programs(interaction,nid,*,edit=False):
    n,_,known=state(nid)
    if not n or n['owner_id']!=str(interaction.user.id):raise ValueError(i18n.text('This is not your menu.'))
    with db.cursor() as c:
        c.execute('SELECT * FROM algae_programs WHERE nation_id=?',(nid,));programs={r['category']:r for r in c.fetchall()}
    e=discord.Embed(title='🧪 '+tech.tr('Programy algae','Algae programs'),color=discord.Color.dark_green())
    e.description=tech.tr('Każdy włączony program zużywa 1 algae na początku miesiąca i daje premię na ten miesiąc. Zmiany ustawień działają od następnego miesiąca. Przy niedoborze pierwszeństwo mają: gospodarka, armia, marynarka, kolonie.','Each enabled program consumes 1 algae at month start and provides bonuses for that month. Setting changes apply next month. Shortage priority: economy, army, navy, colonies.')
    e.description+=f"\n🧪 {json.loads(n['resources_json']).get('algae',0):g} algae"
    view=PrivateView(nid,interaction.user.id)
    for category in tech.CATEGORIES:
        code='algae_'+category;row=programs.get(category,{})
        unlocked=code in known
        status=tech.tr('aktywne premie','bonuses active') if row.get('funded') else tech.tr('bez premii','no bonuses')
        next_state=tech.tr('włączony','enabled') if row.get('enabled') else tech.tr('wyłączony','disabled')
        e.add_field(name=tech.name(code),value=tech.effect_text(tech.PROJECTS[code]['effects'])+'\n'+(status+' · '+next_state if unlocked else tech.tr('🔒 Najpierw ukończ badanie.','🔒 Complete research first.')),inline=False)
        async def toggle(i,k=category,enabled=not bool(row.get('enabled'))):
            tech.set_program(nid,i.user.id,k,enabled);await show_programs(i,nid,edit=True)
        view.button((tech.tr('Wyłącz','Disable') if row.get('enabled') else tech.tr('Włącz','Enable'))+' · '+i18n.term(category),toggle,disabled=not unlocked,row=0)
    async def deposits(i):await deliver(i,embed=locations_embed())
    async def back(i):await show(i,nid,edit=True)
    view.button(tech.tr('Stanowiska algae','Algae deposits'),deposits,row=1)
    view.button(tech.tr('Badania','Research'),back,row=1)
    await deliver(interaction,embed=e,view=view,edit=edit)
