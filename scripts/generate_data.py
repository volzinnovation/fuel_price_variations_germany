#!/usr/bin/env python3
"""Generate stations.json and per-station hourly price variations."""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List

import certifi
import pandas as pd
import pytz
import requests

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


def _read_csv_from_url(url: str) -> pd.DataFrame:
    user = os.environ.get("TK_USER")
    password = os.environ.get("TK_PASS")
    auth = (user, password) if user and password else None
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
            df = _read_csv_from_url(url)
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


def _load_prices(days: DateRange) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for day in days.iter_days():
        url = f"{TANKER_BASE}/{_data_path('prices', day)}"
        try:
            frames.append(_read_csv_from_url(url))
        except Exception:
            continue
    if not frames:
        raise RuntimeError(
            "Failed to download any price CSV files. Set TK_USER and TK_PASS "
            "to access the Tankerkönig data repository."
        )
    data = pd.concat(frames, ignore_index=True)
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
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
    }

    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _hourly_variation(series: pd.Series, window_start: datetime, window_end: datetime) -> pd.DataFrame:
    series = series.sort_index()
    series = series[~series.index.duplicated(keep="last")]
    if series.empty:
        return pd.DataFrame(columns=["hour", "price"])

    if series.index.tz is None:
        series.index = series.index.tz_localize(TZ)
    else:
        series.index = series.index.tz_convert(TZ)

    start = TZ.localize(window_start)
    end = TZ.localize(window_end)

    series = series.loc[(series.index >= start) & (series.index <= end)]
    if series.empty:
        return pd.DataFrame(columns=["hour", "price"])

    full_range = pd.date_range(start=start, end=end, freq="1min", tz=TZ)
    filled = series.reindex(full_range).ffill().dropna()
    if filled.empty:
        return pd.DataFrame(columns=["hour", "price"])

    mean_all = float(filled.mean())
    hourly = filled.resample("1h").mean() - mean_all
    hourly = hourly.to_frame(name="price")
    hourly["hour"] = hourly.index.hour
    grouped = hourly.groupby("hour")["price"].mean().reset_index()
    grouped["price"] = grouped["price"].round(2)
    grouped = grouped.set_index("hour").reindex(range(24), fill_value=0).reset_index()
    grouped = grouped.sort_values("hour")
    return grouped


def _station_output_dir(base: Path, station_id: str) -> Path:
    parts = station_id.split("-")
    return base.joinpath(*parts)


def generate(output_root: Path) -> None:
    today = date.today()
    stations_day = today - timedelta(days=1)
    download_stations(output_root / "data" / "stations.json", stations_day)

    data_start = today - timedelta(days=8)
    data_end = today - timedelta(days=1)
    data = _load_prices(DateRange(data_start, data_end))

    window_start = datetime.combine(today - timedelta(days=7), datetime.min.time())
    window_end = datetime.combine(today - timedelta(days=1), datetime.max.time())

    for station_id in data["station_uuid"].unique():
        station = data[data["station_uuid"] == station_id].copy()
        if station.empty:
            continue
        station["date"] = pd.to_datetime(station["date"], errors="coerce")
        station = station.dropna(subset=["date"])
        station = station.sort_values("date")

        out_dir = _station_output_dir(output_root / "data2", station_id)

        for fuel in ("diesel", "e10", "e5"):
            if fuel not in station.columns:
                continue
            fuel_series = pd.to_numeric(station.set_index("date")[fuel], errors="coerce").dropna()
            if fuel_series.empty:
                continue
            minabs = float(fuel_series.min())
            maxabs = float(fuel_series.max())
            hourly = _hourly_variation(fuel_series, window_start, window_end)
            if hourly.empty:
                continue
            _write_station_output(out_dir, fuel, hourly, minabs=minabs, maxabs=maxabs)


def main() -> None:
    output_root = Path(__file__).resolve().parents[1]
    generate(output_root)


if __name__ == "__main__":
    main()
