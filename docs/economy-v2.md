# Gospodarka: prosty start, decyzje przy rozwoju

Gracz otwiera **Panel → Gospodarka → Zasoby**. Widzi przewidywany bilans
najbliższego miesiąca, obecny skarbiec, żywność oraz podpowiedź, co poprawić.
Podgląd nie zmienia bazy gry. Alternatywy to `/resources` i `/economy status`.
Prognoza zakłada, że do rozliczenia nie pojawią się nowe działania lub eventy.

Domyślnie obowiązują normalne podatki, automatyczna obsada budynków oraz
automatyczne wykorzystanie luksusów. Nie trzeba ustawiać suwaków ani przydzielać
pracowników. W panelu można budować i ulepszać budynki, ustawić rezerwę,
zaproponować umowę miesięczną lub wysłać osadników.

## Populacja i budynki

Punkt odniesienia to **średnio 2000 mieszkańców na prowincję**. Daje to 800
pracowników, czyli około 3–4 zwykłych budynków z pełną obsadą na poziomie 1.
Pozostałe 60% ludności nie wymaga osobnej obsługi. Każda prowincja może mieć
po jednym budynku każdego rodzaju; ograniczeniem produkcji są pracownicy.

| Rola prowincji | Orientacyjna populacja |
|---|---:|
| Mała / peryferyjna | 500–1200 |
| Zwykła | 1500–2500 |
| Miejska | 3000–4500 |
| Duża stolica | 5000–7000 |

To zakresy do projektowania mapy, nie dodatkowe wymagania gry. Nowy import
Azgaara skaluje ląd do średniej 2000. Istniejąca gra zachowuje populację.
GM może użyć `/economy population nation:…`, obejrzeć plik ze zmianami i dopiero
przyciskiem zastosować skalowanie dla państwa. Pomijane są nieukończone kolonie.
Stolica dostaje większą wagę, ale końcowa średnia nadal wynosi około 2000.

| Poziom budynku | Produkcja | Pracownicy | Koszt danego ulepszenia |
|---|---:|---:|---:|
| 1 | 100% | 100% | zwykły koszt budowy |
| 2 | 170% | 150% | 150% kosztu podstawowego |
| 3 | 240% | 200% | 200% kosztu podstawowego |

Przykładowa obsada poziomu 1: farma 200, kopalnia 300, rynek 150,
port 200, uniwersytet 400, spichlerz 50. Fort nie zabiera pracowników.
Najpierw obsadzane są budynki żywnościowe. Opcjonalny priorytet przemysłu,
handlu lub nauki rozstrzyga pozostałą kolejność. „Żywność” i ustawienie
zrównoważone korzystają z tego samego bezpiecznego pierwszeństwa żywności.
Niepełna obsada proporcjonalnie zmniejsza produkcję i zużycie surowców.
Nieczynny budynek nadal kosztuje 25% utrzymania swojego poziomu.

## Podatki i żywność

| Podatki | Złoto / 1000 mieszkańców / miesiąc | Zmiana niezadowolenia | Bazowy wzrost populacji przy nadwyżce żywności |
|---|---:|---:|---:|
| Niskie | 8 | −3 | 0,3% |
| Normalne — domyślne | 12 | −1 | 0,2% |
| Wysokie | 16 | +3 | 0,05% |

Niezadowolenie mieści się w 0–100 i ogranicza podatki maksymalnie o połowę.
Wysokie podatki obniżają stabilność o niezadowolenie/100 miesięcznie;
niskie dodają 0,25. Stabilność mnoży podatki i produkcję przez
`0,75 + stabilność/400`. Kolonie mają dodatkowy mnożnik swojego etapu.
W pełni obsadzony rynek poziomu 1 zwiększa podatki własnej prowincji o 15%;
wyższe poziomy skalują premię tak jak produkcję.

Podstawowe miesięczne potrzeby to `populacja/100 + liczba jednostek/10`,
także dla rezerw. Farma daje 20 żywności, przystań rybacka 15,
pastwisko 8 i 1 konia, plantacja 6 żywności i 3 przyprawy — przed mnożnikami.
Do wzrostu ludności potrzeba żywności na co najmniej 120% bieżących potrzeb.

