# Lista i zbiorcze przygotowanie eventów

`/event list` pokazuje do 15 wydarzeń na stronie, bez flag i miniaturek.
Wpis zawiera status, ID, państwo, datę i krótki fragment tekstu. Przyciski
pozwalają przeglądać również starsze wydarzenia. Bardzo długie nazwy i teksty
mogą zmniejszyć liczbę wpisów, aby zmieścić się w limitach Discorda.

GM widzi wszystkie wydarzenia, także szkice. Gracz domyślnie widzi wydarzenia
własnego państwa; podanie innego państwa pokazuje tylko jego opublikowane,
publiczne wydarzenia. Prywatne eventy widzą tylko właściciel państwa i GM.

## `/event all [description]` — tylko GM

Komenda przygotowuje po jednym brakującym szkicu dla każdego grywalnego
państwa. Opcjonalny opis (do 1000 znaków) określa wspólny temat, np.
„Długa zima utrudnia handel i zaopatrzenie”. Każdy szkic uwzględnia osobno
historię i sytuację państwa oraz język jego właściciela.

- Istniejące szkice zostają zachowane. Komenda nie tworzy obok nich kolejnych.
- Ruiny są pomijane. Państwa w trakcie trzymiesięcznego rozpadu nadal mogą otrzymać szkic.
- Gotowe szkice zapisują się na bieżąco. Błąd AI dla jednego państwa nie blokuje pozostałych.
- Podsumowanie pokazuje nowe i zachowane ID oraz państwa, dla których generowanie się nie udało.
- Ponowienie komendy uzupełnia brakujące szkice. Jeśli chcesz kolejny event dla państwa mającego szkic, najpierw go opublikuj lub użyj `/event generate`.

Przygotowanie szkiców nie wysyła wiadomości graczom i nie zmienia statystyk.
GM sprawdza wynik w `/event list`, edytuje przez `/event edit` i `/event effects`,
a następnie publikuje każdy event przez `/event post`, wybierając widoczność.

Duża partia może trwać dłużej niż ważność interakcji Discorda. Zapisane szkice
pozostają dostępne w `/event list`, także po restarcie bota. Po przerwaniu
generowania można ponownie użyć `/event all`.
