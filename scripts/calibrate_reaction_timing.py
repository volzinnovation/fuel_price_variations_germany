#!/usr/bin/env python3
"""Calibrate competitor reaction timing from Azure raw price events."""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

try:
    from generate_data import DateRange, TZ, _load_prices
except ModuleNotFoundError:
    from scripts.generate_data import DateRange, TZ, _load_prices

try:
    from generate_pricing_report import (
        COMPETITION_DECAY_KM,
        RADIUS_LONG_KM,
        RADIUS_SHORT_KM,
        build_grid,
        candidate_ids,
        competition_tier,
        distance_km,
        load_stations,
        percentile,
    )
except ModuleNotFoundError:
    from scripts.generate_pricing_report import (
        COMPETITION_DECAY_KM,
        RADIUS_LONG_KM,
        RADIUS_SHORT_KM,
        build_grid,
        candidate_ids,
        competition_tier,
        distance_km,
        load_stations,
        percentile,
    )


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "reaction_timing_calibration"
FUELS = ("diesel", "e10", "e5")
FUEL_LABELS = {"diesel": "Diesel", "e10": "E10", "e5": "E5"}
MIN_FOCAL_DROP_CENTS = 0.8
MIN_RESPONSE_DROP_CENTS = 0.3
WINDOW_MINUTES = (30, 60, 120, 180)
DEDUPLICATION_MINUTES = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=5, help="Number of completed days to analyze.")
    parser.add_argument(
        "--max-events-per-fuel",
        type=int,
        default=25000,
        help="Optional cap on focal events per fuel after deduplication.",
    )
    return parser.parse_args()


def require_credentials() -> None:
    if os.environ.get("TK_USER") and os.environ.get("TK_PASS"):
        return
    raise SystemExit("TK_USER and TK_PASS are required.")


def build_competition_tiers() -> dict[str, str]:
    stations = load_stations()
    grid = build_grid(stations)
    scores: list[float] = []
    inputs: dict[str, tuple[float, float, int]] = {}
    for station_id, station in stations.items():
        score = 0.0
        nearest = None
        count_2km = 0
        for candidate_id in candidate_ids(station, grid):
            if candidate_id == station_id:
                continue
            dist = distance_km(station, stations[candidate_id])
            if nearest is None or dist < nearest:
                nearest = dist
            if dist <= RADIUS_SHORT_KM:
                count_2km += 1
            if dist <= RADIUS_LONG_KM:
                score += np.exp(-dist / COMPETITION_DECAY_KM)
        scores.append(score)
        inputs[station_id] = (score, 999.0 if nearest is None else nearest, count_2km)
    sorted_scores = sorted(scores)
    q25 = percentile(sorted_scores, 0.25)
    q75 = percentile(sorted_scores, 0.75)
    return {
        station_id: competition_tier(score, q25, q75, nearest, count_2km)
        for station_id, (score, nearest, count_2km) in inputs.items()
    }


def build_neighbors() -> dict[str, list[str]]:
    stations = load_stations()
    grid = build_grid(stations)
    neighbors: dict[str, list[str]] = {}
    for station_id, station in stations.items():
        nearby: list[tuple[str, float]] = []
        for candidate_id in candidate_ids(station, grid):
            if candidate_id == station_id:
                continue
            dist = distance_km(station, stations[candidate_id])
            if dist <= RADIUS_LONG_KM:
                nearby.append((candidate_id, dist))
        nearby.sort(key=lambda item: item[1])
        neighbors[station_id] = [candidate_id for candidate_id, _ in nearby]
    return neighbors


def load_prices_window(days: int) -> pd.DataFrame:
    today = date.today()
    end_day = today - timedelta(days=1)
    start_day = end_day - timedelta(days=days - 1)
    load_start = start_day - timedelta(days=1)
    load_end = end_day
    print(f"Loading Azure raw prices from {load_start} to {load_end}...")
    prices = _load_prices(DateRange(load_start, load_end))
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices = prices.dropna(subset=["date", "station_uuid"]).sort_values("date")
    return prices


def dedupe_events(events: pd.DataFrame) -> pd.DataFrame:
    events = events.sort_values(["station_uuid", "date"])
    last_time: dict[str, pd.Timestamp] = {}
    keep: list[bool] = []
    for row in events.itertuples(index=False):
        ts = row.date
        last = last_time.get(row.station_uuid)
        if last is not None and ts - last < pd.Timedelta(minutes=DEDUPLICATION_MINUTES):
            keep.append(False)
            continue
        last_time[row.station_uuid] = ts
        keep.append(True)
    return events.loc[keep].copy()


def build_event_arrays(prices: pd.DataFrame, fuel: str) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    frame = prices[["station_uuid", "date", fuel]].copy()
    frame[fuel] = pd.to_numeric(frame[fuel], errors="coerce")
    frame = frame.dropna(subset=[fuel]).sort_values(["station_uuid", "date"])
    previous = frame.groupby("station_uuid")[fuel].shift(1)
    frame["delta_cents"] = (frame[fuel] - previous) * 100.0

    response_events = frame[frame["delta_cents"] <= -MIN_RESPONSE_DROP_CENTS][["station_uuid", "date"]].copy()
    response_arrays: dict[str, np.ndarray] = {}
    for station_id, group in response_events.groupby("station_uuid", sort=False):
        response_arrays[station_id] = group["date"].to_numpy(dtype="datetime64[ns]")

    focal_events = frame[frame["delta_cents"] <= -MIN_FOCAL_DROP_CENTS][["station_uuid", "date", "delta_cents"]].copy()
    focal_events = dedupe_events(focal_events)
    return focal_events, response_arrays


