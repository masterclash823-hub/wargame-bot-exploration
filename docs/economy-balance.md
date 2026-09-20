# Korekty gospodarki

## Utrzymanie

Pierwsze dwa kolejne miesiące niezapłaconych rachunków zachowują pełną
produkcję. W trzecim miesiącu płatne budynki działają na 75%, w czwartym
na 50%, w piątym na 25%, a od szóstego ich produkcja zostaje zatrzymana.
Ograniczenie obejmuje także zużycie materiałów i usługi portów/spichlerzy.
Koszt utrzymania nadal jest należny; budynki nie są usuwane.

Sprawdzenie uwzględnia skarbiec oraz dochód danego miesiąca przy pełnej
wydajności. Jeśli wystarczy na wszystkie rachunki i zaległości, pełna
wydajność wraca już w tym rozliczeniu. Bezpłatne budynki, produkcja bazowa
i opłacone z góry zakłady zagranicznej kompanii nie podlegają ograniczeniu
za długi gospodarza. Prognoza pokazuje tę samą karę co rozliczenie.

## Ludność i zasoby bazowe

Produkcja bazowa prowincji jest mnożona przez `min(1, populacja / 2000)`:
0 mieszkańców = 0%, 300 = 15%, 1000 = 50%, od 2000 = 100%.
Nadal obowiązują mnożniki stabilności i etapu kolonii. Dotyczy to również
bazowego złota. Produkcja budynków nadal zależy od ich obsady; algae nadal
wymaga złoża i farmy. Bazowa produkcja reprezentuje drobną działalność
mieszkańców i nie zajmuje dodatkowej puli pracowników budynków.

## Wybrzeże

Nowe porty i przystanie rybackie oraz ich ulepszenia wymagają potwierdzonej
prowincji lądowej na wybrzeżu. Import Azgaara zapisuje osobno informację o
wybrzeżu, na podstawie wysokości, sąsiadów, `haven` i znacznika `t=1`.
Obowiązuje to również inwestycje kompanii.

Stare mapy bez tej informacji wymagają `/admin map_resync` przed nową
budową lub ulepszeniem. Istniejące budynki działają do czasu uzupełnienia
danych; brak informacji nie jest uznawany za teren śródlądowy. Po imporcie
potwierdzone śródlądowe porty/przystanie zostają nieaktywne: nie produkują,
nie zajmują pracowników ani nie kosztują utrzymania. Budynki są zachowane.

## Jedwab

Standardowe utrzymanie warsztatu jedwabiu spada z 4 do 2 złota miesięcznie
na poziomie 1. Wyższe poziomy kosztują odpowiednio 3 i 4 złota. Produkcja,
surowce wejściowe i cena sprzedaży 4 złota za jedwab pozostają bez zmian.
Przy stabilności 50 trzy warsztaty i jeden zakład sukienniczy mają 21 złota
przychodu ze sprzedaży i 9 złota utrzymania (przed kosztami infrastruktury
handlowej i pod warunkiem dostępnej przepustowości sprzedaży).

Aktualizacja tworzy tabelę wybrzeży przy starcie. Migracja utrzymania jest
jednorazowa i zmienia wyłącznie poprzednią standardową wartość 4 złota;
inne ustawienia GM-a są zachowane. Nie zmienia zapasów ani sald państw.
