#!/usr/bin/env python3
"""Generate stations.json and per-station hourly price variations."""

from __future__ import annotations

import argparse
import io
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Dict
from xml.etree import ElementTree as ET

import certifi
import pandas as pd
import pytz
import requests
from tqdm import tqdm

try:
    from .noon_outputs import (
        build_noon_snapshot,
        collect_history_rows,
        dated_noon_snapshot_path as _dated_noon_snapshot_path,
        write_history_files,
        write_snapshot,
    )
    from .noon_reference import (
        HISTOGRAM_BUCKET_MINUTES,
        build_noon_reference_histograms,
        select_noon_reference_from_series,
    )
    from .time_utils import parse_timestamps_to_utc
except ImportError:  # pragma: no cover
    from noon_outputs import (
        build_noon_snapshot,
        collect_history_rows,
        dated_noon_snapshot_path as _dated_noon_snapshot_path,
        write_history_files,
        write_snapshot,
    )
    from noon_reference import (
        HISTOGRAM_BUCKET_MINUTES,
        build_noon_reference_histograms,
        select_noon_reference_from_series,
    )
    from time_utils import parse_timestamps_to_utc

TZ = pytz.timezone("Europe/Berlin")
LAW_RESET_DATE = date(2026, 4, 1)
TANKER_BASE = (
    "https://data.tankerkoenig.de/"
    "tankerkoenig-organization/tankerkoenig-data/raw/branch/master"
)
FRED_BRENT_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"
ECB_FX_90D_XML_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
LITERS_PER_BARREL = 42 * 3.785411784


@dataclass
class DateRange:
    start: date
    end: date

    def iter_days(self) -> Iterable[date]:
        current = self.start
        while current <= self.end:
            yield current
            current += timedelta(days=1)


def _data_path(prefix: str, day: date) -> str:
    return f"{prefix}/{day:%Y}/{day:%m}/{day:%Y-%m-%d}-{prefix}.csv"


def _read_csv_from_url(url: str, label: str | None = None, show: bool = True) -> pd.DataFrame:
    user = os.environ.get("TK_USER")
    password = os.environ.get("TK_PASS")
    auth = (user, password) if user and password else None
    if label and show:
        print(f"Downloading {label}...")
    response = requests.get(url, timeout=120, verify=certifi.where(), auth=auth)
    response.raise_for_status()
    text = response.text.lstrip()
    if text.startswith("<") or text.startswith("{"):
        raise ValueError("Unexpected response payload (not CSV).")
    return pd.read_csv(io.StringIO(text))


def _read_text_from_url(url: str, label: str | None = None, show: bool = True) -> str:
    if label and show:
        print(f"Downloading {label}...")
    response = requests.get(url, timeout=120, verify=certifi.where())
    response.raise_for_status()
    return response.text


def _select_ecb_fx_rate(
    rates_by_day: Dict[date, float],
    target_day: date,
) -> tuple[date, float]:
    if target_day in rates_by_day:
        return target_day, rates_by_day[target_day]

    prior_days = [day for day in rates_by_day if day <= target_day]
    if prior_days:
        fx_day = max(prior_days)
        return fx_day, rates_by_day[fx_day]

    fx_day = max(rates_by_day)
    return fx_day, rates_by_day[fx_day]


def _fetch_brent_crude_snapshot() -> dict[str, object]:
    brent_text = _read_text_from_url(
        FRED_BRENT_CSV_URL,
        label="Brent crude (FRED/EIA)",
        show=False,
    ).lstrip("\ufeff")
    brent_df = pd.read_csv(io.StringIO(brent_text))
    if brent_df.shape[1] < 2:
        raise ValueError("Unexpected Brent CSV format.")

    date_col, value_col = brent_df.columns[:2]
    brent_df[date_col] = pd.to_datetime(brent_df[date_col], errors="coerce").dt.date
    brent_df[value_col] = pd.to_numeric(brent_df[value_col], errors="coerce")
    brent_df = brent_df.dropna(subset=[date_col, value_col]).sort_values(date_col)
    if brent_df.empty:
        raise ValueError("Brent CSV contained no usable rows.")

    latest_row = brent_df.iloc[-1]
    brent_day = latest_row[date_col]
    brent_usd_per_barrel = float(latest_row[value_col])

    fx_xml = _read_text_from_url(
        ECB_FX_90D_XML_URL,
        label="ECB FX 90d",
        show=False,
    ).lstrip()
    root = ET.fromstring(fx_xml)
    ns = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
    rates_by_day: Dict[date, float] = {}
    for day_cube in root.findall(".//ecb:Cube[@time]", ns):
        day_text = day_cube.attrib.get("time")
        if not day_text:
            continue
        usd_cube = next(
            (
                cube
                for cube in day_cube.findall("ecb:Cube", ns)
                if cube.attrib.get("currency") == "USD"
            ),
            None,
        )
        if usd_cube is None:
            continue
        rate_text = usd_cube.attrib.get("rate")
        if not rate_text:
            continue
        rates_by_day[date.fromisoformat(day_text)] = float(rate_text)

    if not rates_by_day:
        raise ValueError("ECB XML contained no USD exchange rates.")

    fx_day, usd_per_eur = _select_ecb_fx_rate(rates_by_day, brent_day)
    brent_eur_per_barrel = brent_usd_per_barrel / usd_per_eur
    brent_eur_per_crude_liter = brent_eur_per_barrel / LITERS_PER_BARREL

    return {
        "series_id": "DCOILBRENTEU",
        "barrel_liters": round(LITERS_PER_BARREL, 6),
        "brent_as_of": str(brent_day),
        "brent_usd_per_barrel": round(brent_usd_per_barrel, 4),
        "usd_per_eur_as_of": str(fx_day),
        "usd_per_eur": round(usd_per_eur, 6),
        "brent_eur_per_barrel": round(brent_eur_per_barrel, 4),
        "brent_eur_per_crude_liter": round(brent_eur_per_crude_liter, 6),
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "sources": {
            "brent_csv": FRED_BRENT_CSV_URL,
            "fx_xml": ECB_FX_90D_XML_URL,
        },
    }


