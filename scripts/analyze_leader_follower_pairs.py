#!/usr/bin/env python3
"""Identify local leader/follower price-change pairs from raw price events.

This script works on the last N completed days of Tankerkönig raw prices and
scores nearby station pairs in both directions:

- markdown leadership: station A cuts price and nearby station B often follows
- markup leadership: station A raises price and nearby station B often follows

Outputs:
- pair-level markdown and markup rankings
- station-level net leadership balances
- event-aligned example charts for the strongest pairs
- a concise markdown report
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from generate_data import DateRange, TZ, _load_prices
except ModuleNotFoundError:
    from scripts.generate_data import DateRange, TZ, _load_prices

try:
    from generate_pricing_report import build_grid, candidate_ids, distance_km, load_stations, percentile
except ModuleNotFoundError:
    from scripts.generate_pricing_report import (
        build_grid,
        candidate_ids,
        distance_km,
        load_stations,
        percentile,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "output" / "leader_follower_30d"
FUELS = ("diesel", "e10", "e5")
FUEL_LABELS = {"diesel": "Diesel", "e10": "E10", "e5": "E5"}
FUEL_COLORS = {"diesel": "#155e75", "e10": "#c2410c", "e5": "#7c2d12"}


@dataclass(frozen=True)
class DirectionSpec:
    key: str
    label: str
    short_label: str
    focal_threshold_cents: float
    response_threshold_cents: float
    color: str


DIRECTIONS = (
    DirectionSpec(
        key="markdown",
        label="Markdown leadership",
        short_label="markdown",
        focal_threshold_cents=0.8,
        response_threshold_cents=0.3,
        color="#0f766e",
    ),
    DirectionSpec(
        key="markup",
        label="Markup leadership",
        short_label="markup",
        focal_threshold_cents=0.8,
        response_threshold_cents=0.3,
        color="#7c3aed",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of completed days to analyze.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for CSV, charts, and report outputs.",
    )
    parser.add_argument(
        "--prices-csv",
        type=Path,
        help="Optional local raw price CSV to reuse instead of downloading.",
    )
    parser.add_argument(
        "--cache-prices-csv",
        type=Path,
        help="Optional path to write the loaded raw price window as CSV.",
    )
    parser.add_argument(
        "--response-window-minutes",
        type=int,
        default=90,
        help="Max minutes after a leader event to count a follower response.",
    )
    parser.add_argument(
        "--prewindow-exclusion-minutes",
        type=int,
        default=15,
        help="Exclude follower hits if the follower had already moved in the same direction very recently.",
    )
    parser.add_argument(
        "--min-lag-minutes",
        type=int,
        default=1,
        help="Minimum lag to count as a follower event instead of a same-minute tie.",
    )
    parser.add_argument(
        "--deduplication-minutes",
        type=int,
        default=60,
        help="Per-station focal event deduplication window.",
    )
    parser.add_argument(
        "--min-pair-events",
        type=int,
        default=10,
        help="Minimum focal events required before a pair is retained in the output.",
    )
    parser.add_argument(
        "--min-example-responses",
        type=int,
        default=12,
        help="Minimum matched responses for a pair to qualify for example charts.",
    )
    parser.add_argument(
        "--max-example-pairs",
        type=int,
        default=3,
        help="Maximum number of event-aligned charts to render per direction.",
    )
    parser.add_argument(
        "--max-example-events",
        type=int,
        default=24,
        help="Maximum matched events to average inside an example chart.",
    )
    parser.add_argument(
        "--example-pre-minutes",
        type=int,
        default=30,
        help="Minutes before the leader event in aligned example charts.",
    )
    parser.add_argument(
        "--example-post-minutes",
        type=int,
        default=120,
        help="Minutes after the leader event in aligned example charts.",
    )
    return parser.parse_args()


def load_prices_window(days: int, prices_csv: Path | None, cache_prices_csv: Path | None) -> tuple[pd.DataFrame, date, date]:
    today = date.today()
    end_day = today - timedelta(days=1)
    start_day = end_day - timedelta(days=days - 1)

    if prices_csv is not None:
        print(f"Loading raw prices from {prices_csv}...")
        prices = pd.read_csv(prices_csv)
    else:
        load_start = start_day - timedelta(days=1)
        print(f"Downloading raw prices from {load_start} to {end_day}...")
        prices = _load_prices(DateRange(load_start, end_day))

    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices = prices.dropna(subset=["date", "station_uuid"]).sort_values(["station_uuid", "date"])

    window_start = pd.Timestamp(datetime.combine(start_day, datetime.min.time()))
    window_end = pd.Timestamp(datetime.combine(end_day, datetime.max.time()))
    if prices["date"].dt.tz is not None:
        window_start = TZ.localize(window_start.to_pydatetime())
        window_end = TZ.localize(window_end.to_pydatetime())
    prices = prices.loc[(prices["date"] >= window_start) & (prices["date"] <= window_end)].copy()

    if cache_prices_csv is not None:
        cache_prices_csv.parent.mkdir(parents=True, exist_ok=True)
        prices.to_csv(cache_prices_csv, index=False)
        print(f"Wrote raw price cache to {cache_prices_csv}")

    return prices, start_day, end_day


def dedupe_events(events: pd.DataFrame, minutes: int) -> pd.DataFrame:
    events = events.sort_values(["station_uuid", "date"])
    last_time: dict[str, pd.Timestamp] = {}
    keep: list[bool] = []
    cooldown = pd.Timedelta(minutes=minutes)
    for row in events.itertuples(index=False):
        ts = row.date
        last = last_time.get(row.station_uuid)
        if last is not None and ts - last < cooldown:
            keep.append(False)
            continue
        last_time[row.station_uuid] = ts
        keep.append(True)
    return events.loc[keep].copy()


def build_neighbors_with_distance() -> tuple[dict[str, list[tuple[str, float]]], dict[str, object]]:
    stations = load_stations()
    grid = build_grid(stations)
    neighbors: dict[str, list[tuple[str, float]]] = {}
    for station_id, station in stations.items():
        nearby: list[tuple[str, float]] = []
        for candidate_id in candidate_ids(station, grid):
            if candidate_id == station_id:
                continue
            dist = distance_km(station, stations[candidate_id])
            if dist <= 3.0:
                nearby.append((candidate_id, dist))
        nearby.sort(key=lambda item: item[1])
        neighbors[station_id] = nearby
    return neighbors, stations


def build_fuel_frame(prices: pd.DataFrame, fuel: str) -> pd.DataFrame:
    frame = prices[["station_uuid", "date", fuel]].copy()
    frame[fuel] = pd.to_numeric(frame[fuel], errors="coerce")
    frame = frame.dropna(subset=[fuel]).sort_values(["station_uuid", "date"])
    frame["price_cents"] = frame[fuel] * 100.0
    previous = frame.groupby("station_uuid")[fuel].shift(1)
    frame["delta_cents"] = (frame[fuel] - previous) * 100.0
    return frame.dropna(subset=["delta_cents"]).copy()


def build_direction_events(
    fuel_frame: pd.DataFrame,
    direction: DirectionSpec,
    deduplication_minutes: int,
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    if direction.key == "markdown":
        focal = fuel_frame[fuel_frame["delta_cents"] <= -direction.focal_threshold_cents].copy()
        response = fuel_frame[fuel_frame["delta_cents"] <= -direction.response_threshold_cents].copy()
    else:
        focal = fuel_frame[fuel_frame["delta_cents"] >= direction.focal_threshold_cents].copy()
        response = fuel_frame[fuel_frame["delta_cents"] >= direction.response_threshold_cents].copy()

    focal = focal[["station_uuid", "date", "delta_cents", "price_cents"]].copy()
    response = response[["station_uuid", "date"]].copy()
    focal = dedupe_events(focal, deduplication_minutes)

    response_arrays: dict[str, np.ndarray] = {}
    for station_id, group in response.groupby("station_uuid", sort=False):
        response_arrays[station_id] = group["date"].to_numpy(dtype="datetime64[ns]")
    return focal, response_arrays


def fmean(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def safe_timestamp(value: np.datetime64) -> pd.Timestamp:
    return pd.Timestamp(value).tz_localize("UTC").tz_convert(TZ) if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value).tz_convert(TZ)


def analyze_direction_pairs(
    focal_events: pd.DataFrame,
    response_arrays: dict[str, np.ndarray],
    neighbors: dict[str, list[tuple[str, float]]],
    stations: dict[str, object],
    direction: DirectionSpec,
    response_window_minutes: int,
    prewindow_exclusion_minutes: int,
    min_lag_minutes: int,
    min_pair_events: int,
) -> tuple[list[dict[str, object]], dict[tuple[str, str], list[dict[str, object]]]]:
    pair_stats: dict[tuple[str, str], dict[str, object]] = {}
    sample_events: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    for row in focal_events.itertuples(index=False):
        leader_id = str(row.station_uuid)
        t0 = np.datetime64(row.date.to_datetime64())
        leader_station = stations.get(leader_id)
        if leader_station is None:
            continue
        for follower_id, dist in neighbors.get(leader_id, []):
            key = (leader_id, follower_id)
            stats = pair_stats.setdefault(
                key,
                {
                    "leader_id": leader_id,
                    "follower_id": follower_id,
                    "distance_km": dist,
                    "focal_events": 0,
                    "response_events": 0,
                    "lags": [],
                },
            )
            stats["focal_events"] = int(stats["focal_events"]) + 1

            times = response_arrays.get(follower_id)
            if times is None or len(times) == 0:
                continue

            idx = int(np.searchsorted(times, t0, side="right"))
            if idx > 0:
                prev_time = times[idx - 1]
                prev_gap = float((t0 - prev_time) / np.timedelta64(1, "m"))
                if 0.0 <= prev_gap <= float(prewindow_exclusion_minutes):
                    continue

            if idx >= len(times):
                continue

            lag_minutes = float((times[idx] - t0) / np.timedelta64(1, "m"))
            if lag_minutes < float(min_lag_minutes) or lag_minutes > float(response_window_minutes):
                continue

            stats["response_events"] = int(stats["response_events"]) + 1
            stats["lags"].append(lag_minutes)

            if len(sample_events[key]) < 80:
                sample_events[key].append(
                    {
                        "leader_time": safe_timestamp(t0).isoformat(),
                        "follower_time": safe_timestamp(times[idx]).isoformat(),
                        "lag_minutes": round(lag_minutes, 3),
                        "leader_delta_cents": round(float(row.delta_cents), 3),
                        "leader_post_event_price_cents": round(float(row.price_cents), 3),
                    }
                )

    raw_rows: list[dict[str, object]] = []
    for stats in pair_stats.values():
        focal_count = int(stats["focal_events"])
        if focal_count < min_pair_events:
            continue
        leader_station = stations.get(str(stats["leader_id"]))
        follower_station = stations.get(str(stats["follower_id"]))
        lags = [float(value) for value in stats["lags"]]
        response_count = int(stats["response_events"])
        response_share = response_count / focal_count if focal_count else 0.0
        raw_rows.append(
            {
                "direction": direction.key,
                "leader_station_uuid": stats["leader_id"],
                "leader_station_name": getattr(leader_station, "name", ""),
                "leader_brand": getattr(leader_station, "brand", ""),
                "leader_city": getattr(leader_station, "city", ""),
                "follower_station_uuid": stats["follower_id"],
                "follower_station_name": getattr(follower_station, "name", ""),
                "follower_brand": getattr(follower_station, "brand", ""),
                "follower_city": getattr(follower_station, "city", ""),
                "distance_km": round(float(stats["distance_km"]), 3),
                "focal_events": focal_count,
                "response_events": response_count,
                "response_share": round(response_share, 6),
                "median_lag_minutes": round(percentile(sorted(lags), 0.5), 2) if lags else None,
                "p25_lag_minutes": round(percentile(sorted(lags), 0.25), 2) if lags else None,
                "p75_lag_minutes": round(percentile(sorted(lags), 0.75), 2) if lags else None,
            }
        )

    row_map = {
        (str(row["leader_station_uuid"]), str(row["follower_station_uuid"])): row for row in raw_rows
    }
    for row in raw_rows:
        reverse = row_map.get((str(row["follower_station_uuid"]), str(row["leader_station_uuid"])))
        reverse_share = float(reverse["response_share"]) if reverse is not None else 0.0
        reverse_responses = int(reverse["response_events"]) if reverse is not None else 0
        reverse_median_lag = reverse["median_lag_minutes"] if reverse is not None else None
        asymmetry = float(row["response_share"]) - reverse_share
        support = math.sqrt(max(1.0, float(row["response_events"])))
        speed_bonus = 0.0
        median_lag = row["median_lag_minutes"]
        if median_lag is not None and response_window_minutes > 0:
            speed_bonus = max(0.0, (float(response_window_minutes) - float(median_lag)) / float(response_window_minutes))
        leader_score = max(0.0, asymmetry) * support * (1.0 + 0.35 * speed_bonus)
        row["reverse_response_share"] = round(reverse_share, 6)
        row["reverse_response_events"] = reverse_responses
        row["reverse_median_lag_minutes"] = reverse_median_lag
        row["net_response_share"] = round(asymmetry, 6)
        row["leader_score"] = round(leader_score, 6)
        row["sample_event_count"] = len(sample_events.get((str(row["leader_station_uuid"]), str(row["follower_station_uuid"])), []))

    raw_rows.sort(
        key=lambda item: (
            float(item["leader_score"]),
            float(item["net_response_share"]),
            int(item["response_events"]),
            -float(item["distance_km"]),
        ),
        reverse=True,
    )
    return raw_rows, sample_events


def build_station_role_rows(pair_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    scores: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    labels: dict[str, dict[str, str]] = {}
    for row in pair_rows:
        leader_id = str(row["leader_station_uuid"])
        follower_id = str(row["follower_station_uuid"])
        score = float(row["leader_score"])
        net_share = float(row["net_response_share"])
        if score <= 0.0 or net_share <= 0.0:
            continue

        labels.setdefault(
            leader_id,
            {
                "station_uuid": leader_id,
                "station_name": str(row["leader_station_name"]),
                "brand": str(row["leader_brand"]),
                "city": str(row["leader_city"]),
            },
        )
        labels.setdefault(
            follower_id,
            {
                "station_uuid": follower_id,
                "station_name": str(row["follower_station_name"]),
                "brand": str(row["follower_brand"]),
                "city": str(row["follower_city"]),
            },
        )

        scores[leader_id]["outgoing_leader_score"] += score
        scores[leader_id]["outgoing_pairs"] += 1
        scores[follower_id]["incoming_follower_score"] += score
        scores[follower_id]["incoming_pairs"] += 1

    rows: list[dict[str, object]] = []
    for station_id, values in scores.items():
        net_score = float(values["outgoing_leader_score"]) - float(values["incoming_follower_score"])
        if net_score > 0.5:
            role = "leader"
        elif net_score < -0.5:
            role = "follower"
        else:
            role = "mixed"
        rows.append(
            {
                **labels[station_id],
                "outgoing_leader_score": round(float(values["outgoing_leader_score"]), 6),
                "incoming_follower_score": round(float(values["incoming_follower_score"]), 6),
                "net_role_score": round(net_score, 6),
                "outgoing_pairs": int(values["outgoing_pairs"]),
                "incoming_pairs": int(values["incoming_pairs"]),
                "role_label": role,
            }
        )
    rows.sort(key=lambda item: float(item["net_role_score"]), reverse=True)
    return rows


def station_series_from_frame(fuel_frame: pd.DataFrame, station_id: str) -> pd.Series | None:
    station = fuel_frame.loc[fuel_frame["station_uuid"] == station_id, ["date", "price_cents"]].copy()
    if station.empty:
        return None
    series = pd.Series(station["price_cents"].to_numpy(dtype=np.float64), index=pd.to_datetime(station["date"]))
    series = series[~series.index.duplicated(keep="last")].sort_index()
    if series.index.tz is None:
        series.index = series.index.tz_localize(TZ)
    else:
        series.index = series.index.tz_convert(TZ)
    return series


def aligned_change_series(
    price_series: pd.Series,
    event_time: pd.Timestamp,
    pre_minutes: int,
    post_minutes: int,
) -> np.ndarray | None:
    if price_series.empty:
        return None
    if event_time.tzinfo is None:
        event_time = TZ.localize(event_time.to_pydatetime())
    else:
        event_time = event_time.tz_convert(TZ)

    start = event_time - pd.Timedelta(minutes=pre_minutes)
    end = event_time + pd.Timedelta(minutes=post_minutes)
    full_index = pd.date_range(start=start, end=end, freq="1min", tz=TZ)
    union_index = price_series.index.union(full_index)
    filled = price_series.reindex(union_index).sort_index().ffill().reindex(full_index)
    if filled.isna().all():
        return None

    baseline_time = event_time - pd.Timedelta(minutes=1)
    try:
        baseline = float(filled.loc[baseline_time])
    except KeyError:
        baseline = float(filled.iloc[max(0, pre_minutes - 1)])
    if math.isnan(baseline):
        valid = filled.dropna()
        if valid.empty:
            return None
        baseline = float(valid.iloc[0])
    return filled.to_numpy(dtype=np.float64) - baseline


def render_pair_example_chart(
    fuel: str,
    direction: DirectionSpec,
    pair_row: dict[str, object],
    sample_events: list[dict[str, object]],
    fuel_frame: pd.DataFrame,
    output_path: Path,
    pre_minutes: int,
    post_minutes: int,
    max_example_events: int,
) -> bool:
    leader_id = str(pair_row["leader_station_uuid"])
    follower_id = str(pair_row["follower_station_uuid"])
    leader_series = station_series_from_frame(fuel_frame, leader_id)
    follower_series = station_series_from_frame(fuel_frame, follower_id)
    if leader_series is None or follower_series is None:
        return False

    leader_windows: list[np.ndarray] = []
    follower_windows: list[np.ndarray] = []
    lags: list[float] = []

    for sample in sample_events[:max_example_events]:
        leader_time = pd.Timestamp(str(sample["leader_time"]))
        leader_window = aligned_change_series(leader_series, leader_time, pre_minutes, post_minutes)
        follower_window = aligned_change_series(follower_series, leader_time, pre_minutes, post_minutes)
        if leader_window is None or follower_window is None:
            continue
        leader_windows.append(leader_window)
        follower_windows.append(follower_window)
        lags.append(float(sample["lag_minutes"]))

    if not leader_windows or not follower_windows:
        return False

    x = np.arange(-pre_minutes, post_minutes + 1)
    leader_mean = np.nanmean(np.vstack(leader_windows), axis=0)
    follower_mean = np.nanmean(np.vstack(follower_windows), axis=0)

    plt.figure(figsize=(9.5, 5.5))
    plt.axvline(0, color="#6b7280", linewidth=1.2, linestyle="--", alpha=0.8)
    if lags:
        plt.axvline(float(np.median(lags)), color=direction.color, linewidth=1.2, linestyle=":", alpha=0.9)

    plt.plot(x, leader_mean, color=FUEL_COLORS[fuel], linewidth=2.4, label=f"Leader: {pair_row['leader_station_name']}")
    plt.plot(x, follower_mean, color=direction.color, linewidth=2.4, label=f"Follower: {pair_row['follower_station_name']}")

    plt.xlabel("Minutes relative to leader event")
    plt.ylabel("Mean change vs pre-event price (ct/l)")
    plt.title(
        f"{FUEL_LABELS[fuel]} {direction.short_label}: {pair_row['leader_station_name']} -> {pair_row['follower_station_name']}"
    )
    subtitle = (
        f"Distance {float(pair_row['distance_km']):.2f} km | "
        f"response share {100.0 * float(pair_row['response_share']):.1f}% | "
        f"median lag {pair_row['median_lag_minutes']} min | "
        f"n={len(leader_windows)} matched events"
    )
    plt.suptitle(subtitle, y=0.94, fontsize=10)
    plt.grid(axis="y", color="#e5e7eb", linewidth=0.8)
    plt.legend(frameon=False, fontsize=9, loc="lower right")
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=180)
    plt.close()
    return True


def render_station_role_chart(
    station_rows: list[dict[str, object]],
    fuel: str,
    direction: DirectionSpec,
    output_path: Path,
) -> bool:
    if not station_rows:
        return False

    leaders = station_rows[:8]
    followers = list(reversed(station_rows[-8:]))
    combined = followers + leaders
    labels = [
        f"{row['brand'] or 'Unbekannt'} | {str(row['station_name'])[:28]}"
        for row in combined
    ]
    values = [float(row["net_role_score"]) for row in combined]
    colors = ["#b45309" if value < 0 else "#0f766e" for value in values]

    plt.figure(figsize=(10, 6))
    y_pos = np.arange(len(combined))
    plt.barh(y_pos, values, color=colors)
    plt.axvline(0.0, color="#6b7280", linewidth=1.1)
    plt.yticks(y_pos, labels, fontsize=9)
    plt.xlabel("Net role score")
    plt.title(f"{FUEL_LABELS[fuel]} {direction.short_label}: strongest leaders and followers")
    plt.grid(axis="x", color="#e5e7eb", linewidth=0.8)
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=180)
    plt.close()
    return True


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def select_example_pairs(
    pair_rows: list[dict[str, object]],
    min_example_responses: int,
    max_example_pairs: int,
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    used_station_ids: set[str] = set()
    for row in pair_rows:
        if float(row["leader_score"]) <= 0.0:
            continue
        if int(row["response_events"]) < min_example_responses:
            continue
        if float(row["net_response_share"]) < 0.12:
            continue
        leader_id = str(row["leader_station_uuid"])
        follower_id = str(row["follower_station_uuid"])
        if leader_id in used_station_ids or follower_id in used_station_ids:
            continue
        selected.append(row)
        used_station_ids.add(leader_id)
        used_station_ids.add(follower_id)
        if len(selected) >= max_example_pairs:
            break
    return selected


def top_rows_by_fuel(pair_rows: list[dict[str, object]], limit: int = 8) -> list[dict[str, object]]:
    rows = [row for row in pair_rows if float(row["leader_score"]) > 0.0]
    return rows[:limit]


def top_station_rows(station_rows: list[dict[str, object]], limit: int = 8) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    leaders = station_rows[:limit]
    followers = list(reversed(station_rows[-limit:]))
    return leaders, followers


def format_pair_row(row: dict[str, object]) -> str:
    return (
        f"| {row['leader_brand']} | {row['leader_station_name']} | {row['follower_brand']} | "
        f"{row['follower_station_name']} | {float(row['distance_km']):.2f} | {int(row['focal_events'])} | "
        f"{int(row['response_events'])} | {100.0 * float(row['response_share']):.1f}% | "
        f"{100.0 * float(row['reverse_response_share']):.1f}% | {float(row['net_response_share']):.2f} | "
        f"{row['median_lag_minutes']} | {float(row['leader_score']):.3f} |"
    )


def format_station_row(row: dict[str, object]) -> str:
    return (
        f"| {row['brand']} | {row['station_name']} | {row['city']} | {row['role_label']} | "
        f"{float(row['net_role_score']):.3f} | {float(row['outgoing_leader_score']):.3f} | "
        f"{float(row['incoming_follower_score']):.3f} | {int(row['outgoing_pairs'])} | {int(row['incoming_pairs'])} |"
    )


def build_report(
    args: argparse.Namespace,
    start_day: date,
    end_day: date,
    pair_rows_by_fuel_direction: dict[tuple[str, str], list[dict[str, object]]],
    station_rows_by_fuel_direction: dict[tuple[str, str], list[dict[str, object]]],
    example_chart_paths: list[Path],
    role_chart_paths: list[Path],
) -> str:
    lines = [
        "# Leader-Follower Analysis",
        "",
        f"Generated: {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"Window: {start_day.isoformat()} to {end_day.isoformat()} ({args.days} completed days)",
        "",
        "Method:",
        f"- A leader event is a station move of at least `{DIRECTIONS[0].focal_threshold_cents:.1f}` ct/l for markdowns or markups.",
        f"- A follower event is a nearby same-direction move within `{args.response_window_minutes}` minutes, excluding moves that already happened in the previous `{args.prewindow_exclusion_minutes}` minutes.",
        "- Pair strength is directional: a pair only ranks highly when A->B is materially stronger than B->A.",
        "- These scores describe repeated local sequencing, not causal proof. Common cost shocks, brand policy, and upstream timing can still create apparent leadership.",
        "",
    ]

    for direction in DIRECTIONS:
        lines.extend([f"## {direction.label}", ""])
        for fuel in FUELS:
            pair_rows = pair_rows_by_fuel_direction[(fuel, direction.key)]
            station_rows = station_rows_by_fuel_direction[(fuel, direction.key)]
            top_pairs = top_rows_by_fuel(pair_rows)
            top_leaders, top_followers = top_station_rows(station_rows)

            lines.extend([f"### {FUEL_LABELS[fuel]}", ""])
            lines.append(
                f"- Retained directed pairs: `{len(pair_rows)}`"
            )
            if pair_rows:
                strong_pairs = sum(
                    1
                    for row in pair_rows
                    if float(row["leader_score"]) > 0.0 and float(row["net_response_share"]) >= 0.10
                )
                lines.append(f"- Strong asymmetric pairs: `{strong_pairs}`")
            if top_pairs:
                lines.extend(
                    [
                        "",
                        "| Leader Brand | Leader Station | Follower Brand | Follower Station | km | Focal Events | Responses | Share | Reverse Share | Net Share | Median Lag | Leader Score |",
                        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
                    ]
                )
                for row in top_pairs:
                    lines.append(format_pair_row(row))
            else:
                lines.append("- No usable directed pairs found.")

            if top_leaders:
                lines.extend(
                    [
                        "",
                        "Top net leaders:",
                        "",
                        "| Brand | Station | City | Role | Net Score | Outgoing | Incoming | Out Pairs | In Pairs |",
                        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
                    ]
                )
                for row in top_leaders[:5]:
                    lines.append(format_station_row(row))

            if top_followers:
                lines.extend(
                    [
                        "",
                        "Top net followers:",
                        "",
                        "| Brand | Station | City | Role | Net Score | Outgoing | Incoming | Out Pairs | In Pairs |",
                        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
                    ]
                )
                for row in top_followers[:5]:
                    lines.append(format_station_row(row))
            lines.append("")

    if example_chart_paths:
        lines.extend(["## Charts", ""])
        for path in example_chart_paths:
            lines.append(f"- Example pair chart: `{path}`")
        for path in role_chart_paths:
            lines.append(f"- Station role chart: `{path}`")
        lines.append("")

    return "\n".join(lines) + "\n"


def generate(args: argparse.Namespace) -> tuple[Path, list[Path]]:
    args.output_dir.mkdir(parents=True, exist_ok=True)

    prices, start_day, end_day = load_prices_window(args.days, args.prices_csv, args.cache_prices_csv)
    neighbors, stations = build_neighbors_with_distance()

    pair_rows_by_fuel_direction: dict[tuple[str, str], list[dict[str, object]]] = {}
    station_rows_by_fuel_direction: dict[tuple[str, str], list[dict[str, object]]] = {}
    example_chart_paths: list[Path] = []
    role_chart_paths: list[Path] = []

    for fuel in FUELS:
        print(f"Analyzing {fuel}...")
        fuel_frame = build_fuel_frame(prices, fuel)
        for direction in DIRECTIONS:
            focal_events, response_arrays = build_direction_events(
                fuel_frame,
                direction,
                deduplication_minutes=args.deduplication_minutes,
            )
            pair_rows, sample_events = analyze_direction_pairs(
                focal_events,
                response_arrays,
                neighbors,
                stations,
                direction,
                response_window_minutes=args.response_window_minutes,
                prewindow_exclusion_minutes=args.prewindow_exclusion_minutes,
                min_lag_minutes=args.min_lag_minutes,
                min_pair_events=args.min_pair_events,
            )
            station_rows = build_station_role_rows(pair_rows)

            pair_rows_by_fuel_direction[(fuel, direction.key)] = pair_rows
            station_rows_by_fuel_direction[(fuel, direction.key)] = station_rows

            pair_csv_path = args.output_dir / f"{fuel}_{direction.key}_pairs.csv"
            station_csv_path = args.output_dir / f"{fuel}_{direction.key}_station_roles.csv"
            write_csv(pair_csv_path, pair_rows)
            write_csv(station_csv_path, station_rows)

            role_chart_path = args.output_dir / f"{fuel}_{direction.key}_station_roles.png"
            if render_station_role_chart(station_rows, fuel, direction, role_chart_path):
                role_chart_paths.append(role_chart_path)

            for rank, row in enumerate(
                select_example_pairs(
                    pair_rows,
                    min_example_responses=args.min_example_responses,
                    max_example_pairs=args.max_example_pairs,
                ),
                start=1,
            ):
                key = (str(row["leader_station_uuid"]), str(row["follower_station_uuid"]))
                chart_path = args.output_dir / f"{fuel}_{direction.key}_example_{rank}.png"
                ok = render_pair_example_chart(
                    fuel,
                    direction,
                    row,
                    sample_events.get(key, []),
                    fuel_frame,
                    chart_path,
                    pre_minutes=args.example_pre_minutes,
                    post_minutes=args.example_post_minutes,
                    max_example_events=args.max_example_events,
                )
                if ok:
                    example_chart_paths.append(chart_path)

    report_text = build_report(
        args,
        start_day,
        end_day,
        pair_rows_by_fuel_direction,
        station_rows_by_fuel_direction,
        example_chart_paths,
        role_chart_paths,
    )
    report_path = args.output_dir / "leader_follower_report.md"
    report_path.write_text(report_text, encoding="utf-8")
    return report_path, example_chart_paths + role_chart_paths


def main() -> None:
    args = parse_args()
    report_path, chart_paths = generate(args)
    print(f"Wrote {report_path}")
    for path in chart_paths:
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