Pierwszy miesiąc głodu daje ostrzeżenie, drugi może obniżyć stabilność do 8,
a trzeci i kolejne mogą zmniejszać populację do 1%, gdy wyżywienie spada
poniżej 50%. Uzupełnienie zapasów zeruje serię głodu. Psuje się 2% żywności
ponad trzymiesięczne potrzeby; pełna obsada spichlerza ogranicza to do 0,5%.
Nie ma ogólnego psucia wszystkich surowców ponad arbitralny zapas 500.

## Przemysł, luksusy i handel

Młyn prochowy wytwarza 4 prochu z 2 węgla i 1 miedzi. Warsztat jedwabiu
przetwarza 2 sukna na 2 jedwabiu. Ludwisarnia nadal zużywa żelazo zgodnie
z definicją budynku. Najpierw rozliczane są budynki wydobywcze; zakłady
mogą wykorzystać surowiec wydobyty w tym samym miesiącu. Brak połowy
wymaganego surowca pozwala na połowę produkcji, a brak całości wstrzymuje zakład.
Magazyn nie schodzi poniżej zera przez brak materiałów.

Luksusy dają korzyści dopiero przy faktycznym zużyciu lub sprzedaży.
Domyślne „automatycznie” konsumuje do 1 jedwabiu i 1 przypraw na 1000 ludzi,
zachowuje zapas na trzy kolejne miesiące i sprzedaje dostępne nadwyżki.
Konsumpcja poprawia stabilność do 1/miesiąc oraz może dodać 0,1 punktu
procentowego wzrostu populacji przy zapewnionej żywności.
Można wybrać wyłącznie magazynowanie, konsumpcję albo sprzedaż.

Rynek sprzedaje do `populacja prowincji/1000` jednostek luksusów miesięcznie
przy pełnej obsadzie. Port i przypisany szlak dodają ładowność okrętów
pomnożoną przez obsadę portu. Jedwab sprzedaje się po 4 złota, przyprawy po 3;
wspólny limit najpierw wykorzystuje jedwab. Sprzedany towar znika z magazynu.
Szlak bez przypisanego transportu nie daje premii. Ten sam oddział okrętów
może obsługiwać jeden szlak; nie można jednocześnie przypisać go do bitwy.
Szlaki tworzy `/traderoute add`, usuwa `/traderoute remove`.

Umowa gracz–gracz jest osobną możliwością. Najpierw powstaje zwykła oferta
`/trade offer`, potem autor oznacza ją jako miesięczną w panelu lub przez
`/economy recurring`. Odbiorca widzi obie strony wymiany i akceptuje
**teraz i co miesiąc**. Pierwsza dostawa następuje przy akceptacji, kolejne
na początku miesięcy, przed produkcją. Brak zasobów u którejkolwiek strony
wstrzymuje całą dostawę w danym miesiącu; nie powstaje dług za pominięte dostawy.
Obie strony mogą przerwać umowę przyciskiem lub `/economy contract_stop`.

## Wojsko i kolonie

Wojsko istniejące oraz nowo rekrutowane jest domyślnie aktywne (100% kosztu).
Rezerwa kosztuje 35%, wyprawa 150%. Powrót z rezerwy wymaga jednego miesiąca
mobilizacji opłacanej jak aktywne wojsko. Gotowość wraca przy następnym
rozliczeniu; zgłoszenie gotowej jednostki do planu bitwy ustawia wyprawę.
Po bitwie gracz może zmienić tryb w panelu. Samo wypowiedzenie wojny
nie zmienia trybu całej armii.

Niezapłacone utrzymanie tworzy zaległości i stopniowo obniża siłę bojową
(10% za miesiąc zaległości, do 50%). Przez pierwsze dwa kolejne miesiące
braku pieniędzy nie ma dezercji. Od trzeciego ubywa 2% liczebności armii
na miesiąc. Pula strat jest zaokrąglana raz i rozdzielana na grupy.
Spłata zaległości zeruje serię niepłacenia.

Założenie kolonii kosztuje 500 złota, wymaga 5 wolnej ładowności i przenosi
300 osadników z własnej prowincji mającej co najmniej 800 mieszkańców.
Rozrost na sąsiednie pole przenosi 300 osób z prowincji źródłowej i kosztuje
500 złota. Ludność rdzenna nowego pola pozostaje. Dodatkowy transport
300 osadników do własnej kolonii kosztuje 50 złota i 3 żywności.