def download_stations(target_path: Path, target_day: date) -> pd.DataFrame:
    candidates = [target_day - timedelta(days=offset) for offset in range(0, 4)]
    df = None
    for day in candidates:
        url = f"{TANKER_BASE}/{_data_path('stations', day)}"
        try:
            df = _read_csv_from_url(url, label=f"stations {day:%Y-%m-%d}")
            break
        except Exception:
            continue
    if df is None:
        raise RuntimeError(
            "Failed to download stations CSV. Set TK_USER and TK_PASS to "
            "access the Tankerkönig data repository."
        )
    for col in ("openingtimes_json", "first_active"):
        if col in df.columns:
            df = df.drop(columns=[col])
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(df.to_json(orient="records", force_ascii=False), encoding="utf-8")
    return df


def _parse_dates_utc(values: pd.Series) -> pd.Series:
    # Preserve explicit offsets and treat naive source timestamps as Berlin local time.
    return parse_timestamps_to_utc(values, TZ)


def _normalize_station_series(series: pd.Series) -> pd.Series:
    series = series.sort_index()
    series = series[~series.index.duplicated(keep="last")]
    if series.empty:
        return series
    if series.index.tz is None:
        series.index = series.index.tz_localize(TZ)
    else:
        series.index = series.index.tz_convert(TZ)
    return series


def _local_dt(day: date, hour: int = 0, minute: int = 0) -> datetime:
    return TZ.localize(
        datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=minute)
    )


def _filled_minute_series(
    series: pd.Series,
    window_start: datetime,
    window_end: datetime,
) -> pd.Series:
    series = _normalize_station_series(series)
    if series.empty:
        return pd.Series(dtype="float64")

    start = TZ.localize(window_start)
    end = TZ.localize(window_end)
    window_series = series.loc[(series.index >= start) & (series.index <= end)]
    prior = series.loc[series.index < start].tail(1)
    base = pd.concat([prior, window_series]).sort_index()
    if base.empty:
        return pd.Series(dtype="float64")

    full_range = pd.date_range(start=start, end=end, freq="1min", tz=TZ)
    reindex_index = base.index.union(full_range)
    return base.reindex(reindex_index).sort_index().ffill().reindex(full_range)


def _load_prices(days: DateRange) -> pd.DataFrame:
    data, _ = _load_prices_with_days(days)
    return data


def _load_prices_with_days(days: DateRange) -> tuple[pd.DataFrame, list[date]]:
    frames: List[pd.DataFrame] = []
    available_days: list[date] = []
    for day in tqdm(list(days.iter_days()), desc="Downloading prices", unit="day"):
        url = f"{TANKER_BASE}/{_data_path('prices', day)}"
        try:
            frames.append(_read_csv_from_url(url, label=f"prices {day:%Y-%m-%d}", show=False))
            available_days.append(day)
        except Exception:
            continue
    if not frames:
        raise RuntimeError(
            "Failed to download any price CSV files. Set TK_USER and TK_PASS "
            "to access the Tankerkönig data repository."
        )
    data = pd.concat(frames, ignore_index=True)
    data["date"] = _parse_dates_utc(data["date"])
    data = data.dropna(subset=["date", "station_uuid"])
    data = data.sort_values("date")
    return data, available_days


def _range_text(hours: List[int]) -> str:
    if not hours:
        return ""
    hours_sorted = sorted(hours)
    start = hours_sorted[0]
    end = start + 1
    parts = []
    for hour in hours_sorted[1:]:
        if hour == end:
            end += 1
        else:
            parts.append(f"{start} - {end}h")
            start = hour
            end = hour + 1
    parts.append(f"{start} - {end}h")
    return ", ".join(parts)


def _cycle_range_text(hours: List[int]) -> str:
    if not hours:
        return ""
    if len(hours) >= 24:
        return "12 - 12h"

    ordered_hours: list[int] = []
    for raw_hour in hours:
        hour = int(raw_hour) % 24
        if ordered_hours and hour == ordered_hours[-1]:
            continue
        ordered_hours.append(hour)

    start = ordered_hours[0]
    previous = start
    parts: list[str] = []
    for hour in ordered_hours[1:]:
        expected = (previous + 1) % 24
        if hour == expected:
            previous = hour
            continue
        parts.append(f"{start} - {(previous + 1) % 24}h")
        start = hour
        previous = hour
    parts.append(f"{start} - {(previous + 1) % 24}h")
    return ", ".join(parts)


def _clock_text(minutes: float | int | None) -> str | None:
    if minutes is None:
        return None
    rounded = int(round(float(minutes))) % (24 * 60)
    hours, mins = divmod(rounded, 60)
    return f"{hours:02d}:{mins:02d}"


def _latest_noon_cycle_days(target_day: date) -> List[date]:
    if target_day < LAW_RESET_DATE:
        return []
    if target_day == LAW_RESET_DATE:
        return [LAW_RESET_DATE]
    return [target_day - timedelta(days=1), target_day]


def _duration_text(minutes: float | int | None) -> str | None:
    if minutes is None:
        return None
    total = max(0, int(round(float(minutes))))
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours}h {mins:02d}m"
    if hours:
        return f"{hours}h"
    return f"{mins} min"


