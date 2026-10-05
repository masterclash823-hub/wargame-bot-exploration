# Wojny i zakładanie państw

## Zgłoszenie państwa

1. Gracz bez państwa i dostępu coop otwiera `/nation found` lub **Panel → Zgłoś państwo**.
2. Wpisuje nazwę, historię, opcjonalny ustrój i flagę oraz ID 1–10 prowincji.
   Prowincje muszą być aktywne, niezajęte, lądowe i tworzyć połączone terytorium.
   Pierwsza prowincja zostaje stolicą. Szczegóły pola pokazuje `/province info`.
3. `/nation application` lub **Moje zgłoszenie** pokazuje status, pozwala edytować
   oczekujące zgłoszenie albo je wycofać. **Bonusy startowe** lub `/nation bonuses`
   pozwalają rozdzielić domyślnie 35 punktów. Liczba prowincji musi być równa
   punktom wielkości państwa. Do akceptacji ziemia nie jest rezerwowana.
4. GM otwiera `/nation applications` lub **Panel administratora → Państwa → Zgłoszenia państw**.
   Wybiera zgłoszenie, czyta całość i akceptuje albo odrzuca je z powodem widocznym
   dla gracza. Edycja gracza unieważnia wcześniejszy podgląd GM-a.
5. Akceptacja tworzy państwo, nadaje prowincje i stolicę, sumuje ich ludność,
   dodaje projekty jednostek oraz wybrane zasoby, złoto, technologię i własną
   kulturę/religię. [Zasady bonusów i ustawienia GM-a](game-setup.md).

Bot ponownie sprawdza właściciela, nazwę i ziemię przy akceptacji. Jeśli ktoś
wcześniej zajmie prowincję, gracz musi poprawić zgłoszenie. Powtórne kliknięcie
nie tworzy drugiego państwa ani nie przyznaje drugi raz pakietu. Po odrzuceniu
lub wycofaniu gracz może zgłosić nową wersję. Zgłoszenia i decyzje są zapisane
w bazie; po restarcie wystarczy otworzyć panel ponownie.

## Wojna bez ręcznego łączenia planów przez GM-a

**Panel → Dyplomacja i bitwy → Panel wojen** oraz `/war status` pokazują przeciwników,
oczekujące odpowiedzi, terminy i ostatnie rozstrzygnięcia. Są tam skróty do
planu, wyzwania, obrony, raportów i negocjacji pokoju.

1. Wojnę rozpoczyna istniejące `/diplomacy war`. Rezerwy automatycznie przechodzą
   do czynnej służby. Przygotuj plan z przypisanymi jednostkami w panelu lub
   przez `/battle plan`; sam opis jednostek nie wystarcza.
2. Jeśli walczą dwa państwa po jednej stronie, lider zaprasza sojusznika przez
   `/battle invite`, a sojusznik dodaje własne jednostki przez `/battle join`.
   Trzeba to zrobić przed wysłaniem wyzwania.
3. Atakujący wybiera **Wyślij wyzwanie** albo `/war attack nation plan_id cell_id`.
   Pole bitwy musi być aktywne i należeć do jednej z dwóch głównych stron lub
   być niezajęte. Plan atakującego zostaje zablokowany do rozpatrzenia wyzwania.
4. Obrońca otwiera **Odpowiedz na wyzwanie** lub `/war defend challenge_id plan_id`,
   wybiera własny plan i ogląda potwierdzenie z polem bitwy, fortyfikacjami oraz
   zasadami. **Potwierdź i rozegraj bitwę** uruchamia rozliczenie obu stron.
   W bitwie koalicyjnej bot najpierw ocenia plany przez AI.
5. Wynik i straty pozostają w raportach panelu oraz `/battle view`. Każda strona
   widzi własne rozkazy; GM widzi oba plany. Obie strony mogą zaproponować pokój
   przez panel. Przekazanie prowincji i reparacje wymagają zaakceptowanego traktatu.

Automatyczne rozliczenie stosuje istniejące statystyki, technologie i morale
jednostek, premię obrony +10% za poziom fortyfikacji oraz los ataku 0,85–1,15.
Jeśli przynajmniej jedna strona jest koalicją, AI ocenia pełne plany obu stron,
teren i wszystkie przypisane jednostki. Mnożnik taktyczny ×0,7–1,4 obejmuje
całą stronę; liczba sojuszników nie mnoży premii. AI wskazuje też narażenie
poszczególnych grup (wagi ×0,25–4), a silnik rozdziela ustalony budżet strat
na rzeczywiste jednostki ich właścicieli. Technologie i morale każdego
państwa nadal są liczone osobno. Raport pokazuje mnożniki obu stron; rozkazy
przeciwnika i uzasadnienie AI pozostają prywatne (uzasadnienie widzi GM).

Ocena korzysta z dotychczasowych `GEMINI_API_KEY` i `GEMINI_MODEL`, nie z klucza
AI projektów. Błąd, brak pełnej oceny lub limit czasu 25 sekund pozostawia
wyzwanie oczekujące, bez strat i bez zastępowania premii przez ×1. Można
potwierdzić je ponownie. Zmiana planów, jednostek, właścicieli, technologii,
morale lub pola bitwy w czasie oceny również wymaga ponownego potwierdzenia.
Ponowne kliknięcie podczas oceny nie wysyła kolejnego zapytania AI w tej
instancji bota; samo rozliczenie jest atomowe.

W automatycznej bitwie bez koalicji mnożniki i wagi narażenia pozostają ×1,
bez wywołania AI. Granice nie zmieniają się od samego wyniku bitwy. Nietypowe
bitwy GM nadal może rozpatrzyć przez `/battle match` i `/battle resolve`.

Między tą samą parą państw może czekać jedno wyzwanie. Atakujący może je
anulować, a obrońca odrzucić przez przycisk lub `/war cancel`. Bez odpowiedzi
wygasa po dwóch miesiącach gry. Pokój, zmiana właściciela lub usunięcie państwa
również zamykają oczekujące wyzwanie. Wszystkie te sytuacje odblokowują plan
bez strat: brak odpowiedzi nie oznacza automatycznej porażki. Ponowne kliknięcie
rozliczonej bitwy nie nalicza strat drugi raz. Jeśli przycisk wygasł lub bot
został zrestartowany, ponownie otwórz `/war status`.

## Wdrożenie

Nowe tabele tworzą się przez istniejące `db.init_db()`, bez usuwania zapisanych
państw, wojsk i bitew. Po wdrożeniu bot musi wykonać zwykłą synchronizację
komend: `/nation found` otwiera teraz formularz gracza, a do drzewa komend
dochodzą `/nation application`, `/nation applications` i grupa `/war`.