| Awans do | Miesiące na obecnym etapie | Inwestycja | Populacja docelowa | Technologia kolonialna |
|---|---:|---:|---:|---:|
| Osady | 6 | 300 złota | 300 | 3 |
| Kolonii | 12 | 800 złota | 800 | 5 |
| Prowincji | 24 | 2000 złota | 1500 | 0 |

Awans następuje automatycznie, gdy wszystkie wymagania są spełnione.
Trwający co najmniej dwa miesiące głód blokuje awans. Dotychczasowe
inwestycje i czas kolonii pozostają zachowane.

## Aktualizacja istniejącej gry

1. Po połączeniu zmian wdrożenie na Renderze i restart bota dodają nowe
   tabele oraz synchronizują komendy. Nie są potrzebne nowe zmienne środowiskowe.
2. Migracja nie zmienia automatycznie populacji, sald ani zapisanych inwestycji.
   Budynki zaczynają na poziomie 1, armie w trybie aktywnym; ustawienia są domyślne.
3. Domyślne efekty starych budynków są aktualizowane jednorazowo tylko wtedy,
   gdy nadal odpowiadają poprzednim wartościom standardowym. Własne efekty,
   ceny, nazwy i opisy GM-a są zachowywane. Stare szlaki bez przypisanego
   statku trzeba usunąć i utworzyć z przypisaniem transportu.
4. Nowe zasady obowiązują przy następnym miesiącu. GM może wcześniej użyć
   panelu i ewentualnie przeskalować populację przez podgląd. Ręczny ponowny
   import mapy pozostaje osobnym działaniem administracyjnym; dla istniejących
   pól zachowuje ludność, a nowym nadaje populację z przeskalowanego importu.

Kalendarz, salda, kolonizacja, kontrakty i dziennik miesiąca zapisują się
w jednej transakcji. Awaria wycofuje cały miesiąc. Scheduler nadrabia najwyżej
trzy miesiące w jednym przebiegu, zachowując pozostałe zaległości czasu.
Kalendarz nie pozwala drugi raz rozliczyć miesiąca obecnego w dzienniku;
nie należy cofać daty gry w celu ponownego wypłacenia dochodów.

Testy offline obejmują awarię i wycofanie miesiąca, równoczesne uruchomienia,
zgodność prognozy z rozliczeniem, migrację, limity surowców, koszty ulepszeń,
umowy, rezerwę i wyłączność statków. PostgreSQL, Discord i Render wymagają
sprawdzenia integracyjnego przy wdrożeniu; testy używają tymczasowej bazy SQLite.


## Ręczne przydziały pracowników

**Gospodarka → Pracownicy**, przycisk w bilansie i `/economy workers [cell_id]`
otwierają ten sam prywatny panel. Wybierz prowincję, następnie budynek i liczbę
pracowników. Listy mają strony; dostępne są wszystkie własne aktywne prowincje.

- Liczba to rezerwacja pracowników, nie procent: 200 oznacza 200 ludzi.
- 0 wyłącza obsadę; puste pole przywraca automat dla budynku.
- Przycisk „Automat w tej prowincji” usuwa wszystkie jej ręczne przydziały.
- Ręczne rezerwacje mają pierwszeństwo. Pozostali pracownicy trafiają do
  budynków automatycznie: najpierw żywność, potem wybrany priorytet gospodarki.
- Można zarezerwować najwyżej 40% mieszkańców prowincji i nie więcej, niż
  dany budynek potrzebuje na swoim poziomie. Każdy pracownik pracuje tylko raz.
- Gdy populacja później spadnie, ręczne rezerwacje są proporcjonalnie
  zmniejszane. Zapisane liczby pozostają, więc obsada odbuduje się wraz z ludnością.
- Nieaktywna farma algae nie zabiera pracowników innym budynkom.
- Zmiana właściciela prowincji na inne państwo przywraca mu automat, zamiast
  stosować rezerwacje poprzedniego państwa. Przydziały państwa pozostają po
  przekazaniu całego państwa nowemu graczowi.

Podgląd pokazuje rzeczywistą prognozowaną obsadę i ostrzega o brakach jedzenia
oraz ograniczeniu przydziałów po spadku populacji. Ręczne odebranie pracowników
farmie może spowodować głód. Poza zarezerwowanymi budynkami prosty automat
pozostaje domyślnym zachowaniem. Prognoza i miesięczne rozliczenie używają tej
samej funkcji obliczeniowej; podgląd wycofuje wszystkie zmiany w bazie.
