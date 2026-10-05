# Mapa, bonusy startowe i ocena projektów

## Udostępnianie mapy

Mapa jest domyślnie dostępna tylko dla GM-a. **Panel administratora → Mapa
Azgaara → Dostęp graczy do mapy** lub `/admin map_access enabled:true` włącza
graczom `/map` i przycisk **Mapa świata**. Ustawienie jest osobne dla serwera,
zapisane w bazie i sprawdzane przy każdym pobraniu, również po wygenerowaniu pliku.

Gracz dostaje prywatny plik `.map` z aktualnymi granicami, kulturami i religiami;
otwiera go w Azgaarze przez Load → Machine. To pełna mapa, bez mgły wojny.
Bot nie dodaje do niej prywatnych planów ani swoich danych skarbca/wojsk.
GM musi wcześniej zaimportować mapę z oryginalnym projektem `.map`.
`enabled:false` blokuje kolejne pobrania; nie usuwa już pobranych plików.

## Kreator startu

Po `/nation found` gracz wybiera **Bonusy startowe** lub `/nation bonuses`.
Pierwsza lista wybiera kategorię, druga poziom. Podgląd pokazuje sześć kategorii,
wydane i pozostałe punkty oraz rzeczywiste korzyści. Przeglądanie edytuje tę
samą wiadomość. **Zapisz wybór** jest dostępne po rozdaniu całego budżetu.
Początkowa propozycja jest zrównoważona, ale gracz może ją dowolnie zmienić.

| Kategoria | Zakres punktów | Efekt |
| --- | --- | --- |
| Surowce | 0–10 | 50% + 10% pakietu za punkt, całkowite ilości |
| Gotówka | 0–10 | 200 + 60 złota za punkt |
| Wielkość | 1–10 | Dokładnie tyle prowincji startowych, po 1 punkcie każda |
| Kultura | 0–10 | Ekspansja 0,5 + 0,15 za punkt |
| Religia | 0–10 | Ekspansja 0,5 + 0,15 za punkt |
| Technologia | 0–10 | Każda z czterech dziedzin: 2,5 + 0,1 za punkt |

Pakiet bazowy (100%): żywność 200, drewno 150, kamień 100, żelazo 80,
miedź 40, węgiel 40, glina 60, tkanina 30, smoła 30, proch 20, konie 10,
przyprawy 10 i jedwab 5. Punkty surowców dają wyłącznie jednorazowy zapas,
a technologia nie odblokowuje automatycznie nazwanych projektów badawczych.

Własna kultura i religia powstają na podstawie tożsamości stolicy i są przypisane
do prowincji startowych. Nie zmieniają wspólnej kultury/religii innych państw.
Siła jest parametrem ekspansji mapy Azgaara, nie premią bojową ani automatycznym
przekazywaniem cudzej ziemi. Rozmiar zmieniony w kreatorze wymaga dopasowania
listy ID przez edycję `/nation application`; maksymalnie 10 połączonych wolnych
pól lądowych, pierwsze to stolica. Po starcie działa zwykła ekspansja państwa.

GM zmienia budżet przez **Państwa i coop → Budżet startowy** lub
`/nation start_budget points:35` (1–60). Dotyczy nowych i oczekujących
zgłoszeń; nie rozdaje ponownie zasobów istniejącym państwom. Zapis bonusów
unieważnia wcześniejszy podgląd GM-a. Przed akceptacją bot ponownie sprawdza
budżet, liczbę i dostępność prowincji. Długą historię można pobrać przyciskiem
**Pełna historia**. Akceptacja nadaje wszystkie korzyści w jednej transakcji.

## Osobne AI projektów

Dodaj w zmiennych środowiskowych hostingu **PROJECT_AI_API_KEY** z osobnego
projektu Google AI Studio. Limity Gemini są na projekt, więc drugi klucz w
tym samym projekcie nie oddzieli zużycia od eventów. Nie wpisuj klucza w czacie
ani na Discordzie. Opcjonalny **PROJECT_AI_MODEL** domyślnie ma wartość
`gemini-2.5-flash-lite`. Bot nigdy nie użyje tu kluczy eventów lub walki jako
zastępstwa. Bez konfiguracji nadal działa dotychczasowa ręczna ocena GM-a.

`/project review [project_id]`, **Panel → Gospodarka → Ocena projektu AI**
oraz przycisk w panelu GM-a pokazują ocenę zgłoszonego projektu. Gracz ma dostęp
tylko do własnych projektów, GM do wszystkich. Powtórne otwarcie tej samej oceny
odczytuje bazę, także po restarcie. Zmieniony projekt wymaga nowej oceny.

Twarde limity: jeden zasób (żywność, drewno, kamień lub glina), 1–2 jednostki
na miesiąc; minimum 400 złota za jednostkę produkcji i nie mniej niż zgłoszony
budżet; budowa 6–24 miesiące, koszt do 10000 złota. AI może zalecić poprawki
lub odrzucenie. Nie przyznaje technologii, stabilności, wojska, prestiżu,
złota ani algae. Maksymalnie trzy projekty zatwierdzone rekomendacją AI na
państwo; dalsze wymagają ręcznej oceny. Zatwierdza dopiero GM przyciskiem;
gracz następnie płaci przez `/project build`, a efekt działa po budowie.

Limit ochronny: 20 nowych ocen dziennie (UTC) na bota, do dwóch jednocześnie,
jedno żądanie na ocenę, 30 s limitu czasu, do 1024 tokenów odpowiedzi.
Błąd nie przyznaje efektów; bot robi przerwę, respektuje Retry-After i nie
przerzuca żądania do innego dostawcy. Treść projektu jest niezaufanym opisem,
a wynik AI przechodzi walidację dozwolonych premii przed zapisaniem.

## Mniej ruchu do Discorda

Bot zapamiętuje skrót zsynchronizowanych komend osobno dla aplikacji i serwera.
Restart lub ponowne połączenie bez zmian nie wysyła ponownie całego drzewa.
Usunięcie dawnych duplikatów globalnych odbywa się raz. Błędne synchronizacje
mają 15 minut przerwy, a niekompletne ładowanie modułów nie usuwa komend.
Awaryjnie ustaw `COMMAND_SYNC_FORCE=1` na jeden start, następnie usuń ustawienie.

Powiadomienia eventów i badań korzystają z pamięci użytkowników. Odpowiedź 403
przy DM wstrzymuje kolejne takie powiadomienia do tej osoby przez godzinę;
panel nadal działa. Nie ma ręcznego omijania ograniczeń Discorda — standardowa
obsługa limitów biblioteki discord.py pozostaje aktywna. To ogranicza zbędny
ruch; nie gwarantuje braku limitów przy dowolnym obciążeniu lub błędach uprawnień.

## Las i żywność

Terraformacja obejmuje wycinkę lasów umiarkowanych, tropikalnych oraz tajgi,
z podglądem kosztów, czasu i skutków. [Tabela](terraforming.md).
Leśny budynek produkujący żywność to **Plantacja**. Jego standardowa produkcja
bazowa wzrasta z 6 do **7** (6 × 1,10 = 6,6, zaokrąglone). Przyprawy pozostają
bez zmian. Migracja działa raz i zachowuje niestandardowe definicje GM-a.
Pozostałe modyfikatory gospodarki nadal działają normalnie.

Nowe tabele tworzy `db.init_db()`, a komendy synchronizują się po zmianie ich
treści. Nie są wymagane nowe uprawnienia bota na serwerze.