def _station_id_column(stations: pd.DataFrame) -> str:
    return "uuid" if "uuid" in stations.columns else "station_uuid"


def _noon_cycle_windows(analysis_days: List[date]) -> List[dict[str, object]]:
    legal_days = sorted(day for day in analysis_days if day >= LAW_RESET_DATE)
    if not legal_days:
        return []

    analysis_day_set = set(analysis_days)
    full_cycle_days = [day for day in legal_days if day + timedelta(days=1) in analysis_day_set]
    if full_cycle_days:
        windows = []
        for start_day in full_cycle_days:
            prior_reference_time = (
                _local_dt(start_day, 0, 0)
                if start_day == LAW_RESET_DATE
                else _local_dt(start_day - timedelta(days=1), 12, 0)
            )
            windows.append(
                {
                    "date": str(start_day),
                    "start_day": start_day,
                    "kind": "full",
                    "anchor_time": _local_dt(start_day, 12, 0),
                    # Daily metrics cover the completed noon cycle up to 11:59;
                    # the next day's 12:00 belongs to the following cycle.
                    "metric_end_time": _local_dt(start_day + timedelta(days=1), 12, 0)
                    - timedelta(minutes=1),
                    "profile_end_time": _local_dt(start_day + timedelta(days=1), 12, 0),
                    "prior_reference_time": prior_reference_time,
                    "prior_reference_label": "00:00" if start_day == LAW_RESET_DATE else "Vortag 12:00",
                }
            )
        return windows

    if LAW_RESET_DATE not in analysis_day_set:
        return []

    partial_end_day = LAW_RESET_DATE + timedelta(days=1)
    return [
        {
            "date": str(LAW_RESET_DATE),
            "start_day": LAW_RESET_DATE,
            "kind": "partial",
            "anchor_time": _local_dt(LAW_RESET_DATE, 12, 0),
            "metric_end_time": _local_dt(partial_end_day, 0, 0) - timedelta(minutes=1),
            "profile_end_time": _local_dt(partial_end_day, 0, 0),
            "prior_reference_time": _local_dt(LAW_RESET_DATE, 0, 0),
            "prior_reference_label": "00:00",
        }
    ]


def _daily_metric_summary(
    daily_rows: List[dict[str, object]],
    analysis_days: List[date],
) -> dict[str, object]:
    summary: dict[str, object] = {
        "days": len(daily_rows),
        "law_effective_date": str(LAW_RESET_DATE),
        "analysis_start": None,
        "analysis_end": None,
        "analysis_days": len([day for day in analysis_days if day >= LAW_RESET_DATE]),
    }
    if not daily_rows:
        return summary

    summary["analysis_start"] = str(daily_rows[0]["date"])
    summary["analysis_end"] = str(daily_rows[-1]["date"])
    summary["partial_cycles"] = sum(1 for row in daily_rows if row.get("window_kind") == "partial")
    summary["full_cycles"] = sum(1 for row in daily_rows if row.get("window_kind") == "full")

    def numeric_series(key: str) -> pd.Series:
        values = []
        for row in daily_rows:
            raw = row.get(key)
            if raw is None:
                continue
            values.append(float(raw))
        return pd.Series(values, dtype="float64")

    def assign_float_metrics(key: str, digits: int = 3) -> None:
        values = numeric_series(key)
        if values.empty:
            return
        summary[f"{key}_avg"] = round(float(values.mean()), digits)
        summary[f"{key}_median"] = round(float(values.median()), digits)

    def assign_int_metrics(key: str) -> None:
        values = numeric_series(key)
        if values.empty:
            return
        summary[f"{key}_avg"] = round(float(values.mean()), 1)
        summary[f"{key}_median"] = int(round(float(values.median())))

    assign_float_metrics("noon_price")
    assign_float_metrics("max_price")
    assign_float_metrics("prior_reference_price")
    assign_float_metrics("max_price_delta_vs_prior")
    assign_float_metrics("min_price")
    assign_float_metrics("daily_range")
    assign_int_metrics("post_noon_decreases")
    assign_int_metrics("post_noon_increases")

    min_times = numeric_series("min_time_minutes")
    if not min_times.empty:
        avg = int(round(float(min_times.mean())))
        median = int(round(float(min_times.median())))
        summary["min_time_minutes_avg"] = avg
        summary["min_time_minutes_median"] = median
        summary["min_time_text"] = _clock_text(median)

    min_durations = numeric_series("min_duration_minutes")
    if not min_durations.empty:
        avg = int(round(float(min_durations.mean())))
        median = int(round(float(min_durations.median())))
        summary["min_duration_minutes_avg"] = avg
        summary["min_duration_minutes_median"] = median
        summary["min_duration_text"] = _duration_text(median)

    return summary


