# [Untertägige Preisveränderungen für Treibstoffe](https://tankzeit.de)

[Finden Sie die beste Zeit zum Tanken](https://tankzeit.de)

Dieses Repository beobachtet die Preisveränderungen innerhalb eines Tages für Diesel, E10 und E5 an allen deutschen Tankstellen.

Datenquelle: MTS-K via [Tankerkönig](https://www.tankerkoenig.de/).

Die berechneten Preisunterschiede und stündlichen Mittelwerte der Preise für jede Tankstelle werden per GitHub Actions erzeugt und in dieses Repository committed.

## Datenpipeline (Python)

Die tägliche Aktualisierung läuft via GitHub Actions und schreibt die Ergebnisse in dieses Repository.

Erforderlich:
- GitHub Secrets `TK_USER` und `TK_PASS` (Zugang zum Tankerkönig Data Repository)

Abbildung der MTS-K Tankstellen ID auf Ordnerstrukur aus

Beispiel OMV Bad Herrenalb (ID b4ed695f-2cfc-4688-8ecf-268b10cdb93e)

wird

[/b4ed695f/2cfc/4688/8ecf/268b10cdb93e/](https://huggingface.co/datasets/loffenauer/fuel-prices-germany/tree/main/data2/b4ed695f/2cfc/4688/8ecf/268b10cdb93e/) 

mit Daten der jeweiligen Tankstelle


* [Diesel](https://huggingface.co/datasets/loffenauer/fuel-prices-germany/resolve/main/data2/b4ed695f/2cfc/4688/8ecf/268b10cdb93e/diesel.csv)
* [E10](https://huggingface.co/datasets/loffenauer/fuel-prices-germany/resolve/main/data2/b4ed695f/2cfc/4688/8ecf/268b10cdb93e/e10.csv)
* [E5](https://huggingface.co/datasets/loffenauer/fuel-prices-germany/resolve/main/data2/b4ed695f/2cfc/4688/8ecf/268b10cdb93e/e5.csv)

In jedem Ordner finden sich auch JSON Dateien für 

* [Diesel](https://huggingface.co/datasets/loffenauer/fuel-prices-germany/resolve/main/data2/b4ed695f/2cfc/4688/8ecf/268b10cdb93e/diesel.json)
* [E10](https://huggingface.co/datasets/loffenauer/fuel-prices-germany/resolve/main/data2/b4ed695f/2cfc/4688/8ecf/268b10cdb93e/e10.json)
* [E5](https://huggingface.co/datasets/loffenauer/fuel-prices-germany/resolve/main/data2/b4ed695f/2cfc/4688/8ecf/268b10cdb93e/e5.json)
CSV und JSON sind inhaltlich gleich, aber unterschiedlich formatiert.

Jeweils gleichartiger Aufbau dieser Dateien für alle Tankstellen

* 2 Spalten: Uhrzeit, Preisdifferenz in Euro
* 25 Zeilen, Header, dann Stunde, Preisdifferenz

# Frequently Asked Questions (FAQ)

## Wozu? Weshalb? Warum?

Wer sparen will tankt zum richtigen Zeitpunkt. Siehe [Untertägige Preisveränderungen für Treibstoffe](https://tankzeit.de).

## Wie funktioniert das ?

Tankstellen sind gesetzlich verpflichtet ihre Preise an das Bundeskartellamt zu melden. Dieses Amt publiziert den Datensatz MTS-K auf umständlicher Weise. Tankerkönig veröffentlicht und archiviert diese Preise. Die Preise des letzten Tages werden von uns aufbereitet, um die Preisveränderungen nach Stunde darzustellen. Das macht das Script `scripts/generate_data.py`, welches Sie mit Python auf einem System Ihrer Wahl selbst ausführen und den eigenen Bedürfnissen anpassen können.

## Wer hat das gemacht ?

Bei Fragen kontaktieren Sie bitte Raphael Volz (raphael.volz@hs-pforzheim.de). Alle Anregungen sind willkommen.

## Welche Tankstellen gibt es denn in Deutschland und was ist deren MTS-K ID ?

[Eine Liste der Tankstellen und deren ID im Format JSON finden Sie hier](https://huggingface.co/datasets/loffenauer/fuel-prices-germany/resolve/main/data/stations.json), diese Liste entspricht meist der letzten [CSV Datei, die Tankerkönig in ihrem Repository publizieren](https://dev.azure.com/tankerkoenig/_git/tankerkoenig-data). [Sie können die Tankstellen auch alle auf einer Karte betrachten (ACHTUNG: Rechner wird schwitzen...)](https://rpubs.com/loffenauer/mts-k)
