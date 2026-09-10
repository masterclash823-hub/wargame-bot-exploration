"""A shared, bilingual introduction; shortcuts open menus without taking actions."""
import discord
import i18n
from flags import flagged_embed
from utils import get_nation_by_owner


# Each chapter: title, situation, suggested next move, rules worth remembering.
CHAPTERS = [
    dict(key='welcome', target='home', pl=(
        '🏰 Pierwsza rada państwa',
        'Na stole leżą mapa, rachunki i list od sąsiada. Nie musisz rozwiązać wszystkiego naraz. Zacznij od potrzeb mieszkańców, potem wybierz własny kierunek rozwoju.',
        'Otwórz panel i obejrzyj statystyki. Potem przejdź do gospodarki. Kolejne rozdziały podpowiedzą Ci pierwszy ruch — wybieraj to, co pasuje do Twojego państwa.',
        'Większość działań wybierzesz przyciskami. Formularze służą do liczb, nazw i własnych pomysłów. Czytanie przewodnika ani otwieranie panelu nie wydaje zasobów.'), en=(
        '🏰 Your first council',
        'A map, a stack of bills and a letter from your neighbor lie on the table. You do not need to solve everything at once. Meet your people’s needs, then choose your own direction.',
        'Open the panel and look at your nation’s stats, then visit Economy. The next chapters suggest a first move: choose what fits your nation.',
        'Most actions use buttons. Forms are for amounts, names and your own ideas. Reading this guide or opening a panel spends no resources.')),
    dict(key='economy', target='economy', pl=(
        '🌾 Najpierw chleb, potem ambicje',
        'Skarbnik proponuje wielki port. Zarządca spichlerzy pyta, czym nakarmisz budowniczych. Najpierw sprawdź, czy państwo utrzyma kolejny miesiąc.',
        'Gospodarka → Zasoby: spójrz na prognozę złota i żywności oraz „Co teraz?”. Jeśli brakuje jedzenia, rozważ farmę, ulepszenie lub dostawy od sąsiada.',
        'W prowincji można mieć po jednym budynku każdego typu, do poziomu 3. Pracownicy są przydzielani automatycznie. Normalne podatki wystarczą na start; wysokie zwiększają niepokoje i obniżają stabilność, niskie działają odwrotnie. Prognoza dotyczy miesiąca gry.'), en=(
        '🌾 Bread before ambition',
        'Your treasurer proposes a grand harbor. The granary keeper asks how you will feed its builders. First, check whether your nation can sustain another month.',
        'Economy → Resources: inspect projected gold, food and “What next?”. If food is short, consider a farm, an upgrade or regular imports.',
        'A province supports one building of each type, up to level 3. Workers are assigned automatically. Normal taxes are enough to start; high taxes raise unrest and lower stability, while low taxes do the reverse. The forecast covers one game month.')),
    dict(key='goals', target='home', pl=(
        '🎯 Wybierz, z czego zasłynie państwo',
        'Rada jest podzielona: pełne spichlerze, nowe warsztaty czy odkrycia uczonych? Jeden cel pomoże wybrać najbliższe inwestycje.',
        'Przegląd → Cele państwowe. Wybierz bezpieczne zapasy, rozwój państwa albo postęp naukowy. Możesz też odłożyć tę decyzję — cele są opcjonalne.',
        'Zapasy: 3 kolejne miesiące bez głodu, żywność na 2 miesiące i stabilność ≥60. Rozwój: 2 nowe budowy lub ulepszenia po wyborze celu. Nauka: badanie i wzrost dziedziny o 0,3. Każdy cel trwa co najmniej 3 miesiące gry, daje 10 prestiżu i rozlicza się automatycznie. Porzucenie nie kosztuje.'), en=(
        '🎯 Decide what your nation will be known for',
        'The council is divided: full granaries, new workshops or scholarly discoveries? One goal can help you choose your next investments.',
        'Overview → National goals. Choose food security, national development or scientific progress. You can also leave this for later: goals are optional.',
        'Food security: 3 consecutive months without hunger, two months of food and stability ≥60. Development: 2 builds or upgrades after choosing the goal. Science: research and a field increase of 0.3. Every goal takes at least 3 game months and automatically awards 10 prestige. Abandoning is free.')),
    dict(key='trade', target='economy', pl=(
        '🤝 Nie wszystko trzeba produkować samemu',
        'Twoje magazyny są pełne drewna, a sąsiad potrzebuje go do budowy floty. Być może jego nadwyżka żywności rozwiąże Twój problem.',
        'Gospodarka → Nowa wymiana: wybierz partnera i wpisz, co oddajesz oraz czego potrzebujesz. W „Wymianach” sprawdzisz odpowiedź i warunki.',
        'Zasoby przechodzą dopiero po akceptacji. „Umowy miesięczne” pozwalają powtarzać wymianę za zgodą odbiorcy; przy braku zasobów umowa czeka. Każda strona może ją zatrzymać. Warunki są prywatne dla stron i GM.'), en=(
        '🤝 You do not have to produce everything',
        'Your warehouses are full of timber, and a neighbor needs it for a fleet. Perhaps their surplus grain can solve your food problem.',
        'Economy → New trade: choose a partner, what you offer and what you need. Open Trades to review terms and the response.',
        'Resources change hands only after acceptance. Monthly contracts repeat exchanges with the recipient’s consent; a shortage makes the contract wait. Either party can stop it. Terms are private to the parties and GM.')),
    dict(key='diplomacy', target='diplomacy', pl=(
        '📜 Pieczęć ma znaczenie',
        'Poseł przynosi obietnicę pokoju. Zanim przyłożysz pieczęć, sprawdź granice, raty i czas trwania — sama przyjazna deklaracja nie kończy wojny.',
        'Dyplomacja i bitwy → Nowy traktat. Przygotuj szkic, uzupełnij warunki i kliknij „Wyślij propozycję”. Odbiorca przegląda ją w „Traktatach i propozycjach” i akceptuje całość.',
        'Do wyboru: pokój, nieagresja, sojusz, dostęp wojskowy i gwarancja. Pokój może obejmować prowincje, złoto i reparacje. Zerwanie kosztuje 10 reputacji, a uzgodnione raty i dług pozostają. Gwarancja wymaga odpowiedzi na wezwanie przed kolejnym miesiącem gry; nie dołącza automatycznie do wojny.'), en=(
        '📜 Your seal matters',
        'An envoy brings a promise of peace. Before signing, inspect the borders, installments and duration: friendly words alone do not end a war.',
        'Diplomacy & battles → New treaty. Prepare a draft, complete its terms and click Send proposal. The recipient reviews Treaties & proposals and accepts all terms.',
        'Choose peace, non-aggression, alliance, military access or a guarantee. Peace can include provinces, gold and reparations. Breaking costs 10 reputation; agreed installments and debts remain payable. Guarantees require a response before the next game month and never join a war automatically.')),
    dict(key='military', target='military', pl=(
        '⚔️ Rozkaz to więcej niż dobry opis',
        'Dowódca obiecuje utrzymać most. Potrzebuje jednak prawdziwych oddziałów, czasu na mobilizację i rozkazów pasujących do terenu.',
        'Wojsko i technologia → Siły zbrojne: sprawdź jednostki i utrzymanie. Przed bitwą otwórz Dyplomacja i bitwy → Wyślij plan bitwy i zaznacz oddziały, które naprawdę wysyłasz.',
        'Rezerwa kosztuje 35% utrzymania, aktywna służba 100%, wyprawa 150%. Mobilizacja z rezerwy trwa miesiąc gry. Nazwy oddziałów wpisane w opisie nie przypisują ich do planu. Opisz cel, teren i sposób działania; GM ustala ostateczne miejsce i rozstrzyga bitwę.'), en=(
        '⚔️ An order needs more than a good story',
        'Your commander promises to hold the bridge. They still need real troops, time to mobilize and orders that fit the terrain.',
        'Military & technology → Armed forces: review units and upkeep. Before battle, open Diplomacy & battles → Submit battle plan and select the units you actually commit.',
        'Reserves cost 35% upkeep, active duty 100% and expeditions 150%. Mobilizing from reserve takes one game month. Mentioning units in prose does not assign them to a plan. Explain the objective, terrain and approach; the GM sets the final location and resolves the battle.')),
    dict(key='expansion', target='territory', pl=(
        '🧭 Na mapie jest jeszcze miejsce',
        'Odkrywcy donoszą o nowym wybrzeżu. Kolonia potrzebuje ludzi i zaopatrzenia, a nie tylko nazwy na mapie.',
        'Terytorium → Załóż kolonię: wpisz ID prowincji; jeśli go nie znasz, zapytaj administratora. Potem sprawdzaj „Szczegóły kolonii”, aby zobaczyć brakujące wymagania.',
        'Nowa kolonia kosztuje 500 złota, wymaga 5 wolnej ładowności i przeniesienia 300 osadników z własnej prowincji. Awans następuje automatycznie po spełnieniu wymagań finansowania, czasu, ludności, technologii i żywności. Od etapu osady można rozszerzać kolonię na sąsiednie pola. Większe pomysły zgłaszaj w Gospodarka → Zaproponuj projekt; megaprojekt wymaga zgody GM.'), en=(
        '🧭 There is still room on the map',
        'Explorers report a new coast. A colony needs people and supplies, not just a name on a map.',
        'Territory → Found colony: enter the province ID; ask an administrator if you do not know it. Then check Colony details for any missing requirements.',
        'A new colony costs 500 gold, requires 5 available cargo capacity and transfers 300 settlers from your own province. It advances automatically when funding, time, population, technology and food requirements are met. From settlement onward, you can expand into neighboring cells. For larger ideas, use Economy → Propose project; megaprojects need GM approval.')),
    dict(key='events', target='events', pl=(
        '🎭 Twoje decyzje zostają w historii',
        'Na granicy wybucha spór. Możesz zaufać doradcom albo zaproponować własne rozwiązanie. Państwo będzie pamiętać, jak postąpisz.',
        'Wydarzenia → Rozegraj wydarzenie. Wybierz jedną z trzech odpowiedzi albo wpisz własną. Zakończony event znajdziesz później w „Pamięci decyzji”.',
        'Event trwa najwyżej 3 decyzje; własna odpowiedź także liczy się jako decyzja. Skutki są naliczane na końcu, a ich rzeczywiste wartości zapisywane w pamięci. Kolejne eventy mogą nawiązywać do wcześniejszych wyborów. Publiczne ogłoszenie nie ujawnia Twoich opcji ani odpowiedzi. Gdy DM nie dochodzą, użyj panelu.'), en=(
        '🎭 Your decisions become history',
        'A dispute erupts at the border. Follow an adviser or propose your own solution. Your nation will remember what you chose.',
        'Events → Play event. Choose one of three responses or write your own. Find the finished event later in Decision memory.',
        'An event takes at most 3 decisions; a custom answer counts too. Effects apply at the end, and actual changes are recorded in memory. Future events can refer to earlier choices. Public announcements do not reveal your options or answers. If DMs do not arrive, use the panel.')),
    dict(key='routine', target='home', pl=(
        '📰 Następne posiedzenie rady',
        'Nie trzeba codziennie przeklikiwać wszystkich zakładek. Wróć do spraw, które czekają na Twoją decyzję, i zostaw państwu czas na rozwój.',
        'Zacznij od trzech pytań: czy wystarczy żywności i złota? Czy czeka event, oferta lub wezwanie do obrony? Jaki jeden ruch przybliży mnie do wybranego celu?',
        'Gospodarka, raty, cele i rozwój kolonii rozliczają się według miesięcy gry. Kronika działa raz na rzeczywistą dobę, jeśli GM ustawi kanał: pokazuje do dwóch publicznych akcji. Nie zamieszcza tajnych planów i odpowiedzi. W razie wątpliwości zajrzyj do Pomocy lub wróć do dowolnego rozdziału z listy.'), en=(
        '📰 Your next council meeting',
        'You do not need to visit every tab each day. Return to matters awaiting your decision and give your nation time to develop.',
        'Start with three questions: is there enough food and gold? Is an event, offer or defense call waiting? What single move brings me closer to my goal?',
        'Economy, installments, goals and colonial development follow game months. The chronicle runs once per real day if the GM configures a channel, showing up to two public actions. It omits secret plans and answers. Check Help when unsure, or revisit any chapter from the list.')),
]


