#!/usr/bin/env python3
"""Shared helpers for noon snapshot and history outputs."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Sequence

import pandas as pd
import pytz

try:
    from .noon_reference import FUELS, build_noon_reference_snapshot, filter_valid_snapshot_rows
except ImportError:  # pragma: no cover
    from noon_reference import FUELS, build_noon_reference_snapshot, filter_valid_snapshot_rows


HISTORY_COLUMNS: tuple[str, ...] = ("date", "price", "last_update")


def build_noon_snapshot(
    prices: pd.DataFrame,
    station_ids: Sequence[str],
    target_day: date,
    tz: pytz.BaseTzInfo,
    fuels: Sequence[str] = FUELS,
) -> pd.DataFrame:
    snapshot = build_noon_reference_snapshot(prices, station_ids, target_day, tz, fuels=fuels)
    return filter_valid_snapshot_rows(snapshot, fuels=fuels)


def dated_noon_snapshot_path(output_root: Path, target_day: date) -> Path:
    return output_root / "data2" / f"{target_day:%Y}" / f"{target_day:%m}" / f"{target_day:%d}" / "noon.csv"


def write_snapshot(snapshot: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_csv(output_path, index=False, float_format="%.3f")


def history_output_path(output_root: Path, station_id: str, fuel: str) -> Path:
    return output_root / "data2" / Path(*station_id.split("-")) / fuel / "history.csv"


def collect_history_rows(
    rows_by_file: dict[tuple[str, str], list[dict[str, object]]],
    snapshot: pd.DataFrame,
    target_day: date,
    history_start_date: date,
    fuels: Sequence[str] = FUELS,
) -> None:
    if target_day < history_start_date or snapshot.empty or "station_uuid" not in snapshot.columns:
        return

    base = snapshot.copy()
    base["station_uuid"] = base["station_uuid"].astype(str)
    if "last_update" in base.columns:
        base["last_update"] = base["last_update"].fillna("").astype(str)
    else:
        base["last_update"] = ""
    day_label = target_day.isoformat()

    for fuel in fuels:
        if fuel not in base.columns:
            continue
        prices = pd.to_numeric(base[fuel], errors="coerce")
        valid = prices > 0
        if not valid.any():
            continue
        for station_id, price, last_update in zip(
            base.loc[valid, "station_uuid"].tolist(),
            prices.loc[valid].tolist(),
            base.loc[valid, "last_update"].tolist(),
        ):
            rows_by_file.setdefault((str(station_id), fuel), []).append(
                {
                    "date": day_label,
                    "price": round(float(price), 3),
                    "last_update": str(last_update or ""),
                }
            )


def load_history_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)

    frame = pd.read_csv(path, dtype={"date": "string", "last_update": "string"})
    for column in HISTORY_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame[list(HISTORY_COLUMNS)]


def merge_history_rows(existing: pd.DataFrame, additions: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        merged = additions.copy()
    elif additions.empty:
        merged = existing.copy()
    else:
        merged = pd.concat([existing, additions], ignore_index=True)
    if merged.empty:
        return pd.DataFrame(columns=HISTORY_COLUMNS)

    merged["date"] = merged["date"].astype("string").fillna("").str.strip()
    merged["price"] = pd.to_numeric(merged["price"], errors="coerce")
    merged["last_update"] = merged["last_update"].astype("string").fillna("").str.strip()
    merged = merged.loc[merged["date"].ne("") & merged["price"].notna()].copy()
    if merged.empty:
        return pd.DataFrame(columns=HISTORY_COLUMNS)

    merged["_sort_date"] = pd.to_datetime(merged["date"], errors="coerce")
    merged = merged.dropna(subset=["_sort_date"]).sort_values(["_sort_date", "last_update"])
    merged = merged.drop_duplicates(subset=["date"], keep="last")
    return merged[list(HISTORY_COLUMNS)].reset_index(drop=True)


def write_history_files(
    output_root: Path,
    rows_by_file: dict[tuple[str, str], list[dict[str, object]]],
) -> list[Path]:
    written_paths: list[Path] = []

    for (station_id, fuel), rows in sorted(rows_by_file.items()):
        additions = pd.DataFrame(rows, columns=HISTORY_COLUMNS)
        history_path = history_output_path(output_root, station_id, fuel)
        merged = merge_history_rows(load_history_frame(history_path), additions)
        if merged.empty:
            continue
        history_path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(history_path, index=False, float_format="%.3f")
        written_paths.append(history_path)

    return written_paths
