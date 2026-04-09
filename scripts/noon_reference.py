#!/usr/bin/env python3
"""Shared helpers for noon reference snapshot selection."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Sequence

import pandas as pd
import pytz

try:
    from .time_utils import parse_timestamps_to_utc
except ImportError:  # pragma: no cover
    from time_utils import parse_timestamps_to_utc


FUELS: tuple[str, ...] = ("diesel", "e5", "e10")
HISTOGRAM_BUCKET_MINUTES = 15


def fuel_last_update_column(fuel: str) -> str:
    return f"{fuel}_last_update"


def fuel_selection_method_column(fuel: str) -> str:
    return f"{fuel}_selection_method"


def snapshot_output_columns(fuels: Sequence[str] = FUELS) -> tuple[str, ...]:
    columns: list[str] = ["station_uuid", *fuels, "last_update"]
    for fuel in fuels:
        columns.append(fuel_last_update_column(fuel))
    for fuel in fuels:
        columns.append(fuel_selection_method_column(fuel))
    return tuple(columns)


OUTPUT_COLUMNS: tuple[str, ...] = snapshot_output_columns()


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
    normalized["date"] = parse_timestamps_to_utc(normalized["date"], tz)
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


def _isoformat_or_na(value: object, tz: pytz.BaseTzInfo) -> object:
    if pd.isna(value):
        return pd.NA
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.tz_convert(tz).isoformat()


def _bucket_label(bucket_minute: int) -> str:
    hour, minute = divmod(int(bucket_minute), 60)
    return f"{hour:02d}:{minute:02d}"


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
        snapshot[fuel_last_update_column(fuel)] = pd.NA
        snapshot[fuel_selection_method_column(fuel)] = pd.NA
    snapshot["last_update"] = pd.NA

    if snapshot.empty:
        return snapshot[list(snapshot_output_columns(fuels))]

    noon_local = tz.localize(datetime.combine(target_day, datetime.min.time()) + timedelta(hours=12))
    cutoff = pd.Timestamp(noon_local.astimezone(pytz.UTC))
    fallback_rows = _latest_station_rows(normalized, cutoff, fuels)
    if not fallback_rows.empty:
        fallback_rows = fallback_rows.copy()
        fallback_rows["last_update"] = fallback_rows["date"].map(lambda ts: ts.tz_convert(tz).isoformat())

    for fuel in fuels:
        if fuel not in normalized.columns:
            continue

        last_update_column = fuel_last_update_column(fuel)
        selection_method_column = fuel_selection_method_column(fuel)

        increase_rows = _first_daily_increase_rows(normalized, target_day, [fuel], tz)
        if not increase_rows.empty:
            increase_rows = increase_rows.rename(columns={"last_update": last_update_column})
            increase_snapshot = snapshot[["station_uuid"]].merge(
                increase_rows[["station_uuid", fuel, last_update_column]],
                on="station_uuid",
                how="left",
            )
            has_increase = increase_snapshot[last_update_column].notna()
            snapshot.loc[has_increase, fuel] = increase_snapshot.loc[has_increase, fuel].tolist()
            snapshot.loc[has_increase, last_update_column] = increase_snapshot.loc[
                has_increase, last_update_column
            ].tolist()
            snapshot.loc[has_increase, selection_method_column] = "increase"

        if not fallback_rows.empty and fuel in fallback_rows.columns:
            fallback_snapshot = snapshot[["station_uuid"]].merge(
                fallback_rows[["station_uuid", fuel, "last_update"]].rename(
                    columns={"last_update": last_update_column}
                ),
                on="station_uuid",
                how="left",
            )
            needs_fallback = snapshot[last_update_column].isna() & fallback_snapshot[last_update_column].notna()
            snapshot.loc[needs_fallback, fuel] = fallback_snapshot.loc[needs_fallback, fuel].tolist()
            snapshot.loc[needs_fallback, last_update_column] = fallback_snapshot.loc[
                needs_fallback, last_update_column
            ].tolist()
            snapshot.loc[needs_fallback, selection_method_column] = "fallback"

    timestamp_columns = [fuel_last_update_column(fuel) for fuel in fuels]
    timestamp_frame = snapshot[timestamp_columns].apply(pd.to_datetime, errors="coerce", utc=True)
    station_last_update = timestamp_frame.max(axis=1)
    snapshot["last_update"] = station_last_update.map(lambda value: _isoformat_or_na(value, tz))
    return snapshot[list(snapshot_output_columns(fuels))]


def build_noon_reference_histograms(
    snapshot: pd.DataFrame,
    tz: pytz.BaseTzInfo,
    fuels: Sequence[str] = FUELS,
    bucket_minutes: int = HISTOGRAM_BUCKET_MINUTES,
) -> tuple[dict[str, list[dict[str, object]]], dict[str, dict[str, object]]]:
    histograms: dict[str, list[dict[str, object]]] = {}
    summaries: dict[str, dict[str, object]] = {}

    for fuel in fuels:
        timestamp_column = fuel_last_update_column(fuel) if fuel_last_update_column(fuel) in snapshot.columns else "last_update"
        method_column = fuel_selection_method_column(fuel) if fuel_selection_method_column(fuel) in snapshot.columns else None
        if fuel not in snapshot.columns or timestamp_column not in snapshot.columns:
            histograms[fuel] = []
            summaries[fuel] = {"stations": 0, "bucket_minutes": bucket_minutes}
            continue

        fuel_prices = pd.to_numeric(snapshot[fuel], errors="coerce")
        timestamps = pd.to_datetime(snapshot[timestamp_column], errors="coerce", utc=True)
        rows = snapshot.loc[fuel_prices.gt(0) & timestamps.notna(), ["station_uuid"]].copy()
        if rows.empty:
            histograms[fuel] = []
            summaries[fuel] = {"stations": 0, "bucket_minutes": bucket_minutes}
            continue

        rows["local_ts"] = timestamps.loc[rows.index].dt.tz_convert(tz)
        rows["minutes_of_day"] = rows["local_ts"].dt.hour * 60 + rows["local_ts"].dt.minute
        rows["bucket_minute"] = (rows["minutes_of_day"] // bucket_minutes) * bucket_minutes
        rows["bucket_label"] = rows["bucket_minute"].map(_bucket_label)

        if method_column:
            rows["selection_method"] = snapshot.loc[rows.index, method_column].fillna("").astype(str)
            rows["is_increase"] = rows["selection_method"].eq("increase")
            rows["is_fallback"] = rows["selection_method"].eq("fallback")
        else:
            rows["selection_method"] = ""
            rows["is_increase"] = False
            rows["is_fallback"] = False

        grouped = (
            rows.groupby(["bucket_minute", "bucket_label"], as_index=False)
            .agg(
                count=("station_uuid", "size"),
                stations=("station_uuid", "nunique"),
                increase_count=("is_increase", "sum"),
                fallback_count=("is_fallback", "sum"),
            )
            .sort_values(["bucket_minute"])
            .reset_index(drop=True)
        )
        total = int(rows.shape[0])
        grouped["share"] = grouped["count"].map(lambda value: round(float(value) / float(total), 4))
        histogram_rows = grouped.to_dict(orient="records")
        histograms[fuel] = histogram_rows

        peak = grouped.sort_values(["count", "stations", "bucket_minute"], ascending=[False, False, True]).iloc[0]
        summary: dict[str, object] = {
            "stations": total,
            "bucket_minutes": bucket_minutes,
            "peak_bucket_label": str(peak["bucket_label"]),
            "peak_bucket_count": int(peak["count"]),
            "peak_bucket_share": round(float(peak["count"]) / float(total), 4),
        }
        if method_column:
            delayed_increases = rows.loc[rows["is_increase"] & rows["minutes_of_day"].gt(12 * 60)].shape[0]
            summary["increase_stations"] = int(rows["is_increase"].sum())
            summary["fallback_stations"] = int(rows["is_fallback"].sum())
            summary["delayed_increase_stations"] = int(delayed_increases)
        summaries[fuel] = summary

    return histograms, summaries


def filter_valid_snapshot_rows(
    snapshot: pd.DataFrame,
    fuels: Sequence[str] = FUELS,
) -> pd.DataFrame:
    filtered = snapshot.copy()
    for fuel in fuels:
        filtered[fuel] = pd.to_numeric(filtered[fuel], errors="coerce")
    valid = (filtered[list(fuels)] > 0).all(axis=1)
    return filtered.loc[valid, list(snapshot_output_columns(fuels))].reset_index(drop=True)
