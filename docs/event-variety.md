# Różnorodność i równowaga eventów

`/event generate` i `/event all` korzystają ze wspólnej, zapisanej w bazie
rotacji dla każdego państwa. Każda piątka nowych szkiców zawiera dwie szanse,
dwa zagrożenia i jedno wydarzenie mieszane, w losowanej kolejności.
To proporcja szkiców, nie gwarancja wyników: GM zatwierdza wydarzenia i może
zmienić ich tekst oraz skutki, a decyzje gracza nadal mogą odwrócić korzyść w stratę
lub uratować państwo przed zagrożeniem. Nie wymusza się ujemnego wyniku dobrej decyzji.

Bot rotuje dziesięć tematów: handel, rzemiosło, kultura, dyplomacja, społeczeństwo,
przyroda, infrastruktura, eksploracja, wojsko i zdrowie. Pomija trzy ostatnio
użyte kategorie i preferuje najmniej obecne w ostatnich dziesięciu szkicach.
Jedna porażka wyprawy, polityka pracy czy problem gospodarczy nie ma już być
pretekstem do każdego kolejnego eventu. Temat podany przez GM w `/event all`
pozostaje nadrzędny; kategoria wskazuje nowy punkt widzenia w jego ramach.
Jawne wskazanie ruin przez GM nadal tworzy wydarzenie o tych ruinach.

Historia jest tłem i pamięcią faktów. Prompt dopuszcza najwyżej jedno istotne
nawiązanie, pokazuje niedawne otwarcia jako przykłady, których nie powtarzać,
i usuwa dawny, jednostronnie negatywny przykład suszy. Informacje o instytucjach
i ekspedycjach trafiają do promptu tylko przy pasujących tematach.
Stan państwa nadal ogranicza wiarygodność wydarzenia i skalę proponowanych skutków.

Przed zapisem sprawdzane są tekst i efekty:

- Szansa ma przynajmniej jeden dodatni efekt bazowy i żadnego ujemnego.
- Zagrożenie ma przynajmniej jeden ujemny efekt bazowy i żadnego dodatniego.
- Wydarzenie mieszane ma korzyść i koszt na różnych osiach.
- Bliska kopia jednego z sześciu niedawnych otwarć jest odrzucana.
- Niezgodny tekst/JSON wraca do mechanizmu prób kolejnych modeli. Bot nie
  odwraca sam znaków w efekcie, którego opis mówi o czymś innym.

Podobieństwo tekstu jest kontrolą bliskich kopii, a nie pełnym rozumieniem
fabularnym. Głównym sposobem ograniczania powtórek pozostaje rotacja tematów
i instrukcje narracji; GM nadal przegląda szkic przed publikacją.

Kolejne sceny pozostają przy rozpoczętym wątku. Nie zamieniają automatycznie
pozytywnego eventu w ukrytą katastrofę ani nie wracają do starego konfliktu
tylko dlatego, że wystąpił we wspomnieniach. Nadal obowiązują trzy opcje,
własna odpowiedź gracza, najwyżej trzy decyzje i ograniczenia efektów GM.

Tabela `event_generation` tworzy się automatycznie przez `db.init_db()` na
SQLite i PostgreSQL. Zapis szkicu oraz jego miejsca w rotacji jest atomowy.
Równoczesny drugi szkic z nieaktualnym planem zostaje wycofany i wymaga ponowienia;
`/event all` zachowuje już istniejący szkic. Błędy nie zużywają rotacji.
Restart i przekazanie państwa zachowują historię. Dawne eventy pozostają dostępne
i służą do wykrywania kopii, bez dopisywania im domniemanych kategorii.

[Konfiguracja dodatkowych dostawców AI](event-ai-fallback.md).
