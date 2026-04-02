#!/usr/bin/env python3
"""Patch dated management_boxplots.json files with brand distributions from noon.csv."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import pytz

try:
    from .generate_data import TZ, _brand_distribution_summary, _station_brand_table
except ImportError:  # pragma: no cover
    from generate_data import TZ, _brand_distribution_summary, _station_brand_table


FUELS: tuple[str, ...] = ("diesel", "e10", "e5")
MIN_SUPPORTED_DATE = date(2026, 2, 4)


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def _iter_days(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _dated_dir(root: Path, day: date) -> Path:
    return root / "data2" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"


def _default_end_date() -> date:
    return date.today() - timedelta(days=1)


def _load_stations(stations_json: Path) -> pd.DataFrame:
    stations = pd.DataFrame(json.loads(stations_json.read_text(encoding="utf-8")))
    return _station_brand_table(stations)


def _load_noon_snapshot(path: Path) -> pd.DataFrame:
    snapshot = pd.read_csv(path, dtype={"station_uuid": "string"})
    columns = ["station_uuid", *[fuel for fuel in FUELS if fuel in snapshot.columns]]
    return snapshot[columns].copy()


def _brand_timestamp_for(day: date) -> str:
    noon_local = TZ.localize(datetime.combine(day, datetime.min.time()) + timedelta(hours=12))
    return noon_local.isoformat(timespec="minutes")


def patch_management_boxplots_from_noon(
    root: Path,
    stations_json: Path,
    start_date: date,
    end_date: date,
) -> list[Path]:
    station_brands = _load_stations(stations_json)
    written: list[Path] = []

    for target_day in _iter_days(start_date, end_date):
        dated_dir = _dated_dir(root, target_day)
        noon_csv = dated_dir / "noon.csv"
        management_json = dated_dir / "management_boxplots.json"
        if not noon_csv.exists() or not management_json.exists():
            continue

        summary = json.loads(management_json.read_text(encoding="utf-8"))
        if summary.get("brand_distributions"):
            continue
        snapshot = _load_noon_snapshot(noon_csv)
        brand_distributions = {
            fuel: _brand_distribution_summary(snapshot, station_brands, fuel) for fuel in FUELS
        }

        summary["brand_snapshot_label"] = "12:00"
        summary["brand_snapshot_date"] = target_day.isoformat()
        summary["brand_snapshot_timestamp"] = _brand_timestamp_for(target_day)
        summary["brand_distributions"] = brand_distributions

        management_json.write_text(
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        written.append(management_json)
        print(f"Patched {management_json}")

    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root. Defaults to the current project root.",
    )
    parser.add_argument(
        "--stations-json",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "stations.json",
        help="Station metadata JSON used for brand lookups.",
    )
    parser.add_argument(
        "--start-date",
        type=_parse_date,
        default=MIN_SUPPORTED_DATE,
        help=f"First dated folder to patch. Defaults to {MIN_SUPPORTED_DATE:%Y-%m-%d}.",
    )
    parser.add_argument(
        "--end-date",
        type=_parse_date,
        default=_default_end_date(),
        help="Last dated folder to patch. Defaults to yesterday.",
    )
    args = parser.parse_args()
    if args.end_date < args.start_date:
        parser.error("--end-date must be on or after --start-date.")
    return args


def main() -> None:
    args = parse_args()
    written = patch_management_boxplots_from_noon(
        root=args.root,
        stations_json=args.stations_json,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    print(f"Patched {len(written)} management snapshot files")


if __name__ == "__main__":
    main()
