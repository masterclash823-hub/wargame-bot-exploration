# Badania i algae

## Panel i komendy

**Panel → Technologie → Badania** i `/tech status` otwierają ten sam prywatny
panel. Gracz widzi trzy rekomendacje, postęp projektu, ostatnie odkrycia i
obowiązujące premie. **Wszystkie badania** pokazują również projekty zablokowane
wymaganym poziomem, z opisem kosztów i korzyści. `/tech research [project]`
obsługuje podpowiedzi nazw i otwiera potwierdzenie, bez natychmiastowego wydawania
zasobów. Komendy pozostają angielskie; interfejs, poradnik i powiadomienia są PL/EN.

Jeden projekt na państwo. Przycisk rozpoczęcia ponownie sprawdza właściciela,
poziom, wcześniejsze odkrycia i dostępne algae. Wstrzymanie zachowuje postęp;
porzucenie wymaga potwierdzenia i nie zwraca zużytej wiedzy ani algae. Stare
przyciski nie mogą porzucić nowego projektu rozpoczętego pod tą samą nazwą.

## Postęp w miesiącach gry

Każde państwo dostaje **1 darmowy punkt `universal_knowledge` co miesiąc gry**,
również bez uniwersytetu lub aktywnego projektu. Uniwersytety produkują dodatkową
wiedzę, z uwzględnieniem zatrudnienia, poziomu budynku, stabilności i kolonii.
Wiedza z magazynu i bieżącej produkcji trafia do aktywnego projektu aż do
pokrycia kosztu. Duży magazyn nie skraca minimalnego czasu.

| Rodzaj | Wiedza | Minimum miesięcy pracy | Wzrost poziomu | Wymagany poziom |
| --- | ---: | ---: | ---: | ---: |
| Podstawowy | 3 | 3 | +0,5 | 0 |
| Rozwinięty | 8 | 4 | +1 | 4 |
| Zaawansowany | 16 | 6 | +1 | 5 |
| Odkrycie algae | 16 oraz 4 algae na początku | 6 | +1 | 6 |

Badania nie kosztują złota. Każda dziedzina ma cztery zwykłe odkrycia, jedno
odkrycie algae i powtarzalne „Dalsze badania”. Te ostatnie zwiększają poziom do
10, bez powielania premii. Łącznie katalog zawiera 24 pozycje. Projekty z premiami
można ukończyć również przy poziomie 10, jeśli nie były wcześniej odkryte.

Odkrycia gospodarcze: trójpolówka (+10% żywności z farm), przechowywanie żywności
(−25% psucia nadwyżek), rachunkowość (+5% dochodów podatkowych), metoda naukowa
(+25% produkcji wiedzy z uniwersytetów). Podatki i nastroje pozostają odrębnymi
mechanikami; premia rachunkowości nie zmienia stawki podatkowej.

Wojska lądowe: tabory (−5% utrzymania w złocie), musztra (+5% ataku), inżynieria
(+10% obrony), sztab (+5% ataku i obrony). Marynarka: ładownie (+10% cargo),
logistyka (−5% utrzymania), szkutnictwo (+10% obrony), nawigacja (+10% cargo,
+5% ataku). Kolonie: administracja i urząd (po −10% złota na awans), mierniczy
i medycyna (po −10% czasu awansu).

Zwykłe premie produkcji i utrzymania zaczynają działać w kolejnym rozliczeniu.
Poziom i premie do bitew stają się dostępne po zakończeniu badania; awans kolonii
rozpatrywany jest po badaniach w tym samym miesiącu. Prognoza gospodarki korzysta
z tej samej transakcji co rozliczenie, po czym wycofuje wszystkie zmiany.
Zaległe miesiące działają przez istniejący kalendarz; usunięto rzeczywisty
24-godzinny dryft technologii. Nie ma dodatkowego postępu za restart procesu.

## Rzadkie stanowiska