class TutorialView(i18n.LocalizedView):
    def __init__(self, bot, owner_id, lang, page=0):
        super().__init__(timeout=900)
        self.bot, self.owner_id, self.lang = bot, owner_id, lang
        self.page = max(0, min(page, len(CHAPTERS)-1))
        self.chapters.placeholder = 'Wybierz rozdział' if lang == 'pl' else 'Choose a chapter'
        self.chapters.options = [discord.SelectOption(
            label=f"{index+1}. {chapter[lang][0]}", value=str(index), default=index==self.page)
            for index, chapter in enumerate(CHAPTERS)]
        self.previous.label = 'Wstecz' if lang == 'pl' else 'Back'
        self.next_page.label = 'Dalej' if lang == 'pl' else 'Next'
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == len(CHAPTERS)-1
        from cogs.panel import tr
        self.open_panel.label = ('Otwórz: ' if lang == 'pl' else 'Open: ') + tr(lang,CHAPTERS[self.page]['target'])

    @i18n.localized
    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(i18n.text('This is not your tutorial.'), ephemeral=True)
        return False

    def embed(self):
        title, scene, move, rules = CHAPTERS[self.page][self.lang]
        nation = get_nation_by_owner(str(self.owner_id))
        if self.page == 0 and not nation:
            move = ('Nie masz jeszcze państwa. Poproś Game Mastera o jego utworzenie i nadanie Tobie; przygotuj nazwę i krótką historię. GM używa /nation found ze wskazaniem gracza. W międzyczasie możesz poznać zasady z tego przewodnika.'
                    if self.lang == 'pl' else
                    'You do not have a nation yet. Ask the Game Master to create and assign one to you; prepare a name and brief history. The GM uses /nation found with a selected player. Meanwhile, you can explore the rules in this guide.')
        embed = discord.Embed(title=title, description=scene, color=discord.Color.gold())
        if nation:
            flagged_embed(embed,(nation['flag'],nation['name']))
        embed.add_field(name='Twój następny ruch' if self.lang=='pl' else 'Your next move', value=move, inline=False)
        embed.add_field(name='Warto wiedzieć' if self.lang=='pl' else 'Worth knowing', value=rules, inline=False)
        embed.set_footer(text=f"{self.page+1}/{len(CHAPTERS)} · "+(
            'Czytaj po kolei albo wybierz temat. Przycisk otwiera panel; decyzję podejmujesz sam.' if self.lang=='pl' else
            'Follow the chapters or pick a topic. The button opens a panel; you decide what to do.'))
        return embed

    async def navigate(self, interaction, page):
        view = TutorialView(self.bot,self.owner_id,i18n.current_language(),page)
        await interaction.response.edit_message(embed=view.embed(), view=view)
        self.stop()

    @discord.ui.select(row=0)
    @i18n.localized
    async def chapters(self, interaction, select):
        await self.navigate(interaction,int(select.values[0]))

    @discord.ui.button(label='Back',emoji='◀',row=1)
    @i18n.localized
    async def previous(self, interaction, button):
        await self.navigate(interaction,self.page-1)

    @discord.ui.button(label='Next',emoji='▶',row=1)
    @i18n.localized
    async def next_page(self, interaction, button):
        await self.navigate(interaction,self.page+1)

    @discord.ui.button(label='Open panel',style=discord.ButtonStyle.primary,row=2)
    @i18n.localized
    async def open_panel(self, interaction, button):
        from cogs.panel import send_panel
        await send_panel(self.bot,interaction,section=CHAPTERS[self.page]['target'])


@i18n.localized
async def show_tutorial(bot, interaction):
    view = TutorialView(bot,interaction.user.id,i18n.current_language())
    await interaction.response.send_message(embed=view.embed(),view=view,ephemeral=True)