def _daily_noon_reset_metrics(
    series: pd.Series,
    analysis_days: List[date],
) -> tuple[List[dict[str, object]], dict[str, object]]:
    windows = _noon_cycle_windows(analysis_days)
    if not windows:
        return [], _daily_metric_summary([], analysis_days)

    window_start = min(window["prior_reference_time"] for window in windows)
    window_end = max(window["metric_end_time"] for window in windows)
    filled = _filled_minute_series(series, window_start.replace(tzinfo=None), window_end.replace(tzinfo=None))
    normalized = _normalize_station_series(series)
    if filled.empty or filled.dropna().empty or normalized.empty:
        return [], _daily_metric_summary([], analysis_days)

    daily_rows: List[dict[str, object]] = []
    for window in windows:
        anchor_time = window["anchor_time"]
        metric_end_time = window["metric_end_time"]
        prior_reference_time = window["prior_reference_time"]
        reference_time, reference_price, reference_method = select_noon_reference_from_series(
            normalized,
            window["start_day"],
            TZ,
        )
        if reference_time is None or reference_price is None:
            continue

        observed_series = filled.loc[anchor_time:metric_end_time]
        if observed_series.empty:
            continue
        observed_series = observed_series.dropna()
        if observed_series.empty:
            continue

        prior_reference_price = filled.get(prior_reference_time)
        if pd.isna(prior_reference_price):
            continue

        max_price = float(observed_series.max())
        min_price = float(observed_series.min())
        min_points = observed_series[observed_series == min_price]
        if min_points.empty:
            continue
        first_min = min_points.index[0]

        post_noon_decreases = 0
        post_noon_increases = 0
        previous_value = float(reference_price)
        post_noon_events = normalized.loc[
            (normalized.index > reference_time) & (normalized.index <= metric_end_time)
        ]
        for value in post_noon_events.tolist():
            current_value = float(value)
            if current_value < previous_value - 1e-9:
                post_noon_decreases += 1
            elif current_value > previous_value + 1e-9:
                post_noon_increases += 1
            previous_value = current_value

        min_duration_minutes = int(min_points.shape[0])
        min_time_minutes = first_min.hour * 60 + first_min.minute
        daily_rows.append(
            {
                "date": str(window["date"]),
                "window_kind": window["kind"],
                "window_start_timestamp": reference_time.isoformat(timespec="minutes"),
                "window_end_timestamp": metric_end_time.isoformat(timespec="minutes"),
                "noon_price": round(float(reference_price), 3),
                "noon_reference_method": str(reference_method or ""),
                "max_price": round(max_price, 3),
                "prior_reference_price": round(float(prior_reference_price), 3),
                "prior_reference_label": str(window["prior_reference_label"]),
                "max_price_delta_vs_prior": round(float(max_price - prior_reference_price), 3),
                "post_noon_decreases": post_noon_decreases,
                "post_noon_increases": post_noon_increases,
                "min_price": round(min_price, 3),
                "min_timestamp": first_min.isoformat(timespec="minutes"),
                "min_time_minutes": min_time_minutes,
                "min_time_text": _clock_text(min_time_minutes),
                "min_duration_minutes": min_duration_minutes,
                "min_duration_text": _duration_text(min_duration_minutes),
                "daily_range": round(float(max_price - min_price), 3),
            }
        )

    return daily_rows, _daily_metric_summary(daily_rows, analysis_days)


def _noon_to_noon_markdown_profile(
    series: pd.Series,
    analysis_days: List[date],
) -> tuple[List[dict[str, object]], dict[str, object]]:
    summary: dict[str, object] = {
        "days": 0,
        "cycle_start": None,
        "cycle_end": None,
        "partial": False,
        "last_label": None,
    }
    windows = _noon_cycle_windows(analysis_days)
    if not windows:
        return [], summary

    window_start = min(window["anchor_time"] for window in windows)
    window_end = max(window["profile_end_time"] for window in windows)
    filled = _filled_minute_series(series, window_start.replace(tzinfo=None), window_end.replace(tzinfo=None))
    normalized = _normalize_station_series(series)
    if filled.empty or filled.dropna().empty or normalized.empty:
        return [], summary

    markdown_by_hour: Dict[int, List[float]] = {}
    delta_by_hour: Dict[int, List[float]] = {}
    price_by_hour: Dict[int, List[float]] = {}
    used_windows: List[dict[str, object]] = []
    max_offset = -1

    for window in windows:
        cycle_start = window["anchor_time"]
        cycle_end = window["profile_end_time"]
        reference_time, reference_price, _reference_method = select_noon_reference_from_series(
            normalized,
            window["start_day"],
            TZ,
        )
        if reference_time is None or reference_price is None:
            continue
        closing_reference_price = None
        closing_day = window["start_day"] + timedelta(days=1)
        if window.get("kind") == "full":
            _closing_time, closing_reference_price, _closing_method = select_noon_reference_from_series(
                normalized,
                closing_day,
                TZ,
            )

        cycle_values: List[float] = []
        delta_values: List[float] = []
        absolute_values: List[float] = []
        valid_cycle = True
        total_hours = int((cycle_end - cycle_start).total_seconds() // 3600)
        for offset in range(total_hours + 1):
            ts = cycle_start + timedelta(hours=offset)
            if offset == 0:
                absolute_price = float(reference_price)
            elif offset == total_hours and closing_reference_price is not None:
                absolute_price = float(closing_reference_price)
            else:
                price = filled.get(ts)
                if pd.isna(price):
                    valid_cycle = False
                    break
                absolute_price = float(price)
            delta = absolute_price - float(reference_price)
            markdown = max(0.0, float(reference_price) - absolute_price)
            absolute_values.append(absolute_price)
            delta_values.append(delta)
            cycle_values.append(markdown)

        if not valid_cycle:
            continue

        used_windows.append(window)
        max_offset = max(max_offset, total_hours)
        for offset, markdown in enumerate(cycle_values):
            markdown_by_hour.setdefault(offset, []).append(markdown)
            delta_by_hour.setdefault(offset, []).append(delta_values[offset])
            price_by_hour.setdefault(offset, []).append(absolute_values[offset])

    if not used_windows:
        return [], summary

    hourly_rows = []
    for offset in range(max_offset + 1):
        markdown_values = pd.Series(markdown_by_hour.get(offset, []), dtype="float64")
        delta_values = pd.Series(delta_by_hour.get(offset, []), dtype="float64")
        price_values = pd.Series(price_by_hour.get(offset, []), dtype="float64")
        if markdown_values.empty or delta_values.empty or price_values.empty:
            continue
        clock_hour = (12 + offset) % 24
        hourly_rows.append(
            {
                "cycle_hour": offset,
                "clock_hour": clock_hour,
                "label": f"{clock_hour:02d}",
                "count": int(price_values.count()),
                "price_min": round(float(price_values.min()), 3),
                "price_avg": round(float(price_values.mean()), 3),
                "price_median": round(float(price_values.median()), 3),
                "price_max": round(float(price_values.max()), 3),
                "delta_min": round(float(delta_values.min()), 3),
                "delta_avg": round(float(delta_values.mean()), 3),
                "delta_median": round(float(delta_values.median()), 3),
                "delta_max": round(float(delta_values.max()), 3),
                "markdown_min": round(float(markdown_values.min()), 3),
                "markdown_avg": round(float(markdown_values.mean()), 3),
                "markdown_median": round(float(markdown_values.median()), 3),
                "markdown_max": round(float(markdown_values.max()), 3),
            }
        )

    summary = {
        "days": len(used_windows),
        "cycle_start": str(used_windows[0]["start_day"]),
        "cycle_end": str(used_windows[-1]["profile_end_time"].date()),
        "partial": any(window["kind"] == "partial" for window in used_windows),
        "last_label": hourly_rows[-1]["label"] if hourly_rows else None,
    }
    return hourly_rows, summary


def _latest_price_snapshot(
    prices: pd.DataFrame,
    cutoff: pd.Timestamp,
    fuels: tuple[str, ...],
) -> pd.DataFrame:
    available_fuels = [fuel for fuel in fuels if fuel in prices.columns]
    subset = prices.loc[prices["date"] <= cutoff, ["station_uuid", "date", *available_fuels]].copy()
    if subset.empty:
        return pd.DataFrame(columns=["station_uuid", "date", *fuels])
    for fuel in available_fuels:
        subset[fuel] = pd.to_numeric(subset[fuel], errors="coerce")
    latest = subset.sort_values(["station_uuid", "date"]).groupby("station_uuid", sort=False).tail(1)
    for fuel in fuels:
        if fuel not in latest.columns:
            latest[fuel] = pd.NA
    return latest[["station_uuid", "date", *fuels]].reset_index(drop=True)


def _load_noon_snapshot(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"station_uuid": "string"})


