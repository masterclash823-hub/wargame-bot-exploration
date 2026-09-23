# Ilustracje eventów

Poprzednia poprawka umieszczała link w dużym polu Discorda, ale wyszukiwanie
działało tylko dla publicznych eventów. Bot nie sprawdzał, czy obraz da się
pobrać, a jeden nieudany wynik Commons kończył próbę. Obecnie ilustracja jest
domyślnie włączona zarówno dla eventów publicznych, jak i prywatnych.

## Pobieranie i wysyłanie

1. Bot szuka w Wikimedia Commons, a przy braku odpowiedniego, działającego
   obrazu próbuje kolekcji Art Institute of Chicago. W razie potrzeby upraszcza
   rozpoznany temat wyszukiwania. Własnych, nierozpoznanych haseł GM nie zastępuje
   losowym tematem. Do wyszukiwarki trafiają ogólne hasła, nie cała prywatna narracja.
2. Wybiera materiały domeny publicznej/CC0 i pomija oznaczenia generowania AI.
   Nie generuje grafik. Zachowuje autora, źródło i informację o licencji.
3. Pobiera kandydatów po kolei i sprawdza rzeczywisty plik JPG, PNG lub WebP:
   maksymalnie 6 MB i 12 mln pikseli. HTML, błędy pobierania i przekierowania
   poza dozwolone serwery obrazów są odrzucane. Każde źródło ma budżet 12 sekund.
4. Zapisuje obraz w tabeli `event_media`, powiązanej z eventem. Surowy obraz
   nie jest kopiowany do stanu decyzji. Usunięcie eventu usuwa także jego plik.
5. Wysyła świeży załącznik wraz z dużą ilustracją pod tekstem: na kanał,
   w DM, do podglądu GM, w `/event play` i po kolejnych decyzjach. Flaga pozostaje
   osobną miniaturą. Wznowienie po restarcie nie wymaga kontaktu z wyszukiwarką.

Bot potrzebuje na kanale uprawnień **Wyświetlanie kanału**, **Wysyłanie
wiadomości**, **Osadzanie linków** i **Załączanie plików**. Przy braku ostatniego
uprawnienia publikacja z włączoną ilustracją pozostawia event w szkicach i
pokazuje GM konkretną wskazówkę.

## Własny plik i naprawa istniejących eventów

- `/event post … file:obraz.png`: używa pliku GM, pomijając wyszukiwarki.
- `/event post … include_image:False`: całkowicie pomija ilustrację, także
  podany plik i hasła. Nie pokazuje wtedy ostrzeżenia o braku obrazka.
- `/event image event_id:12`: ponawia wyszukiwanie dla rozpoczętego lub
  zakończonego eventu. Można podać `image_query` albo `file`.
- Nowe publiczne posty mają zapisany identyfikator wiadomości, więc komenda
  edytuje ich ilustrację w miejscu. Do postu sprzed tej aktualizacji podaj
  dodatkowo `message_link` — link skopiowany w Discordzie przez GM. Bot sprawdza
  serwer, kanał, autora i treść wiadomości przed zmianą.
- Sama naprawa pliku aktualizuje też `/event play`. Stare wiadomości DM i dawne
  podglądy zachowują swoje załączniki; gracz otwiera aktualny widok przez tę komendę.

Zmiana ilustracji nie resetuje historii ani limitu decyzji, nie uruchamia eventu
ponownie i nie nalicza skutków. Jest bezpieczna również wtedy, gdy gracz właśnie
odpowiada. Nieudane wyszukiwanie przy naprawie zachowuje poprzedni obraz.
Jeśli obie wyszukiwarki zawiodą przy pierwszej publikacji, event działa dalej,
a GM otrzymuje wskazówkę użycia `/event image` lub własnego pliku. Logi rozróżniają
niedostępność źródła i plik, którego nie da się odczytać; nie zapisują treści eventu.

Nie są potrzebne nowe klucze API ani zmienne Rendera. Tabela powstaje przy
standardowym uruchomieniu bota; powinna być przechowywana w tej samej trwałej
bazie co reszta gry.

Dokumentacja źródeł: [MediaWiki Imageinfo](https://www.mediawiki.org/wiki/API:Imageinfo),
[Art Institute of Chicago API](https://api.artic.edu/docs/).
