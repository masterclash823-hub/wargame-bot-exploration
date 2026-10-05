"""Private GM dashboard with paginated nation/province pickers and checked forms."""
import discord
import db
import i18n
from utils import gm_only
from world_service import tr
from cogs.panel import FieldsModal,invoke


class AdminPanel(i18n.LocalizedView):
    def __init__(self,bot,uid,nid=None,cell=None,section='provinces'):
        super().__init__(timeout=900)
        self.bot,self.uid,self.nid,self.cell,self.section=bot,uid,nid,cell,section
        category=discord.ui.Select(row=0,options=[discord.SelectOption(label=tr(pl,en),value=key,default=key==section)
            for key,pl,en in [('provinces','Prowincje','Provinces'),('nations','Państwa i coop','Nations & co-op'),
                              ('events','Eventy','Events'),('world','Kalendarz i bitwy','Calendar & battles'),('map','Mapa Azgaara','Azgaar map')]])
        async def change(i):
            if not await self.interaction_check(i):return
            await self.open(i,section=category.values[0])
        category.callback=i18n.localized(change);self.add_item(category)
        actions=[('nation','Wybierz państwo','Choose nation'),('province','Wybierz prowincję','Choose province')]
        actions+= {
            'provinces':[('claim','Przyznaj prowincje','Grant provinces'),('claim_normalized','Przyznaj i uśrednij','Grant & normalize'),
                         ('unclaim','Odbierz prowincje','Unclaim provinces'),('population','Zmień populację','Set population'),
                         ('biome','Zmień biom','Set biome'),('coast','Ustaw wybrzeże','Set coastline'),
                         ('normalize','Uśrednij populację państwa','Normalize nation population')],
            'nations':[('applications','Zgłoszenia państw','Nation applications'),('start_budget','Budżet startowy','Starting budget'),('transfer','Przekaż państwo','Transfer nation'),
                       ('coop','Współdzielenie','Co-op access'),('stats','Statystyki','Statistics'),('starter','Pakiet startowy','Starter pack')],
            'events':[('generate','Generuj dla państwa','Generate for nation'),('all','Generuj dla wszystkich','Generate for all'),
                      ('events','Lista eventów','Event list'),('post_private','Opublikuj prywatnie','Publish privately'),
                      ('post_public','Opublikuj publicznie','Publish publicly'),('image','Ilustracja z pliku','Upload illustration')],
            'map':[('map_access','Dostęp graczy do mapy','Player map access'),('map_import','Jak wgrać mapę','How to import'),('map_export','Eksportuj .map','Export .map'),
                   ('map_states','Państwa mapy','Map states'),('map_cultures','Kultury','Cultures'),
                   ('map_religions','Religie','Religions'),('map_bind','Powiąż państwo','Link nation'),('identity','Kultura i religia pola','Cell culture & religion'),
                   ('culture_strength','Siła kultury','Culture strength'),('religion_strength','Siła religii','Religion strength')],
            'world':[('calendar_status','Data i stan','Date & status'),('calendar_start','Uruchom kalendarz','Start calendar'),
                     ('calendar_stop','Zatrzymaj kalendarz','Pause calendar'),('tick','Rozlicz miesiące','Settle months'),
                     ('plans','Oczekujące plany bitew','Pending battle plans'),('project_review','Ocena projektu AI','AI project review')],
        }[section]
        for index,(key,pl,en) in enumerate(actions):
            button=discord.ui.Button(label=tr(pl,en),row=1+index//4)
            async def clicked(i,key=key):
                if not await self.interaction_check(i):return
                try:await self.action(i,key)
                except ValueError as exc:
                    from technology_ui import deliver
                    await deliver(i,content=str(exc))
            button.callback=i18n.localized(clicked);self.add_item(button)

    async def interaction_check(self,i):
        if i.user.id==self.uid and gm_only(i):return True
        await i.response.send_message(tr('Panel jest dostępny dla administratora, który go otworzył.',
                                        'This panel is only available to the administrator who opened it.'),ephemeral=True)
        return False

    def nation(self):
        with db.cursor() as c:
            c.execute('SELECT * FROM nations WHERE id=?',(self.nid,));n=c.fetchone()
        if not n:raise ValueError(tr('Najpierw wybierz państwo.','Choose a nation first.'))
        return n

    def embed(self):
        e=discord.Embed(title=tr('Panel administratora','Admin panel'),color=discord.Color.dark_gold())
        try:n=self.nation();name=n['name']
        except ValueError:name=tr('nie wybrano','not selected')
        e.description=tr('Państwo: ','Nation: ')+name+'\n'+tr('Prowincja: ','Province: ')+(str(self.cell) if self.cell is not None else '—')
        if self.section=='provinces':e.description+='\n\n'+tr(
            'Uśrednianie obejmuje wszystkie pełne prowincje wybranego państwa: średnio 2000 mieszkańców, z większą stolicą. '
            'Etapy kolonii zachowują populację. Zmiana biomu aktualizuje teren i naturalne zasoby.',
            'Normalization covers all full provinces of the selected nation: average 2000 inhabitants, with a larger capital. '
            'Colonies keep their population. Changing a biome updates terrain and natural resources.')
        if self.section=='events':e.description+='\n\n'+tr('Kanał publiczny ustaw przez /event channel. Własny obrazek wgraj przez /event image file.',
                                                                           'Set a public channel with /event channel. Upload your own image using /event image file.')
        if self.section=='map':e.description+='\n\n'+tr(
            'Siła kultury i religii określa parametr ekspansji zapisywany w mapie i eksporcie do Azgaara.',
            'Culture and religion strength is the expansion parameter saved in the map and exported to Azgaar.')
        return e

    async def open(self,i,**changes):
        values=dict(nid=self.nid,cell=self.cell,section=self.section);values.update(changes)
        view=AdminPanel(self.bot,self.uid,**values)
        await i.response.edit_message(embed=view.embed(),view=view)

    async def form(self,i,title,fields,callback):
        async def submit(j,*values):
            if not await self.interaction_check(j):return
            await callback(j,*values)
        await i.response.send_modal(FieldsModal(title,fields,submit))

    async def call(self,i,cog,command,*args):
        if not gm_only(i):raise ValueError(tr('Utracono uprawnienia GM.','GM access was revoked.'))
        await invoke(self.bot.get_cog(cog),command,i,*args)

    async def picker(self,i,rows,callback):
        if not rows:raise ValueError(tr('Brak pozycji.','No entries.'))
        await i.response.send_message(view=AdminPicker(self,rows,callback),ephemeral=True)

    async def player(self,i,callback):
        view=i18n.LocalizedView(timeout=300)
        select=discord.ui.UserSelect(placeholder=tr('Wybierz gracza','Choose player'))
        async def chosen(j):
            if not await self.interaction_check(j):return
            member=select.values[0]
            if member.bot:
                await j.response.send_message(tr('Wybierz konto gracza.','Choose a player account.'),ephemeral=True);return
            await callback(j,member)
        select.callback=i18n.localized(chosen);view.add_item(select)
        await i.response.send_message(view=view,ephemeral=True)

    async def action(self,i,key):
        if key=='project_review':await self.call(i,'EconomyCog','mp_review');return
        if key=='map_access':
            async def selected(j,value):await self.call(j,'ProvincesCog','map_access',value=='1')
            from workflow_ui import choose
            from game_setup import map_available
            await choose(i,[discord.SelectOption(label=tr('Udostępnij','Share'),value='1'),discord.SelectOption(label=tr('Ukryj','Hide'),value='0')],selected,
                content=tr('Obecnie: ','Currently: ')+str(map_available(i.guild_id)));return
        if key=='start_budget':
            async def submit(j,points):await self.call(j,'NationCog','start_budget',int(points))
            from game_setup import budget
            await self.form(i,tr('Budżet startowy','Starting budget'),[dict(label=tr('Punkty 1–60 (domyślnie 35)','Points 1–60 (default 35)'),default=str(budget(i.guild_id)),max_length=2)],submit);return
        if key in ('nation','province'):
            with db.cursor() as c:
                if key=='nation':c.execute('SELECT id,name FROM nations ORDER BY name')
                elif self.nid is None:c.execute('SELECT azgaar_cell_id AS id,name FROM provinces WHERE active=1 ORDER BY azgaar_cell_id')
                else:c.execute('SELECT azgaar_cell_id AS id,name FROM provinces WHERE owner_nation_id=? AND active=1 ORDER BY azgaar_cell_id',(self.nid,))
                rows=[(str(r['id']),f"#{r['id']} {r['name']}") for r in c.fetchall()]
            async def choose(j,value):await self.open(j,**({'nid':int(value),'cell':None} if key=='nation' else {'cell':int(value)}))
            await self.picker(i,rows,choose);return
        if key in ('population','biome','coast') and self.cell is None:
            raise ValueError(tr('Najpierw wybierz prowincję.','Choose a province first.'))
        if key in ('claim','claim_normalized','unclaim'):
            name=self.nation()['name'] if key!='unclaim' else ''
            async def submit(j,ids):
                if key=='unclaim':await self.call(j,'ProvincesCog','unclaim',ids)
                else:await self.call(j,'ProvincesCog','claim',name,ids,key=='claim_normalized')
            await self.form(i,tr('Prowincje','Provinces'),[dict(label=tr('ID komórek: 1,2,5-10','Cell IDs: 1,2,5-10'),max_length=1000)],submit)
        elif key=='population':
            async def submit(j,value):await self.call(j,'ProvincesCog','population',self.cell,int(value))
            await self.form(i,tr('Populacja prowincji','Province population'),[dict(label=tr('Liczba mieszkańców','Inhabitants'),max_length=10)],submit)
        elif key=='biome':
            from cogs.provinces import BIOME_RESOURCES
            async def choose(j,value):await self.call(j,'ProvincesCog','biome',self.cell,value)
            await self.picker(i,[(name,name) for name in BIOME_RESOURCES],choose)
        elif key=='coast':
            async def choose(j,value):await self.call(j,'ProvincesCog','coast',self.cell,value=='1')
            await self.picker(i,[('1',tr('Ląd na wybrzeżu','Coastal land')),('0',tr('Poza wybrzeżem','Inland'))],choose)
        elif key=='map_import':
            await i.response.send_message(tr('W Azgaarze zapisz projekt .map i Export → JSON → Full Data. Wgraj je przez /admin map_import file:JSON map_file:MAP. Kolejne aktualizacje: /admin map_resync.',
                'In Azgaar save a .map project and Export → JSON → Full Data. Upload with /admin map_import file:JSON map_file:MAP. Later updates: /admin map_resync.'),ephemeral=True)
        elif key=='map_export':await self.call(i,'ProvincesCog','map_export')
        elif key in ('map_states','map_cultures','map_religions'):
            await self.call(i,'ProvincesCog','map_entities',key[4:])
        elif key in ('culture_strength','religion_strength'):
            from azgaar_service import catalog
            kind='cultures' if key=='culture_strength' else 'religions'
            entries={str(row['state_id']):row['entity'] for row in catalog(kind) if row['state_id']>0}
            async def choose(j,value):
                entity=entries[value]
                async def submit(k,strength):
                    await self.call(k,'ProvincesCog','map_strength',kind,int(value),float(strength.replace(',','.')))
                current=entity.get('expansionism')
                await self.form(j,tr('Siła kultury','Culture strength') if kind=='cultures' else tr('Siła religii','Religion strength'),[
                    dict(label=tr('Siła ekspansji (co najmniej 0)','Expansion strength (at least 0)'),
                         default=str(current) if current is not None else None,placeholder='1.5',max_length=30)],submit)
            await self.picker(i,[(key,f"#{key} {str(entity.get('name') or '—')[:60]} · "+
                tr('siła: ','strength: ')+str(entity.get('expansionism','—'))) for key,entity in entries.items()],choose)
        elif key=='map_bind':
            name=self.nation()['name']
            async def submit(j,value):await self.call(j,'ProvincesCog','map_bind',int(value),name)
            await self.form(i,tr('ID państwa Azgaara','Azgaar state ID'),[dict(label=tr('ID z /admin map_entities','ID from /admin map_entities'),max_length=5)],submit)
        elif key=='identity':
            if self.cell is None:raise ValueError(tr('Najpierw wybierz prowincję.','Choose a province first.'))
            async def submit(j,culture,religion):await self.call(j,'ProvincesCog','province_identity',self.cell,int(culture) if culture else None,int(religion) if religion else None)
            await self.form(i,tr('Kultura i religia','Culture & religion'),[
                dict(label=tr('ID kultury (puste = bez zmian)','Culture ID (blank = unchanged)'),required=False,max_length=5),
                dict(label=tr('ID religii (puste = bez zmian)','Religion ID (blank = unchanged)'),required=False,max_length=5)],submit)
        elif key=='normalize':await self.call(i,'EconomyControlCog','population',self.nation()['name'])
        elif key=='applications':await self.call(i,'NationCog','applications')
        elif key=='transfer':
            name=self.nation()['name']
            async def chosen(j,member):await self.call(j,'NationCog','transfer',name,member)
            await self.player(i,chosen)
        elif key=='coop':await self.call(i,'NationCog','coop',self.nation()['name'])
        elif key=='stats':await self.call(i,'NationCog','stats',self.nation()['name'])
        elif key=='starter':await self.call(i,'EconomyCog','starter_pack',self.nation()['name'])
        elif key=='generate':await self.call(i,'EventsCog','event_generate',self.nation()['name'])
        elif key=='all':
            async def submit(j,description):await self.call(j,'EventsCog','event_all',description)
            await self.form(i,tr('Eventy dla wszystkich','Events for all'),[dict(label=tr('Motyw przewodni (opcjonalnie)','Theme (optional)'),required=False,max_length=1000)],submit)
        elif key=='events':await self.call(i,'EventsCog','event_list',self.nation()['name'] if self.nid else '')
        elif key=='image':
            await i.response.send_message(tr('Wyszukiwanie jest wyłączone. Dodaj plik przez /event image event_id:ID file:obrazek.',
                                             'Search is disabled. Upload with /event image event_id:ID file:image.'),ephemeral=True)
        elif key in ('post_private','post_public'):
            async def submit(j,event_id):
                await self.call(j,'EventsCog','event_post',int(event_id),'public' if key=='post_public' else 'private')
            await self.form(i,tr('Opublikuj event','Publish event'),[dict(label='Event ID',max_length=10)],submit)
        elif key.startswith('calendar_'):await self.call(i,'EconomyCog',key)
        elif key=='plans':await self.call(i,'CombatCog','plans_pending')
        elif key=='tick':
            async def submit(j,months):
                count=int(months)
                if not 1<=count<=120:
                    await j.response.send_message(tr('Wpisz od 1 do 120 miesięcy.', 'Enter 1 to 120 months.'),ephemeral=True);return
                await self.call(j,'EconomyCog','admin_tick',count)
            await self.form(i,tr('Potwierdź rozliczenie','Confirm settlement'),[dict(label=tr('Miesiące do rozliczenia (1–120)','Months to settle (1–120)'),default='1',max_length=3)],submit)


class AdminPicker(i18n.LocalizedView):
    def __init__(self,panel,rows,handler,page=0):
        super().__init__(timeout=600)
        self.panel=panel
        select=discord.ui.Select(options=[discord.SelectOption(value=value,label=label[:100]) for value,label in rows[page*25:(page+1)*25]])
        async def chosen(i):
            if not await panel.interaction_check(i):return
            try:await handler(i,select.values[0])
            except ValueError as exc:
                from technology_ui import deliver
                await deliver(i,content=str(exc))
        select.callback=i18n.localized(chosen);self.add_item(select)
        for target,label in ((page-1,'◀'),(page+1,'▶')):
            if target<0 or target*25>=len(rows):continue
            button=discord.ui.Button(label=label,row=1)
            async def turn(i,target=target):
                if not await panel.interaction_check(i):return
                await i.response.edit_message(view=AdminPicker(panel,rows,handler,target))
            button.callback=i18n.localized(turn);self.add_item(button)

    async def interaction_check(self,i):return await self.panel.interaction_check(i)