def _snapshot_reference_prices(
    snapshot: pd.DataFrame,
    fuels: tuple[str, ...],
) -> Dict[str, Dict[str, float]]:
    if "station_uuid" not in snapshot.columns:
        return {fuel: {} for fuel in fuels}

    snapshot = snapshot.copy()
    snapshot["station_uuid"] = snapshot["station_uuid"].astype(str)
    references: Dict[str, Dict[str, float]] = {}
    for fuel in fuels:
        if fuel not in snapshot.columns:
            references[fuel] = {}
            continue
        values = pd.to_numeric(
            snapshot[fuel].astype("string").str.strip().str.replace(",", ".", regex=False),
            errors="coerce",
        )
        valid = values > 0
        references[fuel] = {
            station_id: float(price)
            for station_id, price in zip(
                snapshot.loc[valid, "station_uuid"].tolist(),
                values.loc[valid].tolist(),
            )
        }
    return references


def _load_noon_reference_prices(
    output_root: Path,
    fuels: tuple[str, ...],
    analysis_days: List[date],
) -> Dict[date, Dict[str, Dict[str, float]]]:
    legal_days = sorted(day for day in analysis_days if day >= LAW_RESET_DATE)
    if not legal_days:
        return {}

    reference_days = sorted(
        {reference_day for day in legal_days for reference_day in (day - timedelta(days=1), day)}
    )
    references: Dict[date, Dict[str, Dict[str, float]]] = {}

    for target_day in reference_days:
        snapshot_path = _dated_noon_snapshot_path(output_root, target_day)
        snapshot = (
            _load_noon_snapshot(snapshot_path)
            if snapshot_path.exists()
            else pd.DataFrame(columns=["station_uuid", *fuels])
        )
        references[target_day] = _snapshot_reference_prices(snapshot, fuels)

    return references


def _load_midnight_reference_prices(
    output_root: Path,
    fuels: tuple[str, ...],
) -> Dict[str, Dict[str, float]]:
    snapshot_path = output_root / "data" / "midnight.csv"
    if not snapshot_path.exists():
        return {fuel: {} for fuel in fuels}
    snapshot = _load_noon_snapshot(snapshot_path)
    return _snapshot_reference_prices(snapshot, fuels)


def _raw_noon_snapshot(
    prices: pd.DataFrame,
    station_ids: List[str],
    target_day: date,
    fuels: tuple[str, ...],
) -> pd.DataFrame:
    return build_noon_snapshot(prices, station_ids, target_day, TZ, fuels=fuels)


def _station_brand_table(stations: pd.DataFrame) -> pd.DataFrame:
    id_column = _station_id_column(stations)
    brands = stations[[id_column, "brand"]].copy()
    brands = brands.rename(columns={id_column: "station_uuid"})
    brands["brand"] = (
        brands["brand"].fillna("").astype(str).str.strip().str.upper().replace({"": "UNBEKANNT"})
    )
    return brands


def _brand_distribution_row(label: str, values: pd.Series) -> dict[str, object]:
    series = pd.Series(values, dtype="float64").dropna()
    return {
        "brand": label,
        "count": int(series.count()),
        "min": round(float(series.min()), 3),
        "q1": round(float(series.quantile(0.25)), 3),
        "median": round(float(series.quantile(0.5)), 3),
        "avg": round(float(series.mean()), 3),
        "q3": round(float(series.quantile(0.75)), 3),
        "max": round(float(series.max()), 3),
    }


