# Terraformacja i populacja wolnych prowincji

`/province terraform [cell_id]` oraz **Panel → Terytorium → Terraformacja**
otwierają prywatną listę własnych prowincji. Wybór pokazuje obecny biom,
docelowy biom, zmianę zasobów bazowych, koszty, wymagania i termin ukończenia.
Samo przeglądanie nie kosztuje nic. Dopiero **Zapłać i rozpocznij** pobiera
złoto i materiały. Menu pokazuje też trwający projekt oraz ostatnie wyniki.

| Projekt | Zmiana biomu | Złoto | Materiały | Miesiące gry | Technologia gospodarcza |
| --- | --- | ---: | --- | ---: | ---: |
| Zalesianie | Temperate Grassland → Temperate Deciduous Forest | 800 | 100 drewna, 50 kamienia | 6 | 4 |
| Wycinka lasu | Temperate Deciduous Forest / Temperate Rainforest → Temperate Grassland | 800 | 50 drewna, 50 kamienia, 50 żelaza | 6 | 4 |
| Wycinka lasu tropikalnego | Tropical Rainforest / Tropical Seasonal Forest → Savanna | 1000 | 50 drewna, 75 kamienia, 75 żelaza | 9 | 5 |
| Wycinka tajgi | Taiga → Tundra | 800 | 50 drewna, 50 kamienia, 50 żelaza | 6 | 4 |
| Osuszanie mokradeł | Wetland → Temperate Grassland | 1200 | 150 drewna, 150 kamienia, 50 żelaza | 9 | 5 |
| Nawadnianie pustyni | Desert / Hot Desert → Savanna | 1600 | 100 drewna, 200 kamienia, 75 żelaza | 12 | 5 |
| Zalesianie tundry | Tundra → Taiga | 1800 | 150 drewna, 150 kamienia, 100 żelaza | 12 | 6 |

Koszt przebudowy jest wyższy od zakupu sąsiedniej prowincji (300–500 złota),
a zwrot rozciąga się na wiele miesięcy. Celem jest specjalizacja istniejącego
terytorium. Zmiana nie daje mieszkańców, magazynowanych surowców ani nowych
rzadkich złóż. Zastępuje bazową produkcję biomu; zachowuje dodatkowe złoża.

Wymagane: aktywna własna prowincja lądowa, co najmniej 1000 mieszkańców,
stabilność państwa co najmniej 40 i technologia z tabeli. Kolonia musi być
rozwinięta do pełnej prowincji. Nie można zmieniać wody, lodowców, gór ani
wysokości i granic pól. Mapa musi zawierać docelowy biom w swoim katalogu.
Istniejące budynki muszą pasować do nowego biomu; podczas prac nie można
budować ani ulepszać niezgodnych budynków, także przez firmy.

Państwo prowadzi najwyżej jeden projekt naraz. Po ukończeniu ta sama prowincja
ma 12 miesięcy przerwy przed kolejną terraformacją, również po zmianie
właściciela. Stary biom produkuje do końca prac; nowy zaczyna w następnym
miesiącu gry. Projekty rozlicza wspólny kalendarz, a podgląd prognozy nie
przyspiesza prac. Restart, podwójne kliknięcie i ponowienie błędnego ticka
nie pobierają kosztów drugi raz.

Utrata prowincji, jej dezaktywacja, upadek państwa lub niezgodna zmiana
biomu/mapy/budynków przerywa prace podczas rozliczenia miesiąca bez zwrotu
kosztów. Zamknięcie menu zamyka tylko podgląd. Koszty i wynik trafiają do
historii projektu; jego zakończenie także do historii państwa i raportu ticka.
Eksport Full Data JSON i `.map` uwzględnia ukończony biom bez zmiany geometrii.
GM nadal może bezpośrednio edytować biom dotychczasową komendą administracyjną.

## Wolne prowincje

Przy pierwszym uruchomieniu tej wersji bot naprawia populację aktywnych,
niezajętych prowincji lądowych: średnia wynosi dokładnie **2000**, a pojedyncze
prowincje mają **500–4000** mieszkańców. Zera otrzymują bazę 2000 przed
wyrównaniem średniej. Rozkład zachowuje różnice osadnictwa, zamiast nadawać
każdemu polu identyczną liczbę mieszkańców. Wolna woda pozostaje bezludna.

Naprawa przy starcie jest jednorazowa. Kolejne importy mapy normalizują wolny
ląd po ustaleniu własności pól. Populacje prowincji należących do państw oraz
nieaktywne pola pozostają nietknięte. Zakup, przeglądanie menu i zwykły tick
nie resetują populacji. Eksport nadal używa oryginalnej skali ludności Azgaara.
