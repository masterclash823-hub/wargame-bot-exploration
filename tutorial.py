"""A shared, bilingual introduction; shortcuts open menus without taking actions."""
import discord
import i18n
from flags import flagged_embed
from utils import get_nation_by_owner


# Each topic has a title, a short explanation and a shortcut to the relevant panel.
CHAPTERS = [
    dict(key='welcome', target='home', pl=(
        'Od czego zacząć',
        'Najczęściej będziesz używać /panel. Są tam zasoby, budowa, badania i sprawy czekające na odpowiedź.\n\n'
        'Zacznij od Gospodarka → Zasoby. Zobacz, czy wystarczy żywności i złota na kolejny miesiąc gry. Język zmienisz przez /language.'), en=(
        'Getting started',
        'Use /panel for resources, construction, research and anything waiting for your reply.\n\n'
        'Start with Economy → Resources. Check whether you have enough food and gold for the next game month. Use /language to change the bot’s language.')),
    dict(key='economy', target='economy', pl=(
        'Gospodarka i rozwój',
        'Zakładka Gospodarka → Zasoby pokazuje przewidywany bilans. Gdy brakuje żywności, zbuduj farmę w odpowiedniej prowincji albo kup dostawy od innego gracza.\n\n'
        'W każdej prowincji możesz mieć po jednym budynku danego typu i ulepszać go do poziomu 3. Pracownicy są przydzielani automatycznie.\n\n'
        'W menu Technologie → Badania wybierz projekt. Możesz prowadzić jeden naraz; postęp nalicza się co miesiąc gry.'), en=(
        'Economy and research',
        'Economy → Resources shows your forecast. If food is running short, build a farm in a suitable province or buy supplies from another player.\n\n'
        'Each province can have one building of each type, with upgrades up to level 3. Workers are assigned automatically.\n\n'
        'Choose a project in Technology → Research. You can run one at a time; it progresses each game month.')),
    dict(key='trade', target='diplomacy', pl=(
        'Handel i umowy',
        'Gospodarka → Nowa wymiana: wybierz państwo i wpisz, co oddajesz oraz co chcesz dostać. Wymiana dojdzie do skutku po akceptacji drugiego gracza.\n\n'
        'Sojusze i pokój ustalisz w Dyplomacja i bitwy → Nowy traktat. Przeczytaj warunki przed zaakceptowaniem — mogą obejmować prowincje i płatności.'), en=(
        'Trade and treaties',
        'Economy → New trade: choose a nation, what you offer and what you want in return. The exchange happens once the other player accepts.\n\n'
        'For alliances and peace, use Diplomacy & battles → New treaty. Read the terms before accepting: they can include provinces and payments.')),
    dict(key='events', target='events', pl=(
        'Wydarzenia',
        'Eventy znajdziesz w menu Wydarzenia → Rozegraj wydarzenie. Wybierz jedną z trzech odpowiedzi albo napisz własną.\n\n'
        'Event kończy się po najwyżej trzech decyzjach. Wtedy bot nalicza skutki. Możesz rozegrać go w panelu, nawet jeśli nie dotarła wiadomość prywatna.'), en=(
        'Events',
        'Open Events → Play event to respond to an event assigned to your nation. Choose one of three answers or write your own.\n\n'
        'An event ends after at most three decisions. Its effects apply at the end. You can play it through the panel even if you did not receive a direct message.')),
    dict(key='military', target='military', pl=(
        'Przed bitwą',
        'Sprawdź jednostki w Wojsko → Siły zbrojne. Oddziały w rezerwie potrzebują miesiąca gry na mobilizację.\n\n'
        'Plan wyślesz przez Dyplomacja i bitwy → Wyślij plan bitwy. Zaznacz jednostki w formularzu — samo wymienienie ich w opisie nie wysyła ich do walki. '
        'Napisz, co chcesz osiągnąć i jak mają działać. Bitwę rozstrzyga GM.'), en=(
        'Before a battle',
        'Check your units in Military → Armed forces. Units in reserve need one game month to mobilize.\n\n'
        'Submit your plan through Diplomacy & battles → Submit battle plan. Select the units in the form; naming them in the description does not assign them. '
        'Explain your objective and how the troops should act. The GM resolves the battle.')),
]


class TutorialView(i18n.LocalizedView):
    def __init__(self, bot, owner_id, lang, page=0):
        super().__init__(timeout=900)
        self.bot, self.owner_id, self.lang = bot, owner_id, lang
        self.page = max(0, min(page, len(CHAPTERS)-1))
        self.chapters.placeholder = 'Wybierz temat' if lang == 'pl' else 'Choose a topic'
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
        title, text = CHAPTERS[self.page][self.lang]
        nation = get_nation_by_owner(str(self.owner_id))
        if self.page == 0 and not nation:
            text = (('Poproś Game Mastera o utworzenie i przydzielenie państwa. Przygotuj nazwę i krótką historię.'
                     if self.lang == 'pl' else
                     'Ask the Game Master to create and assign a nation to you. Prepare a name and a short history.')
                    + '\n\n' + text)
        embed = discord.Embed(title=title, description=text, color=discord.Color.gold())
        if nation:
            flagged_embed(embed,(nation['flag'],nation['name']))
        embed.set_footer(text=f"{self.page+1}/{len(CHAPTERS)} · /help")
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
