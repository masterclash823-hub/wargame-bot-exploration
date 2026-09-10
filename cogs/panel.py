"""Button-first player panel for the everyday game loop."""
from __future__ import annotations
from flags import flag_text, flagged_embed

import json
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

import db
import i18n
from utils import get_nation_by_owner, gm_only


PL = {
    'goals':'Cele państwowe', 'memories':'Pamięć decyzji', 'treaties':'Traktaty i propozycje',
    'new_treaty':'Nowy traktat', 'calls':'Wezwania do obrony',
    'posture':'Rezerwa i mobilizacja', 'settlers':'Wyślij osadników', 'contracts':'Umowy miesięczne',
    "panel": "Panel gracza", "open": "Otwórz panel gracza", "home": "Przegląd",
    "economy": "Gospodarka", "military": "Wojsko i technologia", "territory": "Terytorium",
    "diplomacy": "Dyplomacja i bitwy", "events": "Wydarzenia", "settings": "Ustawienia",
    "choose": "Wybierz kategorię", "refresh": "Odśwież", "stats": "Statystyki państwa",
    "resources": "Zasoby", "calendar": "Kalendarz", "found": "Załóż państwo",
    "build": "Zbuduj budynek", "buildings": "Katalog budynków", "yield": "Produkcja prowincji",
    "trades": "Wymiany", "new_trade": "Nowa wymiana", "projects": "Megaprojekty",
    "new_project": "Zaproponuj projekt", "start_project": "Rozpocznij projekt",
    "forces": "Siły zbrojne", "blueprints": "Projekty jednostek", "recruit": "Zbuduj jednostki",
    "move": "Przemieść jednostkę", "new_blueprint": "Nowy projekt", "research": "Badania",
    "tech": "Poziomy technologii", "provinces": "Lista prowincji", "province": "Szczegóły prowincji",
    "colonies": "Kolonie", "colony_view": "Szczegóły kolonii", "colony_found": "Załóż kolonię",
    "colony_develop": "Rozwiń kolonię", "colony_expand": "Rozszerz kolonię",
    "routes": "Szlaki handlowe", "relations": "Relacje",
    "war": "Wypowiedz wojnę", "peace": "Zawrzyj pokój", "alliance": "Zawrzyj sojusz",
    "battle_plan": "Wyślij plan bitwy", "battles": "Raporty bitew", "event_list": "Lista wydarzeń",
    "event_play": "Rozegraj wydarzenie", "help": "Pomoc", "tutorial": "Poradnik",
    "no_nation": "Nie masz jeszcze państwa. Poproś Game Mastera o utworzenie i nadanie go Tobie.",
    "private": "Ten panel jest prywatny. Publiczne wydarzenia, wojny i osiągnięcia mogą trafić do kroniki.",
    "not_yours": "To nie jest Twój panel.", "empty": "Brak dostępnych pozycji.",
    "shortened": "Pokazano pierwsze 25 pozycji.", "select": "Wybierz pozycję",
    "published": "Panel gracza został opublikowany.", "launcher_desc": "Kliknij przycisk, aby otworzyć prywatny panel. Szczegóły decyzji i propozycji pozostają prywatne.",
}


def tr(lang: str, key: str) -> str:
    en = {
        'goals':'National goals', 'memories':'Decision memory', 'treaties':'Treaties & proposals',
        'new_treaty':'New treaty', 'calls':'Defense calls',
        'posture':'Reserves & mobilization', 'settlers':'Send settlers', 'contracts':'Monthly contracts',
        "panel":"Player panel","open":"Open player panel","home":"Overview","economy":"Economy",
        "military":"Military & technology","territory":"Territory","diplomacy":"Diplomacy & battles",
        "events":"Events","settings":"Settings","choose":"Choose a category","refresh":"Refresh",
        "stats":"Nation stats","resources":"Resources","calendar":"Calendar","found":"Found a nation",
        "build":"Construct building","buildings":"Building catalogue","yield":"Province yield",
        "trades":"Trades","new_trade":"New trade","projects":"Megaprojects","new_project":"Propose project",
        "start_project":"Start project","forces":"Armed forces","blueprints":"Unit blueprints",
        "recruit":"Build units","move":"Move unit","new_blueprint":"New blueprint","research":"Research",
        "tech":"Technology levels","provinces":"Province list","province":"Province details",
        "colonies":"Colonies","colony_view":"Colony details","colony_found":"Found colony",
        "colony_develop":"Develop colony","colony_expand":"Expand colony",
        "routes":"Trade routes","relations":"Relations",
        "war":"Declare war","peace":"Make peace","alliance":"Form alliance",
        "battle_plan":"Submit battle plan","battles":"Battle reports","event_list":"Event list",
        "event_play":"Play event","help":"Help","tutorial":"Tutorial",
        "no_nation":"You do not have a nation yet. Ask the Game Master to create and assign one to you.",
        "private":"This panel is private. Public events, wars and milestones may appear in the chronicle.","not_yours":"This is not your panel.",
        "empty":"There are no available items.","shortened":"Only the first 25 items are shown.",
        "select":"Choose an item","published":"The player panel has been published.",
        "launcher_desc":"Click the button to open your private panel. Decision and proposal details remain private.",
    }
    return PL[key] if lang == "pl" else en[key]


