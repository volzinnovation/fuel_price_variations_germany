#!/usr/bin/env python3
"""Export per-station daily increase reference prices for a historical date range."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import pandas as pd

try:
    from .generate_data import TZ, TANKER_BASE, _data_path, _parse_dates_utc, _read_csv_from_url
    from .noon_reference import (
        FUELS,
        build_noon_reference_snapshot,
        filter_valid_snapshot_rows,
    )
except ImportError:  # pragma: no cover
    from generate_data import TZ, TANKER_BASE, _data_path, _parse_dates_utc, _read_csv_from_url
    from noon_reference import (
        FUELS,
        build_noon_reference_snapshot,
        filter_valid_snapshot_rows,
    )


DEFAULT_START_DATE = date(2026, 3, 1)
DEFAULT_END_DATE = date(2026, 3, 30)
DEFAULT_LOOKBACK_DAYS = 3


def _parse_target_date(value: str) -> date:
    return date.fromisoformat(value)


def _iter_days(start_date: date, end_date: date) -> Sequence[date]:
    current = start_date
    days: list[date] = []
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


def _load_station_ids(target_day: date, cache: dict[date, list[str]]) -> list[str]:
    candidates = [target_day - timedelta(days=offset) for offset in range(0, 4)]
    for day in candidates:
        cached = cache.get(day)
        if cached is not None:
            return cached

        url = f"{TANKER_BASE}/{_data_path('stations', day)}"
        try:
            df = _read_csv_from_url(url, label=f"stations {day:%Y-%m-%d}")
        except Exception:
            continue

        id_column = "station_uuid" if "station_uuid" in df.columns else "uuid"
        if id_column not in df.columns:
            raise RuntimeError("Stations CSV did not contain a station identifier column.")

        station_ids = sorted(df[id_column].dropna().astype(str).unique().tolist())
        if station_ids:
            cache[day] = station_ids
            return station_ids

    raise RuntimeError(f"Failed to download stations CSV for noon backfill on {target_day:%Y-%m-%d}.")


def _load_price_day(target_day: date, cache: dict[date, pd.DataFrame]) -> pd.DataFrame:
    cached = cache.get(target_day)
    if cached is not None:
        return cached

    url = f"{TANKER_BASE}/{_data_path('prices', target_day)}"
    try:
        prices = _read_csv_from_url(url, label=f"prices {target_day:%Y-%m-%d}", show=False)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download price CSV for noon backfill: {target_day:%Y-%m-%d}."
        ) from exc

    required_columns = ["station_uuid", "date", *[fuel for fuel in FUELS if fuel in prices.columns]]
    prices = prices[required_columns].copy()
    prices["date"] = _parse_dates_utc(prices["date"])
    prices = prices.dropna(subset=["date", "station_uuid"]).sort_values(["station_uuid", "date"])
    cache[target_day] = prices
    return prices


def _window_prices(target_day: date, lookback_days: int, cache: dict[date, pd.DataFrame]) -> pd.DataFrame:
    days = [target_day - timedelta(days=offset) for offset in range(lookback_days, -1, -1)]
    frames = [_load_price_day(day, cache) for day in days]
    if not frames:
        return pd.DataFrame(columns=["station_uuid", "date", *FUELS])
    return pd.concat(frames, ignore_index=True)


def build_noon_snapshot(prices: pd.DataFrame, station_ids: Sequence[str], target_day: date) -> pd.DataFrame:
    snapshot = build_noon_reference_snapshot(prices, station_ids, target_day, TZ, fuels=FUELS)
    return filter_valid_snapshot_rows(snapshot, fuels=FUELS)


def _dated_output_path(output_root: Path, target_day: date) -> Path:
    return output_root / "data2" / f"{target_day:%Y}" / f"{target_day:%m}" / f"{target_day:%d}" / "noon.csv"


def _write_snapshot(snapshot: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_csv(output_path, index=False, float_format="%.3f")


def _prune_cache(cache: dict[date, pd.DataFrame], oldest_day_to_keep: date) -> None:
    for day in list(cache):
        if day < oldest_day_to_keep:
            del cache[day]


def generate_noon_price_backfill(
    output_root: Path,
    latest_output_path: Path,
    start_date: date,
    end_date: date,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    write_latest_output: bool = True,
) -> list[Path]:
    station_cache: dict[date, list[str]] = {}
    price_cache: dict[date, pd.DataFrame] = {}
    written_paths: list[Path] = []
    latest_snapshot: pd.DataFrame | None = None

    for target_day in _iter_days(start_date, end_date):
        station_ids = _load_station_ids(target_day, station_cache)
        prices = _window_prices(target_day, lookback_days, price_cache)
        snapshot = build_noon_snapshot(prices, station_ids, target_day)
        dated_output_path = _dated_output_path(output_root, target_day)
        _write_snapshot(snapshot, dated_output_path)
        written_paths.append(dated_output_path)
        latest_snapshot = snapshot
        print(f"{target_day:%Y-%m-%d}: captured {len(snapshot):,} station reference prices")
        _prune_cache(price_cache, target_day - timedelta(days=lookback_days))

    if latest_snapshot is None:
        raise RuntimeError("No noon snapshots were generated.")

    if write_latest_output:
        _write_snapshot(latest_snapshot, latest_output_path)
    return written_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-date",
        type=_parse_target_date,
        default=DEFAULT_START_DATE,
        help=f"First local snapshot day in YYYY-MM-DD format. Defaults to {DEFAULT_START_DATE:%Y-%m-%d}.",
    )
    parser.add_argument(
        "--end-date",
        type=_parse_target_date,
        default=DEFAULT_END_DATE,
        help=f"Last local snapshot day in YYYY-MM-DD format. Defaults to {DEFAULT_END_DATE:%Y-%m-%d}.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"How many prior local days to scan for carry-forward prices. Defaults to {DEFAULT_LOOKBACK_DAYS}.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root that contains data/ and data2/. Defaults to the project root.",
    )
    parser.add_argument(
        "--latest-output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "noon.csv",
        help="Top-level CSV output path for the last processed day. Defaults to data/noon.csv.",
    )
    parser.add_argument(
        "--skip-latest-output",
        action="store_true",
        help="Do not overwrite the top-level latest snapshot. Useful for historical backfills.",
    )
    args = parser.parse_args()
    if args.end_date < args.start_date:
        parser.error("--end-date must be on or after --start-date.")
    if args.lookback_days < 0:
        parser.error("--lookback-days must be zero or greater.")
    return args


def main() -> None:
    args = parse_args()
    written_paths = generate_noon_price_backfill(
        output_root=args.output_root,
        latest_output_path=args.latest_output,
        start_date=args.start_date,
        end_date=args.end_date,
        lookback_days=args.lookback_days,
        write_latest_output=not args.skip_latest_output,
    )
    if args.skip_latest_output:
        print("Skipped top-level latest snapshot update")
    else:
        print(f"Wrote {args.latest_output}")
    print(f"Wrote {len(written_paths)} dated noon snapshots under {args.output_root / 'data2'}")


if __name__ == "__main__":
    main()
