# Konkretne wybory, język i koszt eventów

## Reakcja na kliknięcie

Bot najpierw potwierdza kliknięcie widocznym stanem oczekiwania, a dopiero potem
odczytuje stan eventu i wywołuje AI. Przyciski nowych widoków mają trwałe
identyfikatory: działają po restarcie i nie wymagają otwartego w pamięci menu
z ostatnich 10 minut. Nie trzeba drugi raz klikać podczas generowania.

Jedna decyzja blokuje inne decyzje i ponowienia tego samego eventu na czas
przetwarzania w procesie bota. Kontrola wersji w bazie nadal chroni zapis przed
powtórzeniem, również między procesami. Podwójne kliknięcie nie nalicza dwóch
decyzji ani nagród. Stary przycisk pokazuje aktualny zapisany etap zamiast
stosować swój numer do nowych opcji. Uprawnienia właściciela/coopa są
sprawdzane ponownie; cofnięty dostęp nie pozwala podejmować decyzji.

**Po wdrożeniu starsze wiadomości z losowymi identyfikatorami przycisków trzeba
raz odświeżyć przez `/event play` lub panel.** Historia i efekty są zachowane.
Nowe wiadomości korzystają już z trwałej obsługi. Postęp po restarcie wymaga
zachowania tej samej bazy danych.

## Gdy odpowiedzi są ogólne

Widok eventu rozpoznaje także pojedyncze ogólne etykiety, różną wielkość liter,
interpunkcję i inną kolejność opcji. Nie wymaga już dokładnej zgodności całej
trójki ze starym szablonem. Gdy scena jest awaryjna, ma typowe ogólnikowe wybory
lub wykryty angielski tekst przy polskim języku państwa, numery 1/2/3 i ich
etykiety są ukryte. Zamiast nich widoczny jest przycisk **Załaduj odpowiedzi
ponownie** i możliwość opisania własnego działania.

Ponowne ładowanie nie zużywa decyzji, nie nalicza skutków i nie resetuje historii.
Stare kliknięcie ogólnej opcji jest blokowane także po stronie serwera. Kilka
równoczesnych kliknięć ponowienia tego samego eventu uruchamia tylko jedno
generowanie w procesie bota. Po awarii można spróbować ponownie; ostatni zapisany
stan pozostaje zachowany. Dla dawnych wiadomości otwórz aktualny widok przez
`/event play` lub panel gracza.

## Język

Język narracji i odpowiedzi wynika z ustawienia głównego właściciela państwa,
np. `/language lang:pl`. Polecenie dla AI podkreśla język przed i po kontekście.
Usunięto angielskie przykładowe etykiety ze schematu odpowiedzi. Lokalna kontrola
typowych angielskich słów odrzuca wyraźnie angielskie szkice, sceny, wybory i
uzasadnienia przy języku polskim, bez dodatkowego wywołania tłumaczącego AI.
To kontrola heurystyczna, nie gwarancja wykrycia każdego obcego słowa.

Zmiana języka właściciela pozwala ponownie załadować aktywną scenę bez utraty
postępu. Wcześniejsze decyzje i zatwierdzony publiczny tekst nie są przepisywane.
Osoby z ustawionym językiem angielskim nadal otrzymują angielskie eventy.

## Mniejsze zużycie i oczekiwanie

- Kontekst generowania zawiera do 3 ostatnich wpisów historii po 240 znaków
  oraz 1 zapamiętaną decyzję. Historia wyborów i pamięć nie są kopiowane drugi
  raz w tym samym prompcie.
- Zachowany jest cały liczbowy bilans żywności: zapas, produkcja, potrzeby,
  zmiana netto, zapas końcowy, niedobór i psucie. Stan gospodarki odświeża się
  przed kolejnymi decyzjami; niski zapas nie jest utożsamiany z głodem.
- Przy wyborze przyciskiem ocena skutków i następna scena powstają w jednym
  zapytaniu zamiast dwóch. Mają wspólny kontekst i limit 1600 tokenów.
  Niewłaściwe, ogólne lub powtórzone opcje nadal są odrzucane; poprawna ocena
  skutków może zostać zachowana, a scena naprawiona w pozostałym czasie.
- Pozostałe limity odpowiedzi: szkic 1000, samodzielna scena z wyborami
  1200, końcowa ocena skutków 600, klasyfikacja własnej odpowiedzi 256. Groq GPT-OSS
  ma dolny limit 1200, ponieważ do limitu wlicza też wewnętrzne rozumowanie.
- Gemini 2.5 Flash/Flash-Lite używa `thinkingBudget: 0`; Gemini 3.1 Flash-Lite
  i 3 Flash — `thinkingLevel: minimal`. Nie zmienia to mechanicznych limitów skutków.
- Jedno zwykłe generowanie ma budżet czasu 40 sekund i kończy automatyczne
  próby po dwóch niepoprawnych odpowiedziach. Modele, które właśnie zwróciły
  błędne dane, są pomijane przez 15 sekund; po ponowieniu można wykorzystać
  kolejne modele. Limity API nadal mają własne okresy oczekiwania.
- Decyzja lub ponowienie odpowiedzi ma **wspólny budżet 24 sekund** na etapy AI,
  w tym modele zapasowe, klasyfikację własnego tekstu i ewentualną naprawę sceny.
  Każdy etap używa tylko pozostałego czasu. To ograniczenie oczekiwania na AI,
  nie gwarancja czasu całej operacji: dochodzi odczyt/zapis bazy i wysyłka Discorda.
- Gemini pozostaje pierwsze w kolejce. Wyszukiwanie ilustracji jest wyłączone.
  Brak AI nadal jest jawnie oznaczany; limit czasu nie omija walidacji języka,
  kontroli skutków GM ani zasady naliczania efektów dopiero po trzeciej decyzji.

Parametry Gemini: [dokumentacja sterowania rozumowaniem](https://ai.google.dev/gemini-api/docs/generate-content/thinking).

## Żywność

Bazowe stawki po poprzednim zwiększeniu zaokrąglono do najbliższej całości:
gospodarstwo **21**, pastwisko **8**, przystań rybacka **16**, plantacja **6**.
Jednorazowa migracja przy starcie zmienia tylko poprzednie standardowe wartości
8,4 / 15,75 / 6,3. Niestandardowe definicje GM pozostają zachowane.
Końcowa produkcja po uwzględnieniu poziomu, obsady i innych modyfikatorów nadal
może mieć część ułamkową; nie jest dodatkowo zaokrąglana w każdym budynku.
