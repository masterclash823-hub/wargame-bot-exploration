# Obsługa po polsku

Po wdrożeniu użyj **`/translate` bez parametrów**, aby włączyć polskie odpowiedzi.
`/translate lang:pl` i dotychczasowe `/language lang:pl` zapisują to samo ustawienie.
Angielski pozostaje dostępny przez wybór `en`. Wybór języka działa także przed
aktywacją serwera i utrzymuje się po restarcie, jeśli baza danych jest trwała.

W polskiej wersji Discorda nazwy komend również są polskie: `/pomoc`, `/wojsko
lista`, `/bitwa rozstrzygnij`, `/wydarzenie graj`, `/tłumaczenie`. Discord dobiera
nazwy, opisy formularzy i stałe opcje do **języka aplikacji Discord**. Zapisany
przez bota język odpowiada za wiadomości, dynamiczne podpowiedzi i przyciski.
Użytkownik angielskiego Discorda może nadal otrzymywać polskie odpowiedzi;
aby mieć także polskie formularze, powinien wybrać polski w ustawieniach Discorda.
Dotychczasowe identyfikatory komend pozostają zgodne ze starszymi instrukcjami.

Na Renderze ustaw `DEFAULT_LANGUAGE=pl`, jeżeli chcesz polskie ogłoszenia
kalendarza i domyślny język wydarzeń właścicieli bez zapisanej preferencji.
Brak tej zmiennej także oznacza teraz `pl`. Indywidualne ustawienie gracza ma
pierwszeństwo; przy jego braku odpowiedzi komend uwzględniają język Discorda.
`GM_ROLE_NAME=Game Master` nadal wystarcza do dostępu GM.

## Zakres

- Pomoc gracza i GM, poradnik, narody, prowincje, gospodarka i megaprojekty.
- Oferty wymiany i ich błędy, wojsko, projektowanie okrętów, badania, bitwy,
  dyplomacja, kolonie i szlaki; przyciski oraz formularze komend.
- Nazwy zasobów, terenów, statusów, kadłubów i modułów w prezentowanych danych.
- Nowe wydarzenia AI używają języka właściciela narodu; uzasadnienie bitwy
  używa języka GM, który ją rozstrzyga. Działa również lokalizowany wariant
  awaryjny przy niedostępności AI.
- Prywatna oferta wymiany jest składana w języku odbiorcy. Publiczna wiadomość
  używa języka osoby wykonującej komendę; jedna wspólna wiadomość nie może
  jednocześnie przyjąć różnych języków wszystkich czytających.

Wymiana przyjmuje np. `{"drewno":50,"żelazo":20}`. Dopuszczalne są też nazwy
bez polskich znaków i dotychczasowe angielskie klucze. Podanie obu nazw tego
samego zasobu w jednym obiekcie jest odrzucane. W bazie pozostają `wood`, `iron`
itd. Złoto oferty wpisuje się w osobnym polu.

Budynki można wybrać z polskich podpowiedzi lub wpisać np. `farma` i `kopalnia`.
Efekty wydarzenia mogą mieć postać
`{"stabilność":5,"skarbiec":100,"zasoby":{"żywność":50}}`.
W megaprojektach dostępne są też klucze `zasoby jednorazowo`, `zasoby co miesiąc`
i `złoto co miesiąc`. Nazwy narodów, projekty nazwane przez graczy, rozkazy,
notatki GM oraz już zapisane opowieści i historyczne wpisy nie są automatycznie
przepisywane ani tłumaczone przez AI.

## Wdrożenie i sprawdzenie

Ten zestaw zawiera też wcześniejsze poprawki z PR #3 i #4. Po scaleniu należy
wdrożyć/restartować Render. Startup synchronizuje lokalizacje we wszystkich
serwerach i zachowuje definicje komend na kolejne połączenie. Testy offline
obejmują składnię i limity ładunków Discorda, izolację języków równoczesnych
interakcji, polską budowę, oferty prywatne, menu i zachowanie treści graczy.
Nie łączą się z produkcyjnym Discordem, PostgreSQL ani Gemini.

W istniejącej bazie nie trzeba zmieniać kluczy zasobów ani kasować danych.
Po wdrożeniu sprawdź `/translate`, `/help`, budowę farmy i testową wymianę.
Opis pozostałych, wcześniej wykrytych problemów algorytmu gospodarki znajduje
się w [analizie gospodarki](economy-review.md).

## Utrzymanie tłumaczeń

`i18n/ui_pl.json` zawiera tłumaczenia źródłowych szablonów, a `terms_pl.json`
nazwy technicznych pojęć. `i18n.text()` tłumaczy szablon przed podstawieniem
wartości gracza. Dekorator `@i18n.localized` ustawia język tylko na czas danej
interakcji, a `LocalizedView` lokalizuje deklaratywne przyciski przy utworzeniu
widoku. Nowe odpowiedzi i callbacki powinny używać tych samych mechanizmów.
Nie należy tłumaczyć SQL, identyfikatorów przechowywanych w bazie ani nazw graczy.
