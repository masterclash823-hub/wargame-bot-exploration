# Wyprawy i jeńcy

## Eksploracja

`/exploration` otwiera formularz przygotowań: cel, ludzie, zapasy, trasa i
zabezpieczenia, od 20 do 4000 znaków. Można też podać parametr `preparations`.
W panelu: Wydarzenia → Eksploracja. Nazwa komendy pozostaje angielska zgodnie
z pozostałymi komendami; opisy i formularze są PL/EN.

Bot przedstawia przygodę lub przeszkodę w języku gracza. Jedyny przycisk
odpowiedzi otwiera pole tekstowe do 4000 znaków. Nie ma gotowych wariantów
działania ani wymogu włączenia Message Content Intent. Opis i odpowiedzi
są prywatne. Wyprawa kończy się po najwyżej trzech odpowiedziach; może
zakończyć się wcześniej, gdy cel osiągnięto albo stał się nieosiągalny.

Po zakończeniu bot publikuje **sukces lub niepowodzenie z opisem** na kanale,
na którym rozpoczęto wyprawę, i pinguje skonfigurowaną rolę GM. Wznowienie
na innym kanale nie zmienia miejsca ogłoszenia. Wynik jest fabularny:
nie przyznaje samodzielnie prowincji, zasobów czy jednostek. GM rozstrzyga
ewentualne skutki dla mapy i gospodarki.

- Jedna aktywna wyprawa na państwo. `/exploration` bez przygotowań wznawia
  aktywną wyprawę; `expedition_id` pozwala otworzyć wskazany zapis także po restarcie.
- Aktualny właściciel państwa może kontynuować; stary właściciel traci dostęp.
- Błąd AI nie zużywa odpowiedzi ani nie losuje zastępczego wyniku.
- Historia i wynik są zapisane; późniejsze eventy znają ostatnie zakończenia.
- Publiczne ogłoszenie nie zawiera przycisków, zasad ani pełnych przygotowań.
- Bot potrzebuje wysyłania wiadomości, osadzeń i odczytu historii kanału.
  Rola `GM_ROLE_ID`, a przy jego braku `GM_ROLE_NAME` (domyślnie Game Master),
  musi istnieć i pozwalać na ping: być oznaczalna lub bot musi mieć uprawnienie
  do oznaczania ról. Pozostałe wzmianki są zablokowane.
- Publikacja jest ponawiana co minutę. Po niepewnym wyniku wysyłki bot
  najpierw sprawdza historię, by nie ponawiać pingu. Używa również stałego nonce.
  Jeśli historia po próbie przekracza 100 wiadomości i wyniku nie znaleziono,
  zachowuje niepewny stan zamiast ryzykować duplikat; zapis wyprawy pozostaje dostępny.

## Jeńcy po zwycięstwie

Panel → Wojsko → Jeńcy lub `/captives list`. Lista pokazuje ID uprawnienia,
źródło i limit. Po wybraniu podaje się ID własnej prowincji i liczbę ludzi,
a następnie zatwierdza koszt. Alternatywnie `/captives take claim_id cell_id quantity`
otwiera ten sam podgląd. Można też zrezygnować z uprawnienia bez korzyści i kar.

**Po bitwie:** wyłącznie zwycięzca rozliczonej bitwy z faktycznie zastosowanymi
stratami. Limit to 25% strat lądowych przegranego, zaokrąglone w dół. Okręty
nie tworzą jeńców. Jeńcy są częścią tych strat, nie kolejnym usunięciem jednostek;
opis bitwy otrzymuje ich liczbę. Jedna jednostka limitu oznacza jedną umowną
jednostkę ludności w systemie gry. Remis i rozliczenie bez zastosowania strat
nie dają uprawnienia. Dawne bitwy nie są przeliczane wstecz.

**Po wojnie:** autor oczekującego traktatu pokojowego wskazuje zwycięzcę
przyciskiem Wynik wojny lub `/treaty outcome`. Dostępne wartości: `proposer`,
`recipient`, `none`. Druga strona musi zaakceptować wynik razem z warunkiem
jeńców; zmiana unieważnia stare przyciski akceptacji. Po podpisaniu zwycięzca
otrzymuje limit 1% pozostałej ludności przegranego, maksymalnie 500. Sam pokój,
bez uzgodnionego zwycięzcy, nie daje jeńców. Dawne traktaty zachowują brak zwycięzcy.

Zniewolenie wymaga polityki niewolnictwa i kosztuje **0,5 złota na osobę oraz
5 reputacji za decyzję**. Jedno uprawnienie można wykorzystać tylko raz, do
sześciu miesięcy od powstania. Wybranie części limitu zamyka całe uprawnienie.
Źródła z różnych bitew i zakończenia wojny są odrębne, ale każde przeniesienie
rzeczywiście zmniejsza populację przegranego.

Ludzie są odejmowani od aktywnych prowincji przegranego, zaczynając od
najludniejszych, z pominięciem wcześniej oznaczonych jeńców. Trafiają do
wskazanej aktywnej prowincji zwycięzcy; rozwijane kolonie nie są celem.
Nie powstaje nowa ludność, nie usuwa się ponownie oddziałów ani nie tworzy
surowca do handlu. Jeńcy wliczają się w zwykłe zużycie żywności i pulę 40%
pracowników. Sam ich licznik nie daje dodatkowej premii do algae.

Zniesienie niewolnictwa uwalnia oznaczonych jeńców, pozostawiając mieszkańców
w prowincjach. Liczniki są ograniczane przy spadku populacji. Przekazanie
prowincji traktatem do państwa z wolną pracą również ich uwalnia. Koszty,
własność, limit i dostępność ludności są ponownie sprawdzane przy zatwierdzeniu.

## Wdrożenie

Aktualizacja dodaje trzy tabele i indeks. `db.init_db()` tworzy je przy starcie;
brak nowych zmiennych środowiskowych lub ręcznego SQL. Nie migruje dawnych
bitew do nowych uprawnień. Rejestracja dwóch cogów odbywa się w `bot.py`.
