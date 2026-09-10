# Kronika, pamięć państw, traktaty i cele

Aktualizacja rozszerza gospodarkę v2. Istniejące państwa zachowują właścicieli,
zasoby, prowincje, wojsko i historię. Nowe możliwości są dostępne w `/panel`.
Nazwy komend pozostają angielskie; odpowiedzi, przyciski, parametry i wybory
mają polskie i angielskie tłumaczenia.

## Podatki a nastroje

Gospodarka śledzi stabilność oraz niepokoje społeczne, które reprezentują
niezadowolenie z podatków. Nie ma osobnego wskaźnika „zadowolenie”.

| Podatki | Niepokoje co miesiąc | Wpływ na stabilność | Bazowy przyrost przy pokryciu 120% potrzeb żywności |
| --- | --- | --- | --- |
| Niskie | −3, minimum 0 | +0,25 | 0,3% |
| Normalne | −1, minimum 0 | Brak bezpośredniej zmiany | 0,2% |
| Wysokie | +3, maksimum 100 | −niepokoje / 100 po ich aktualizacji | 0,05% |

Niepokoje zmniejszają wpływy z podatków o maksymalnie 50%. Stabilność wpływa
również na produkcję. Żywność i konsumpcja luksusów mogą dodatkowo zmieniać
stabilność, więc wynik miesiąca zależy od całej sytuacji państwa.

## Codzienny raport

GM ustawia `/chronicle configure channel:#kronika hour_utc:18 language:pl`.
Godzina jest w UTC, niezależnie od prędkości kalendarza gry. Potwierdzenie
pokazuje termin pierwszego raportu w lokalnym czasie Discorda. Domyślnie jest
to 18:00 UTC i język polski; można wybrać angielski. Raport nie działa przed
skonfigurowaniem kanału. Bot potrzebuje dostępu do kanału, wysyłania wiadomości,
osadzeń oraz odczytu historii wiadomości.

Raport pokazuje do dwóch działań z poprzednich 24 rzeczywistych godzin:
wypowiedzenie wojny, rozstrzygnięcie bitwy, podpisanie lub zerwanie publicznego
traktatu, dotrzymanie gwarancji, ukończenie celu, zakończenie publicznego eventu,
rozrost kolonii, założenie kolonii, ukończenie megaprojektu, badania lub budowę.
Kolejność na tej liście określa wagę wydarzeń. Druga pozycja preferuje inne
państwo, a następnie inny rodzaj działania. Przy remisie wygrywa nowszy wpis.
Przy braku aktywności raport informuje o tym; nie dopisuje fikcyjnych działań.

Kronika korzysta z zapisanych faktów, bez dodatkowego wywołania AI. Nie kopiuje
planów wojskowych, treści decyzji, warunków wymian, reparacji ani prywatnych
eventów. Publiczny traktat ujawnia tylko strony i rodzaj umowy. Wiadomości nie
wywołują wzmianek. Flagi zapisane jako URL są wyświetlane jako obrazy.

- `/chronicle preview` — prywatny podgląd ostatnich 24 godzin bez publikacji.
- `/chronicle pause` — wstrzymanie raportów; `configure` ponownie je włącza.
- `/chronicle status` — kanał, pora i ostatnie stany wysyłki.
- `/chronicle retry` — podgląd nieudanej wysyłki i przycisk ponowienia dla GM.

Po restarcie bot odczytuje harmonogram z bazy. Po dłuższej przerwie przygotowuje
jeden najnowszy raport. Każdy termin ma osobny zapis wysyłki, więc równoległe
wykonania nie publikują dwóch kopii. Po niepewnym wyniku wysyłki bot szuka
własnej wiadomości z oznaczeniem terminu w ostatnich 100 wiadomościach kanału.
Jeśli nie może potwierdzić dostarczenia, pozostawia stan do sprawdzenia przez
GM. Przycisk ponowienia służy przypadkom, w których GM sprawdził kanał.

Kanał i harmonogram ustawia się osobno dla serwera. Dane świata, jak wcześniej
w tym bocie, są wspólne dla używanej bazy danych.

## Pamięć decyzji

**Panel → Wydarzenia → Pamięć decyzji** oraz `/memories` otwierają prywatne
archiwum. Po zakończeniu eventu bot zapisuje sytuację początkową, wszystkie
decyzje, własne odpowiedzi i rzeczywiście zastosowane efekty. Jeśli skarbiec
zatrzymał się na zerze, pamięć zapisuje rzeczywistą stratę, nie większy planowany
koszt. Zapis i rozliczenie końcowej decyzji są jedną transakcją.

