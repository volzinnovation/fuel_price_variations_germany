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

import certifi
import pandas as pd
import pytz
import requests
from tqdm import tqdm

TZ = pytz.timezone("Europe/Berlin")
LAW_RESET_DATE = date(2026, 4, 1)
TANKER_BASE = (
    "https://data.tankerkoenig.de/"
    "tankerkoenig-organization/tankerkoenig-data/raw/branch/master"
)


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


def download_stations(target_path: Path, target_day: date) -> None:
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


def _parse_dates_utc(values: pd.Series) -> pd.Series:
    # Normalize mixed DST offsets to UTC so pandas 3.x does not fail on spring/fall transitions.
    return pd.to_datetime(values, errors="coerce", utc=True)


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
    frames: List[pd.DataFrame] = []
    for day in tqdm(list(days.iter_days()), desc="Downloading prices", unit="day"):
        url = f"{TANKER_BASE}/{_data_path('prices', day)}"
        try:
            frames.append(_read_csv_from_url(url, label=f"prices {day:%Y-%m-%d}", show=False))
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
    return data


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


def _clock_text(minutes: float | int | None) -> str | None:
    if minutes is None:
        return None
    rounded = int(round(float(minutes))) % (24 * 60)
    hours, mins = divmod(rounded, 60)
    return f"{hours:02d}:{mins:02d}"


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

    assign_float_metrics("midnight_price")
    assign_float_metrics("noon_price")
    assign_float_metrics("min_price")
    assign_float_metrics("daily_range")
    assign_int_metrics("post_noon_decreases")

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
    legal_days = [day for day in analysis_days if day >= LAW_RESET_DATE]
    if not legal_days:
        return [], _daily_metric_summary([], analysis_days)

    window_start = datetime.combine(legal_days[0], datetime.min.time())
    window_end = datetime.combine(legal_days[-1], datetime.min.time()) + timedelta(
        days=1, minutes=-1
    )
    filled = _filled_minute_series(series, window_start, window_end)
    normalized = _normalize_station_series(series)
    if filled.empty or filled.dropna().empty or normalized.empty:
        return [], _daily_metric_summary([], analysis_days)

    daily_rows: List[dict[str, object]] = []
    for day in legal_days:
        day_start = TZ.localize(datetime.combine(day, datetime.min.time()))
        day_end = day_start + timedelta(days=1, minutes=-1)
        noon = day_start + timedelta(hours=12)

        day_series = filled.loc[day_start:day_end]
        if day_series.empty:
            continue
        midnight_price = day_series.iloc[0]
        noon_price = filled.loc[noon] if noon in filled.index else float("nan")
        if pd.isna(midnight_price) or pd.isna(noon_price):
            continue

        day_series = day_series.dropna()
        if day_series.empty:
            continue

        min_price = float(day_series.min())
        min_points = day_series[day_series == min_price]
        if min_points.empty:
            continue
        first_min = min_points.index[0]

        post_noon_decreases = 0
        previous_value = float(noon_price)
        post_noon_events = normalized.loc[(normalized.index > noon) & (normalized.index <= day_end)]
        for value in post_noon_events.tolist():
            current_value = float(value)
            if current_value < previous_value - 1e-9:
                post_noon_decreases += 1
            previous_value = current_value

        min_duration_minutes = int(min_points.shape[0])
        min_time_minutes = first_min.hour * 60 + first_min.minute
        daily_rows.append(
            {
                "date": str(day),
                "midnight_price": round(float(midnight_price), 3),
                "noon_price": round(float(noon_price), 3),
                "post_noon_decreases": post_noon_decreases,
                "min_price": round(min_price, 3),
                "min_timestamp": first_min.isoformat(timespec="minutes"),
                "min_time_minutes": min_time_minutes,
                "min_time_text": _clock_text(min_time_minutes),
                "min_duration_minutes": min_duration_minutes,
                "min_duration_text": _duration_text(min_duration_minutes),
                "daily_range": round(float(day_series.max() - min_price), 3),
            }
        )

    return daily_rows, _daily_metric_summary(daily_rows, analysis_days)


def _noon_to_noon_markdown_profile(
    series: pd.Series,
    analysis_days: List[date],
) -> tuple[List[dict[str, object]], dict[str, object]]:
    legal_days = [day for day in analysis_days if day >= LAW_RESET_DATE]
    summary: dict[str, object] = {
        "days": 0,
        "cycle_start": None,
        "cycle_end": None,
    }
    if len(legal_days) < 2:
        return [], summary

    window_start = datetime.combine(legal_days[0], datetime.min.time()) + timedelta(
        hours=12
    )
    window_end = datetime.combine(legal_days[-1], datetime.min.time()) + timedelta(
        hours=12
    )
    filled = _filled_minute_series(series, window_start, window_end)
    if filled.empty or filled.dropna().empty:
        return [], summary

    markdown_by_hour: Dict[int, List[float]] = {hour: [] for hour in range(25)}
    used_days: List[date] = []

    for start_day in legal_days[:-1]:
        cycle_start = TZ.localize(datetime.combine(start_day, datetime.min.time())) + timedelta(
            hours=12
        )
        anchor_price = filled.get(cycle_start)
        if pd.isna(anchor_price):
            continue

        cycle_values: List[float] = []
        valid_cycle = True
        for offset in range(25):
            ts = cycle_start + timedelta(hours=offset)
            price = filled.get(ts)
            if pd.isna(price):
                valid_cycle = False
                break
            markdown = max(0.0, float(anchor_price) - float(price))
            cycle_values.append(markdown)

        if not valid_cycle:
            continue

        used_days.append(start_day)
        for offset, markdown in enumerate(cycle_values):
            markdown_by_hour[offset].append(markdown)

    if not used_days:
        return [], summary

    hourly_rows = []
    for offset in range(25):
        values = pd.Series(markdown_by_hour[offset], dtype="float64")
        if values.empty:
            continue
        clock_hour = (12 + offset) % 24
        hourly_rows.append(
            {
                "cycle_hour": offset,
                "clock_hour": clock_hour,
                "label": f"{clock_hour:02d}",
                "count": int(values.count()),
                "markdown_min": round(float(values.min()), 3),
                "markdown_avg": round(float(values.mean()), 3),
                "markdown_median": round(float(values.median()), 3),
                "markdown_max": round(float(values.max()), 3),
            }
        )

    summary = {
        "days": len(used_days),
        "cycle_start": str(used_days[0]),
        "cycle_end": str(used_days[-1] + timedelta(days=1)),
    }
    return hourly_rows, summary


