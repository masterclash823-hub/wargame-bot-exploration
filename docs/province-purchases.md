# Zakup prowincji

`/province buy` otwiera prywatną listę wolnych prowincji lądowych sąsiadujących
z aktywną prowincją własnego państwa. Lista ma strony po 25 pozycji.

Wybierz prowincję z listy, aby zobaczyć jej nazwę i ID, teren, biom, ludność,
kulturę, religię, fortyfikacje, budynki, zasoby bazowe i pełną cenę.
Opcjonalne `cell_id` otwiera od razu podgląd wskazanej prowincji z tej listy.
Otwarcie menu, wybór, zmiana strony i odświeżenie nie pobierają złota.

Cena wynosi 500 złota, minus 100 za kulturę zgodną ze stolicą i osobno minus
100 za zgodną religię. Obie zniżki dają cenę 300 złota. Brak danych tożsamości
lub stolicy oznacza brak odpowiedniej zniżki.

Dopiero **Potwierdź zakup** pobiera złoto i przenosi prowincję do państwa.
**Anuluj** zamyka menu bez zakupu. Bot ponownie sprawdza uprawnienia gracza,
państwo, cenę, granicę, właściciela, aktywność prowincji i dostępne złoto.
Zmiana ceny wymaga odświeżenia podglądu i ponownego potwierdzenia.
Nieaktualny panel ani podwójne kliknięcie nie pozwalają kupić prowincji dwa razy.

Jeśli lista jest pusta, brak wolnych sąsiadujących pól albo danych sąsiedztwa
mapy. W drugim przypadku GM powinien ponownie zaimportować mapę.