def language(interaction: discord.Interaction) -> str:
    locale = getattr(getattr(interaction, "locale", None), "value", None)
    return i18n.get_user_language(interaction.user.id, locale)


def active_guild(guild_id: int | None) -> bool:
    if not guild_id:
        return False
    with db.cursor() as cur:
        cur.execute("SELECT value FROM game_config WHERE key=?", (f"guild_active_{guild_id}",))
        row = cur.fetchone()
    return bool(row and row["value"] == "1")


async def reply(interaction: discord.Interaction, *, content=None, embed=None, view=None):
    kwargs = {"content": content, "embed": embed, "view": view, "ephemeral": True}
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


class _PrivateSender:
    """Keep command replies private when a callback was launched from the panel."""
    def __init__(self, sender):
        self._sender = sender

    def __getattr__(self, name):
        return getattr(self._sender, name)

    async def send_message(self, *args, **kwargs):
        kwargs["ephemeral"] = True
        return await self._sender.send_message(*args, **kwargs)

    async def send(self, *args, **kwargs):
        kwargs["ephemeral"] = True
        return await self._sender.send(*args, **kwargs)

    async def defer(self, *args, **kwargs):
        kwargs["ephemeral"] = True
        return await self._sender.defer(*args, **kwargs)


class _PrivateInteraction:
    def __init__(self, interaction):
        self._interaction = interaction
        self.response = _PrivateSender(interaction.response)
        self.followup = _PrivateSender(interaction.followup)

    def __getattr__(self, name):
        return getattr(self._interaction, name)


async def invoke(cog: commands.Cog, attr: str, interaction: discord.Interaction, *args):
    """Run an existing app-command callback so all game validation stays central."""
    command = getattr(cog, attr)
    await command.callback(cog, _PrivateInteraction(interaction), *args)


class OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float | None = 600):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    @i18n.localized
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(tr(language(interaction), "not_yours"), ephemeral=True)
        return False


class ChoiceView(OwnedView):
    def __init__(self, owner_id: int, lang: str, options: list[discord.SelectOption],
                 handler: Callable[[discord.Interaction, str], Awaitable[None]], *, multiple=False):
        super().__init__(owner_id)
        self.handler = handler
        select = discord.ui.Select(
            placeholder=tr(lang, "select"), options=options[:25],
            min_values=0 if multiple else 1,
            max_values=min(25, len(options)) if multiple else 1,
        )

        async def selected(interaction: discord.Interaction):
            value = ",".join(select.values) if multiple else select.values[0]
            await self.handler(interaction, value)
        select.callback = selected
        self.add_item(select)


class FieldsModal(discord.ui.Modal):
    def __init__(self, title: str, fields: list[dict], submit: Callable):
        super().__init__(title=title[:45], timeout=600)
        self.submitter = submit
        self.inputs = []
        for field in fields:
            item = discord.ui.TextInput(
                label=field["label"][:45], placeholder=field.get("placeholder", "")[:100],
                required=field.get("required", True), default=field.get("default"),
                style=field.get("style", discord.TextStyle.short), max_length=field.get("max_length"),
            )
            self.inputs.append(item)
            self.add_item(item)

    @i18n.localized
    async def on_submit(self, interaction: discord.Interaction):
        await self.submitter(interaction, *[item.value.strip() for item in self.inputs])

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        if isinstance(error, ValueError):
            message = ("Wpisz poprawną liczbę w polu liczbowym." if language(interaction) == "pl"
                       else "Enter a valid number in every numeric field.")
        else:
            message = i18n.t(language(interaction), "generic_error")
        await reply(interaction, content=message)