Kolejne eventy otrzymują do sześciu powiązanych lub najnowszych wspomnień
spośród ostatnich 100 zapisów. Archiwum zachowuje wszystkie wpisy. AI używa ich
do kontynuacji historii i oceny nowych decyzji w zatwierdzonych przez GM
granicach efektów. Pamięć nie przyznaje dodatkowych premii poza rozliczeniem
eventu i nie ujawnia innym graczom prywatnych decyzji.

GM może zajrzeć do pamięci przez `/memories nation:nazwa`. Pierwsze uruchomienie
uzupełnia archiwum z zapisanych, zakończonych eventów interaktywnych, bez ponownego
naliczania efektów ani publikowania starych wydarzeń w kronice. Starszych
decyzji, których bot nie zapisał w stanie eventu, nie da się odtworzyć.

## Traktaty dyplomatyczne i pokój po wojnie

**Panel → Dyplomacja i bitwy → Nowy traktat** prowadzi przez wybór państwa,
rodzaju traktatu oraz warunków. `/treaty propose` udostępnia te same dane jako
komendę. Traktat zaczyna się jako szkic widoczny dla autora i GM. Autor może
ustawić raty, opis i widoczność, a następnie kliknąć **Wyślij propozycję**.
Odbiorca widzi ją w swoim panelu i na liście traktatów. Musi kliknąć
**Akceptuj wszystkie warunki**. Do tego czasu nie ma płatności ani zmiany granic.

| Rodzaj | Działanie |
| --- | --- |
| Pokój | Kończy istniejącą wojnę, ustanawia rozejm i rozlicza warunki pokoju |
| Nieagresja | Wypowiedzenie wojny w okresie obowiązywania narusza pakt |
| Sojusz | Wzajemny dostęp wojskowy; dołączenie do wojny wymaga decyzji |
| Dostęp wojskowy | Wzajemne prawo przemarszu przez terytorium |
| Gwarancja | Autor gwarantuje bezpieczeństwo odbiorcy i otrzymuje wezwanie po ataku |

`/diplomacy peace` i `/diplomacy alliance` także otwierają szkic traktatu.
Nie kończą już jednostronnie wojny i nie nadają jednostronnie sojuszu.
Podczas wojny można zaproponować pokój; pozostałe rodzaje wymagają pokoju.
Dotychczasowe relacje pozostają zachowane. Stare sojusze nadal umożliwiają
przemarsz, ale nie otrzymują wymyślonych terminów, rat ani gwarancji.

Traktat może obowiązywać od 1 do 120 miesięcy gry. Warunki obejmują złoto
oddawane i otrzymywane, do 25 prowincji po każdej stronie oraz opis do 2000
znaków dla stron i GM. **Raty, opis i widoczność** pozwalają ustawić miesięczne
reparacje, ich płatnika i liczbę rat. Alternatywnie użyj `/treaty tribute`.
Kwota każdej płatności mieści się w zakresie 0–1 000 000 złota.
Opis fabularny jest do oceny graczy i GM; automatyka wykonuje pola liczbowe
i wskazane prowincje. Pełen opis pozostaje widoczny przed akceptacją.

Zmiana warunków unieważnia wcześniejszy przycisk akceptacji. Przy akceptacji bot
ponownie sprawdza właścicieli, fundusze i prowincje. Błąd cofa całe rozliczenie.
Przekazana prowincja zachowuje budynki, populację, kolonię i związane z nią
megaprojekty. Oddziały strony oddającej wracają do nieprzydzielonej puli;
utracona stolica przestaje być stolicą. Szlak bez własnego portu jest zamykany,
a przypisany okręt zwalniany. Sumy populacji obu państw są przeliczane.

Raty zaczynają się od następnego miesiąca gry i są pobierane przed zwykłym
rozliczeniem gospodarki. Brak złota powoduje częściową wpłatę i zapis zaległości,
bez ujemnego skarbca. Dwa kolejne miesiące zaległości zrywają aktywny traktat
i odbierają płatnikowi 10 reputacji. Zerwanie lub wygaśnięcie umowy **nie usuwa
uzgodnionych rat ani długu**. Zaległości są spłacane w kolejnych miesiącach.

Świadome zerwanie aktywnego traktatu kosztuje 10 reputacji. Wypowiedzenie wojny
zrywa aktywne traktaty między tymi państwami, z jedną karą 10 punktów za to
wypowiedzenie. Prestiż i reputacja są widoczne w statystykach państwa; reputacja
zaczyna się od 50/100 i jest wskaźnikiem dotrzymywania umów, bez ukrytej kary
gospodarczej.

