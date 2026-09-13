# Mariaże i polityka pracy

## Mariaż w sojuszu

Panel → Dyplomacja i bitwy → Traktaty i propozycje → aktywny sojusz →
Mariaż dynastyczny. Alternatywnie `/treaty marriage treaty_id`.

Każde państwo ma trzy umowne postacie dynastyczne: władcę/władczynię,
jednego dorosłego syna i jedną dorosłą córkę. `self` oznacza postać władcy,
nie gracza Discorda. Każda strona sojuszu może zaproponować parę przez dwie
listy wyboru. Odbiorca przegląda obie postacie i akceptuje lub odrzuca;
autor może wycofać propozycję. Samo wysłanie nie daje premii.

- Jeden oczekujący lub aktywny mariaż na sojusz.
- Każda postać może należeć tylko do jednego aktywnego mariażu. W razie
  konkurujących propozycji decyduje pierwsza poprawna akceptacja.
- Mariaż daje obu państwom +0,25 stabilności miesięcznie, łącznie najwyżej
  +0,5 ze wszystkich mariaży. Nie daje jednorazowego złota ani prestiżu.
- Zerwanie takiego sojuszu, wypowiedzenie wojny lub naruszenie rat kosztuje
  sprawcę łącznie 20 reputacji i 5 stabilności. Zwykłe zerwanie kosztuje 10 reputacji.
  Przy wojnie z kilkoma traktatami podstawowa kara nie jest dublowana.
- Wygaśnięcie sojuszu kończy umowną więź i zwalnia postacie bez kary.
  Zerwanie również kończy mariaż; zobowiązania finansowe traktatu pozostają.
- Propozycja wygasa po 6 miesiącach lub z końcem sojuszu. Przekazanie państwa
  anuluje oczekujące mariaże, zachowuje aktywne i odbiera uprawnienia staremu właścicielowi.
- Publiczny sojusz pozwala kronice opisać fakt zawarcia mariażu. Przy prywatnym
  nie powstaje publiczna aktywność. Każda strona ma wpis prywatnej historii.

## Niewolnictwo i emancypacja

Panel → Gospodarka → Polityka pracy lub `/economy labor`.
Przed zatwierdzeniem widać skutki, aktualny koszt i okres przejściowy.
Mechanika opisuje instytucję państwa: nie dodaje ludności, nie zmienia puli
40% pracowników ani nie tworzy ludzi jako surowca do kupowania. Ręczne
przydziały pracowników zachowują swoje działanie.

| Polityka | Produkcja | Koszt miesięczny | Skutki społeczne miesięcznie |
| --- | --- | --- | --- |
| Wolna praca (domyślna) | Bez zmian | Brak | Brak dodatkowych zmian |
| Niewolnictwo | +10% w wybranych budynkach | 1 złota / 1000 mieszkańców | +2 niepokojów, −0,5 stabilności, −1 reputacji |
| Pierwsze 3 miesiące po zniesieniu | −5% w tych samych budynkach | Brak nadzoru | Brak kar niewolnictwa |

Premia i okres przejściowy dotyczą farm, pastwisk, plantacji, obozów drwali,
kopalń zwykłych i miedzi, glinianek i smolarni. Mnożą dodatnią produkcję
po uwzględnieniu badań. Nie obejmują bazowych surowców terenu, algae,
uniwersytetów, podatków ani manufaktur. Zatrudnienie, stabilność i etap kolonii
nadal ograniczają produkcję. Nadzór wlicza się w utrzymanie i może tworzyć zaległości.

Wprowadzenie kosztuje 50 złota, 5 stabilności i 10 reputacji.
Zniesienie kosztuje 20 złota na 1000 mieszkańców własnych aktywnych prowincji.
Koszty nadzoru i comiesięczne kary kończą się od razu. Przejściowy spadek
produkcji działa podczas trzech następnych rozliczeń. Nie ma nagrody za
powtarzanie reform. Pokazana wycena jest ponownie sprawdzana przy kliknięciu;
zmiana populacji wymagająca innej kwoty oznacza potrzebę ponownego przeglądu.

Niepokoje z niewolnictwa sumują się ze skutkami podatków; przy normalnych
podatkach i dodatnich niepokojach zmiana netto wynosi zwykle +1/miesiąc.
Stabilność może być równocześnie zmieniana przez żywność, luksusy i mariaże.

## Zapisy, języki i testy

Migracja dodaje dwie tabele i indeks; nie przypisuje istniejącym państwom
niewolnictwa ani mariaży i nie wymaga nowych zmiennych Rendera.
Oba interfejsy są PL/EN, nazwy komend pozostają angielskie. Eventy otrzymują
faktyczny stan instytucji i aktywnych więzi; sam opis AI nie zmienia zasad.
Publiczne reformy i publiczne mariaże mogą trafić do codziennej kroniki.

Prognoza i faktyczne rozliczenie korzystają ze wspólnego silnika, a prognoza
wycofuje także reputację, zakończenia mariaży i postęp emancypacji.
Testy obejmują zgody, konflikty postaci, współbieżność, koszty, transfer państwa,
wygaśnięcie, prywatność, okres przejściowy, ręcznych pracowników, algae i interfejsy.
