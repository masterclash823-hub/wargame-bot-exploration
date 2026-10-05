# Wargame Bot

## Ustawienia mapy, kreator startu i AI projektów

GM udostępnia mapę przez `/admin map_access` (domyślnie wyłączone).
Gracz wybiera bonusy w `/nation bonuses`: standardowo 35 punktów, maksymalnie
10 prowincji. Budżet ustawia `/nation start_budget`.
`/project review` ocenia projekty przez osobny **PROJECT_AI_API_KEY**, bez
zużywania kluczy eventów i walki; rekomendację zatwierdza GM.
Doszły wycinka lasów, produkcja plantacji 6 → 7 żywności i ograniczenie
zbędnych synchronizacji Discorda. [Zasady, panel i konfiguracja](docs/game-setup.md).

## Wojny graczy i zgłoszenia państw

**Panel → Dyplomacja i bitwy → Panel wojen** lub `/war status` pokazuje przeciwników,
wyzwania do odpowiedzi, terminy i raporty. Atakujący wybiera swój plan oraz pole
bitwy; obrońca wybiera plan obrony i potwierdza rozliczenie. Bot sam oblicza wynik,
straty i premie fortyfikacji. W bitwach koalicyjnych AI ocenia plany obu stron,
przyznając mnożniki taktyczne ×0,7–1,4 dla całych stron. Granice ustala osobno
zaakceptowany traktat.

Gracz bez państwa używa **Zgłoś państwo** w panelu lub `/nation found`, podaje
nazwę, historię i prowincje startowe. GM otwiera **Zgłoszenia państw** w panelu
administratora lub `/nation applications` i akceptuje bądź odrzuca zgłoszenie.
Akceptacja przyznaje ziemię, stolicę, projekty jednostek i pakiet startowy.
[Przebieg, komendy i zasady](docs/player-workflows.md).

## Panel administratora i coop

`/admin panel` udostępnia wybór państwa i prowincji, zmianę populacji i biomu,
przyznawanie prowincji z opcjonalnym uśrednianiem oraz skróty do eventów,
kalendarza i bitew. Główny właściciel lub GM dodaje współgracza przez
`/nation coop_add`; obaj korzystają z jednego państwa i wspólnych limitów.
[Nowe komendy, zasady współdzielenia i poprawki budynków](docs/admin-coop-update.md).

## Pierwsza rada państwa — przewodnik gracza

`/tutorial` oraz **Panel → Ustawienia → Poradnik** otwierają ten sam przewodnik
z jedenastoma krótkimi rozdziałami: start, gospodarka, cele, wymiany, traktaty, badania, algae,
wojsko, ekspansja, eventy i codzienna gra. Każdy rozdział zawiera scenkę,
propozycję następnego ruchu oraz przycisk otwierający właściwą kategorię panelu.
Można czytać kolejno albo wybierać temat z listy. Gracz bez państwa otrzymuje
instrukcję zgłoszenia państwa do akceptacji GM-a. Przewodnik jest prywatny, działa po polsku i angielsku,
a jego przyciski nie wykonują za gracza zakupów ani decyzji.

## Kronika, traktaty i cele państwowe

Panel pozwala wybrać opcjonalny cel za prestiż, przeglądać pamięć decyzji oraz
negocjować traktaty z obustronną akceptacją, reparacjami i przekazywaniem prowincji.
Gracz zgłasza państwo przez `/nation found`, a GM akceptuje je w `/nation applications`.
GM może później przekazać państwo przez `/nation transfer`.

`/chronicle configure channel:#kronika` włącza codzienny raport do dwóch
publicznych akcji graczy. Szczegółowe zasady, wpływ podatków i konfiguracja:
[Aktualizacja świata](docs/world-update.md).

## Rankingi państw

GM tworzy ranking przez `/ranking category:prestige`. Bez `channel` wynik jest
prywatny; `/ranking category:population channel:#rankingi limit:20` publikuje go
na wskazanym kanale. Dostępne są prestiż, ludność, liczba prowincji, skarbiec,
miesięczny bilans złota, technologia, siła armii i siła floty. Wiadomość pokazuje
do 25 państw, a plik TXT zawiera wszystkie. [Zasady obliczeń](docs/rankings.md).