Wezwania gwarancyjne pojawiają się w panelu oraz `/treaty calls`. Gwarant ma
czas do kolejnego rozliczenia miesiąca gry. Kliknięcie dołączenia wypowiada
wojnę napastnikowi i daje +2 reputacji. Odmowa lub brak odpowiedzi zrywa
gwarancję i kosztuje 10 reputacji; bot nie dołącza do wojny automatycznie.
Jeżeli gwarant już walczy z napastnikiem, wezwanie jest uznane za wykonane
przy rozliczeniu miesiąca. Gdy broniona wojna się skończyła, wezwanie jest zamykane.

Warunki traktatów zawsze czytają tylko strony i GM. Widoczność „publiczne”
pozwala kronice ogłosić ich zawarcie lub naruszenie. Szkice i niewysłane
warunki pozostają prywatne również po anulowaniu. Niewysłane i oczekujące
propozycje wygasają po sześciu miesiącach od utworzenia.

## Opcjonalne cele państwowe

**Panel → Przegląd → Cele państwowe** lub `/goals status` pokazują trzy cele.
Można wybrać jeden naraz, bez kosztu, albo porzucić go bez kary.

| Cel | Wymagania |
| --- | --- |
| Bezpieczne zapasy | 3 kolejne miesiące bez głodu, z zapasem żywności na co najmniej 2 miesiące i stabilnością co najmniej 60 |
| Rozwój państwa | 2 budowy lub ulepszenia budynków wykonane po wyborze celu |
| Postęp naukowy | Badania wykonane po wyborze celu oraz wzrost dowolnej dziedziny o co najmniej 0,3 od poziomu początkowego |

Każdy cel trwa co najmniej trzy miesiące gry, nie ma terminu końcowego i daje
**10 prestiżu**. Postęp i nagroda rozliczają się automatycznie. Cel żywnościowy
wymaga państwa z ludnością zużywającą żywność; sam pusty kraj nie spełni warunku.
Samo pasywne narastanie technologii nie wystarcza do celu naukowego. Po
ukończeniu można wybrać następny cel. Nagrody nie zwiększają dochodów ani siły
wojska. Podgląd gospodarki nie nalicza prestiżu, płatności ani postępu na stałe.

## Państwo nadawane przez GM

GM używa `/nation found player:@gracz name:nazwa history:historia`.
Flaga i ustrój są opcjonalne. Gracz nie tworzy państwa samodzielnie: panel
osoby bez państwa prosi o kontakt z Game Masterem. Jedno konto może mieć
jedno państwo. Komenda odrzuca konta botów i zajęte nazwy.

`/nation transfer nation:nazwa player:@gracz` pokazuje GM podgląd przekazania
z przyciskiem potwierdzenia. Nowy gracz musi nie mieć innego państwa.
Zmienia się właściciel, a przy państwie pozostają wojsko, zasoby, historia,
cele, pamięć i aktywne zobowiązania, także reparacje i umowy miesięczne.
Oczekujące oferty wymian oraz szkice i propozycje traktatów są anulowane,
aby nowy właściciel mógł sam ustalić ich warunki.

Stare formularze badań i projektowania okrętów sprawdzają bieżącego właściciela
przed zapisem. Tak samo postępują nowe panele celów, pamięci i traktatów.
Trwające eventy można kontynuować jako nowy właściciel. Rola GM jest sprawdzana
ponownie przy potwierdzeniu przekazania; `GM_ROLE_NAME=Game Master` nadal wystarcza.

## Wdrożenie i sprawdzenie

Zmiana opiera się na aktualizacji gospodarki z PR #15. Nowe tabele tworzą się
automatycznie przy starcie bota, a uzupełnienie pamięci jest jednorazowe.
Nie wymaga resetu istniejącej gry ani ręcznej zmiany populacji. Przy wdrożeniu
na Renderze potrzebna jest trwała baza, jak w dotychczasowej konfiguracji.
Po uruchomieniu i synchronizacji komend GM wybiera kanał raportu; gracze mogą
od razu korzystać z nowych przycisków w `/panel`.

Testy używają tymczasowego SQLite i atrap Discorda/AI. Sprawdzają transakcje,
równoległą akceptację, stare przyciski, przekazanie państwa, pamięć rzeczywistych
efektów, brak prywatnych danych w raporcie oraz odtwarzanie wysyłki po restarcie.
PostgreSQL i wysyłka na prawdziwym serwerze Discord wymagają sprawdzenia po
wdrożeniu; nie były uruchamiane na produkcyjnej grze.
