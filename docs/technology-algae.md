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

Po pierwszym uruchomieniu na istniejącej mapie albo po pierwszym imporcie
wybierane jest `min(5, ceil(liczba aktywnych pól lądowych / 200))` stanowisk,
minimum jedno. Preferowane są działające wcześniej farmy, potem mokradła
i pola z gliną. Wybór jest deterministyczny i preferuje niesąsiadujące pola.
Na mapie bez mokradeł możliwe są inne tereny: o uprawnieniu do wydobycia decyduje
stanowisko. **Powtórny import, zmiana właściciela i restart nie losują złóż od
nowa.** Nieaktywne pola znikają z listy; nie tworzymy zastępczych złóż. Dane
stanowisk należą do tej samej bazy co świat, a nie do pliku na Renderze.

Farma algae wymaga posiadania stanowiska i gospodarki co najmniej 6.
Domyślnie kosztuje **400 złota + 60 drewna**, potrzebuje **250 pracowników**
i **8 złota miesięcznego utrzymania** przy pełnej obsadzie. W prowincji można
mieć jeden taki budynek. Produkcja przed stabilnością, zatrudnieniem i etapem
kolonii: poziom 1 — **0,5**, poziom 2 — **0,85**, poziom 3 — **1,2 algae/miesiąc**.
Premie przemysłowe nie mnożą algae. Samo posiadanie stanowiska nie daje surowca.
Dostępne pozostają wymiany jednorazowe i istniejące kontrakty miesięczne.

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
bez gospodarki 6 pozostają w prowincjach, ale nie produkują i nie pobierają
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
