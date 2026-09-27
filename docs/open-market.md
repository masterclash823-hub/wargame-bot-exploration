# Wolny rynek surowców

**Panel → Gospodarka → Wolny rynek** otwiera rynek ofert graczy. Nie wymaga
technologii, budynku ani AI. Handel odbywa się między państwami za złoto,
bez opłaty dla bota i bez tworzenia nowych surowców lub pieniędzy.

**Sprzedaj** rezerwuje podaną ilość surowca po cenie za jedną jednostkę.
Towar opuszcza dostępne zapasy: nie można go jednocześnie wydać, wykorzystać
w produkcji ani sprzedać drugi raz. Przy wystawianiu żywności zostaw zapas
dla mieszkańców. Zarezerwowany towar czeka poza miesięczną gospodarką państwa.

Kupujący widzi **jedną najtańszą ofertę każdego surowca od innych państw**.
Przy jednakowej cenie pierwszeństwo ma starsza oferta. Droższe pozostają
w bazie i pojawiają się po wykupieniu lub wycofaniu tańszych. Restart nie
usuwa kolejki. Własne oferty, także oczekujące, są w zakładce **Moje oferty**.

Wybierz surowiec, wpisz ilość i potwierdź sprzedawcę, cenę oraz łączny koszt.
Można kupić część oferty. Bot nie dobiera automatycznie droższej oferty,
gdy zabraknie towaru w najtańszej. Kup mniejszą ilość i odśwież widok.
Zmiana najtańszej oferty przed potwierdzeniem wymaga zaakceptowania nowych
warunków. Jedno potwierdzenie rozlicza się raz.

Wycofanie oferty zwraca tylko niesprzedaną część towaru. Coopowie zarządzają
wspólnymi ofertami i nie mogą kupować od własnego państwa. Cofnięcie dostępu
blokuje stare widoki. Przekazanie państwa lub jego upadek anuluje oferty
i zwraca towar do państwa; usunięcie państwa usuwa jego oferty.

| Komenda | Działanie |
| --- | --- |
| `/market list` | Najtańsze oferty i panel rynku |
| `/market sell resource:wood quantity:50 price:2` | Rezerwuje 50 drewna po 2 złota za jednostkę |
| `/market buy resource:wood quantity:10` | Podgląd i potwierdzenie zakupu 10 drewna |
| `/market mine` | Wszystkie własne aktywne oferty, z podziałem na strony |
| `/market cancel offer_id:12` | Potwierdzenie wycofania oferty nr 12 |

Ilości mają do 3 miejsc po przecinku, ceny do 2; formularze przyjmują kropkę
lub przecinek. Minimalna ilość to 0,001, cena 0,01 złota za jednostkę. Algae
można sprzedawać ułamkowo. Łączny koszt może być mniejszy niż 0,01 złota.
Maksymalna ilość/cena to 1 000 000, pojedynczy zakup do miliarda złota.
Dotychczasowe wymiany `/trade` działają osobno. Rynek nie tworzy umów miesięcznych.

Start bota dodaje tabele `market_offers` i `market_fills`. W PostgreSQL
jednorazowo rozszerza typ skarbca z REAL do DOUBLE PRECISION, aby małe płatności
nie ginęły wskutek precyzji float32. Nie resetuje sald. Nowe zmienne ani klucze
nie są potrzebne. Testy offline obejmują współbieżne zakupy, wycofanie transakcji
po błędzie, kolejkę cen, zwroty i uprawnienia. Nie zastępują próby na docelowym
Discordzie i PostgreSQL.
