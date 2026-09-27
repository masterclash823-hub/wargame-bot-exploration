# Ilustracje eventów

Wyszukiwanie obrazków jest **wyłączone**, zarówno przy publikacji, jak i przez
`/event image`. Bot nie odpytuje wyszukiwarek ani nie generuje ilustracji przez AI.
Opcja `image_query` pozostaje dla zgodności, ale nie uruchamia wyszukiwania.

Event domyślnie publikuje się bez obrazka i bez ostrzeżenia o jego braku.
Nie wymaga wtedy uprawnienia Załączanie plików. Własny plik można dodać przez:

- `/event post event_id:12 file:obraz.png` — publikuje event z plikiem.
- `/event image event_id:12 file:obraz.png` — dodaje lub zastępuje ilustrację
  istniejącego eventu bez resetowania decyzji, historii lub efektów.
- `message_link` pozwala wskazać stary publiczny post. Bot sprawdza serwer,
  kanał, autora i treść przed edycją. Może użyć wcześniej zapisanego pliku.
- `include_image:False` pomija także ręcznie podany plik przy publikacji.

Plik JPG, PNG lub WebP może mieć do 6 MB i 12 mln pikseli. Bot sprawdza zawartość,
zapisuje go w bazie i wysyła jako dużą ilustrację pod tekstem, niezależnie od flagi.
Wysyłanie własnych plików wymaga uprawnienia Załączanie plików.

Stare ilustracje pozostają widoczne. `/event play`, kolejne decyzje i wznowienie
po restarcie korzystają z zapisanych obrazów bez kontaktowania się z wyszukiwarką.
