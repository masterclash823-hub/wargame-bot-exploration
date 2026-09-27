# Mapa między botem a Azgaarem

## Pierwsze wgranie

1. Otwórz **tę samą mapę, na której toczy się gra**, w Azgaarze.
2. Zapisz projekt przez **Save → Machine** (`.map`). Bez zmieniania mapy wybierz także **Export → JSON → Full Data**.
3. GM uruchamia `/admin map_import file:pełny.json map_file:projekt.map` i potwierdza podsumowanie.

Bot zapisuje oba pliki w bazie. Są dostępne także po ponownym uruchomieniu na Renderze. JSON dostarcza pełną geometrię i sąsiedztwo pól; projekt zachowuje grafikę, ustawienia, rzeki, znaczniki i pozostałe dane potrzebne do otwarcia mapy w Azgaarze. Sam JSON nie jest projektem otwieranym przez Load.

Jeżeli na początku wgrasz tylko Full Data JSON, później możesz dodać projekt przez `/admin map_export file:projekt.map`. Bot go zapamięta. Samodzielny `.map` można importować **po pierwszym wgraniu zgodnego Full Data JSON**: format `.map` nie zapisuje bezpośrednio geometrii i sąsiedztwa spakowanych pól.

## Państwa, kultury i religie

- `/admin map_entities kind:states` pokazuje ID i powiązania państw mapy. `kind:cultures` i `kind:religions` pokazują pozostałe listy. Dłuższe listy mają parametr `page`.
- Przy pierwszym imporcie bot rozpoznaje istniejące państwo gry po jednoznacznie zgodnej nazwie lub pełnej nazwie. Później powiązania opierają się na zapisanych ID.
- Pozostałe państwa mapy czekają na powiązanie. Nie otrzymują sztucznych kont Discorda i nie stają się automatycznie grywalnymi państwami.
- GM tworzy państwo dla gracza przez dotychczasowe `/nation found`, a następnie łączy je komendą `/admin map_bind state_id:ID nation:nazwa`. Powiązanie przyznaje jego wolne pola lądowe, zachowując pola zajęte już przez inne państwa. Istniejące państwo można powiązać tą samą komendą.
- Powiązane państwo dalej obsługuje `/nation transfer`, coop, gospodarkę i pozostałe mechaniki bota. Import nie zmienia właściciela Discord ani współgraczy.
- Nowe państwa utworzone tylko w bocie otrzymują trwałe ID przy eksporcie. Przed pierwszym eksportem powiąż je z istniejącym państwem mapy, jeżeli ma to być ten sam kraj.
- Kultura i religia są zapisane **dla każdego pola**, a nie tylko całego państwa. Nazwy i metadane (kolory, bóstwa, pochodzenie itd.) są zachowywane.
- `/province info cell_id:ID` pokazuje kulturę i religię. GM może je zmienić przez `/province identity cell_id:ID culture_id:ID religion_id:ID`; pominięty parametr pozostaje bez zmian, ID 0 oznacza brak przypisania.

Kultury i religie są na razie danymi mapy: ten PR nie dodaje bonusów gospodarczych, nawracania ani asymilacji.

## Bot → Azgaar

Uruchom `/admin map_export`, pobierz `wargame.map` i otwórz go w Azgaarze przez **Load → Machine**.

Eksport obejmuje aktualne granice państw gry, nazwy państw, kultury i religie pól. Aktualizuje przypisania miast i stolic oraz jednostek administracyjnych Azgaara do państw. Jeżeli nowa granica przecina jednostkę administracyjną, dzieli ją pomiędzy nowe państwa. Zachowuje geografię i oryginalny wygląd projektu. Państwa mapy jeszcze niepowiązane z grą pozostają na mapie; usunięte państwo gry nie jest wskrzeszane przez ponowny eksport.

`/admin map_export format:json` zwraca Full Data JSON. Dane skarbca, zasobów, wojsk i prywatnych planów **z bazy bota** nie są dodawane do żadnego eksportu. Pliki mapy mogą oczywiście nadal zawierać opisy i dane zapisane wcześniej w samym Azgaarze.

## Azgaar → bot po edycji

Po zmianach politycznych, kultur lub religii zapisz projekt i użyj `/admin map_resync file:projekt.map`. Możesz też wgrać ponownie Full Data JSON i pasujący projekt `.map`.

Domyślnie granice istniejących prowincji gry pozostają zachowane. Aby świadomie zastąpić je granicami z Azgaara, dodaj **`sync_owners:true`** i potwierdź podsumowanie. Pola niepowiązanych państw staną się wtedy nieprzypisane w grze. Dlatego przed takim importem powiąż państwa przez `map_bind`.

Każdy import aktualizuje kultury i religie. Istniejące budynki, fortyfikacje, populacja, biom, naturalne zasoby, skarbiec i postęp gry pozostają zachowane. Nowe pola lądowe otrzymują dotychczasowe uśrednianie populacji gry; pola wodne mają 0 mieszkańców. Populacja mapy Azgaara i populacja gospodarki bota używają różnych skal — eksport zachowuje populację Azgaara.

Import wykonuje się w jednej transakcji: błędny plik nie pozostawia połowy zmian. Przycisk potwierdzenia ponownie sprawdza uprawnienia GM i nie pozwala wykonać importu drugi raz.

## Panel i zgodność

W `/admin panel` wybierz **Mapa Azgaara**: instrukcja importu, eksport projektu, listy państw/kultur/religii, powiązanie wybranego państwa oraz zmiana kultury i religii wybranej prowincji.

- Obsługiwane: Full Data JSON z listą pól lub starszymi tablicami równoległymi oraz natywne zapisy `.map` (tekst, gzip, starsze base64). JSON Minimal, Grid Cells i obrazy nie zastępują Full Data.
- Limit wejścia: 24 MB także po rozpakowaniu, do 200 000 pól. Wyjściowy plik jest kompresowany, jeśli wymaga tego limit załączników Discorda.
- Geometria musi pozostać ta sama. Bot sprawdza identyfikator świata, współrzędne i układ pól, a przy projekcie `.map` także siatkę i wysokości. Zmiana mapy, rozdzielczości, wysokości powodująca przebudowę pól lub wygenerowanie nowego świata wymaga osobnej migracji gry — nie wolno przypisywać starych budynków do ponownie użytych numerów pól.
- Projekty z niestandardową geometrią `GraphOverride` nie są obecnie obsługiwane w wymianie `.map`; bot zgłasza to przed zmianą danych.
- Przy pierwszym imporcie do starszej gry bot nie ma wcześniejszej geometrii do porównania. GM musi dostarczyć mapę, z której pochodzą istniejące ID prowincji.

Format sprawdzony na oficjalnych źródłach Azgaara z 27.09.2026:
[save.ts](https://github.com/Azgaar/Fantasy-Map-Generator/blob/master/src/services/io/save.ts),
[load.ts](https://github.com/Azgaar/Fantasy-Map-Generator/blob/master/src/services/io/load.ts),
[export-json.ts](https://github.com/Azgaar/Fantasy-Map-Generator/blob/master/src/services/io/export-json.ts).

Migracja dodaje cztery tabele `azgaar_*` przy starcie bota, bez resetowania gry i bez nowych zależności. Testy obejmują pełny cykl wymiany, polityczne granice i stolice, kultury/religie, trwałe ID, cofanie błędnych importów, uprawnienia, istniejącą gospodarkę oraz odczyt rzeczywistego `start1809.map` w wersji 1.153.1.
