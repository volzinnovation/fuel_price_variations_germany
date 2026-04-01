#!/usr/bin/env python3
"""Export per-station prices valid at local midnight."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence

import pandas as pd
import pytz

try:
    from .generate_data import TZ, TANKER_BASE, _data_path, _parse_dates_utc, _read_csv_from_url
except ImportError:  # pragma: no cover
    from generate_data import TZ, TANKER_BASE, _data_path, _parse_dates_utc, _read_csv_from_url


FUELS: tuple[str, ...] = ("diesel", "e5", "e10")


def _parse_target_date(value: str) -> date:
    return date.fromisoformat(value)


def _load_station_ids(target_day: date) -> list[str]:
    candidates = [target_day - timedelta(days=offset) for offset in range(0, 4)]
    for day in candidates:
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
            return station_ids
    raise RuntimeError("Failed to download stations CSV for midnight snapshot export.")


def _load_previous_day_prices(target_day: date) -> pd.DataFrame:
    source_day = target_day - timedelta(days=1)
    url = f"{TANKER_BASE}/{_data_path('prices', source_day)}"
    try:
        prices = _read_csv_from_url(url, label=f"prices {source_day:%Y-%m-%d}", show=False)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download previous-day price CSV for midnight snapshot export: "
            f"{source_day:%Y-%m-%d}."
        ) from exc
    prices["date"] = _parse_dates_utc(prices["date"])
    prices = prices.dropna(subset=["date", "station_uuid"]).sort_values(["station_uuid", "date"])
    return prices


def _latest_fuel_values(prices: pd.DataFrame, fuel: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    if fuel not in prices.columns:
        return pd.DataFrame(columns=["station_uuid", fuel])
    subset = prices.loc[prices["date"] <= cutoff, ["station_uuid", "date", fuel]].copy()
    subset[fuel] = pd.to_numeric(subset[fuel], errors="coerce")
    subset = subset.dropna(subset=[fuel]).sort_values(["station_uuid", "date"])
    if subset.empty:
        return pd.DataFrame(columns=["station_uuid", fuel])
    return subset.groupby("station_uuid", sort=False).tail(1)[["station_uuid", fuel]]


def _filter_valid_rows(snapshot: pd.DataFrame) -> pd.DataFrame:
    filtered = snapshot.copy()
    for fuel in FUELS:
        filtered[fuel] = pd.to_numeric(filtered[fuel], errors="coerce")
    valid = (filtered[list(FUELS)] > 0).all(axis=1)
    return filtered.loc[valid, ["station_uuid", *FUELS]].reset_index(drop=True)


def build_midnight_snapshot(
    prices: pd.DataFrame,
    station_ids: Sequence[str],
    target_day: date,
) -> pd.DataFrame:
    midnight_local = TZ.localize(datetime.combine(target_day, datetime.min.time()))
    cutoff = pd.Timestamp(midnight_local.astimezone(pytz.UTC))
    snapshot = pd.DataFrame({"station_uuid": sorted({str(station_id) for station_id in station_ids})})
    for fuel in FUELS:
        snapshot = snapshot.merge(_latest_fuel_values(prices, fuel, cutoff), on="station_uuid", how="left")
    return _filter_valid_rows(snapshot[["station_uuid", *FUELS]])


def generate_midnight_csv(output_path: Path, target_day: date | None = None) -> Path:
    snapshot_day = target_day or date.today()
    station_ids = _load_station_ids(snapshot_day)
    prices = _load_previous_day_prices(snapshot_day)
    snapshot = build_midnight_snapshot(prices, station_ids, snapshot_day)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_csv(output_path, index=False, float_format="%.3f")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-date",
        type=_parse_target_date,
        default=None,
        help="Local midnight snapshot day in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "midnight.csv",
        help="CSV output path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = generate_midnight_csv(args.output, target_day=args.target_date)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