## Gospodarka z prostym panelem

**Panel → Gospodarka → Zasoby** pokazuje bilans najbliższego miesiąca,
żywność i podpowiedzi. Normalne podatki, obsada pracowników oraz obsługa
luksusów działają automatycznie. Gracz może opcjonalnie ulepszać budynki
do poziomu 3, korzystać z rezerw, zawierać umowy miesięczne i wysyłać osadników.

Docelowa średnia wynosi 2000 mieszkańców na prowincję. Populacja istniejącej
gry pozostaje zachowana; GM dostaje `/economy population` z podglądem zmian.
Szczegółowe zasady, stawki i aktualizacja Rendera:
[Gospodarka v2](docs/economy-v2.md).

Przyciski **Zbudowane budynki** i **Bilans surowców** pokazują wszystkie
budynki wraz z prowincjami oraz miesięczny bilans każdego surowca i złota.
Te same raporty otwierają `/buildings owned` i `/economy income`; można
przeglądać strony lub pobrać całość w TXT. [Opis raportów](docs/economy-reports.md).

**Panel → Przegląd → Stabilność i zadowolenie** oraz `/economy stability`
pokazują bieżące wartości, przyczyny zmiany w punktach, prognozę i ostatnie
rozliczenie. Zadowolenie jest widoczną postacią istniejącego niezadowolenia;
aktualizacja nie zmienia balansu. [Zasady stabilności](docs/stability.md).

## Wolny rynek

**Panel → Gospodarka → Wolny rynek** lub `/market list` pokazuje najtańszą
ofertę każdego surowca od innych państw. Droższe oferty pozostają zapisane.
`/market sell` rezerwuje towar, `/market buy` otwiera potwierdzenie zakupu,
a `/market mine` pozwala wycofać własne oferty. [Zasady i komendy](docs/open-market.md).

## Podział strat w bitwie

Automatyczne bitwy graczy używają neutralnych mnożników taktycznych i jednakowych
wag narażenia. Poniższa ocena AI dotyczy opcjonalnego rozliczenia bitwy przez GM-a.

AI ocenia narażenie każdego oddziału na podstawie obu planów, terenu i jednostek
(waga 0,25–4; wraz z uzasadnieniem). Wynik bitwy ustala łączną pulę strat strony.
Bot rozdziela ją według liczebności i narażenia, nie przekraczając liczby wysłanych
jednostek. Zaokrągla pulę w górę raz dla całej strony, a następnie rozdziela resztę
według największych części ułamkowych. Mały oddział nie traci już automatycznie
jednej jednostki tylko przez osobne zaokrąglenie.

Narracja AI otrzymuje ostateczny podział strat i jego uzasadnienia; raport pokazuje
również dokładne straty każdego oddziału. Niedostępne AI lub niepoprawna ocena
oznaczają neutralną wagę 1 dla danego oddziału. W trybie bez odejmowania strat
podział pozostaje symulacją. Zmiana dotyczy nowych rozstrzygnięć.

## Publikowanie eventów

Przy odpowiedziach awaryjnych widoczny jest przycisk **Załaduj odpowiedzi ponownie**;
nie zużywa decyzji ani zasobów. Typowe ogólne opcje są ukryte, a stare kliknięcia
blokowane. Generowanie używa mniejszego kontekstu historycznego, zachowując pełny
bilans żywności. [Język, ponawianie i ograniczenie kosztów](docs/event-reliability.md).

GM ustawia kanał przez `/event channel channel:#wydarzenia`.
`/event post event_id:12 visibility:public` pokazuje wszystkim narrację.
**Wyszukiwanie obrazków jest wyłączone.** Opcjonalny plik można dodać przez
`file` przy publikacji albo `/event image`. Bez pliku event pojawia się bez
ilustracji. `image_query` nie uruchamia wyszukiwania.