def _write_station_output(
    out_dir: Path,
    fuel: str,
    hourly: pd.DataFrame,
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
    best = hourly[hourly["price"] == min_val]["hour"].astype(int).tolist()

    payload = {
        "hourly": hourly.to_dict(orient="records"),
        "text": _range_text(best),
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


def _hourly_variation(
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


def _station_output_dir(base: Path, station_id: str) -> Path:
    parts = station_id.split("-")
    return base.joinpath(*parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-days",
        type=int,
        default=8,
        help="Number of completed days to aggregate into the station-level snapshot.",
    )
    return parser.parse_args()


def generate(output_root: Path, analysis_days_count: int = 8) -> None:
    today = date.today()
    print("Starting data generation...")
    stations_day = today - timedelta(days=1)
    download_stations(output_root / "data" / "stations.json", stations_day)

    if analysis_days_count < 2:
        raise SystemExit("--analysis-days must be at least 2.")
    analysis_start = today - timedelta(days=analysis_days_count)
    analysis_end = today - timedelta(days=1)
    data_start = analysis_start - timedelta(days=1)
    data_end = analysis_end
    data = _load_prices(DateRange(data_start, data_end))
    print(f"Loaded {len(data):,} price rows.")

    window_start = datetime.combine(analysis_start, datetime.min.time())
    window_end = datetime.combine(analysis_end, datetime.max.time())
    analysis_days = [analysis_start + timedelta(days=offset) for offset in range((analysis_end - analysis_start).days + 1)]

    fuels = ("diesel", "e10", "e5")
    # Collect fast-loading management summaries for both legacy hourly and 12->12 cycle views.
    mgmt_hourly_values: Dict[str, Dict[int, List[float]]] = {
        fuel: {hour: [] for hour in range(24)} for fuel in fuels
    }
    mgmt_cycle_values: Dict[str, Dict[int, List[float]]] = {
        fuel: {hour: [] for hour in range(25)} for fuel in fuels
    }
    mgmt_hourly_station_counts: Dict[str, int] = {fuel: 0 for fuel in fuels}
    mgmt_cycle_station_counts: Dict[str, int] = {fuel: 0 for fuel in fuels}

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
            hourly, minabs, maxabs, used_days, filled_minutes = _hourly_variation(
                fuel_series, window_start, window_end, analysis_days
            )
            if hourly.empty:
                continue
            daily_rows, daily_summary = _daily_noon_reset_metrics(fuel_series, analysis_days)
            cycle_hourly, cycle_summary = _noon_to_noon_markdown_profile(
                fuel_series,
                analysis_days,
            )

            if cycle_hourly:
                mgmt_cycle_station_counts[fuel] += 1
                for row in cycle_hourly:
                    mgmt_cycle_values[fuel][int(row["cycle_hour"])].append(
                        float(row["markdown_median"])
                    )
            else:
                mgmt_hourly_station_counts[fuel] += 1
                for row in hourly.itertuples(index=False):
                    mgmt_hourly_values[fuel][int(row.hour)].append(float(row.price))

            _write_station_output(
                out_dir,
                fuel,
                hourly,
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
    mgmt_summary = {
        "snapshot_date": str(analysis_end),
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "analysis_start": str(analysis_start),
        "analysis_end": str(analysis_end),
        "station_counts": {},
        "view_modes": {},
        "fuels": {},
    }
    for fuel in fuels:
        fuel_stats = []
        use_cycle = mgmt_cycle_station_counts[fuel] > 0
        mgmt_summary["view_modes"][fuel] = "cycle" if use_cycle else "hourly"
        mgmt_summary["station_counts"][fuel] = (
            mgmt_cycle_station_counts[fuel]
            if use_cycle
            else mgmt_hourly_station_counts[fuel]
        )
        bucket_count = 25 if use_cycle else 24
        values_by_bucket = mgmt_cycle_values[fuel] if use_cycle else mgmt_hourly_values[fuel]
        for hour in range(bucket_count):
            values = values_by_bucket[hour]
            if values:
                s = pd.Series(values, dtype="float64")
                row = {
                    "hour": hour,
                    "count": int(s.count()),
                    "min": float(s.min()),
                    "q1": float(s.quantile(0.25)),
                    "median": float(s.quantile(0.5)),
                    "q3": float(s.quantile(0.75)),
                    "max": float(s.max()),
                }
            else:
                row = {
                    "hour": hour,
                    "count": 0,
                    "min": 0.0,
                    "q1": 0.0,
                    "median": 0.0,
                    "q3": 0.0,
                    "max": 0.0,
                }
            if use_cycle:
                row["cycle_hour"] = hour
                row["clock_hour"] = (12 + hour) % 24
                row["label"] = f"{((12 + hour) % 24):02d}"
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
    generate(output_root, analysis_days_count=args.analysis_days)


if __name__ == "__main__":
    main()
