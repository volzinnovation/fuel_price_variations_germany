#!/usr/bin/env python3
"""Generate compact heatmap aggregates from dated noon snapshots."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import DefaultDict, Iterable


FUELS: tuple[str, ...] = ("diesel", "e10", "e5")
DEFAULT_START_DATE = "2026-04-01"


def normalize_location(value: object) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def load_station_locations(stations_json: Path) -> tuple[dict[str, str], dict[str, str]]:
    stations = json.loads(stations_json.read_text(encoding="utf-8"))
    labels_by_key: dict[str, Counter[str]] = defaultdict(Counter)
    station_location: dict[str, str] = {}

    for station in stations:
        station_id = str(station.get("uuid") or "").strip()
        city_label = " ".join(str(station.get("city") or "").strip().split())
        location_key = normalize_location(city_label)
        if not station_id or not location_key:
            continue
        station_location[station_id] = location_key
        labels_by_key[location_key][city_label] += 1

    location_labels = {
        key: labels.most_common(1)[0][0]
        for key, labels in labels_by_key.items()
        if labels
    }
    return station_location, location_labels


def iter_noon_snapshot_paths(data_root: Path) -> Iterable[Path]:
    yield from sorted(data_root.glob("20[0-9][0-9]/*/*/noon.csv"))


def date_from_noon_path(data_root: Path, path: Path) -> str | None:
    try:
        relative = path.relative_to(data_root)
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 4:
        return None
    year, month, day = parts[:3]
    return f"{year}-{month}-{day}"


def summarize_prices(values_by_date: dict[str, list[float]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for date_label in sorted(values_by_date):
        values = values_by_date[date_label]
        if not values:
            continue
        rows.append(
            {
                "date": date_label,
                "price": round(float(statistics.median(values)), 3),
                "count": len(values),
            }
        )
    return rows


def summarize_price_series(
    values_by_date: dict[str, list[float]],
    dates: list[str],
) -> dict[str, list[float | int | None]]:
    prices: list[float | None] = []
    counts: list[int] = []
    for date_label in dates:
        values = values_by_date.get(date_label, [])
        if values:
            prices.append(round(float(statistics.median(values)), 3))
            counts.append(len(values))
        else:
            prices.append(None)
            counts.append(0)
    return {"prices": prices, "counts": counts}


def parse_price(row: dict[str, str], fuel: str) -> float | None:
    try:
        price = float(row.get(fuel) or "")
    except ValueError:
        return None
    return price if price > 0 else None


def generate_heatmap_summary(
    output_root: Path,
    fuels: tuple[str, ...] = FUELS,
    start_date: str = DEFAULT_START_DATE,
) -> list[Path]:
    data_root = output_root / "data2"
    station_location, location_labels = load_station_locations(output_root / "data" / "stations.json")
    overall_values: dict[str, DefaultDict[str, list[float]]] = {
        fuel: defaultdict(list) for fuel in fuels
    }
    location_values: dict[str, DefaultDict[str, DefaultDict[str, list[float]]]] = {
        fuel: defaultdict(lambda: defaultdict(list)) for fuel in fuels
    }
    stations_by_location: dict[str, DefaultDict[str, set[str]]] = {
        fuel: defaultdict(set) for fuel in fuels
    }

    for noon_path in iter_noon_snapshot_paths(data_root):
        date_label = date_from_noon_path(data_root, noon_path)
        if not date_label:
            continue
        if date_label < start_date:
            continue
        with noon_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                station_id = str(row.get("station_uuid") or "").strip()
                location_key = station_location.get(station_id)
                if not station_id or not location_key:
                    continue
                for fuel in fuels:
                    price = parse_price(row, fuel)
                    if price is None:
                        continue
                    overall_values[fuel][date_label].append(price)
                    location_values[fuel][location_key][date_label].append(price)
                    stations_by_location[fuel][location_key].add(station_id)

    out_dir = data_root / "heatmaps"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for fuel in fuels:
        dates = sorted(overall_values[fuel])
        locations: list[dict[str, object]] = []
        for location_key, values_by_date in location_values[fuel].items():
            if not any(values_by_date.values()):
                continue
            station_ids = sorted(stations_by_location[fuel].get(location_key, set()))
            locations.append(
                {
                    "key": location_key,
                    "label": location_labels.get(location_key, location_key),
                    "station_count": len(station_ids),
                    "station_ids": station_ids,
                    **summarize_price_series(values_by_date, dates),
                }
            )
        locations.sort(key=lambda item: (str(item["label"]).casefold(), str(item["key"])))
        summary = {
            "fuel": fuel,
            "metric": "median",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dates": dates,
            "overall": {
                "label": "Gesamtuebersicht",
                **summarize_price_series(overall_values[fuel], dates),
            },
            "locations": locations,
        }
        out_path = out_dir / f"{fuel}.json"
        out_path.write_text(json.dumps(summary, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        written.append(out_path)

    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing data/ and data2/.",
    )
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START_DATE,
        help=f"First local snapshot day to include. Defaults to {DEFAULT_START_DATE}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    written = generate_heatmap_summary(args.output_root, start_date=args.start_date)
    for path in written:
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
