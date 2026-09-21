# Rankingi państw

Komenda `/ranking` jest dostępna dla roli GM rozpoznawanej przez istniejące
`GM_ROLE_NAME` lub `GM_ROLE_ID`; nie wymaga uprawnień administratora Discorda.
Domyślnie pokazuje prywatny ranking prestiżu. Podanie `channel` publikuje wynik
na tym kanale, w języku wybranym przez GM. Nic nie jest publikowane cyklicznie.

Przykłady z angielskimi nazwami opcji (Discord tłumaczy opisy i opcje na polski):

- `/ranking`: prywatny podgląd prestiżu.
- `/ranking category:population channel:#rankingi limit:20`: publikacja ludności.
- `/ranking category:income`: podgląd miesięcznego bilansu złota.
- `/ranking category:army`: podgląd siły wojsk lądowych.

`limit` określa liczbę widocznych państw: od 1 do 25, domyślnie 10.
Załączony plik TXT zawsze zawiera cały ranking, także państwa poza limitem
i pełne nazwy skrócone w wiadomości. Bot potrzebuje na kanale prawa odczytu,
wysyłania wiadomości, osadzeń i załączników. Wiadomość nie pinguje graczy.

| Kategoria | Co jest porównywane |
| --- | --- |
| `prestige` | Zapisane punkty prestiżu, m.in. z wykonanych celów. |
| `population` | Suma mieszkańców aktywnych prowincji, również kolonii; nie korzysta z nieaktualnej sumy zapisanej przy państwie. |
| `provinces` | Liczba aktywnych prowincji, w tym kolonii. |
| `treasury` | Aktualne złoto w skarbcu, bez wyceny surowców. |
| `income` | Prognoza zmiany skarbca w kolejnym miesiącu po wydatkach, umowach i spłatach. To bilans netto, nie przychód brutto. |
| `technology` | Średnia czterech poziomów: economy, land, naval i colonial; uwzględnia części ułamkowe. |
| `army` | Łączna siła ataku i obrony gotowych jednostek lądowych. |
| `navy` | Łączna siła ataku i obrony gotowych okrętów. |

Siła wojska korzysta z tego samego obliczenia co bitwy: liczebności i statystyk
projektów, odpowiedniej technologii lądowej lub morskiej, bonusów ukończonych
badań (w tym finansowanych programów algae) i kary do morale za nieopłacanie
utrzymania. Obrona okrętu to 10% jego HP. Pomija rezerwy, trwającą mobilizację
oraz okręty przypisane do szlaków handlowych. Siły przydzielone do planów nadal
liczą się jako część armii. Podzielenie jednostek na mniejsze grupy nie dodaje
punktów. Nie uwzględnia terenu, fortyfikacji, planów ani losowania, więc nie
przewiduje zwycięzcy konkretnej bitwy.

Państwa upadłe (`ruins`) są pomijane; państwa w trakcie rozpadu nadal uczestniczą.
Jeżeli państwo upadnie przy następnym rozliczeniu, jego prognozowany bilans
operacyjny wynosi zero. Nieaktywne prowincje nie zwiększają ludności ani terytorium.
Wartości są porównywane do dwóch miejsc po przecinku; remisy dzielą pozycję
(np. 1, 1, 3), a kolejność przy remisie ustala nazwa, następnie ID państwa.

Rankingi są odczytem bieżącego stanu, nie przyznają nagród ani nie zmieniają
rozgrywki. Prognoza gospodarcza rozlicza wszystkie państwa razem tylko raz
i wycofuje całą transakcję: zapasy, badania, historia i kalendarz pozostają bez
zmian. Opublikowane rankingi pozostają stanem z chwili wygenerowania; odświeża
się je kolejnym wywołaniem komendy. Nie wymagają Gemini ani limitu API AI.