def _brand_distribution_summary(
    snapshot: pd.DataFrame,
    stations: pd.DataFrame,
    fuel: str,
    top_n_brands: int = 9,
) -> List[dict[str, object]]:
    if snapshot.empty or fuel not in snapshot.columns:
        return []

    joined = snapshot[["station_uuid", fuel]].copy()
    joined[fuel] = pd.to_numeric(joined[fuel], errors="coerce")
    joined = joined.dropna(subset=[fuel])
    joined = joined.loc[joined[fuel] > 0]
    if joined.empty:
        return []

    joined = joined.merge(stations, on="station_uuid", how="left")
    joined["brand"] = joined["brand"].fillna("UNBEKANNT")

    counts = joined["brand"].value_counts()
    focus_brands = counts.head(top_n_brands).index.tolist()
    joined["brand_group"] = joined["brand"].where(joined["brand"].isin(focus_brands), "MISC")

    rows = [_brand_distribution_row("Gesamtmarkt", joined[fuel])]
    for brand in focus_brands:
        brand_values = joined.loc[joined["brand_group"] == brand, fuel]
        if brand_values.empty:
            continue
        rows.append(_brand_distribution_row(brand, brand_values))

    misc_values = joined.loc[joined["brand_group"] == "MISC", fuel]
    if not misc_values.empty and counts.size > len(focus_brands):
        rows.append(_brand_distribution_row("MISC", misc_values))
    return rows


