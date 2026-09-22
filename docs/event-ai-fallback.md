# Zapasowe modele eventów

Eventy korzystają ze wspólnej kolejki modeli dla tworzenia szkiców, `/event all`,
kolejnych scen, interpretacji własnej odpowiedzi gracza i oceny skutków.
Ekspedycje korzystają z tego samego mechanizmu. Wcześniej bot próbował tylko
jednego modelu, po czym stosował opis lub skutki awaryjne.

## Ustawienia Rendera

| Zmienna | Domyślna wartość | Znaczenie |
| --- | --- | --- |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Pierwszy model; istniejąca konfiguracja pozostaje w użyciu. |
| `GEMINI_FALLBACK_MODELS` | `gemini-2.5-flash-lite,gemini-2.5-flash` | Modele zapasowe, oddzielone przecinkami, w kolejności prób. |

Nie trzeba dodawać nowej zmiennej, żeby włączyć domyślne modele zapasowe.
Jawnie pusta lista wyłącza zapasowe modele. Powtórzenia i puste wpisy są pomijane.
Wszystkie żądania korzystają z dotychczasowego `GEMINI_API_KEY`.
Wybrane modele muszą być dostępne w projekcie Google użytkownika.

## Przebieg awarii

- Po limicie HTTP 429, niedostępnym modelu 404, błędzie serwera 500/502/503/504
  lub błędzie połączenia bot próbuje następnego modelu z tym samym poleceniem.
- Jedna próba trwa najwyżej 20 sekund, a całe wywołanie ma budżet 60 sekund.
  Każdy model jest próbowany najwyżej raz w danym wywołaniu.
- Niedostępny model jest pomijany przez co najmniej 60 sekund; dłuższy czas
  wskazany przez serwer w sekundach (`Retry-After` / `RetryInfo`) wydłuża przerwę.
  Dzięki temu następna scena i masowe tworzenie eventów nie ponawiają od razu
  żądań do modelu z wyczerpanym limitem. Po przerwie kolejna operacja ponownie
  zaczyna od pierwszego dostępnego modelu. Pamięć przerw znika po restarcie.
- Błędny klucz, brak uprawnień, niepoprawne żądanie oraz odrzucona lub pusta
  odpowiedź nie powodują prób na kolejnych modelach.
- Gdy żaden model nie odpowie, istniejący event nadal ma trzy opcje i obsługuje
  własną odpowiedź gracza przez reguły awaryjne. Ocena skutków zachowuje
  dotychczasowe ograniczenia liczbowe i listę zasobów ustaloną przez GM.
  `/event all` oznacza nieudane generowanie jako błąd i nie zapisuje pustego
  szkicu; ponowienie dla brakujących państw pozostaje dostępne.

W logach przełączenia znajdują się nazwa modelu i kod błędu, bez klucza,
polecenia gracza i treści odpowiedzi dostawcy. Przełączenie modeli dotyczy
tekstu: ilustracje pochodzą z Wikimedia Commons lub Art Institute of Chicago,
ewentualnie z pliku wskazanego przez GM, nie z generatora AI.
Mechanizm nie zmienia wywołań AI rozstrzygających bitwy.

Limity Google zależą od modelu, ale obowiązują na poziomie projektu.
Model zapasowy pomaga, gdy ma własny dostępny limit; wspólny limit wydatków,
brak środków lub wyczerpanie wszystkich modeli nadal może uniemożliwić
generowanie. Bot nie zmienia kluczy ani nie omija ograniczeń projektu.

Dokumentacja dostawcy: [limity](https://ai.google.dev/gemini-api/docs/rate-limits),
[modele](https://ai.google.dev/gemini-api/docs/models),
[terminy wycofania modeli](https://ai.google.dev/gemini-api/docs/deprecations).
Nazwy domyślnych modeli zapasowych sprawdzono 21 września 2026 r.
