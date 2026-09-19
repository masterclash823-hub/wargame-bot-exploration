# Flagi, kalendarz i zasoby

## Flagi

`/nation flag file:<załącznik>` zapisuje flagę w bazie gry. Właściciel zmienia
własną flagę; GM może dodać `nation:<nazwa>` i zmienić flagę dowolnego państwa.
PNG, JPG, WebP i GIF: do 2 MB i 4 mln pikseli. Bot zapisuje PNG do 1024 px;
w przypadku animacji używa pierwszego kadru. Zachowuje kolory i przezroczystość.
Niepoprawny lub całkiem przezroczysty plik nie zastępuje dotychczasowej flagi.

Alternatywnie `/nation flag flag:<emoji lub URL>`. Linki do stron plików GitHuba
oraz Wikimedia są poprawiane przy wyświetlaniu, również dla istniejących flag.
Emoji krajów i niestandardowe emoji Discorda są też pokazywane jako obrazki.
Podpisy wygasających linków do załączników pozostają nienaruszone. Już wygasły
lub usunięty obraz trzeba wgrać ponownie; bot nie odtworzy go z samego linku.
Zewnętrzne adresy nie są pobierane na serwer i pozostają zależne od dostawcy.

Wgrane flagi są publicznymi obrazkami pod `/flags/<hash>.png`, bez dostępu do
pozostałych danych bota. Zmiana pliku daje nowy adres i odświeża miniaturę.
Na Render Web Service bot korzysta z `RENDER_EXTERNAL_URL`; na innym hostingu
ustaw `FLAG_PUBLIC_BASE_URL` na publiczny adres HTTPS serwera bota. Brak adresu
jest zgłaszany przed zapisem. Serwer uwzględnia zmienną `PORT`.
Trwałość plików zależy od bazy gry, tak samo jak trwałość danych państw.

Po wdrożeniu otwórz nową kartę lub panel. Stare wiadomości nie są edytowane
automatycznie. `/nation list` nadal wyświetla listę państw bez flag.

## Kalendarz

- `/calendar set` bez daty zachowuje bieżący miesiąc i rok. Zmiana prędkości
  zachowuje przebytą część miesiąca. Prędkość: od minuty do 8760 godzin/miesiąc.
- Początkową datę można ustawić przed pierwszym rozliczeniem. Po nim używaj
  `/admineco tick` do przesuwania czasu; konfiguracja nie cofa historii gry.
- Powtórne `/calendar start` nie zeruje odliczania. Wznawianie po pauzie zachowuje
  część miesiąca sprzed pauzy; czas pauzy nie nalicza dochodów.
- Jeśli stara konfiguracja cofnęła datę, zegar wraca do ostatniego zapisanego
  rozliczenia. Nie nalicza ponownie dochodów ani nie usuwa historii.
- Znaczniki czasu bez strefy są odczytywane jako UTC. Brakujący lub uszkodzony
  znacznik jest inicjalizowany od chwili naprawy; bot nie zgaduje zaległości.
- Po restarcie kalendarz nadrabia do trzech zaległych miesięcy na minutę.
  Błąd kanału ogłoszeń nie zatrzymuje rozliczania. Nieudane rozliczenie jest
  wycofywane w całości i ponawiane, a `/calendar status` pokazuje opóźnienie
  oraz rodzaj ostatniego błędu. Szczegóły błędu są w logach `[CALENDAR]`.

Jeśli kalendarz jest wstrzymany, GM wznawia go przez `/calendar start`.
Bot musi być uruchomiony; czas niedostępności jest nadrabiany po ponownym starcie.

## Zasoby

Zakładka od razu pokazuje skarbiec, populację i aktualny magazyn, bez symulowania
całego świata. Osobny przycisk **Prognoza** oblicza następny miesiąc. Gdy
prognoza ma błąd, aktualne zasoby pozostają widoczne. Przy długiej liście
magazynu bot dołącza pełne `resources.txt`, zamiast gubić pozycje przez limit
Discorda. Podglądy nie zmieniają sald, umów, badań ani kalendarza.
