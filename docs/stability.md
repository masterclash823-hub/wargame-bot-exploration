# Stabilność i zadowolenie

Otwórz **Panel → Przegląd → Stabilność i zadowolenie** albo `/economy stability`.
Przycisk jest też pod zasobami w gospodarce. Stabilność i zadowolenie widać
w `/nation stats`, panelu i zasobach. Pełne przyczyny zmian są prywatne:
widzą je właściciel, coop i GM. GM może podać `nation` w komendzie.

Stabilność jest pokazywana z dokładnością do 0,01. Raport rozdziela stan teraz,
prognozę kolejnego miesiąca i faktyczne ostatnie rozliczenie. Przykład:
`50,00 → 49,71 (−0,29 pkt)` z przyczyną `Podatki: −0,29 pkt`.
Prognoza uwzględnia bieżące ustawienia i nie przesuwa kalendarza. Dalsze
decyzje graczy lub GM mogą zmienić jej wynik.

## Co oznaczają liczby?

**Stabilność** opisuje sprawność państwa. Mnoży produkcję i podatki przez
`0,75 + stabilność/400`, czyli od 75% do 100%.

**Zadowolenie** pokazuje dotąd ukryte niezadowolenie związane z podatkami
i polityką pracy: `zadowolenie = 100 − niezadowolenie`. To ten sam istniejący
wskaźnik, bez nowego kosztu lub zmiany balansu. Mnożnik podatków wynosi
`0,5 + zadowolenie/200`: 100 zadowolenia daje 100% wpływów, 50 daje 75%,
a 0 daje 50%. To osobny mnożnik, obok stabilności i premii budynków.
Żywność i luksusy działają bezpośrednio na stabilność.

## Skutki miesiąca

| Źródło | Zadowolenie | Stabilność |
|---|---|---|
| Niskie podatki | do +3 pkt | +0,25 pkt |
| Normalne podatki | do +1 pkt | bez bezpośredniej zmiany |
| Wysokie podatki | do −3 pkt | −(100 − zadowolenie po rozliczeniu)/100 pkt |
| Niewolnictwo | do −2 pkt, po podatkach | −0,5 pkt |
| Głód | bez bezpośredniej zmiany | pierwszy miesiąc: 0; od drugiego: do −8 pkt, proporcjonalnie do brakującej żywności |
| Zużycie jedwabiu i przypraw | bez bezpośredniej zmiany | łącznie do +1 pkt; samo magazynowanie nie wystarcza |
| Aktywne mariaże | bez bezpośredniej zmiany | +0,25 pkt za każdy, łącznie do +0,5 pkt |
| Trzy państwa prowadzące w prestiżu | bez bezpośredniej zmiany | +1 pkt na koniec ticka, do limitu 100 |

Premia prestiżu jest liczona raz na miesiąc po pozostałych skutkach ticka.
Przy remisie o trzech beneficjentach decydują kolejno nazwa państwa i ID.
Ruiny nie otrzymują premii. Prognoza uwzględnia ją bez zapisywania zmian,
a raport pokazuje ją osobno jako **Top 3 prestiżu**.

Wpływy podatkowe używają zadowolenia z **początku** miesiąca. Potem podatki
i polityka pracy zmieniają zadowolenie, a kara stabilności za wysokie podatki
używa wartości **po tych zmianach**. Dlatego rośnie przy długim utrzymywaniu
wysokich podatków. Przy zadowoleniu 74 wysokie podatki obniżą je do 71,
a stabilność o 0,29 pkt, jeśli nie ma innych efektów.

Oba wskaźniki mają granice 0–100. Raport pokazuje wpływ limitu na końcową
zmianę stabilności; w zadowoleniu pokazuje faktyczny skutek każdego kroku.
Przykładowo normalne podatki przy zadowoleniu 100 nie dodadzą punktu,
a następujące po nich niewolnictwo obniży zadowolenie do 98.

Pełne rozliczenie uwzględnia też zmiany stabilności przez ukończone projekty
i skutki traktatów. Decyzje między miesiącami są już zawarte w bieżącej
wartości; różnica względem ostatniego rozliczenia pojawia się osobno.
Szczegóły decyzji pozostają w historii państwa.

Nie trzeba migrować danych. Starsze raporty bez zapisanych przyczyn są
oznaczane jako starsze; bot nie odtwarza ich z obecnych ustawień.
Nowe rozliczenia zapisują wartości przed i po zmianie oraz poszczególne
przyczyny. Podgląd tych danych nie korzysta z AI.
