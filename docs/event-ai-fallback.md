# AI eventów: dostawcy zapasowi i darmowe limity

Szkice, `/event all`, sceny, własne odpowiedzi, ocena skutków i ekspedycje
korzystają ze wspólnego mechanizmu przełączania AI. Obok Gemini można włączyć
Groq, Mistral i darmowe modele OpenRouter. Bitwy nadal używają dotychczasowego
wywołania Gemini. Ilustracje eventów są wyszukiwane w internecie.

## Konfiguracja Rendera

W Render → usługa bota → Environment dodaj dowolne z poniższych kluczy.
Każdy dostawca jest opcjonalny; brak klucza powoduje jego pominięcie.

| Zmienna | Gdzie uzyskać klucz / domyślna wartość |
| --- | --- |
| `GROQ_API_KEY` | [Groq Console → API Keys](https://console.groq.com/keys), konto Free |
| `MISTRAL_API_KEY` | [Mistral Studio → API Keys](https://console.mistral.ai/), tryb Free |
| `OPENROUTER_API_KEY` | [OpenRouter → Keys](https://openrouter.ai/settings/keys) |
| `GROQ_EVENT_MODEL` | `openai/gpt-oss-120b` |
| `MISTRAL_EVENT_MODEL` | `mistral-small-latest` |
| `OPENROUTER_EVENT_MODEL` | `openrouter/free` |
| `EVENT_AI_PROVIDERS` | `gemini,groq,mistral,openrouter` |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite`, zgodnie z dotychczasowym ustawieniem |
| `GEMINI_FALLBACK_MODELS` | `gemini-2.5-flash-lite,gemini-2.5-flash` |

Nie wklejaj kluczy do kodu ani Discorda. Po zapisaniu zmiennych uruchom nową
wersję usługi. Dotychczasowy `GEMINI_API_KEY` pozostaje wymagany przez bota,
ponieważ Gemini obsługuje również inne funkcje.

Domyślne próby przy dodaniu wszystkich kluczy:
Gemini główny → Groq → Mistral → OpenRouter → pozostałe modele Gemini.
Najpierw próbowane są niezależne konta, a dopiero potem kolejny model tego
samego dostawcy. Możesz ustawić np. `EVENT_AI_PROVIDERS=groq,mistral,gemini,openrouter`,
aby oszczędzać limit Google dla bitew. Jawnie pusta lista wyłącza AI eventów;
powtórzenia, nieznane nazwy dostawców i brakujące klucze są pomijane.

OpenRouter przyjmuje tu wyłącznie `openrouter/free` lub identyfikator z `:free`.
Konfiguracja płatnego modelu wyłącza tego dostawcę i zapisuje ostrzeżenie w logu.
Nie ma automatycznego przejścia z darmowej wersji na płatną. Router `openrouter/free`
sam dobiera dostępny darmowy model, więc jakość i dostępność mogą się różnić.

## Co oznacza darmowy dostęp

Stan dokumentacji sprawdzony 22 września 2026 r.:

- Groq udostępnia GPT-OSS 120B w planie Free z limitami żądań i tokenów.
  Dokładny przydział konta widać w panelu [Limits](https://console.groq.com/settings/limits);
  zobacz [zasady limitów](https://console.groq.com/docs/rate-limits).
- Mistral oferuje API w trybie Free bez karty, z ograniczeniami użycia.
  [Instrukcja aktywacji i utworzenia klucza](https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key).
- OpenRouter oferuje [darmowe warianty](https://openrouter.ai/docs/guides/routing/model-variants/free)
  i [router darmowych modeli](https://openrouter.ai/docs/cookbook/get-started/free-models-router-playground),
  objęte [limitami konta](https://openrouter.ai/docs/api_reference/limits).

Zostaw konta Groq i Mistral w trybie Free, jeśli chcesz korzystać bez opłat.
Bot nie zmienia planu ani ustawień rozliczeń dostawcy. Darmowe API nie oznaczają
nielimitowanego użycia, a samo dodanie integracji nie tworzy kont i kluczy.

## Awarie i zużycie limitów

- Limit 429, brak modelu 404, błędy 500/502/503/504 i problemy połączenia
  uruchamiają kolejny dostępny model. Każdy cel jest próbowany raz na wywołanie.
- Jedna próba ma maksymalnie 20 sekund, całe wywołanie maksymalnie 60.
  Budżet dzielony jest pomiędzy dostępne cele, aby zawieszony pierwszy model
  nie pozbawił szansy pozostałych dostawców. Nie ma ukrytych ponowień SDK.
- Wyczerpany model odpoczywa co najmniej 60 sekund. `Retry-After`, Google
  `RetryInfo` i reset limitu konta OpenRouter mogą wydłużyć przerwę.
  Przerwy współdzielą eventy, masowe generowanie i ekspedycje; pamięć znika po restarcie.
- Błąd konfiguracji lub autoryzacji 400/401/402/403 pomija danego dostawcę na
  co najmniej 15 minut; pozostali nadal działają. Zmiana klucza rozpoczyna nowe próby.
- Niepoprawny JSON, niewłaściwy znak bazowych efektów, bliska kopia poprzedniego
  eventu lub ucięta odpowiedź powodują próbę kolejnego modelu. Odrzucenie treści
  przez dostawcę kończy wywołanie. Wyniki nadal przechodzą walidację gry.
- Odpowiedzi mają limit 2400 tokenów. GPT-OSS używa niskiego poziomu rozumowania;
  narrator otrzymuje krótszą historię i najwyżej dwa zapamiętane eventy.
- Jeśli wszystkie próby zawiodą, `/event generate` i `/event all` nie zapisują
  fikcyjnego szkicu ani nie zużywają pozycji w rotacji. Istniejący event zachowuje
  trzy opcje i reguły awaryjne oceny skutków. Nie zmieniają się granice zatwierdzone przez GM.

Logi przełączania podają dostawcę, model i klasę/kod błędu, bez kluczy,
treści promptu lub odpowiedzi dostawcy. Integracje są sprawdzane testami HTTP
z odpowiedziami zastępczymi; faktyczny dostęp wymaga kluczy właściciela bota.

Protokoły: [Groq](https://console.groq.com/docs/openai),
[GPT-OSS i rozumowanie](https://console.groq.com/docs/reasoning),
[Gemini](https://ai.google.dev/gemini-api/docs/rate-limits).
