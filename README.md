# Untertägige Preisveränderungen für Treibstoffe

Berechnung der Preisveränderungen innerhalb eines Tages für Diesel, E10 und E5 an allen deutschen Tankstellen.

Datenquelle: MTS-K via [Tankerkönig](https://www.tankerkoenig.de/).

Die berechneten Preisunterschiede für jede Tankstelle finden sich im Ordner data. 

Abbildung der MTS-K Tankstellen ID auf Ordnerstrukur aus

0d58c4ba-3267-404a-89d6-6ba15a8fc422

wird

[0d58c4ba/3267/404a/89d6/6ba15a8fc422](https://www.volzinnovation.com/fuel_price_variations_germany/data/0d58c4ba/3267/404a/89d6/6ba15a8fc422/) 

mit Daten der jeweiligen Tankstelle

In jedem Ordner finden sich drei CSV Dateien für

* [Diesel](https://www.volzinnovation.com/fuel_price_variations_germany/data/0d58c4ba/3267/404a/89d6/6ba15a8fc422/diesel.csv)
* [E10](https://www.volzinnovation.com/fuel_price_variations_germany/data/0d58c4ba/3267/404a/89d6/6ba15a8fc422/e10.csv)
* [E5](https://www.volzinnovation.com/fuel_price_variations_germany/data/0d58c4ba/3267/404a/89d6/6ba15a8fc422/e5.csv)

In jedem Ordner finden sich auch JSON Dateien für 

* [Diesel](https://www.volzinnovation.com/fuel_price_variations_germany/data/0d58c4ba/3267/404a/89d6/6ba15a8fc422/diesel.json)
* [E10](https://www.volzinnovation.com/fuel_price_variations_germany/data/0d58c4ba/3267/404a/89d6/6ba15a8fc422/e10.json)
* [E5](https://www.volzinnovation.com/fuel_price_variations_germany/data/0d58c4ba/3267/404a/89d6/6ba15a8fc422/e5.json)

CSV und JSON sind inhaltlich gleich, aber unterschiedlich formatiert.

Jeweils gleichartiger Aufbau dieser Dateien für alle Tankstellen

* 2 Spalten: Uhrzeit, Preisdifferenz in Euro
* 25 Zeilen, Header, dann Stunde, Preisdifferenz

# Frequently Asked Questions (FAQ)

## Wozu? Weshalb? Warum?

Wer sparen will tankt zum richtigen Zeitpunkt.

## Wie funktioniert das ?

Tankstellen sind gesetzlich verpflichtet ihre Preise an das Bundeskartellamt zu melden. Dieses Amt publiziert den Datensatz MTS-K auf umständlicher Weise. Tankerkönig veröffentlicht und archiviert diese Preise. Die Preise des letzten Tages werden von uns aufbereitet, um die Preisveränderungen nach Stunde darzustellen. Das macht das Script [calculation.r](https://github.com/volzinnovation/fuel_price_variations_germany/blob/master/calculation.r), welches sie mit der freien Software R auf einem System ihrer Wahl selbst ausführen und den eigenen Bedürfnissen anpassen können.

## Wer hat das gemacht ?

Bei Fragen kontaktieren Sie bitte Raphael Volz (rv@volzinnovation.com). Alle Anregungen sind willkommen.

## Welche Tankstellen gibt es denn in Deutschland und was ist deren MTS-K ID ?

[Eine Liste der Tankstellen und deren ID im Format JSON finden Sie hier](https://www.volzinnovation.com/fuel_price_variations_germany/data/stations.json), diese Liste entspricht meist der letzten [CSV Datei, die Tankerkönig in ihrem Repository publizieren](https://dev.azure.com/tankerkoenig/_git/tankerkoenig-data). [Sie können die Tankstellen auch alle auf einer Karte betrachten (ACHTUNG: Rechner wird schwitzen...)](https://rpubs.com/loffenauer/mts-k)