`/algae locations` oraz przycisk **Stanowiska algae** pokazują ID komórek,
nazwy, teren i właścicieli wszystkich aktywnych stanowisk. Dostęp nie wymaga
własnego państwa. Znacznik pojawia się też w `/province info` oraz eksporcie
markerów do Azgaara.

Złoża ustawia wyłącznie GM przez `/algae deposit_add cell_id` i usuwa przez
`/algae deposit_remove cell_id`. Nie powstają automatycznie podczas importu
ani restartu. Limit wynosi **5 złóż**, również z nieaktywnych pól; ich ID
pozostają na liście, żeby GM mógł zwolnić limit. Dodawanie wymaga aktywnego
pola lądowego. Dotychczas zapisane złoża są zachowane do ręcznej zmiany przez GM.
Usunięcie złoża zatrzymuje wydobycie, ale zachowuje budynek oraz zapasy graczy.
Powtórny import i restart nie odtwarzają usuniętych złóż.

## Automatyczne wydobycie od gospodarki 3

**Technologie → Wydobycie algae** i `/algae production` pokazują prognozę,
własne złoża oraz przyciski budowy i ulepszania farm. Gracz raz buduje farmę,
a potem surowiec trafia do magazynu przy każdym miesięcznym rozliczeniu.
Ręczne zbieranie `/algae gather` zostało zastąpione tym mechanizmem.

Farma poziomu 1 wymaga własnego aktywnego złoża i **gospodarki 3**.
Domyślny koszt pozostaje **400 złota + 60 drewna**, potrzeba **250 pracowników**,
a utrzymanie przy pełnej obsadzie to **8 złota miesięcznie**.

| Technologia gospodarcza | Bazowa produkcja farmy poziomu 1 / miesiąc |
| --- | ---: |
| poniżej 3 | 0 — farma nieaktywna |
| od 3 do poniżej 4 | 0,05 algae |
| od 4 do poniżej 5 | 0,1 algae |
| od 5 do poniżej 6 | 0,2 algae |
| 6 i więcej | 0,5 algae |

Są to **poziomy technologii gospodarczej**, nie poziomy budynku. Poziomy
ułamkowe nie przyspieszają produkcji przed przekroczeniem następnego progu.
Próg osiągnięty po zakończeniu badania działa od kolejnego rozliczenia produkcji.

Ulepszenia do poziomów budynku 2 i 3 wymagają gospodarki **6**. Ich dotychczasowa
bazowa produkcja pozostaje: **0,85 i 1,2 algae miesięcznie**. Koszty ulepszeń,
pracownicy i utrzymanie pozostają takie jak dla innych budynków. Jeśli GM
obniży technologię poniżej 6, ulepszona farma tymczasowo działa jak poziom 1;
zapisane ulepszenia wracają do działania po odzyskaniu gospodarki 6.

Rzeczywistą produkcję mnożą obsada, stabilność i etap kolonii. Ręczne przydziały
pracowników działają również dla farm algae. Premie przemysłowe nie zwiększają
algae. Samo złoże bez farmy nie produkuje surowca; bez własnego złoża nie można
ani zbudować farmy, ani prowadzić śladowego wydobycia.

Nie ma dodatkowego kliknięcia ani opłaty za pojedynczą partię, ani limitu
jednej partii na państwo. Każda własna obsadzona farma na złożu produkuje
raz na miesiąc gry. Prognoza nie zapisuje produkcji; restart nie daje
bonusowej wypłaty. Dotychczasowe zapasy, w tym pozyskane wcześniej ręcznie,
pozostają. Stara tabela limitów ręcznego zbierania nie jest już używana.

Migracja obniża stary domyślny wymóg farm z 6 do 3. Zachowuje własne ceny,
ustawienia GM oraz zapisane budynki. Starsze farmy na złożach zaczynają działać
od gospodarki 3 bez potrzeby ponownego budowania. Niestandardowy wyższy wymóg
budowy ustawiony przez GM pozostaje jego ustawieniem.

