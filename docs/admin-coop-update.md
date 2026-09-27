# Panel administratora, wspólne państwa i poprawki rozgrywki

## Panel i prowincje

`/admin panel` otwiera prywatny panel GM/administratora. Jest też przycisk
**Panel administratora** w panelu gracza. Dostęp daje skonfigurowana rola
`Game Master` lub uprawnienie Administrator na serwerze. Każdy przycisk
i formularz ponownie sprawdza uprawnienia osoby, która otworzyła panel.

Panel ma cztery kategorie: prowincje, państwa i coop, eventy oraz kalendarz
i bitwy. Listy państw i prowincji mają strony. Wybierz państwo, a następnie
prowincję albo działanie. Nadal działają komendy:

- `/province population cell_id:123 population:2000` — zmienia ludność prowincji
  i natychmiast przelicza populację państwa.
- `/province biome cell_id:123 biome:Taiga` — zmienia biom i jego naturalne
  zasoby. Zachowuje górzysty/pagórkowaty teren oraz dodatkowe zasoby geograficzne
  i złoża; pozostały teren dostosowuje do biomu.
- `/province coast cell_id:123 coastal:True` — naprawia oznaczenie lądowej
  prowincji nad wodą, wymagane przez przystań i port.
- `/province claim nation:Państwo ids:123,125-130 normalize_population:True`
  — przyznaje prowincje i stosuje istniejące uśrednianie populacji. Uśrednia
  **wszystkie pełne prowincje państwa odbierającego**, do średnio 2000 mieszkańców
  z większą stolicą. Rozwijające się kolonie zachowują ludność. Domyślnie opcja
  jest wyłączona. Osobne uśrednianie z podglądem nadal oferuje `/economy population`.

Odebranie lub przekazanie prowincji aktualizuje liczby mieszkańców obu państw
i usuwa stare przydziały pracowników na przekazywanym terenie.

## Coop

Główny właściciel lub GM dodaje gracza przez `/nation coop_add player:@gracz`,
a usuwa go przez `/nation coop_remove player:@gracz`. GM może wskazać `nation`.
`/nation coop` oraz **Panel → Ustawienia → Współdzielenie państwa** otwierają
listę członków i wybór gracza do dodania/usunięcia.

Każdy gracz może należeć do jednego państwa. Coop korzysta z tego samego panelu,
zasobów, wojska, gospodarki, badań, traktatów i eventów. Wszystkie limity są
wspólne, w tym jedna ekspedycja na miesiąc gry. Odpowiedź jednego członka
w evencie unieważnia starsze przyciski pozostałych; skutki naliczają się raz.

Coop nie może dodawać innych coopów ani przekazywać własności państwa.
Odebranie mu dostępu blokuje także stare widoki i niedokończone decyzje.
Przekazanie państwa przez GM usuwa dotychczasowe dostępy coop; nowy właściciel
nadaje je ponownie. Można awansować dotychczasowego coopa na właściciela.

Event jest wspólny, a jego język nadal wynika z ustawienia głównego właściciela.
Automatyczna wiadomość DM trafia do głównego właściciela; coop otwiera event
z panelu lub `/event play`. Prywatną historię mogą czytać członkowie państwa i GM.

## Budynki i eventy

Bazowa produkcja żywności standardowych gospodarstw, pastwisk, przystani
i plantacji rośnie o **5%**. Nie zmienia to kosztów ani zapotrzebowania ludności.
Tartak można budować także w tajdze, w tym na jej pagórkowatych polach;
korzysta z tego również automat kompanii.

Ulepszenia budynku dają mnożniki produkcji 1 / 1,7 / 2,4 oraz zapotrzebowania
na pracowników 1 / 1,5 / 2. Przykład przystani, przy pełnej obsadzie i stabilności
100, bez dodatkowych modyfikatorów:

| Poziom | Pracownicy | Żywność / miesiąc |
| --- | ---: | ---: |
| 1 | 200 | 15,75 |
| 2 | 300 | 26,775 |
| 3 | 400 | 37,8 |

Ręczny przydział 200 osób pozostaje 200 po ulepszeniu. Pełną wydajność wyższego
poziomu uzyskuje się po zwiększeniu obsady przez `/economy workers`, o czym
informuje teraz komunikat ulepszenia. Automatyczna obsada uwzględnia nowy poziom,
o ile państwo ma wystarczająco pracowników.

Kolejne fazy eventu mają różne zadania: wybór podejścia, jego realizację
i zamknięcie sprawy. AI dostaje wcześniejsze decyzje oraz treść oferowanych
odpowiedzi. Powtórzone lub niemal identyczne opcje są odrzucane podczas generowania
i uruchamiają kolejną próbę w istniejącym łańcuchu modeli. Awaryjne odpowiedzi
również różnią się między fazami. Pełny kontekst gospodarki pozostaje dostępny.

Ilustracje są pobierane, sprawdzane i wysyłane jako duże załączniki pod eventem,
niezależnie od flagi. Można też przesłać plik albo naprawić istniejący post przez
`/event image`. [Szczegóły ilustracji i uprawnień](event-images.md).

## Uruchomienie aktualizacji

Po scaleniu PR i restarcie bota standardowy start dodaje tabele członków coop
i ilustracji oraz aktualizuje standardowe definicje żywności i terenów tartaku.
Zmiany są jednorazowe; niestandardowe efekty ustawione przez GM są zachowane.
Ludność istniejących prowincji nie jest automatycznie uśredniana przy starcie.
Bot synchronizuje nowe komendy. Ilustracje nie wymagają dodatkowych kluczy API.

Testy offline obejmują rzeczywiste rozliczenie przystani na trzech poziomach,
obsadę ręczną, migracje, edycję prowincji, nadawanie i odbieranie dostępu coop,
wspólne wymiany/traktaty/eventy, ograniczenia panelu oraz wysyłanie ilustracji.
Nie zastępują sprawdzenia uprawnień bota na docelowym kanale Discorda.
