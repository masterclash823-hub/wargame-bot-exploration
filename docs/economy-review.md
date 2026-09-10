# Przegląd gospodarki — 6 września 2026

To zapis historyczny. Aktualne zasady i naprawy opisuje
[Gospodarka v2](economy-v2.md); poniższe stawki dotyczą starszego kodu.

Zakres: kod z `main` po PR #2 (cdfb91e), szczególnie `cogs/economy.py`,
`cogs/colonialism.py`, `cogs/military.py` i schemat `db.py`.
Nie odczytywano bazy produkcyjnej ani konfiguracji Rendera.
Ten PR naprawia pomoc GM i zmienia eventy; **nie naprawia wymienionych niżej
usterek gospodarki ani nie zmienia stawek produkcji**.

## Najważniejsze problemy

| Priorytet | Ustalenie | Skutek | Podstawa |
|---|---|---|---|
| Wysoki | Tick składa się z wielu osobnych transakcji i odczytuje salda na początku. | Awaria może przesunąć kalendarz bez rozliczenia wszystkich narodów. Tick może nadpisać równoległą wymianę lub finał eventu, korzystając ze starego salda. | Analiza `_run_tick`, `_cfg_set`, `_apply_mp_effect`. Ryzyko współbieżności, nie odtworzenie na produkcji. |
| Wysoki | Ukończenie megaprojektu ponownie wczytuje stan sprzed produkcji miesiąca. | Produkcja z bieżącego ticka znika. Przykład: 20 drewna produkcji + 10 jednorazowo daje 10, nie 30. | Odtworzone w izolowanej bazie z translacją składni SQL PostgreSQL na SQLite. |
| Wysoki | `compute_trade_route_income` oczekuje `income_per_tick`, którego nie tworzy `db.py`. Dodawanie/listowanie tras oczekuje też `from_cell`, `to_cell`, `ship_id` zamiast schematu `from_cell_id`, `to_cell_id`. | Świeża baza nie obsłuży tych tras. Wyjątek przy naliczaniu dochodu pomija również `tick_colonies`, bo oba wywołania są w jednym `try`. Produkcyjna baza mogła mieć ręczne zmiany — tego nie potwierdzono. | Porównanie SQL ze schematem; test wykazał brak `income_per_tick`. `traderoute_add` dodatkowo odwołuje się do nieistniejącego lokalnie `_nation_owner`, zamiast `_nat_owner`. |
| Wysoki | Konsumpcja surowca przez budynek jest zwykłym ujemnym przyrostem, bez kontroli dostępności. | Odlewnia produkuje 6 prochu mimo braku żelaza i zapisuje `iron=-2`. | Odtworzone. |
| Średni | `_seed_buildings` nadpisuje definicje bazowych budynków przy każdym uruchomieniu modułu. | Edycja GM przez `building_set` nie przetrwa restartu dla tych kluczy. Dochód rynku zmieniony na 999 wraca do 15. | Odtworzone z translacją samych placeholderów. |
| Średni | Zbiorcze nadrabianie kalendarza ogranicza się do 3 miesięcy, po czym zapisuje bieżący czas. | Po np. 10 miesiącach przerwy pozostałe 7 miesięcy zaległości i ułamkowa część miesiąca zostają pominięte. | Analiza `calendar_loop`: `min(months, 3)` oraz `last_tick_ts = now`. |
| Średni | Fallback płaskich efektów megaprojektu traktuje `gold_once`, `stability`, `resources_once` jako zasoby miesięczne. | Powstają fikcyjne klucze magazynu, np. `gold_once=100`, `stability=5`, zamiast poprawnego rozróżnienia efektów jednorazowych i cyklicznych. | Odtworzone dla ukończonego megaprojektu. |
| Średni | Populacja narodu jest zapisywana z sumy sprzed wzrostu/głodu w prowincjach. | Statystyki narodu są opóźnione o tick: naród 1000, prowincja 990. | Odtworzone po głodzie. |
| Średni | W części SQL pozostają `%s` i `NOW()` bez obsługi SQLite. | `_seed_buildings` nie pozwala uruchomić modułu ekonomii na SQLite; kolejne ścieżki też zawodzą. | Odtworzone: `OperationalError: near "%": syntax error`. Render z PostgreSQL nie ma tego konkretnego problemu. |

## Obecne reguły, które warto znać

- Mnożnik stabilności: `0.75 + stability/100 * 0.25`; przy 50 wynosi **0.875**, nie 0.9, jak sugeruje komentarz.
- Miesięczne zapotrzebowanie na żywność: `populacja/100 + liczba jednostek/10`.
- Przy wystarczającej żywności i zapasie co najmniej 120% potrzeb: wzrost do 0.5% populacji na miesiąc.
- Przy braku żywności: spadek stabilności do 8 na miesiąc; przy wyżywieniu poniżej 50% także spadek populacji.
- Jedwab i przyprawy dają po 1 złota za 5 jednostek zapasu, do 50 złota dla każdego zasobu miesięcznie. Zapas nie jest zużywany: razem do 100 złota/miesiąc.
- Utrzymanie wojska w wojnie jest mnożone przez 3. Niedobór złota kończy się obcięciem skarbca do zera — brak długu lub automatycznego rozwiązania jednostek.

To opis implementacji, nie ocena, że te wartości są zbalansowane. Bez liczby
prowincji, wielkości populacji i czasu sesji nie da się rzetelnie dostroić stawek.

## Związek z nowymi eventami

- Efekty bazowe nadal zatwierdza GM. Trzy decyzje nie dają trzech pełnych wypłat:
  końcowy mnożnik to średnia wybranych skal 0.5 / 1 / 1.5.
- Skale obejmują zarówno korzyści, jak i straty. Przy samych dodatnich efektach
  opcja 1.5 jest korzystniejsza; GM powinien projektować także koszt/ryzyko, jeśli
  chce rzeczywistego dylematu. AI nie dopisuje własnych nagród.
- Własna odpowiedź zostaje przypisana do jednego z tych trzech profili i wpływa
  na następny opis fabularny. Awaria AI daje jawny fallback ×1.
- Efekty liczbowe powstają raz, w jednej transakcji finałowej z blokadą narodu.
  Skarbiec i zmieniane zapasy mają dolną granicę 0, stabilność 0–100 oraz zmianę
  eventową maksymalnie ±20. Podsumowanie pokazuje faktycznie zastosowane zmiany.
- To chroni przed podwójnym kliknięciem eventu, lecz **nie naprawia starego ticka,
  który może później nadpisać saldo ze swojej wcześniejszej kopii**. Ujednolicenie
  transakcji gospodarki jest najpilniejszym następnym zadaniem.

## Odtworzenie

Po instalacji zależności, w katalogu repozytorium:

```sh
python scripts/audit_economy.py
python -m unittest discover -s tests -v
```

Skrypt diagnostyczny używa wyłącznie nowych baz tymczasowych, wymusza SQLite,
a dla dwóch ścieżek specyficznych dla PG tłumaczy tylko `%s` i `NOW()`.
Nie jest to test integracyjny PostgreSQL. Nie należy uruchamiać pełnego bota
z produkcyjnymi sekretami do odtwarzania tych scenariuszy.

Rekomendowana kolejność napraw gospodarki: transakcyjny/idempotentny tick,
zgodność schematu szlaków, kończenie megaprojektów, surowce wejściowe budynków,
kalendarz i migracje definicji. Dopiero potem strojenie balansu.