def _write_station_output(
    out_dir: Path,
    fuel: str,
    hourly: pd.DataFrame,
    best_hourly: pd.DataFrame,
    minabs: float,
    maxabs: float,
    daily_rows: List[dict[str, object]],
    daily_summary: dict[str, object],
    cycle_hourly: List[dict[str, object]],
    cycle_summary: dict[str, object],
    analysis_start: date,
    analysis_end: date,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{fuel}.csv"
    json_path = out_dir / f"{fuel}.json"

    hourly.to_csv(csv_path, index=False)

    min_val = float(hourly["price"].min())
    max_val = float(hourly["price"].max())
    span = float(max_val - min_val)
    best_source = best_hourly if not best_hourly.empty else hourly
    best_price = float(best_source["price"].min())
    best = best_source[best_source["price"] == best_price]["hour"].astype(int).tolist()
    best_text = _range_text(best)
    cycle_best_rows = [
        row
        for row in cycle_hourly
        if int(row.get("cycle_hour", -1)) < 24 and row.get("price_median") is not None
    ]
    if cycle_best_rows:
        cycle_best_price = min(float(row["price_median"]) for row in cycle_best_rows)
        cycle_best = [
            int(row["clock_hour"])
            for row in cycle_best_rows
            if abs(float(row["price_median"]) - cycle_best_price) <= 0.0005
        ]
        if cycle_best:
            best = cycle_best
            best_text = _cycle_range_text(cycle_best)

    payload = {
        "hourly": hourly.to_dict(orient="records"),
        "text": best_text,
        "besthours": best,
        "min": min_val,
        "max": max_val,
        "minabs": float(minabs),
        "maxabs": float(maxabs),
        "span": span,
        "daily": daily_rows,
        "summary": daily_summary,
        "cycle_hourly": cycle_hourly,
        "cycle_summary": cycle_summary,
        "law_effective_date": str(LAW_RESET_DATE),
        "analysis_start": str(analysis_start),
        "analysis_end": str(analysis_end),
        "analysis_days": (analysis_end - analysis_start).days + 1,
    }

    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _legacy_hourly_variation(
    series: pd.Series,
    window_start: datetime,
    window_end: datetime,
    analysis_days: List[date],
) -> tuple[pd.DataFrame, float, float, int, int]:
    filled = _filled_minute_series(series, window_start, window_end)
    if filled.empty or filled.dropna().empty:
        return pd.DataFrame(columns=["hour", "price"]), 0.0, 0.0, 0, 0

    minabs = float(filled.min())
    maxabs = float(filled.max())

    daily_frames = []
    used_days = 0
    for day in analysis_days:
        day_start = TZ.localize(datetime.combine(day, datetime.min.time()))
        day_end = TZ.localize(datetime.combine(day, datetime.max.time()))
        day_series = filled.loc[day_start:day_end]
        if day_series.dropna().empty:
            continue
        used_days += 1
        daily_mean = float(day_series.mean())
        hourly_mean = day_series.resample("1h").mean()
        hourly_dev = (hourly_mean - daily_mean).to_frame(name="price")
        hourly_dev["hour"] = hourly_dev.index.hour
        daily_frames.append(hourly_dev[["hour", "price"]])

    if not daily_frames:
        return pd.DataFrame(columns=["hour", "price"]), minabs, maxabs, used_days, int(filled.notna().sum())

    grouped = pd.concat(daily_frames).groupby("hour")["price"].mean().reset_index()
    grouped["price"] = grouped["price"].round(2)
    grouped = grouped.set_index("hour").reindex(range(24), fill_value=0).reset_index()
    grouped = grouped.sort_values("hour")
    return grouped, minabs, maxabs, used_days, int(filled.notna().sum())


def _noon_reference_hourly_variation(
    series: pd.Series,
    analysis_days: List[date],
    noon_reference_prices: Dict[date, float],
    midnight_reference_price: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, float, float, int, int]:
    legal_days = sorted(day for day in analysis_days if day >= LAW_RESET_DATE)
    if not legal_days:
        return (
            pd.DataFrame(columns=["hour", "price"]),
            pd.DataFrame(columns=["hour", "price"]),
            0.0,
            0.0,
            0,
            0,
        )

    target_day = max(legal_days)
    fill_start = (
        _local_dt(target_day, 0, 0)
        if target_day == LAW_RESET_DATE
        else _local_dt(target_day - timedelta(days=1), 12, 0)
    )
    day_start = _local_dt(target_day, 0, 0)
    day_end = _local_dt(target_day, 23, 59)
    filled = _filled_minute_series(
        series,
        fill_start.replace(tzinfo=None),
        day_end.replace(tzinfo=None),
    )
    if filled.empty or filled.dropna().empty:
        return (
            pd.DataFrame(columns=["hour", "price"]),
            pd.DataFrame(columns=["hour", "price"]),
            0.0,
            0.0,
            0,
            0,
        )

    day_series = filled.loc[day_start:day_end].dropna()
    if day_series.empty:
        return (
            pd.DataFrame(columns=["hour", "price"]),
            pd.DataFrame(columns=["hour", "price"]),
            0.0,
            0.0,
            0,
            int(filled.notna().sum()),
        )

    prior_day_reference_price = (
        midnight_reference_price
        if target_day == LAW_RESET_DATE and midnight_reference_price is not None and not pd.isna(midnight_reference_price)
        else noon_reference_prices.get(target_day - timedelta(days=1))
    )
    same_day_reference_price = noon_reference_prices.get(target_day)
    if (
        prior_day_reference_price is None
        or pd.isna(prior_day_reference_price)
        or same_day_reference_price is None
        or pd.isna(same_day_reference_price)
    ):
        return (
            pd.DataFrame(columns=["hour", "price"]),
            pd.DataFrame(columns=["hour", "price"]),
            float(day_series.min()),
            float(day_series.max()),
            0,
            int(filled.notna().sum()),
        )

    # The minute-filled series represents the observable price state for every
    # minute. Aggregate that to hourly averages so shortly-after-noon changes are
    # attributed to hour 12 in proportion to how much of the hour they affect.
    # Post-law hours switch reference at noon: before noon against the previous
    # day's noon (or midnight on the first legal day), after noon against the
    # same day's noon reference.
    hourly_average = day_series.resample("1h").mean().dropna()
    if hourly_average.empty:
        return (
            pd.DataFrame(columns=["hour", "price"]),
            pd.DataFrame(columns=["hour", "price"]),
            float(day_series.min()),
            float(day_series.max()),
            0,
            int(filled.notna().sum()),
        )

    delta_frame = hourly_average.to_frame(name="price")
    delta_frame["hour"] = delta_frame.index.hour
    delta_frame["reference_price"] = delta_frame["hour"].map(
        lambda hour: float(prior_day_reference_price) if hour < 12 else float(same_day_reference_price)
    )
    delta_frame["price"] = delta_frame["price"] - delta_frame["reference_price"]
    grouped = delta_frame[["hour", "price"]].copy()
    grouped["price"] = grouped["price"].round(2)
    grouped = grouped.set_index("hour").reindex(range(24), fill_value=0).reset_index()
    grouped = grouped.sort_values("hour")

    absolute_grouped = hourly_average.to_frame(name="price")
    absolute_grouped["hour"] = absolute_grouped.index.hour
    absolute_grouped = absolute_grouped[["hour", "price"]].copy()
    absolute_grouped["price"] = absolute_grouped["price"].round(3)
    absolute_grouped = absolute_grouped.set_index("hour").reindex(range(24)).reset_index()
    absolute_grouped = absolute_grouped.sort_values("hour")

    return (
        grouped,
        absolute_grouped,
        float(day_series.min()),
        float(day_series.max()),
        1,
        int(filled.notna().sum()),
    )


def _hourly_variation(
    series: pd.Series,
    window_start: datetime,
    window_end: datetime,
    analysis_days: List[date],
    noon_reference_prices: Dict[date, float] | None = None,
    midnight_reference_price: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, float, float, int, int]:
    if any(day >= LAW_RESET_DATE for day in analysis_days):
        hourly, best_hourly, minabs, maxabs, used_days, filled_minutes = (
            _noon_reference_hourly_variation(
                series,
                analysis_days,
                noon_reference_prices or {},
                midnight_reference_price=midnight_reference_price,
            )
        )
        if not hourly.empty:
            return hourly, best_hourly, minabs, maxabs, used_days, filled_minutes

    hourly, minabs, maxabs, used_days, filled_minutes = _legacy_hourly_variation(
        series,
        window_start,
        window_end,
        analysis_days,
    )
    return hourly, hourly.copy(), minabs, maxabs, used_days, filled_minutes


def _station_output_dir(base: Path, station_id: str) -> Path:
    parts = station_id.split("-")
    return base.joinpath(*parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-days",
        type=int,
        default=1,
        help="Number of completed days to aggregate into the station-level snapshot.",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="Override the local run date in YYYY-MM-DD format.",
    )
    return parser.parse_args()


def generate(
    output_root: Path,
    analysis_days_count: int = 1,
    today_override: date | None = None,
) -> None:
    today = today_override or date.today()
    print("Starting data generation...")
    try:
        brent_snapshot = _fetch_brent_crude_snapshot()
    except Exception as exc:
        print(f"Warning: failed to refresh Brent crude snapshot: {exc}")
    else:
        brent_path = output_root / "data" / "brent.json"
        brent_path.parent.mkdir(parents=True, exist_ok=True)
        brent_path.write_text(
            json.dumps(brent_snapshot, ensure_ascii=False),
            encoding="utf-8",
        )

    if analysis_days_count < 1:
        raise SystemExit("--analysis-days must be at least 1.")
    desired_analysis_end = today - timedelta(days=1)
    desired_analysis_start = today - timedelta(days=analysis_days_count)
    raw_start = desired_analysis_start
    if analysis_days_count > 1 or desired_analysis_end >= LAW_RESET_DATE:
        raw_start = desired_analysis_start - timedelta(days=1)
    data_end = desired_analysis_end
    data, available_days = _load_prices_with_days(DateRange(raw_start, data_end))
    if desired_analysis_end not in available_days:
        raise RuntimeError(
            f"Required raw price day {desired_analysis_end:%Y-%m-%d} was not available."
        )
    analysis_end = desired_analysis_end
    analysis_start = desired_analysis_start
    stations_frame = download_stations(output_root / "data" / "stations.json", analysis_end)
    print(f"Loaded {len(data):,} price rows.")

    window_start = datetime.combine(analysis_start, datetime.min.time())
    window_end = datetime.combine(analysis_end, datetime.max.time())
    analysis_days = [analysis_start + timedelta(days=offset) for offset in range((analysis_end - analysis_start).days + 1)]

    fuels = ("diesel", "e10", "e5")
    station_ids = (
        stations_frame[_station_id_column(stations_frame)].dropna().astype(str).sort_values().unique().tolist()
    )
    noon_snapshot = build_noon_snapshot(data, station_ids, analysis_end, TZ, fuels=fuels)
    write_snapshot(noon_snapshot, _dated_noon_snapshot_path(output_root, analysis_end))
    write_snapshot(noon_snapshot, output_root / "data" / "noon.csv")
    history_rows: dict[tuple[str, str], list[dict[str, object]]] = {}
    collect_history_rows(
        history_rows,
        noon_snapshot,
        target_day=analysis_end,
        history_start_date=LAW_RESET_DATE,
        fuels=fuels,
    )
    history_paths = write_history_files(output_root, history_rows)
    if history_paths:
        print(f"Wrote {len(history_paths):,} station fuel noon history files")
    noon_reference_prices = _load_noon_reference_prices(
        output_root,
        fuels,
        analysis_days,
    )
    midnight_reference_prices = _load_midnight_reference_prices(output_root, fuels)

    # Collect fast-loading management summaries from the latest completed noon cycle.
    mgmt_hourly_values: Dict[str, Dict[int, List[float]]] = {fuel: {} for fuel in fuels}
    mgmt_cycle_values: Dict[str, Dict[int, List[float]]] = {fuel: {} for fuel in fuels}
    mgmt_hourly_station_counts: Dict[str, int] = {fuel: 0 for fuel in fuels}
    mgmt_cycle_station_counts: Dict[str, int] = {fuel: 0 for fuel in fuels}
    management_cycle_days = _latest_noon_cycle_days(analysis_end)

    for station_id in tqdm(data["station_uuid"].unique(), desc="Processing stations", unit="station"):
        station = data[data["station_uuid"] == station_id].copy()
        if station.empty:
            continue
        station["date"] = _parse_dates_utc(station["date"])
        station = station.dropna(subset=["date"])
        station = station.sort_values("date")

        out_dir = _station_output_dir(output_root / "data2", station_id)

        for fuel in fuels:
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
            hourly, best_hourly, minabs, maxabs, used_days, filled_minutes = _hourly_variation(
                fuel_series,
                window_start,
                window_end,
                analysis_days,
                noon_reference_prices=station_noon_references,
                midnight_reference_price=station_midnight_reference,
            )
            if hourly.empty:
                continue
            cycle_days = management_cycle_days or analysis_days
            daily_rows, daily_summary = _daily_noon_reset_metrics(fuel_series, cycle_days)
            cycle_hourly, cycle_summary = _noon_to_noon_markdown_profile(
                fuel_series,
                cycle_days,
            )

            mgmt_hourly_station_counts[fuel] += 1
            for row in hourly.itertuples(index=False):
                if pd.isna(row.price):
                    continue
                mgmt_hourly_values[fuel].setdefault(int(row.hour), []).append(float(row.price))

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
                    cycle_hour = int(row["cycle_hour"])
                    mgmt_cycle_values[fuel].setdefault(cycle_hour, []).append(float(delta_value))

            _write_station_output(
                out_dir,
                fuel,
                hourly,
                best_hourly=best_hourly,
                minabs=minabs,
                maxabs=maxabs,
                daily_rows=daily_rows,
                daily_summary=daily_summary,
                cycle_hourly=cycle_hourly,
                cycle_summary=cycle_summary,
                analysis_start=analysis_start,
                analysis_end=analysis_end,
            )
            if used_days < len(analysis_days):
                print(
                    f"{station_id} {fuel}: used {used_days}/{len(analysis_days)} days, "
                    f"filled minutes {filled_minutes:,}"
                )

    # Write management summary (boxplot stats per hour) for fast frontend rendering.
    station_brands = _station_brand_table(stations_frame)
    brand_snapshot_time = _local_dt(analysis_end, 12, 0)
    brand_snapshot = noon_snapshot
    brand_distributions = {
        fuel: _brand_distribution_summary(brand_snapshot, station_brands, fuel)
        for fuel in fuels
    }
    noon_reference_histograms, noon_reference_summaries = build_noon_reference_histograms(
        noon_snapshot,
        TZ,
        fuels=fuels,
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
    for fuel in fuels:
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

    mgmt_path = (
        output_root
        / "data2"
        / f"{analysis_end:%Y}"
        / f"{analysis_end:%m}"
        / f"{analysis_end:%d}"
        / "management_boxplots.json"
    )
    mgmt_path.parent.mkdir(parents=True, exist_ok=True)
    mgmt_path.write_text(json.dumps(mgmt_summary, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    args = parse_args()
    output_root = Path(__file__).resolve().parents[1]
    generate(output_root, analysis_days_count=args.analysis_days, today_override=args.today)


if __name__ == "__main__":
    main()
