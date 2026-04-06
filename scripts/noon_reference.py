#!/usr/bin/env python3
"""Shared helpers for noon reference snapshot selection."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Sequence

import pandas as pd
import pytz


FUELS: tuple[str, ...] = ("diesel", "e5", "e10")
OUTPUT_COLUMNS: tuple[str, ...] = ("station_uuid", "diesel", "e5", "e10", "last_update")


def _available_fuels(prices: pd.DataFrame, fuels: Sequence[str]) -> list[str]:
    return [fuel for fuel in fuels if fuel in prices.columns]


def _normalize_prices(
    prices: pd.DataFrame,
    fuels: Sequence[str],
    tz: pytz.BaseTzInfo,
) -> pd.DataFrame:
    available = _available_fuels(prices, fuels)
    if "station_uuid" not in prices.columns or "date" not in prices.columns:
        return pd.DataFrame(columns=["station_uuid", "date", "local_day", *fuels])

    normalized = prices[["station_uuid", "date", *available]].copy()
    normalized["station_uuid"] = normalized["station_uuid"].astype(str)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce", utc=True)
    normalized = normalized.dropna(subset=["station_uuid", "date"])
    for fuel in available:
        normalized[fuel] = pd.to_numeric(normalized[fuel], errors="coerce")
    normalized = (
        normalized.sort_values(["station_uuid", "date"])
        .drop_duplicates(subset=["station_uuid", "date"], keep="last")
        .reset_index(drop=True)
    )
    for fuel in fuels:
        if fuel not in normalized.columns:
            normalized[fuel] = pd.NA
    normalized["local_day"] = normalized["date"].dt.tz_convert(tz).dt.date
    return normalized[["station_uuid", "date", "local_day", *fuels]]


def _latest_station_rows(
    prices: pd.DataFrame,
    cutoff: pd.Timestamp,
    fuels: Sequence[str],
) -> pd.DataFrame:
    available = _available_fuels(prices, fuels)
    subset = prices.loc[prices["date"] <= cutoff, ["station_uuid", "date", *available]].copy()
    if subset.empty:
        return pd.DataFrame(columns=["station_uuid", "date", *fuels])

    for fuel in available:
        subset[fuel] = pd.to_numeric(subset[fuel], errors="coerce")
    subset = subset.sort_values(["station_uuid", "date"]).groupby("station_uuid", sort=False).tail(1).copy()
    for fuel in fuels:
        if fuel not in subset.columns:
            subset[fuel] = pd.NA
    return subset[["station_uuid", "date", *fuels]].reset_index(drop=True)


def _first_daily_increase_rows(
    prices: pd.DataFrame,
    target_day: date,
    fuels: Sequence[str],
    tz: pytz.BaseTzInfo,
) -> pd.DataFrame:
    available = _available_fuels(prices, fuels)
    if prices.empty or not available:
        return pd.DataFrame(columns=["station_uuid", *fuels, "last_update"])

    rows = prices.copy()
    increase_mask = pd.Series(False, index=rows.index)
    for fuel in available:
        previous = rows.groupby("station_uuid")[fuel].shift(1)
        increase_mask = increase_mask | (
            rows[fuel].notna()
            & previous.notna()
            & (rows[fuel] > previous + 1e-9)
        )

    increases = rows.loc[(rows["local_day"] == target_day) & increase_mask].copy()
    if increases.empty:
        return pd.DataFrame(columns=["station_uuid", *fuels, "last_update"])

    firsts = (
        increases.sort_values(["station_uuid", "date"])
        .groupby("station_uuid", sort=False)
        .head(1)
        .copy()
    )
    firsts["last_update"] = firsts["date"].map(lambda ts: ts.tz_convert(tz).isoformat())
    return firsts[["station_uuid", *fuels, "last_update"]].reset_index(drop=True)


def build_noon_reference_snapshot(
    prices: pd.DataFrame,
    station_ids: Sequence[str],
    target_day: date,
    tz: pytz.BaseTzInfo,
    fuels: Sequence[str] = FUELS,
) -> pd.DataFrame:
    normalized = _normalize_prices(prices, fuels, tz)
    snapshot = pd.DataFrame({"station_uuid": sorted({str(station_id) for station_id in station_ids})})
    for fuel in fuels:
        snapshot[fuel] = pd.NA
    snapshot["last_update"] = pd.NA

    if snapshot.empty:
        return snapshot[list(OUTPUT_COLUMNS)]

    increase_rows = _first_daily_increase_rows(normalized, target_day, fuels, tz)
    if not increase_rows.empty:
        increase_snapshot = snapshot[["station_uuid"]].merge(increase_rows, on="station_uuid", how="left")
        has_increase = increase_snapshot["last_update"].notna()
        for column in [*fuels, "last_update"]:
            snapshot.loc[has_increase, column] = increase_snapshot.loc[has_increase, column].tolist()

    noon_local = tz.localize(datetime.combine(target_day, datetime.min.time()) + timedelta(hours=12))
    cutoff = pd.Timestamp(noon_local.astimezone(pytz.UTC))
    fallback_rows = _latest_station_rows(normalized, cutoff, fuels)
    if not fallback_rows.empty:
        fallback_rows = fallback_rows.copy()
        fallback_rows["last_update"] = noon_local.isoformat()
        fallback_snapshot = snapshot[["station_uuid"]].merge(
            fallback_rows[["station_uuid", *fuels, "last_update"]],
            on="station_uuid",
            how="left",
        )
        needs_fallback = snapshot["last_update"].isna() & fallback_snapshot["last_update"].notna()
        for column in [*fuels, "last_update"]:
            snapshot.loc[needs_fallback, column] = fallback_snapshot.loc[needs_fallback, column].tolist()

    return snapshot[list(OUTPUT_COLUMNS)]


def filter_valid_snapshot_rows(
    snapshot: pd.DataFrame,
    fuels: Sequence[str] = FUELS,
) -> pd.DataFrame:
    filtered = snapshot.copy()
    for fuel in fuels:
        filtered[fuel] = pd.to_numeric(filtered[fuel], errors="coerce")
    valid = (filtered[list(fuels)] > 0).all(axis=1)
    return filtered.loc[valid, list(OUTPUT_COLUMNS)].reset_index(drop=True)
