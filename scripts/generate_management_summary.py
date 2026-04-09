#!/usr/bin/env python3
"""Build a compact management summary from existing data2 JSON files.

This is useful if data2/ already exists and you don't want to re-download
and recompute everything via generate_data.py.

Output: data2/YYYY/MM/DD/management_boxplots.json (snapshot for yesterday)
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd
import pytz
from tqdm import tqdm

try:
    from .generate_data import _brand_distribution_summary, _station_brand_table
    from .noon_reference import HISTOGRAM_BUCKET_MINUTES, build_noon_reference_histograms
except ImportError:  # pragma: no cover
    from generate_data import _brand_distribution_summary, _station_brand_table
    from noon_reference import HISTOGRAM_BUCKET_MINUTES, build_noon_reference_histograms

TZ = pytz.timezone("Europe/Berlin")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    data2 = root / "data2"
    if not data2.exists():
        raise SystemExit("data2/ not found. Run generate_data.py first.")

    fuels = ("diesel", "e10", "e5")
    mgmt_hourly_values: Dict[str, Dict[int, List[float]]] = {fuel: {} for fuel in fuels}
    station_counts_hourly: Dict[str, int] = {fuel: 0 for fuel in fuels}
    view_modes: Dict[str, str] = {fuel: "hourly" for fuel in fuels}

    for fuel in fuels:
        files = list(data2.rglob(f"{fuel}.json"))
        for path in tqdm(files, desc=f"Reading {fuel}", unit="file"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                cycle_rows = payload.get("cycle_hourly") or []
                if cycle_rows:
                    usable_cycle_rows = [
                        row
                        for row in cycle_rows
                        if row.get("delta_median") is not None and int(row.get("cycle_hour", -1)) >= 0
                    ]
                    if usable_cycle_rows:
                        view_modes[fuel] = "cycle"
                        station_counts_hourly[fuel] += 1
                        for row in usable_cycle_rows:
                            cycle_hour = int(row.get("cycle_hour"))
                            delta_value = row.get("delta_median")
                            if delta_value is None or pd.isna(delta_value):
                                continue
                            mgmt_hourly_values[fuel].setdefault(cycle_hour, []).append(float(delta_value))
                        continue
                if view_modes[fuel] == "cycle":
                    continue
                hourly = payload.get("hourly") or []
                if not hourly:
                    continue
                station_counts_hourly[fuel] += 1
                for row in hourly:
                    hour = int(row.get("hour"))
                    price = float(row.get("price"))
                    if 0 <= hour <= 23 and pd.notna(price):
                        mgmt_hourly_values[fuel].setdefault(hour, []).append(price)
            except Exception:
                continue

    snapshot_date = date.today() - timedelta(days=1)
    summary = {
        "snapshot_date": str(snapshot_date),
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "station_counts": {},
        "view_modes": {},
        "bucket_counts": {},
        "fuels": {},
        "brand_snapshot_label": "12:00-Referenz",
        "brand_snapshot_date": str(snapshot_date),
        "brand_snapshot_timestamp": TZ.localize(
            datetime.combine(snapshot_date, datetime.min.time()) + timedelta(hours=12)
        ).isoformat(timespec="minutes"),
        "brand_distributions": {fuel: [] for fuel in fuels},
        "noon_reference_bucket_minutes": HISTOGRAM_BUCKET_MINUTES,
        "noon_reference_histograms": {fuel: [] for fuel in fuels},
        "noon_reference_summaries": {
            fuel: {"stations": 0, "bucket_minutes": HISTOGRAM_BUCKET_MINUTES} for fuel in fuels
        },
    }

    for fuel in fuels:
        fuel_stats = []
        summary["view_modes"][fuel] = view_modes[fuel]
        summary["station_counts"][fuel] = station_counts_hourly[fuel]
        values_by_bucket = mgmt_hourly_values[fuel]
        bucket_count = max(
            values_by_bucket.keys(),
            default=(23 if view_modes[fuel] == "hourly" else 24),
        ) + 1
        summary["bucket_counts"][fuel] = bucket_count
        for hour in range(bucket_count):
            values = values_by_bucket.get(hour, [])
            clock_hour = (12 + hour) % 24
            if values:
                s = pd.Series(values, dtype="float64")
                base_row = {
                    "count": int(s.count()),
                    "min": float(s.min()),
                    "q1": float(s.quantile(0.25)),
                    "median": float(s.quantile(0.5)),
                    "q3": float(s.quantile(0.75)),
                    "max": float(s.max()),
                }
            else:
                base_row = {
                    "count": 0,
                    "min": 0.0,
                    "q1": 0.0,
                    "median": 0.0,
                    "q3": 0.0,
                    "max": 0.0,
                }
            if view_modes[fuel] == "cycle":
                row = {
                    "cycle_hour": hour,
                    "clock_hour": clock_hour,
                    "label": f"{clock_hour:02d}",
                    **base_row,
                }
            else:
                row = {
                    "hour": hour,
                    **base_row,
                }
            fuel_stats.append(row)
        summary["fuels"][fuel] = fuel_stats

    noon_csv = (
        root
        / "data2"
        / f"{snapshot_date:%Y}"
        / f"{snapshot_date:%m}"
        / f"{snapshot_date:%d}"
        / "noon.csv"
    )
    stations_json = root / "data" / "stations.json"
    if noon_csv.exists():
        noon_snapshot = pd.read_csv(noon_csv, dtype={"station_uuid": "string"})
        histograms, histogram_summaries = build_noon_reference_histograms(
            noon_snapshot,
            TZ,
            fuels=fuels,
            bucket_minutes=HISTOGRAM_BUCKET_MINUTES,
        )
        summary["noon_reference_histograms"] = histograms
        summary["noon_reference_summaries"] = histogram_summaries
        if stations_json.exists():
            stations = pd.DataFrame(json.loads(stations_json.read_text(encoding="utf-8")))
            station_brands = _station_brand_table(stations)
            summary["brand_distributions"] = {
                fuel: _brand_distribution_summary(noon_snapshot, station_brands, fuel) for fuel in fuels
            }

    out = (
        root
        / "data2"
        / f"{snapshot_date:%Y}"
        / f"{snapshot_date:%m}"
        / f"{snapshot_date:%d}"
        / "management_boxplots.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