class TradeActions(OwnedView):
    def __init__(self, cog, owner_id, trade_id):
        super().__init__(owner_id)
        self.cog, self.trade_id = cog, trade_id

    @discord.ui.button(label="View / Pokaż", style=discord.ButtonStyle.secondary)
    async def view_trade(self, interaction, button):
        await invoke(self.cog, "trade_view", interaction, self.trade_id)

    @discord.ui.button(label="Accept / Akceptuj", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        await invoke(self.cog, "trade_accept", interaction, self.trade_id)

    @discord.ui.button(label="Cancel / Anuluj", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        await invoke(self.cog, "trade_cancel", interaction, self.trade_id)


SECTIONS = [
    ("home", "🏠", "home"), ("economy", "💰", "economy"), ("military", "⚔️", "military"),
    ("territory", "🗺️", "territory"), ("diplomacy", "🤝", "diplomacy"),
    ("events", "🎭", "events"), ("settings", "⚙️", "settings"),
]

ACTIONS = {
    "home": [("stats","📊"),("resources","📦"),("calendar","📅"),("goals","🎯"),("refresh","🔄")],
    "economy": [("resources","💰"),("build","🏗️"),("buildings","📚"),("yield","🌾"),("trades","🔁"),("new_trade","➕"),
                ("projects","🏛️"),("new_project","📝"),("start_project","▶️"),("contracts","📆")],
    "military": [("forces","🛡️"),("blueprints","📐"),("recruit","➕"),("move","➡️"),
                 ("new_blueprint","🧰"),("tech","🔬"),("research","🧪"),("posture","⏳")],
    "territory": [("provinces","🗺️"),("province","🔎"),("colonies","🏝️"),("colony_view","🔎"),
                  ("colony_found","🚩"),("colony_develop","📈"),("colony_expand","🧭"),("routes","🚢"),("settlers","👥")],
    "diplomacy": [("relations","📜"),("war","⚔️"),("peace","🕊️"),("alliance","🤝"),
                  ("battle_plan","🗒️"),("battles","📖"),("treaties","📜"),("new_treaty","📝"),("calls","🛡️")],
    "events": [("event_list","📋"),("event_play","🎭"),("memories","🧠")],
    "settings": [("help","❓"),("tutorial","📘"),("language_pl","🇵🇱"),("language_en","🇬🇧")],
}


class PlayerPanel(OwnedView):
    def __init__(self, bot: commands.Bot, owner_id: int, lang: str, section="home"):
        super().__init__(owner_id, timeout=900)
        self.bot, self.lang, self.section = bot, lang, section
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        options = [discord.SelectOption(label=tr(self.lang, key), value=value, emoji=emoji,
                    default=value == self.section) for value, emoji, key in SECTIONS]
        category = discord.ui.Select(placeholder=tr(self.lang, "choose"), options=options, row=0)
        async def change(interaction):
            self.section = category.values[0]
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)
        category.callback = change
        self.add_item(category)
        for index, (action, emoji) in enumerate(ACTIONS[self.section]):
            key = action.removeprefix("language_") if action.startswith("language_") else action
            label = {"pl":"Polski", "en":"English"}[key] if action.startswith("language_") else tr(self.lang, key)
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary,
                                       row=1 + index // 4)
            async def clicked(interaction, name=action):
                with i18n.using_language(language(interaction)):
                    await self.dispatch(interaction, name)
            button.callback = clicked
            self.add_item(button)

    def embed(self):
        nation = get_nation_by_owner(str(self.owner_id))
        embed = discord.Embed(title=f"🎮 {tr(self.lang, 'panel')} · {tr(self.lang, self.section)}",
                              color=discord.Color.blurple())
        if not nation:
            embed.description = tr(self.lang, "no_nation") + "\n\n" + tr(self.lang, "private")
            return embed
        resources = json.loads(nation["resources_json"] or "{}")
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM provinces WHERE owner_nation_id=? AND active=1", (nation["id"],))
            provinces = cur.fetchone()["n"]
            cur.execute("SELECT COALESCE(SUM(quantity),0) AS n FROM military_units WHERE nation_id=?", (nation["id"],))
            forces = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM trades WHERE status='pending' AND (from_nation_id=? OR to_nation_id=?)", (nation["id"], nation["id"]))
            trades = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM events WHERE nation_id=? AND status IN ('posted','active')", (nation["id"],))
            events = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM treaties WHERE status='proposed' AND recipient_id=?",(nation['id'],))
            proposals=cur.fetchone()['n']
            cur.execute("SELECT COUNT(*) AS n FROM guarantee_calls g JOIN treaties t ON t.id=g.treaty_id WHERE t.proposer_id=? AND g.status='pending'",(nation['id'],))
            calls=cur.fetchone()['n']
        embed.description = f"{flag_text(nation['flag'])} **{nation['name']}**\n{tr(self.lang, 'private')}"
        flagged_embed(embed, (nation['flag'], nation['name']))
        embed.add_field(name="💰 " + ("Skarbiec" if self.lang == "pl" else "Treasury"), value=f"{nation['treasury']:,.0f}")
        embed.add_field(name="⚖️ " + ("Stabilność" if self.lang == "pl" else "Stability"), value=f"{nation['stability']:.0f}/100")
        embed.add_field(name="🗺️/⚔️/🔁/🎭", value=f"{provinces} / {forces} / {trades} / {events}")
        if proposals or calls:
            embed.add_field(name='📬 '+tr(self.lang,'diplomacy'),
                value=(f'{proposals} propozycji traktatów · {calls} wezwań do obrony. Otwórz kategorię Dyplomacja.' if self.lang=='pl' else
                       f'{proposals} treaty proposals · {calls} defense calls. Open Diplomacy.'),inline=False)
        shown = sorted(resources.items(), key=lambda x: -float(x[1]))[:6]
        embed.add_field(name="📦 " + tr(self.lang, "resources"),
                        value=" · ".join(f"{i18n.term(k, self.lang)} {v:g}" for k,v in shown) or "—", inline=False)
        return embed

    def cog(self, name):
        return self.bot.get_cog(name)

    async def rows(self, interaction, sql, params, mapper, handler, *, multiple=False):
        with db.cursor() as cur:
            cur.execute(sql, params)
            records = cur.fetchall()
        if not records:
            await reply(interaction, content=tr(self.lang, "empty")); return
        options = [mapper(row) for row in records[:25]]
        note = tr(self.lang, "shortened") if len(records) > 25 else None
        await reply(interaction, content=note, view=ChoiceView(self.owner_id, self.lang, options, handler, multiple=multiple))

    async def dispatch(self, interaction: discord.Interaction, action: str):
        nation = get_nation_by_owner(str(self.owner_id))
        if action == "refresh":
            self.lang = language(interaction); self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self); return
        if action.startswith("language_"):
            new_lang = action[-2:]; i18n.set_user_language(self.owner_id, new_lang); self.lang = new_lang
            self.rebuild(); await interaction.response.edit_message(embed=self.embed(), view=self); return
        if action == "help":
            from cogs.economy import HelpView
            view = HelpView(is_gm=gm_only(interaction), current="general", lang=self.lang)
            await reply(interaction, embed=view._embed(), view=view); return
        if action == "tutorial":
            await reply(interaction, content=("Użyj kategorii powyżej, wybierz działanie i wskaż obiekt z listy. Formularz pojawi się tylko dla nazw, liczb lub rozkazów." if self.lang == "pl" else "Choose a category, select an action, then pick an item from the list. A form appears only for names, numbers or orders.")); return
        if not nation:
            await reply(interaction, content=tr(self.lang,"no_nation")); return

        simple = {
            'goals':('WorldCog','status',[]), 'memories':('WorldCog','memory',[]),
            'treaties':('TreatiesCog','list_treaties',[]), 'calls':('TreatiesCog','calls',[]),
            "stats":("NationCog","stats",[""]), "resources":("EconomyCog","resources",[]),
            "calendar":("EconomyCog","calendar_status",[]), "buildings":("EconomyCog","buildings_list",[]),
            "yield":("ProvincesCog","province_yield",[""]), "projects":("EconomyCog","mp_list",[]),
            "forces":("MilitaryCog","mil_list",[""]), "blueprints":("MilitaryCog","bp_list",[]),
            "tech":("TechCog","tech_status",[""]), "provinces":("ProvincesCog","province_list",[nation["name"],1]),
            "colonies":("ColonialismCog","colony_list",[""]), "routes":("ColonialismCog","traderoute_list",[]),
            "relations":("CombatCog","diplo_status",[]), "event_list":("EventsCog","event_list",[""]),
        }
        if action in simple:
            cog, attr, args = simple[action]; await invoke(self.cog(cog), attr, interaction, *args); return
        handlers = {
            "new_treaty":self.choose_treaty,
            "build":self.choose_build, "province":self.choose_province, "new_trade":self.choose_trade_target,
            "trades":self.choose_trade, "new_project":self.project_modal, "start_project":self.choose_project,
            "recruit":self.choose_blueprint, "move":self.choose_unit, "new_blueprint":self.choose_blueprint_type,
            "research":self.choose_research, "colony_view":self.choose_colony_view,
            "colony_found":self.choose_empty_province, "colony_develop":self.choose_colony_develop,
            "colony_expand":self.choose_colony_expand,
            "war":lambda i:self.choose_nation(i,"declare_war"), "peace":lambda i:self.choose_nation(i,"make_peace"),
            "alliance":lambda i:self.choose_nation(i,"alliance"), "battle_plan":self.choose_battle_units,
            "battles":self.choose_battle, "event_play":self.choose_event,
            'posture':self.choose_posture, 'settlers':self.choose_settlers, 'contracts':self.choose_contract,
        }
        if action in handlers: await handlers[action](interaction)

    async def choose_posture(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        async def unit(i2,uid):
            opts=[discord.SelectOption(label=label,value=value) for value,label in
                  [('reserve','Rezerwa 35% / Reserve 35%'),('active','Mobilizacja 100% / Mobilize 100%'),('deployed','Wyprawa 150% / Expedition 150%')]]
            async def mode(i3,value):await invoke(self.cog('EconomyControlCog'),'posture',i3,int(uid),value)
            await reply(i2,view=ChoiceView(self.owner_id,self.lang,opts,mode))
        await self.rows(i,'SELECT u.id,u.quantity,b.name FROM military_units u LEFT JOIN blueprints b ON b.id=u.blueprint_id WHERE u.nation_id=? ORDER BY u.id',(n['id'],),
                        lambda r:discord.SelectOption(label=f"#{r['id']} {r['name'] or 'Unit'} ×{r['quantity']}"[:100],value=str(r['id'])),unit)

    async def choose_settlers(self,i):
        async def target(i2,cell):await invoke(self.cog('EconomyControlCog'),'settlers',i2,int(cell))
        n=get_nation_by_owner(str(self.owner_id))
        await self.rows(i,'SELECT p.azgaar_cell_id,p.name FROM provinces p JOIN colonies x ON x.province_id=p.id WHERE p.owner_nation_id=? AND p.active=1 ORDER BY p.name',
                        (n['id'],),lambda r:discord.SelectOption(label=(r['name'] or str(r['azgaar_cell_id']))[:100],value=str(r['azgaar_cell_id']),
                        description='300 osadników · 50g + 3 żywności' if self.lang=='pl' else '300 settlers · 50g + 3 food'),target)

    async def choose_contract(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        async def choose(i2,tid):
            with db.cursor() as c:
                c.execute('SELECT t.*,x.status AS contract FROM trades t LEFT JOIN trade_contracts x ON x.trade_id=t.id WHERE t.id=?',(int(tid),))
                trade=c.fetchone()
            pl=self.lang=='pl'
            opts=[]
            if trade['status']=='pending' and trade['from_nation_id']==n['id'] and trade['contract'] is None:
                opts.append(discord.SelectOption(label='Zaproponuj wymianę co miesiąc' if pl else 'Propose a monthly exchange',value='recurring'))
            if trade['status']=='pending' and trade['to_nation_id']==n['id'] and trade['contract']=='proposed':
                opts.append(discord.SelectOption(label='Akceptuj teraz i co miesiąc' if pl else 'Accept now and every month',value='accept'))
            if trade['contract'] in ('active','waiting','proposed'):
                opts.append(discord.SelectOption(label='Zatrzymaj umowę' if pl else 'Stop contract',value='contract_stop'))
            if not opts:
                await reply(i2,content='Autor musi najpierw oznaczyć ofertę jako miesięczną.' if pl else 'The author must first mark this offer as monthly.');return
            async def action(i3,value):
                if value=='accept':await invoke(self.cog('EconomyCog'),'trade_accept',i3,int(tid),True)
                else:await invoke(self.cog('EconomyControlCog'),value,i3,int(tid))
            giving='offer' if trade['from_nation_id']==n['id'] else 'receive'
            receiving='receive' if giving=='offer' else 'offer'
            def terms(prefix):return i18n.resource_list(dict(json.loads(trade[prefix+'_resources_json']),gold=trade[prefix+'_gold']),self.lang)
            message=('Pierwsza wymiana po akceptacji, kolejne co miesiąc. Każda strona może zatrzymać umowę.' if pl else
                     'First exchange on acceptance, then every month. Either party can stop the contract.')
            message+='\n'+('Oddajesz: ' if pl else 'You give: ')+terms(giving)+'\n'+('Otrzymujesz: ' if pl else 'You receive: ')+terms(receiving)
            await reply(i2,content=message,view=ChoiceView(self.owner_id,self.lang,opts,action))
        await self.rows(i,"SELECT t.id,t.status,c.status AS contract FROM trades t LEFT JOIN trade_contracts c ON c.trade_id=t.id WHERE (t.from_nation_id=? OR t.to_nation_id=?) AND (t.status='pending' OR c.status IN ('active','waiting')) ORDER BY t.id",(n['id'],n['id']),
                        lambda r:discord.SelectOption(label=f"#{r['id']} · {i18n.term(r['contract'] or r['status'],self.lang)}"[:100],value=str(r['id'])),choose)

    async def choose_treaty(self, interaction):
        import treaty_service
        n=get_nation_by_owner(str(self.owner_id))
        async def target(i, name):
            options=[discord.SelectOption(label=labels[0 if self.lang=='pl' else 1],value=key)
                     for key,labels in treaty_service.KINDS.items()]
            async def kind(i2, key):
                async def submit(i3, duration, give, receive, give_cells, receive_cells):
                    await invoke(self.cog('TreatiesCog'),'propose',i3,name,key,int(duration),float(give or 0),float(receive or 0),give_cells,receive_cells)
                await i2.response.send_modal(FieldsModal(tr(self.lang,'new_treaty'),[
                    {'label':'Czas w miesiącach / Months','default':'12'},
                    {'label':'Oddajesz złoto / Gold you give','default':'0'},
                    {'label':'Otrzymujesz złoto / Gold you receive','default':'0'},
                    {'label':'Oddajesz ID prowincji / Ceded cell IDs','required':False,'placeholder':'1, 2, 3'},
                    {'label':'Otrzymujesz ID prowincji / Received IDs','required':False,'placeholder':'4, 5, 6'},
                ],submit))
            await reply(i,view=ChoiceView(self.owner_id,self.lang,options,kind))
        await self.rows(interaction,'SELECT name FROM nations WHERE id<>? ORDER BY name',(n['id'],),
                        lambda r:discord.SelectOption(label=r['name'][:100],value=r['name']),target)

    async def choose_build(self, i):
        n=get_nation_by_owner(str(self.owner_id))
        async def province(i2,cell):
            with db.cursor() as c:
                c.execute('SELECT buildings_json FROM provinces WHERE azgaar_cell_id=?',(int(cell),))
                existing=json.loads(c.fetchone()['buildings_json'])
            async def building(i3,key):
                if key in existing:
                    await invoke(self.cog('EconomyControlCog'),'upgrade',i3,int(cell),key)
                else:
                    await invoke(self.cog("EconomyCog"),"build",i3,int(cell),key)
            await self.rows(i2,"SELECT key,name,description FROM building_defs ORDER BY tier,name",(),
                lambda r:discord.SelectOption(label=((('↑ ' if r['key'] in existing else '+ '))+i18n.term(r['key'],self.lang))[:100],value=r['key'],description=('Ulepsz / Upgrade' if r['key'] in existing else 'Buduj / Build')),building)
        await self.owned_provinces(i,n,province)

    async def owned_provinces(self,i,n,handler):
        await self.rows(i,"SELECT azgaar_cell_id,name,terrain FROM provinces WHERE owner_nation_id=? AND active=1 ORDER BY name,azgaar_cell_id",(n['id'],),
            lambda r:discord.SelectOption(label=(r['name'] or f"Cell {r['azgaar_cell_id']}")[:100],value=str(r['azgaar_cell_id']),description=f"ID {r['azgaar_cell_id']} · {i18n.term(r['terrain'],self.lang)}"[:100]),handler)

    async def choose_province(self,i):
        async def done(i2,v): await invoke(self.cog("ProvincesCog"),"info",i2,int(v))
        await self.owned_provinces(i,get_nation_by_owner(str(self.owner_id)),done)

    async def choose_trade_target(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        async def target(i2,name):
            async def submit(i3,give,gold,receive,want,note):
                await invoke(self.cog("EconomyCog"),"trade_offer",i3,name,give or "{}",float(gold or 0),receive or "{}",float(want or 0),note,"")
            await i2.response.send_modal(FieldsModal(tr(self.lang,"new_trade"),[
                {"label":"Oddajesz zasoby / Give resources","default":"{}"},{"label":"Oddajesz złoto / Give gold","default":"0"},
                {"label":"Chcesz zasoby / Receive resources","default":"{}"},{"label":"Chcesz złoto / Receive gold","default":"0"},
                {"label":"Publiczna notatka / Public note","required":False,"max_length":300}],submit))
        await self.rows(i,"SELECT name,flag FROM nations WHERE id<>? ORDER BY name",(n['id'],),
            lambda r:discord.SelectOption(label=f"{flag_text(r['flag'])} {r['name']}"[:100],value=r['name']),target)

    async def choose_trade(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        async def done(i2,v): await reply(i2,view=TradeActions(self.cog("EconomyCog"),self.owner_id,int(v)))
        await self.rows(i,"SELECT t.id,t.status,a.name AS a,b.name AS b FROM trades t JOIN nations a ON a.id=t.from_nation_id JOIN nations b ON b.id=t.to_nation_id WHERE t.from_nation_id=? OR t.to_nation_id=? ORDER BY t.id DESC",(n['id'],n['id']),
            lambda r:discord.SelectOption(label=f"#{r['id']} {r['a']} → {r['b']}"[:100],value=str(r['id']),description=r['status']),done)

    async def project_modal(self,i):
        async def submit(i2,name,effect,gold): await invoke(self.cog("EconomyCog"),"mp_propose",i2,name,effect,int(gold))
        await i.response.send_modal(FieldsModal(tr(self.lang,"new_project"),[
            {"label":"Nazwa / Name"},{"label":"Pożądany efekt / Desired effect","style":discord.TextStyle.paragraph,"max_length":500},{"label":"Budżet złota / Gold budget"}],submit))

    async def choose_project(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        async def done(i2,v): await invoke(self.cog("EconomyCog"),"mp_build",i2,int(v))
        await self.rows(i,"SELECT id,name,status FROM megaprojects WHERE nation_id=? AND status='approved' ORDER BY id",(n['id'],),lambda r:discord.SelectOption(label=f"#{r['id']} {r['name']}"[:100],value=str(r['id']),description=r['status']),done)

    async def choose_blueprint(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        async def bp(i2,bpid):
            async def province(i3,cell):
                async def submit(i4,qty): await invoke(self.cog("MilitaryCog"),"mil_build",i4,int(bpid),int(qty),int(cell))
                await i3.response.send_modal(FieldsModal(tr(self.lang,"recruit"),[{"label":"Ilość / Quantity","default":"1"}],submit))
            with db.cursor() as cur:
                cur.execute("SELECT azgaar_cell_id,name FROM provinces WHERE owner_nation_id=? AND active=1 ORDER BY name",(n['id'],)); ps=cur.fetchall()
            opts=[discord.SelectOption(label="Bez przydziału / Unassigned",value="0")]+[discord.SelectOption(label=(p['name'] or f"Cell {p['azgaar_cell_id']}")[:100],value=str(p['azgaar_cell_id'])) for p in ps[:24]]
            await reply(i2,view=ChoiceView(self.owner_id,self.lang,opts,province))
        await self.rows(i,"SELECT id,name,type FROM blueprints WHERE nation_id=? ORDER BY name",(n['id'],),lambda r:discord.SelectOption(label=r['name'][:100],value=str(r['id']),description=i18n.term(r['type'],self.lang)),bp)

    async def choose_unit(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        async def unit(i2,uid):
            async def province(i3,cell): await invoke(self.cog("MilitaryCog"),"mil_move",i3,int(uid),int(cell))
            await self.owned_provinces(i2,n,province)
        await self.rows(i,"SELECT u.id,u.quantity,b.name FROM military_units u LEFT JOIN blueprints b ON b.id=u.blueprint_id WHERE u.nation_id=? ORDER BY u.id",(n['id'],),lambda r:discord.SelectOption(label=f"#{r['id']} {r['name'] or 'Unit'} ×{r['quantity']}"[:100],value=str(r['id'])),unit)

    async def choose_blueprint_type(self,i):
        opts=[discord.SelectOption(label="Milicja / Militia",value="unit:militia"),discord.SelectOption(label="Pikinierzy / Pikemen",value="unit:pikemen"),discord.SelectOption(label="Muszkieterzy / Musketeers",value="unit:musketeers"),discord.SelectOption(label="Dragoni / Dragoons",value="unit:dragoons"),discord.SelectOption(label="Armata / Field cannon",value="unit:field_cannon"),discord.SelectOption(label="Slup / Sloop",value="ship:sloop"),discord.SelectOption(label="Fregata / Frigate",value="ship:frigate"),discord.SelectOption(label="Galeon / Galleon",value="ship:galleon"),discord.SelectOption(label="Okręt liniowy / Ship of the line",value="ship:ship_of_the_line")]
        async def done(i2,value):
            kind,key=value.split(":",1)
            async def submit(i3,name):
                choice=app_commands.Choice(name=key.replace('_',' ').title(),value=key)
                await invoke(self.cog("MilitaryCog"),"design_ship" if kind=="ship" else "create_unit",i3,name,choice)
            await i2.response.send_modal(FieldsModal(tr(self.lang,"new_blueprint"),[{"label":"Nazwa projektu / Blueprint name"}],submit))
        await reply(i,view=ChoiceView(self.owner_id,self.lang,opts,done))

    async def choose_research(self,i):
        opts=[discord.SelectOption(label=i18n.term(x,self.lang).title(),value=x) for x in ('naval','land','economy','colonial')]
        async def done(i2,category):
            async def submit(i3,steps): await invoke(self.cog("TechCog"),"tech_research",i3,app_commands.Choice(name=category,value=category),int(steps))
            await i2.response.send_modal(FieldsModal(tr(self.lang,"research"),[{"label":"Liczba kroków / Steps","default":"1"}],submit))
        await reply(i,view=ChoiceView(self.owner_id,self.lang,opts,done))

    async def colonies(self,i,handler):
        n=get_nation_by_owner(str(self.owner_id))
        await self.rows(i,"SELECT c.name,c.status,p.azgaar_cell_id FROM colonies c JOIN provinces p ON p.id=c.province_id WHERE c.nation_id=? ORDER BY c.name",(n['id'],),lambda r:discord.SelectOption(label=r['name'][:100],value=str(r['azgaar_cell_id']),description=i18n.term(r['status'],self.lang)),handler)

    async def choose_colony_view(self,i):
        async def done(i2,v): await invoke(self.cog("ColonialismCog"),"colony_view",i2,int(v))
        await self.colonies(i,done)

    async def choose_colony_develop(self,i):
        async def colony(i2,cell):
            async def submit(i3,gold): await invoke(self.cog("ColonialismCog"),"colony_develop",i3,int(cell),int(gold))
            await i2.response.send_modal(FieldsModal(tr(self.lang,"colony_develop"),[{"label":"Ilość złota / Gold amount"}],submit))
        await self.colonies(i,colony)

    async def choose_empty_province(self,i):
        async def submit(i2,cell,name):
            await invoke(self.cog("ColonialismCog"),"colony_found",i2,int(cell),name)
        hint=("Jeśli nie znasz ID, poproś administratora" if self.lang == "pl"
              else "Ask an administrator if you do not know the ID")
        await i.response.send_modal(FieldsModal(tr(self.lang,"colony_found"),[
            {"label":"ID prowincji / Province ID","placeholder":hint},
            {"label":"Nazwa kolonii / Colony name"},
        ],submit))

    async def choose_colony_expand(self,i):
        async def source(i2,source_cell):
            async def submit(i3,target_cell,name):
                await invoke(self.cog("ColonialismCog"),"colony_expand",i3,int(source_cell),int(target_cell),name)
            hint=("Wpisz ID sąsiedniego pola; w razie potrzeby zapytaj administratora" if self.lang == "pl"
                  else "Enter an adjacent cell ID; ask an administrator if needed")
            await i2.response.send_modal(FieldsModal(tr(self.lang,"colony_expand"),[
                {"label":"ID sąsiedniej prowincji / Target ID","placeholder":hint},
                {"label":"Nazwa nowej placówki / Outpost name"},
            ],submit))
        n=get_nation_by_owner(str(self.owner_id))
        await self.rows(i,
            "SELECT c.name,c.status,p.azgaar_cell_id FROM colonies c JOIN provinces p ON p.id=c.province_id "
            "WHERE c.nation_id=? AND c.status IN ('settlement','colony','province') ORDER BY c.name",
            (n['id'],),
            lambda r:discord.SelectOption(label=r['name'][:100],value=str(r['azgaar_cell_id']),description=f"ID {r['azgaar_cell_id']} · {i18n.term(r['status'],self.lang)}"[:100]),
            source,
        )

    async def choose_nation(self,i,command):
        n=get_nation_by_owner(str(self.owner_id))
        async def done(i2,name): await invoke(self.cog("CombatCog"),command,i2,name)
        await self.rows(i,"SELECT name,flag FROM nations WHERE id<>? ORDER BY name",(n['id'],),lambda r:discord.SelectOption(label=f"{flag_text(r['flag'])} {r['name']}"[:100],value=r['name']),done)

    async def choose_battle_units(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        with db.cursor() as cur:
            cur.execute("SELECT u.id,u.quantity,b.name FROM military_units u LEFT JOIN blueprints b ON b.id=u.blueprint_id WHERE u.nation_id=? ORDER BY u.id",(n['id'],)); rows=cur.fetchall()
        opts=[discord.SelectOption(label=f"#{r['id']} {r['name'] or 'Unit'} ×{r['quantity']}"[:100],value=str(r['id'])) for r in rows[:25]]
        async def units(i2,ids):
            async def submit(i3,location,orders,note): await invoke(self.cog("CombatCog"),"battle_plan",i3,location,orders,note,ids)
            await i2.response.send_modal(FieldsModal(tr(self.lang,"battle_plan"),[{"label":"Miejsce / Location"},{"label":"Rozkazy / Orders","style":discord.TextStyle.paragraph,"max_length":1000},{"label":"Opis sił / Forces note","required":False,"style":discord.TextStyle.paragraph,"max_length":500}],submit))
        if rows: await reply(i,content=(tr(self.lang,"shortened") if len(rows)>25 else None),view=ChoiceView(self.owner_id,self.lang,opts,units,multiple=True))
        else: await units(i,"")

    async def choose_battle(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        async def done(i2,v): await invoke(self.cog("CombatCog"),"battle_view",i2,int(v))
        await self.rows(i,"SELECT DISTINCT b.id,b.status FROM battles b JOIN battle_plans a ON a.id=b.plan_a_id JOIN battle_plans d ON d.id=b.plan_b_id WHERE a.nation_id=? OR d.nation_id=? ORDER BY b.id DESC",(n['id'],n['id']),lambda r:discord.SelectOption(label=f"Battle #{r['id']}",value=str(r['id']),description=r['status']),done)

    async def choose_event(self,i):
        n=get_nation_by_owner(str(self.owner_id))
        async def done(i2,v): await invoke(self.cog("EventsCog"),"event_play",i2,int(v))
        await self.rows(i,"SELECT id,status,COALESCE(NULLIF(gm_final_text,''),ai_draft_text) AS text FROM events WHERE nation_id=? AND status IN ('posted','active') ORDER BY id DESC",(n['id'],),lambda r:discord.SelectOption(label=f"Event #{r['id']}",value=str(r['id']),description=(r['text'] or r['status'])[:100]),done)


async def send_panel(bot, interaction):
    if not active_guild(interaction.guild_id):
        await reply(interaction, content=i18n.text("⛔ Bot not activated on this server. The GM must run `/activate <auth_key>` first.", lang=language(interaction))); return
    lang=language(interaction); view=PlayerPanel(bot,interaction.user.id,lang)
    await reply(interaction,embed=view.embed(),view=view)


class PanelLauncher(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None); self.bot=bot

    @discord.ui.button(label="Open player panel / Otwórz panel",emoji="🎮",style=discord.ButtonStyle.primary,custom_id="wargame:player-panel:open")
    async def open_panel(self,interaction,button): await send_panel(self.bot,interaction)


class PanelCog(commands.Cog):
    def __init__(self,bot): self.bot=bot

    @app_commands.command(name="panel",description="Open the player panel / Otwórz panel gracza")
    @i18n.localized
    async def panel(self,interaction:discord.Interaction): await send_panel(self.bot,interaction)

    @app_commands.command(name="panel_publish",description="Post the permanent player panel button (GM only)")
    @i18n.localized
    async def panel_publish(self,interaction:discord.Interaction):
        lang=language(interaction)
        if not gm_only(interaction):
            await interaction.response.send_message(i18n.t(lang,"gm_only"),ephemeral=True); return
        embed=discord.Embed(title=f"🎮 {tr(lang,'panel')}",description=tr(lang,"launcher_desc"),color=discord.Color.blurple())
        await interaction.channel.send(embed=embed,view=PanelLauncher(self.bot))
        await interaction.response.send_message(tr(lang,"published"),ephemeral=True)


async def setup(bot:commands.Bot):
    await bot.add_cog(PanelCog(bot))
    bot.add_view(PanelLauncher(bot))
