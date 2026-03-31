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


def _write_station_output(
    out_dir: Path,
    fuel: str,
    hourly: pd.DataFrame,
    minabs: float,
    maxabs: float,
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
    series = series.sort_index()
    series = series[~series.index.duplicated(keep="last")]
    if series.empty:
        return pd.DataFrame(columns=["hour", "price"]), 0.0, 0.0, 0, 0

    if series.index.tz is None:
        series.index = series.index.tz_localize(TZ)
    else:
        series.index = series.index.tz_convert(TZ)

    start = TZ.localize(window_start)
    end = TZ.localize(window_end)

    series = series.loc[(series.index >= start) & (series.index <= end)]
    if series.empty:
        return pd.DataFrame(columns=["hour", "price"]), 0.0, 0.0, 0, 0

    full_range = pd.date_range(start=start, end=end, freq="1min", tz=TZ)
    filled = series.reindex(full_range, method="ffill")
    if filled.dropna().empty:
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
    # Collect per-hour deviations across all stations to build a fast-loading management summary.
    mgmt_values: Dict[str, Dict[int, List[float]]] = {
        fuel: {hour: [] for hour in range(24)} for fuel in fuels
    }
    mgmt_station_counts: Dict[str, int] = {fuel: 0 for fuel in fuels}

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

            mgmt_station_counts[fuel] += 1
            for row in hourly.itertuples(index=False):
                mgmt_values[fuel][int(row.hour)].append(float(row.price))

            _write_station_output(
                out_dir,
                fuel,
                hourly,
                minabs=minabs,
                maxabs=maxabs,
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
        "station_counts": mgmt_station_counts,
        "fuels": {},
    }
    for fuel in fuels:
        fuel_stats = []
        for hour in range(24):
            values = mgmt_values[fuel][hour]
            if values:
                s = pd.Series(values, dtype="float64")
                fuel_stats.append(
                    {
                        "hour": hour,
                        "count": int(s.count()),
                        "min": float(s.min()),
                        "q1": float(s.quantile(0.25)),
                        "median": float(s.quantile(0.5)),
                        "q3": float(s.quantile(0.75)),
                        "max": float(s.max()),
                    }
                )
            else:
                fuel_stats.append(
                    {
                        "hour": hour,
                        "count": 0,
                        "min": 0.0,
                        "q1": 0.0,
                        "median": 0.0,
                        "q3": 0.0,
                        "max": 0.0,
                    }
                )
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