## Cztery programy algae

Ukończenie odkrycia odblokowuje program. Gracz sam włącza go w **Programach
algae** lub `/algae programs`. Każdy włączony program zużywa **1 algae na
początku miesiąca**. Dostawy zaakceptowanych umów są rozliczane wcześniej;
algae wyprodukowane w tym miesiącu może opłacić następny. Ustawienia działają
od następnego miesiąca. Opłacona premia pozostaje do następnego rozliczenia,
więc przełączanie przycisków nie odnawia ani nie zwraca zużytego surowca.

| Program | Premie podczas finansowania |
| --- | --- |
| Biokataliza | +25% żywności z farm i +15% dodatniej produkcji budynków poza wiedzą i algae |
| Medycyna wojskowa | +20% ataku i obrony wojsk lądowych |
| Żywe poszycie | +20% ataku i obrony okrętów, +25% cargo |
| Odporni osadnicy | −30% złota na awans kolonii, −25% czasu awansu |

Premie do tego samego parametru sumują się. Biokataliza daje więc farmom +40%
łącznie, lub +50% razem z trójpolówką. Łączne obniżenie wymagań awansu kolonii
jest ograniczone do 50%; złoto i czas są zaokrąglane w górę. Wpłacone inwestycje
zostają zapisane także po ustaniu premii. Interfejs, wpłaty i automatyczne
awansowanie korzystają z tych samych wymagań.

Przy niedoborze algae kolejność finansowania to gospodarka, wojska lądowe,
marynarka, kolonie. Niefinansowane programy tracą premie, ale zachowują odkrycia
oraz ustawienie włączenia i automatycznie wracają po wznowieniu dostaw.
Prognoza gospodarki ostrzega o braku surowca. Można wyłączyć dowolne programy,
by przeznaczyć zapas na inną dziedzinę, badania, handel albo istniejące
zaczarowane wyposażenie okrętów.

Bitwy obliczają premie osobno dla wojsk lądowych i okrętów, z właściwym poziomem
technologii. Okręty używają poziomu marynarki. AI otrzymuje obliczony wynik i
premie do opisu bitwy, bez uprawnienia do tworzenia dodatkowych korzyści.
Premie cargo są uwzględniane w handlu, sprawdzeniu miejsca dla osadników
i zestawieniu floty; nie zmieniają zapisanych projektów jednostek.

## Powiadomienia i ciągłość zapisów

Po ukończeniu badania powstaje prywatny wpis historii i odkrycie w panelu.
Bot próbuje wysłać DM w języku aktualnego właściciela, ze zwięzłym opisem,
efektami i wskazaniem kolejnego kroku. Nie ponawia automatycznie niepewnej
wysyłki, aby uniknąć duplikatów; zablokowane DM nie wstrzymują badań ani zapisu.
Kronika może pokazać fakt przełomu, bez nazwy odkrycia, poziomu ani premii.
Cel „Postęp naukowy” wymaga ukończenia badania po wyborze celu i minimum
trzech miesięcy. Samo zwiększenie poziomu przez GM go nie zalicza.

Migracja zachowuje państwa, właścicieli, poziomy technologii, odblokowania,
magazyny (również algae i wiedzę), jednostki, budynki i istniejące projekty.
Nowe odkrycia zaczynają się od pustego katalogu osiągnięć. Nowe tabele dodaje
`db.init_db`; nie potrzeba ręcznego SQL ani nowych zmiennych środowiskowych.

**Zmiana dla istniejącego wydobycia:** farmy poza wybranymi stanowiskami lub
bez gospodarki 3 pozostają w prowincjach, ale nie produkują i nie pobierają
pracowników ani utrzymania. Domyślna produkcja starych farm zmienia się z 1 na
0,5; własne ceny i inne ustawienia GM są zachowane. Dodatnia produkcja algae
z bazowych zasobów, innych budynków i cyklicznych efektów projektów jest
wyłączona. GM nie może zatwierdzić nowego projektu dającego algae co miesiąc.
Jednorazowe nagrody GM i eventów nadal są możliwe. Pozwala to prowadzącemu
przyznać specjalną nagrodę bez tworzenia dodatkowego stałego źródła na mapie.

