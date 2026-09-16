# Rozpad państw i odkrywanie ruin

GM uruchamia rozpad przez `/nation decay nation:<nazwa> reason:<opcjonalny powód>`.
Państwo nadal działa przez okres przejściowy. Przy trzecim kolejnym rozliczeniu
miesiąca gry staje się ruinami. Przykład: start w styczniu, upadek w kwietniu.
Chodzi o kalendarz bota; zatrzymany kalendarz zatrzymuje odliczanie.
Ponowne uruchomienie rozpadu nie przesuwa terminu.

Przed upadkiem GM może użyć `/nation decay_stop nation:<nazwa>`.
`/nation stats` pokazuje pozostałe miesiące, a `/nation list` oznacza rozpad i ruiny.
Nie ma automatycznego wskrzeszania upadłego państwa.

Po upadku historia, prowincje i zasoby pozostają w archiwum. Państwo przestaje
produkować, rozliczać podatki i utrzymanie, rozwijać kolonie oraz technologie.
Gracz traci kontrolę, a jego konto można przypisać do nowego państwa.
Umowy handlowe, traktaty, mariaże i koncesje kończą się; oczekujące bitwy oraz
aktywne wyprawy są anulowane. Nierozstrzygnięty plan przeciwnika wraca na listę
oczekujących. Żadne prowincje ani zasoby nie trafiają automatycznie do sąsiadów.

Gracz wybiera **Panel → Eventy → Ruiny** lub `/ruins list` i wskazuje upadłe
państwo. Musi graniczyć z aktywnym polem jego ruin według połączeń pól z importu
mapy. Bez tych połączeń bot nie zgaduje sąsiedztwa. Otrzymuje numer szkicu eventu
do przekazania GM-owi. Ponowne zgłoszenie tej samej pary państw zwraca ten sam
event, również po zakończeniu przygody.

GM ustala efekty i publikuje szkic standardowymi komendami eventów. Samo
zgłoszenie nie przyznaje nagród. Może też wygenerować dalszy event przez
`/event generate nation:<sąsiad> ruins:<ID upadłego państwa>` — ID jest widoczne
na liście ruin. Przygoda korzysta ze zwykłych wyborów i odpowiedzi gracza;
ustawienia widoczności i języka pozostają takie jak w innych eventach.
Kontekst ruin zawiera nazwę, pola i publiczny opis historyczny, bez prywatnych
planów wojennych i decyzji dawnego gracza.

Zmiana bazy jest addytywna: tabele rozpadu, pól ruin i powiązań eventów powstają
przy starcie. Żadne istniejące państwo nie rozpada się bez decyzji GM-a.