def summarize_fuel(
    focal_events: pd.DataFrame,
    response_arrays: dict[str, np.ndarray],
    competition_tiers: dict[str, str],
    neighbors: dict[str, list[str]],
    max_events: int,
) -> list[dict[str, object]]:
    if max_events > 0 and len(focal_events) > max_events:
        focal_events = focal_events.iloc[:max_events].copy()

    stats: dict[str, dict[str, list[float]]] = {
        tier: defaultdict(list) for tier in ("intense", "medium", "relaxed")
    }

    for row in focal_events.itertuples(index=False):
        tier = competition_tiers.get(row.station_uuid)
        if tier is None:
            continue
        t0 = np.datetime64(row.date.to_datetime64())
        best_lag = None
        for neighbor_id in neighbors.get(row.station_uuid, []):
            times = response_arrays.get(neighbor_id)
            if times is None or len(times) == 0:
                continue
            idx = int(np.searchsorted(times, t0, side="right"))
            if idx >= len(times):
                continue
            lag_min = float((times[idx] - t0) / np.timedelta64(1, "m"))
            if lag_min < 0 or lag_min > WINDOW_MINUTES[-1]:
                continue
            if best_lag is None or lag_min < best_lag:
                best_lag = lag_min
        stats[tier]["lags"].append(best_lag if best_lag is not None else np.nan)
        for horizon in WINDOW_MINUTES:
            stats[tier][f"within_{horizon}"].append(
                1.0 if best_lag is not None and best_lag <= horizon else 0.0
            )

    rows: list[dict[str, object]] = []
    for tier, values in stats.items():
        lags = [float(lag) for lag in values["lags"] if not np.isnan(lag)]
        total = len(values["lags"])
        if total == 0:
            continue
        row = {
            "competition_tier": tier,
            "events": total,
            "response_events": len(lags),
            "response_share_30m": round(float(np.mean(values["within_30"])), 4),
            "response_share_60m": round(float(np.mean(values["within_60"])), 4),
            "response_share_120m": round(float(np.mean(values["within_120"])), 4),
            "response_share_180m": round(float(np.mean(values["within_180"])), 4),
            "median_lag_minutes": round(percentile(sorted(lags), 0.5), 1) if lags else None,
            "p25_lag_minutes": round(percentile(sorted(lags), 0.25), 1) if lags else None,
            "p75_lag_minutes": round(percentile(sorted(lags), 0.75), 1) if lags else None,
        }
        rows.append(row)
    return rows


def calibration_multiplier(row: dict[str, object]) -> float:
    median_lag = float(row["median_lag_minutes"] or 180.0)
    response_60 = float(row["response_share_60m"])
    response_120 = float(row["response_share_120m"])
    multiplier = 0.75 + 0.55 * response_60 + 0.30 * response_120 + 0.25 * max(0.0, (90.0 - median_lag) / 90.0)
    return round(clamp(multiplier, 0.75, 1.65), 3)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def generate(days: int, max_events: int) -> tuple[Path, Path]:
    require_credentials()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    competition_tiers = build_competition_tiers()
    neighbors = build_neighbors()
    prices = load_prices_window(days)

    summary_rows: list[dict[str, object]] = []
    for fuel in FUELS:
        print(f"Calibrating reaction timing for {fuel}...")
        focal_events, response_arrays = build_event_arrays(prices, fuel)
        fuel_rows = summarize_fuel(
            focal_events,
            response_arrays,
            competition_tiers,
            neighbors,
            max_events=max_events,
        )
        for row in fuel_rows:
            row["fuel"] = fuel
            row["fuel_label"] = FUEL_LABELS[fuel]
            row["retaliation_multiplier"] = calibration_multiplier(row)
            summary_rows.append(row)

    csv_path = OUTPUT_DIR / "reaction_timing_summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    report_lines = [
        "# Reaction Timing Calibration",
        "",
        f"Generated: {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"Window: last {days} completed days",
        "",
        "## Summary",
        "",
        "| Fuel | Tier | Events | Response <= 30m | Response <= 60m | Response <= 120m | Median lag (min) | Retaliation multiplier |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary_rows:
        report_lines.append(
            "| "
            + row["fuel_label"]
            + f" | {row['competition_tier']}"
            + f" | {row['events']}"
            + f" | {100.0 * float(row['response_share_30m']):.1f}%"
            + f" | {100.0 * float(row['response_share_60m']):.1f}%"
            + f" | {100.0 * float(row['response_share_120m']):.1f}%"
            + f" | {row['median_lag_minutes'] if row['median_lag_minutes'] is not None else 'n/a'}"
            + f" | {row['retaliation_multiplier']:.2f} |"
        )

    report_path = OUTPUT_DIR / "reaction_timing_report.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return report_path, csv_path


def main() -> None:
    args = parse_args()
    report_path, csv_path = generate(args.days, args.max_events_per_fuel)
    print(f"Wrote {report_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