`visibility:private` (domyślnie) udostępnia event właścicielowi i coopom wybranego
państwa oraz GM. Państwo wskazuje się wcześniej przy `/event generate`.
Decyzje w obu trybach trafiają do głównego właściciela przez DM; coopowie oraz
właściciel z zamkniętymi DM używają panelu lub `/event play`.
Publiczna wiadomość nie zawiera opcji ani efektów liczbowych.
Prywatne eventy i ich historia nie są widoczne dla innych graczy.

Ilustracja pojawia się w dużym formacie pod tekstem eventu, a flaga państwa
pozostaje osobną miniaturą. Zapisany obraz jest też widoczny w DM, `/event play`,
kolejnych scenach i podsumowaniu; nie wymaga ponownego wyszukiwania.
Bot zapisuje plik w bazie i wysyła go jako załącznik. `/event image` pozwala
naprawić ilustrację istniejącego eventu lub wgrać własny plik.
[Ilustracje, naprawa postów i wymagane uprawnienia](docs/event-images.md).

Eventy rotują dziesięć tematów; na każde pięć nowych szkiców dla państwa
przypadają dwie szanse, dwa zagrożenia i jedno wydarzenie mieszane.
Decyzje graczy nadal rozstrzygają wynik. [Zasady różnorodności](docs/event-variety.md).

Po wyczerpaniu limitu bot może przejść z Gemini do Groq, Mistral i darmowych
modeli OpenRouter. W Renderze dodaj opcjonalne `GROQ_API_KEY`, `MISTRAL_API_KEY`
i `OPENROUTER_API_KEY`. Bez nowych kluczy pozostają modele zapasowe Gemini.
[Darmowe plany, konfiguracja i zachowanie przy awarii](docs/event-ai-fallback.md).

Plan bitwy bez przypisanych jednostek nadal można zapisać, ale autor oraz GM
przeglądający `/battle plans_pending` otrzymają ostrzeżenie. Wpisanie jednostek
wyłącznie w opisie planu nie przypisuje ich do bitwy.

## Panel gracza

Po wdrożeniu GM uruchamia raz `/panel_publish` na wybranym kanale. Bot publikuje
stały przycisk, z którego każdy gracz otwiera własny prywatny panel. Panel dzieli
działania na gospodarkę, wojsko i technologię, terytorium, dyplomację i bitwy,
wydarzenia oraz ustawienia. Państwa, prowincje, jednostki, oferty i eventy wybiera
się z list; formularze służą jedynie do nazw, liczb i własnych rozkazów.

Alternatywnie gracz może użyć `/panel`. Publiczny przycisk działa także po
restarcie bota. Szczegółowy projekt znajduje się w
[docs/player-panel-design.md](docs/player-panel-design.md).

## Polska wersja interfejsu

Użyj `/translate` bez parametrów, aby zapisać polski język odpowiedzi.
Komendy, formularze, przyciski, zasoby i nowe komunikaty mają polską wersję.
Instrukcje dla Rendera i ustawień języka Discorda:
[Obsługa po polsku](docs/polish-interface.md).

## Interactive events and GM help

GM help now has pages: the old GM tab exceeded Discord's 25-field limit
(26 English / 27 Polish fields). `GM_ROLE_NAME=Game Master` is sufficient for
that role; `GM_ROLE_ID` is optional and does not fix embed-size errors.

New event workflow:

1. GM uses `/event generate`, optionally `/event edit` and `/event effects`.
2. `/event post` starts an interactive event without applying any effects yet.
3. The nation owner uses buttons 1/2/3 or **Custom response**. Each custom answer
   counts as one decision, just like a button. `/event play <id>` restores the
   latest saved state after a missed DM. New buttons also work after a Render restart.
4. After exactly three accepted decisions, the event ends and applies effects
   once. Old buttons/repeated submissions cannot pay out again. GM can inspect
   with `/event play`; the nation owner and co-op members can make choices.

