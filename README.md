# Untertägige Preisveränderungen
Berechnung der Preisveränderungen innerhalb eines Tages für Diesel, E10 und E5 an allen deutschen Tankstellen.

Datenquelle: MTS-K via [Tankerkönig](https://www.tankerkoenig.de/).

Die berechneten Preisunterschiede für jede Tankstelle finden sich im Ordner data. 

Abbildung der MTS-K Tankstellen ID auf Ordnerstrukur aus

0d58c4ba-3267-404a-89d6-6ba15a8fc422

wird

0d58c4ba/3267/404a/89d6/6ba15a8fc422

mit Daten der jeweiligen Tankstelle.

In jedem Ordner finden sich drei CSV Dateien für

* Diesel
* E10
* E5

Gleichartiger Aufbau dieser Dateien

* 2 Spalten: Uhrzet, Preisdifferenz in Euro
* 25 Zeilen, Header, dann Stunde, Preisdifferenz