Dawne megaprojekty nazywają się teraz **projektami**: `/project propose`,
`/project build`, `/project list`. GM używa `/admineco project_approve` oraz
`/admineco project_advance`, z parametrem `project_id`. Stara tabela
`megaprojects` pozostaje wewnętrznym formatem zapisu, dzięki czemu historia,
zatwierdzenia, postęp i wypłaty istniejących projektów nie są przenoszone.

Po połączeniu PR należy wdrożyć nową wersję na Renderze. Restart synchronizuje
komendy Discorda. Testy automatyczne używają tymczasowych baz SQLite i atrap
Discorda; nie wysyłają wiadomości do graczy ani nie zmieniają produkcyjnej bazy.

## Rok technologiczny i jednostki elitarne

`/nation stats` oraz panel badań pokazują orientacyjny odpowiednik roku IRL.
To umowna skala świata gry, a nie datowanie wynalezienia technologii ani data
kalendarza. Każda z czterech dziedzin ma osobny szacunek na podstawie poziomu
i ukończonych badań, a rok państwa to ich średnia zaokrąglona do 25 lat.
Skala poziomów 0–10: 1400, 1450, 1500, 1550, 1600, 1650, 1700, 1725,
1750, 1775, 1800. Poziomy ułamkowe interpolujemy. Zwykłe odkrycia ustanawiają
umowne minima 1550/1650/1700, dalsze badania 1700 + 25 lat za ukończenie
(do 1800). Algae nie ma własnej historycznej daty. Wskaźnik niczego nie
odblokowuje i nie zwiększa premii. Stare poziomy działają także bez historii badań.

Nowe jednostki są dostępne w **Wojsko → Nowy projekt** i przez istniejące
`/blueprint create_unit` / `/blueprint design_ship`. Wymagają poziomu 6
odpowiedniej dziedziny oraz ukończenia odkrycia `algae_land` albo `algae_naval`.

| Jednostka | Koszt bazowy za sztukę | Utrzymanie aktywne / miesiąc |
| --- | --- | ---: |
| Gwardia algae | 350 złota, 20 żelaza, 10 prochu, 3 algae | 18 złota |
| Jeźdźcy algae | 500 złota, 12 koni, 25 żelaza, 4 algae | 25 złota |
| Fregata algae | 1800 złota, 300 drewna, 120 żelaza, 30 smoły, 6 algae | 45 złota |

Do okrętu dochodzą koszty wybranych modułów; do partii jednostek lądowych
1 sukno na 5 jednostek, zaokrąglane w górę. Algae na jednostkę to koszt
jednorazowy; program miesięczny jest osobną decyzją. Jednostki mają własne
statystyki, a opłacone programy mogą je dodatkowo wzmacniać.

**Limit rekrutacji: jedna elitarna na cztery zwykłe jednostki**, osobno dla
armii i floty. Wszystkie elitarne typy i projekty danej kategorii współdzielą
limit. Liczą się sztuki, a nie liczba grup. Rezerwy też należą do sił państwa.
Nie można obejść limitu przez zmianę nazwy projektu, równoległe kliknięcia,
rozwiązanie potrzebnych zwykłych jednostek ani usunięcie używanego projektu.
Straty wojenne mogą zmienić proporcję: ocalała elita pozostaje, lecz kolejną
można rekrutować dopiero po uzupełnieniu wsparcia. Limit nie wymaga wysyłania
zwykłych jednostek do każdej bitwy razem z elitą.

Ręczne przydziały pracowników opisuje [gospodarka](economy-v2.md#ręczne-przydziały-pracowników).