Each decision contributes one third of its assessed effect. AI evaluates signs
independently for each GM-approved axis, within the existing magnitude limits.
Choices display accumulated pending effects. Custom text shapes the story;
it cannot introduce arbitrary rewards. AI failure uses a visible bounded fallback;
generic choices are blocked and can be reloaded without spending a decision.
The stored event language follows the owner's `/language` setting when posted.
Existing posted events are left unchanged and are not paid out again.

Deployment: merge and deploy, then startup `db.init_db()` adds `event_runs`
without deleting existing tables. Run the test suite and try a disposable event
first; verify no balances change on post or decisions 1/2, and exactly one change
after decision 3. The GM/owner can inspect the final result through `/event play`.
New decision buttons use durable IDs instead of expiring after 10 minutes.
Old pre-update messages need one `/event play` refresh to receive the new buttons.
Clicks are acknowledged immediately; assessment and the next scene share one AI
request, with a 24-second shared AI budget including fallbacks. Repeated clicks
cannot duplicate decisions, and stale buttons restore the current stage.
[Details and limits](docs/event-reliability.md).
Resuming after a restart requires retaining the same database (use the configured
PostgreSQL database on the hosted bot, not a disposable test SQLite file).

The [historical economy review](docs/economy-review.md) describes issues in the
previous implementation. The [economy update](docs/economy-v2.md) replaces monthly
settlement with one transaction, protects project rewards, limits production
by available inputs and preserves scheduler backlog. Current regression tests
include `tests/test_economy_v2.py`; no live PostgreSQL or Discord tests were run.
## Battle resolution

`/battle match` now returns the real PostgreSQL battle ID. `/battle resolve`
without an ID lists pending battles, including records created by older versions
that displayed `Battle #None`. Resolve one with `/battle resolve <id>`.

When resolving with an ID, the GM also supplies the final battlefield. If it is\nomitted, Discord opens a location form. A province name or Azgaar cell ID loads\nits terrain, biome and fortification; descriptive locations remain available for\nsea and off-map battles. Gemini receives the exact committed units, both plans\nand battlefield data. The saved/public report explains the opening engagement,\nturning point and outcome, and `/battle view` shows the same narrative later.\n\nResolution uses `GEMINI_MODEL` and falls back to neutral modifiers when AI is
unavailable. AI modifiers are constrained to 0.7–1.4; optional GM overrides must
be 0.1–3.0 (use 0/blank for AI). The battle row, both plan statuses, logs and
optional casualties commit atomically. Repeated or concurrent resolution cannot
apply losses twice. Casualties affect only quantities committed in the two plans,
not every military unit owned by the nations. The result is saved before Discord
announcement; a channel delivery failure does not undo the completed battle.

After merging, deploy/restart Render so Discord command parameters synchronize.
Test first with `apply_casualties: false`, then inspect `/battle view <id>`.
Offline tests do not call production PostgreSQL, Discord or Gemini.

## GM access and regression checks

`/battle plans_pending` accepts PostgreSQL timestamps and paginates large queues.
New AI events use the nation owner's saved `/language` preference (`pl` or `en`),
falling back to `DEFAULT_LANGUAGE` when no preference is saved. The invoking GM's
language does not control the narrative. Public/DM labels follow the owner;
`/event list` labels follow its viewer. Existing event text and GM edits are
preserved, not automatically translated. AI output language is requested in the
prompt; the GM should still review the draft before posting.

The `/help` GM tab and GM commands use the same role check. Set `GM_ROLE_ID`
to the Discord role ID (recommended; it survives role renaming). If unset,
`GM_ROLE_NAME` defaults to `Game Master` and ignores outer whitespace and case.
For a role named `GM`, set `GM_ROLE_NAME=GM`. An explicit ID overrides the name.
Restart the bot after changing environment variables. Server Administrator
permission also grants GM access.

Run offline regression tests after installing `requirements.txt`:

```sh
python -m unittest discover -s tests -v
```

Tests use temporary SQLite databases and mocked Discord interactions, never a
live bot or production game database. PostgreSQL settlement uses row locks and
`CURRENT_TIMESTAMP`; live PostgreSQL verification remains a deployment check.

