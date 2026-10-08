# Zakup prowincji

`/province buy` otwiera prywatną listę wolnych prowincji lądowych sąsiadujących
z aktywną prowincją własnego państwa. Lista ma strony po 25 pozycji.
To samo menu jest dostępne przez **Panel → Terytorium → Kup prowincję**.

Wybierz prowincję z listy, aby zobaczyć jej nazwę i ID, teren, biom, ludność,
kulturę, religię, fortyfikacje, budynki, zasoby bazowe i pełną cenę.
Opcjonalne `cell_id` otwiera od razu podgląd wskazanej prowincji z tej listy.
Otwarcie menu, wybór, zmiana strony i odświeżenie nie pobierają złota.

Lista wyboru pokazuje kierunek położenia prowincji, np. „↗ północny wschód”.
Podgląd podaje także współrzędne i odległość w jednostkach mapy. Punktem
odniesienia jest aktywna własna stolica gry, a gdy nie ma jej współrzędnych —
stolica powiązanego państwa Azgaara, jeżeli nadal należy do gracza. W pozostałych
przypadkach bot używa środka aktywnych własnych prowincji lądowych i wyraźnie
opisuje go jako „centrum państwa”. Brak współrzędnych oznacza brak wskazania
kierunku; numer pola nie służy do zgadywania położenia.

Pól wodnych nie można kupować. Bot sprawdza teren, biom Marine oraz wysokość
pola w zapisanej mapie (poniżej 20 oznacza wodę). Dzięki temu pole wodne jest
blokowane również wtedy, gdy starszy zapis prowincji nadal opisuje je jako ląd.
Sprawdzenie odbywa się zarówno przy tworzeniu listy, jak i przy potwierdzaniu
zakupu. Ląd nad wodą lub z rzeką pozostaje dostępny.

Cena wynosi 500 złota, minus 100 za kulturę obecną już we własnym państwie
i osobno minus 100 za obecną religię. Bot sprawdza wszystkie aktywne prowincje
lądowe należące do państwa; ustawienie stolicy nie jest wymagane. Kultura
i religia mogą występować w różnych prowincjach. Każda zniżka nalicza się tylko
raz, więc obie dają cenę 300 złota. Cudze, nieaktywne i nieprzypisane prowincje
nie dają zniżek. Dotyczy to także prowincji, którą dopiero zamierzasz kupić.

Bot porównuje zapisane ID kultur i religii z mapy. ID 0 oznacza brak przypisania
i nie daje zniżki. Podgląd pokazuje nazwę lub ID, jeżeli brakuje nazwy w katalogu.
„Brak danych” oznacza brak zapisanych danych mapy dla prowincji; GM może je
uzupełnić przez import Full Data JSON z tej samej mapy.

Dopiero **Potwierdź zakup** pobiera złoto i przenosi prowincję do państwa.
**Anuluj** zamyka menu bez zakupu. Bot ponownie sprawdza uprawnienia gracza,
państwo, cenę, granicę, właściciela, aktywność prowincji i dostępne złoto.
Zmiana ceny wymaga odświeżenia podglądu i ponownego potwierdzenia.
Nieaktualny panel ani podwójne kliknięcie nie pozwalają kupić prowincji dwa razy.

Jeśli lista jest pusta, brak wolnych sąsiadujących pól albo danych sąsiedztwa
mapy. W drugim przypadku GM powinien ponownie zaimportować mapę.

## Darmowe nazwy prowincji

**Panel → Terytorium → Nazwij prowincję** lub `/province rename` otwiera listę
własnych aktywnych prowincji, ze stronami po 25 pozycji. Wybierz prowincję,
wpisz nazwę miasta (1–80 znaków) i zapisz. Możesz też użyć
`/province rename cell_id:123 name:Nowy Kraków`.

Nadanie i kolejne zmiany nazwy kosztują **0 złota**, nie wymagają zgody GM-a
i są dostępne również dla coopa. Bot sprawdza własność ponownie przy zapisie;
utrata prowincji lub dostępu do państwa unieważnia otwarty formularz.

Nowa nazwa pojawia się w listach i szczegółach prowincji, pozostaje po restarcie
i synchronizacji tej samej mapy. Eksport mapy przenosi ją również do danych
istniejącego miasta Azgaara na tym polu. Sama nazwa nie tworzy osady ani nie
zmienia ludności, budynków, zasobów czy ID prowincji.
