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

Jeweils gleichartiger Aufbau dieser Dateien für alle Tankstellen

* 2 Spalten: Uhrzet, Preisdifferenz in Euro
* 25 Zeilen, Header, dann Stunde, Preisdifferenz

# Frequently Asked Questions (FAQ)

## Wozu? Weshalb? Warum?

Wer sparen will tankt zum richtigen Zeitpunkt

## Wie funktioniert das ?

Tankstellen sind gesetzlich verpflichtet ihre Preise an das Bundeskartellamt zu melden. Dieses Amt publiziert den Datensatz MTS-K auf umständlicher Weise. Tankerkönig veröffentlicht und archiviert diese Preise. Die Preise des letzten Tages werden von uns aufbereitet, um die Preisveränderungen nach Stunde aufzubereiten. Das macht das Script calcuation.r, welches sie in diesem Repository finden und mit der freien Software R auf einem System ihrer Wahl ausführen können.

## Wer hat das gemacht ?

Bei Fragen kontaktieren Sie bitte Raphael Volz (rv@volzinnovation.com). Alle Anregungen sind willkommen