What's here so far: bot connects, syncs slash commands, and has `/help` + `/language`
working end-to-end with the English/Polish localization system. Everything later
(nations, provinces, economy, combat) builds on this same pattern - a slash command in
`bot.py` (or a new cog file once we have more), a table in `db.py`'s `SCHEMA`, strings in
`i18n/strings_en.json` / `strings_pl.json`.

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Create a Discord application + bot at https://discord.com/developers/applications,
   enable it, copy the bot token.
3. Get a free Gemini API key at https://aistudio.google.com/apikey (not used by any
   command yet, but config.py requires it now so later steps don't need new setup).
4. On Replit: put `DISCORD_TOKEN` and `GEMINI_API_KEY` (and optionally `GM_ROLE_NAME`,
   `DB_PATH`, `DEFAULT_LANGUAGE`) in the **Secrets** tab.
   Locally: copy `.env.example` to `.env` and fill it in.
5. Invite the bot to your server with the `applications.commands` and `bot` scopes.
6. Run it:
   ```
   python bot.py
   ```
7. In Discord, try `/help` and `/language`.

## What's next
Step 2 will add the nations/provinces/economy tables to `db.py` and the first real
gameplay commands (`/nation found`, `/nation stats`).

## Badania i algae

Panel → Technologie oraz `/tech status` prowadzą przez nazwane odkrycia.
Każde państwo otrzymuje 1 darmową wiedzę na miesiąc gry; uniwersytety przyspieszają
badania. Jeden projekt naraz, automatyczny postęp, istniejące poziomy zachowane.

`/algae locations` pokazuje do pięciu złóż wskazanych przez GM komendami
`/algae deposit_add` i `/algae deposit_remove`. Algae z wydobycia lub handlu zasila silne programy gospodarki, armii, marynarki i kolonii.
`/algae programs` pozwala włączyć programy za 1 algae miesięcznie każdy.
Megaprojekty mają nazwę „projekty” i komendy `/project`; zapisane projekty pozostają.

[Pełne zasady, premie i migracja](docs/technology-algae.md). Dotychczasowe farmy
poza stanowiskami są nieaktywne i nie ponoszą kosztów utrzymania.


`/algae production` i **Technologie → Wydobycie algae** obsługują budowę farm
na własnych złożach i pokazują prognozę. Farma poziomu 1 jest dostępna od
gospodarki 3. Automatyczna bazowa produkcja co miesiąc gry: gospodarka 3 → 0,05;
4 → 0,1; 5 → 0,2; 6+ → 0,5 algae. Od gospodarki 6 można ulepszyć budynek:
poziom 2 → 0,85; poziom 3 → 1,2. Obsada, stabilność i etap kolonii wpływają na wynik.

W panelu wojska są kosztowne elitarne
gwardie, jeźdźcy i fregaty algae; rekrutacja wymaga 4 zwykłych jednostek na
każdą elitarną. `/nation stats` pokazuje umowny odpowiednik roku technologicznego.

`/economy workers` i **Gospodarka → Pracownicy** pozwalają opcjonalnie
rezerwować ludzi w konkretnych budynkach. Domyślnie obsada pozostaje automatyczna.


### Mapa Azgaara w obie strony

GM może importować i eksportować mapę wraz z państwami, kulturami oraz religiami.
Pierwszy import: `/admin map_import file:pełny.json map_file:projekt.map`
(JSON z **Export → JSON → Full Data**, projekt z **Save → Machine**).
Następnie `/admin map_export` tworzy `.map` otwierany przez **Load → Machine**.
Dostępne także: `/admin map_resync`, `/admin map_entities`, `/admin map_bind`
oraz `/province identity`; panel GM ma kategorię **Mapa Azgaara**.
Obecne granice gry są domyślnie zachowane; zastąpienie ich wymaga `sync_owners:true`
i potwierdzenia. [Pełna instrukcja i ograniczenia](docs/azgaar-map-exchange.md).
