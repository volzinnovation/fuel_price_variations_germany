#!/usr/bin/env python3
"""Generate a dated management_boxplots.json from raw historical price data."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd
import pytz
from tqdm import tqdm

try:
    from .generate_data import (
        TZ,
        DateRange,
        _latest_noon_cycle_days,
        _brand_distribution_summary,
        _hourly_variation,
        _load_midnight_reference_prices,
        _latest_price_snapshot,
        _load_noon_reference_prices,
        _noon_to_noon_markdown_profile,
        _load_prices,
        _local_dt,
        _parse_dates_utc,
        _station_brand_table,
        download_stations,
    )
    from .noon_outputs import build_noon_snapshot
    from .noon_reference import HISTOGRAM_BUCKET_MINUTES, build_noon_reference_histograms
except ImportError:  # pragma: no cover
    from generate_data import (
        TZ,
        DateRange,
        _latest_noon_cycle_days,
        _brand_distribution_summary,
        _hourly_variation,
        _load_midnight_reference_prices,
        _latest_price_snapshot,
        _load_noon_reference_prices,
        _noon_to_noon_markdown_profile,
        _load_prices,
        _local_dt,
        _parse_dates_utc,
        _station_brand_table,
        download_stations,
    )
    from noon_outputs import build_noon_snapshot
    from noon_reference import HISTOGRAM_BUCKET_MINUTES, build_noon_reference_histograms


FUELS: tuple[str, ...] = ("diesel", "e10", "e5")
DEFAULT_ANALYSIS_DAYS = 2


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def _management_summary_path(root: Path, target_day: date) -> Path:
    return (
        root
        / "data2"
        / f"{target_day:%Y}"
        / f"{target_day:%m}"
        / f"{target_day:%d}"
        / "management_boxplots.json"
    )


def generate_historical_management_boxplots(
    output_root: Path,
    target_day: date,
    analysis_days_count: int = DEFAULT_ANALYSIS_DAYS,
) -> Path:
    if analysis_days_count < 2:
        raise SystemExit("--analysis-days must be at least 2.")

    analysis_end = target_day
    analysis_start = target_day - timedelta(days=analysis_days_count - 1)
    data_start = analysis_start - timedelta(days=1)
    analysis_days = [
        analysis_start + timedelta(days=offset)
        for offset in range((analysis_end - analysis_start).days + 1)
    ]

    with tempfile.TemporaryDirectory(prefix="tankzeit-management-") as tmpdir:
        stations_path = Path(tmpdir) / "stations.json"
        stations_frame = download_stations(stations_path, analysis_end)

    data = _load_prices(DateRange(data_start, analysis_end))
    print(f"Loaded {len(data):,} price rows.")
    station_ids = (
        stations_frame["station_uuid"].dropna().astype(str).sort_values().unique().tolist()
        if "station_uuid" in stations_frame.columns
        else stations_frame.iloc[:, 0].dropna().astype(str).sort_values().unique().tolist()
    )
    noon_snapshot = build_noon_snapshot(data, station_ids, analysis_end, TZ, fuels=FUELS)

    window_start = datetime.combine(analysis_start, datetime.min.time())
    window_end = datetime.combine(analysis_end, datetime.max.time())
    noon_reference_prices = _load_noon_reference_prices(
        output_root,
        FUELS,
        analysis_days,
    )
    midnight_reference_prices = _load_midnight_reference_prices(output_root, FUELS)

    management_cycle_days = _latest_noon_cycle_days(analysis_end)
    mgmt_hourly_values: Dict[str, Dict[int, List[float]]] = {fuel: {} for fuel in FUELS}
    mgmt_cycle_values: Dict[str, Dict[int, List[float]]] = {fuel: {} for fuel in FUELS}
    mgmt_hourly_station_counts: Dict[str, int] = {fuel: 0 for fuel in FUELS}
    mgmt_cycle_station_counts: Dict[str, int] = {fuel: 0 for fuel in FUELS}

    for station_id in tqdm(data["station_uuid"].unique(), desc="Processing stations", unit="station"):
        station = data[data["station_uuid"] == station_id].copy()
        if station.empty:
            continue
        station["date"] = _parse_dates_utc(station["date"])
        station = station.dropna(subset=["date"]).sort_values("date")

        for fuel in FUELS:
            if fuel not in station.columns:
                continue
            fuel_series = pd.to_numeric(station.set_index("date")[fuel], errors="coerce").dropna()
            if fuel_series.empty:
                continue

            station_noon_references = {
                reference_day: day_prices.get(fuel, {}).get(str(station_id))
                for reference_day, day_prices in noon_reference_prices.items()
            }
            station_midnight_reference = midnight_reference_prices.get(fuel, {}).get(str(station_id))
            hourly, _best_hourly, _minabs, _maxabs, _used_days, _filled_minutes = _hourly_variation(
                fuel_series,
                window_start,
                window_end,
                analysis_days,
                noon_reference_prices=station_noon_references,
                midnight_reference_price=station_midnight_reference,
            )
            if hourly.empty:
                continue

            mgmt_hourly_station_counts[fuel] += 1
            for row in hourly.itertuples(index=False):
                if pd.isna(row.price):
                    continue
                mgmt_hourly_values[fuel].setdefault(int(row.hour), []).append(float(row.price))

            cycle_hourly, _cycle_summary = _noon_to_noon_markdown_profile(
                fuel_series,
                management_cycle_days,
            )
            cycle_rows = [
                row
                for row in cycle_hourly
                if row.get("delta_median") is not None
            ]
            if cycle_rows:
                mgmt_cycle_station_counts[fuel] += 1
                for row in cycle_rows:
                    delta_value = row.get("delta_median")
                    if delta_value is None or pd.isna(delta_value):
                        continue
                    mgmt_cycle_values[fuel].setdefault(int(row["cycle_hour"]), []).append(
                        float(delta_value)
                    )

    station_brands = _station_brand_table(stations_frame)
    brand_snapshot_time = _local_dt(analysis_end, 12, 0)
    brand_snapshot = noon_snapshot
    brand_distributions = {
        fuel: _brand_distribution_summary(brand_snapshot, station_brands, fuel) for fuel in FUELS
    }
    noon_reference_histograms, noon_reference_summaries = build_noon_reference_histograms(
        noon_snapshot,
        TZ,
        fuels=FUELS,
        bucket_minutes=HISTOGRAM_BUCKET_MINUTES,
    )

    mgmt_summary = {
        "snapshot_date": str(analysis_end),
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "analysis_start": str(analysis_start),
        "analysis_end": str(analysis_end),
        "station_counts": {},
        "view_modes": {},
        "bucket_counts": {},
        "fuels": {},
        "brand_snapshot_label": "12:00-Referenz",
        "brand_snapshot_date": str(analysis_end),
        "brand_snapshot_timestamp": brand_snapshot_time.isoformat(timespec="minutes"),
        "brand_distributions": brand_distributions,
        "noon_reference_bucket_minutes": HISTOGRAM_BUCKET_MINUTES,
        "noon_reference_histograms": noon_reference_histograms,
        "noon_reference_summaries": noon_reference_summaries,
    }

    for fuel in FUELS:
        fuel_stats = []
        fuel_view_mode = "cycle" if management_cycle_days and mgmt_cycle_station_counts[fuel] > 0 else "hourly"
        mgmt_summary["view_modes"][fuel] = fuel_view_mode
        if fuel_view_mode == "cycle":
            mgmt_summary["station_counts"][fuel] = mgmt_cycle_station_counts[fuel]
            values_by_bucket = mgmt_cycle_values[fuel]
        else:
            mgmt_summary["station_counts"][fuel] = mgmt_hourly_station_counts[fuel]
            values_by_bucket = mgmt_hourly_values[fuel]
        bucket_count = max(
            values_by_bucket.keys(),
            default=(23 if fuel_view_mode == "hourly" else 24),
        ) + 1
        mgmt_summary["bucket_counts"][fuel] = bucket_count
        for hour in range(bucket_count):
            values = values_by_bucket.get(hour, [])
            clock_hour = (12 + hour) % 24
            if values:
                series = pd.Series(values, dtype="float64")
                base_row = {
                    "count": int(series.count()),
                    "min": float(series.min()),
                    "q1": float(series.quantile(0.25)),
                    "median": float(series.quantile(0.5)),
                    "q3": float(series.quantile(0.75)),
                    "max": float(series.max()),
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
            if fuel_view_mode == "cycle":
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
        mgmt_summary["fuels"][fuel] = fuel_stats

    output_path = _management_summary_path(output_root, target_day)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(mgmt_summary, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {output_path}")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-date",
        required=True,
        type=_parse_date,
        help="Local snapshot day in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--analysis-days",
        type=int,
        default=DEFAULT_ANALYSIS_DAYS,
        help="Number of completed days to aggregate. Defaults to 2.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root that contains data/ and data2/. Defaults to the project root.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generate_historical_management_boxplots(
        output_root=args.output_root,
        target_day=args.target_date,
        analysis_days_count=args.analysis_days,
    )


if __name__ == "__main__":
    main()
