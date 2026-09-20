# Cele państwowe

Gracz otwiera **Panel → Cele państwowe** lub `/goals status`.
Budowa, ulepszenia i ukończone badania są widoczne od razu po odświeżeniu.
Automatyczne przyznanie nagrody następuje przy rozliczeniu miesiąca,
po spełnieniu warunków i upływie wymaganych trzech miesięcy gry.

Cel żywnościowy liczy zapasy po rozliczeniu dostaw zagranicznych kompanii.
Wymaga trzech kolejnych miesięcy bez głodu, zapasu na dwa miesiące i
stabilności co najmniej 60. Pominięty miesiąc przerywa serię.

## Obsługa GM

- `/goals create nation:… title:… description:…` tworzy własny cel opisowy.
  Tytuł ma do 80 znaków, warunki do 1500. Cel pojawia się w panelu właściciela.
- `/goals status nation:…` pokazuje cel wybranego państwa, ID i przycisk
  **Potwierdź wykonanie (+10 prestiżu)**.
- `/goals complete nation:… goal_id:…` otwiera ten sam podgląd do potwierdzenia.
  Dopiero kliknięcie przyznaje nagrodę. GM może zatwierdzić zarówno cel
  opisowy, jak i standardowy, niezależnie od automatycznego minimum czasu.

Państwo ma jeden aktywny cel naraz. Gracz może porzucić cel bez kary przed
przyznaniem kolejnego. Cel opisowy nie kończy się automatycznie po wykonaniu
przypadkowego badania: zawsze wymaga potwierdzenia GM.

Nagroda wynosi 10 prestiżu i jest przyznawana dokładnie raz. Równoczesne
kliknięcia i miesięczne rozliczenie nie powielają nagrody. Stary przycisk
nie zatwierdzi nowego celu. Każde potwierdzenie ponownie sprawdza rolę GM,
a historia zapisuje ID celu, miesiąc oraz osobę zatwierdzającą.

Cele pozostają przy państwie po zmianie właściciela. Dane dawnych celów
są zachowane; ta część aktualizacji nie wymaga migracji tabel.
