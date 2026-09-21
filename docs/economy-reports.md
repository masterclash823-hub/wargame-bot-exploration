# Budynki i bilans surowców

W **Panel → Gospodarka** są dwa nowe przyciski: **Zbudowane budynki**
i **Bilans surowców**. Są też dostępne w panelu zasobów.

- `/buildings owned`: wszystkie budynki w prowincjach własnego państwa,
  również koloniach. Każdy wpis zawiera nazwę budynku, zapisany poziom,
  nazwę prowincji i jej ID. Prowincje nieaktywne mają osobne oznaczenie.
  Lista obejmuje także budynki chwilowo nieprodukujące, a nie tylko
  zakłady uwzględnione w prognozie. Katalog budynków pozostaje oddzielny.
- `/economy income`: prognoza wszystkich surowców na następny miesiąc.
  Pokazuje produkcję po zużyciu materiałów, zmianę zapasu netto oraz
  przewidywany zapas po rozliczeniu. Bilans obejmuje żywność, psucie,
  miesięczne umowy, dostawy kompanii, inwestycje automatyczne, badania,
  programy algae i projekty. Złoto pokazuje zmianę skarbca po wszystkich
  wydatkach i spłacie zaległości. Nie należy sumować kolumny produkcji
  z bilansem: produkcja jest już częścią bilansu.

Bilans jest liczony przez ten sam mechanizm co rozliczenie miesiąca,
z zapasów sprzed wszystkich operacji do zapasów końcowych. Podgląd
nie przesuwa czasu i nie zmienia zapasów. Jest prognozą bieżących
ustawień, a nie obietnicą dochodu po późniejszych decyzjach gracza.

Raporty są prywatne, mają przyciski stron i pełny plik TXT do pobrania.
Zmiana właściciela państwa odbiera dostęp do dalszego przeglądania.
Zasoby ułamkowe (np. 0,05 algae) są widoczne; raport uwzględnia także
niestandardowe zasoby oraz zera dla surowców z katalogu budynków.
