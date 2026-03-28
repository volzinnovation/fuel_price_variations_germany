#!/usr/bin/env python3
"""Simulate counterfactual fuel pricing under a noon-reset regime.

Counterfactual rule:
- stations may increase the price once per day at 12:00
- after 12:00, prices may only stay flat or decrease until the next noon

The script uses the repo snapshot to calibrate local competition, price spans,
and hour-of-day demand opportunity proxies. It then solves a simple best-
response problem for each station and fuel against a nearby-competitor
reference path.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

try:
    from generate_pricing_report import (
        COMPETITION_DECAY_KM,
        DATA2_PATH,
        FUEL_COLORS,
        FUEL_LABELS,
        FUELS,
        MAX_COMPETITORS,
        RADIUS_LONG_KM,
        RADIUS_SHORT_KM,
        RESEARCH_SOURCES,
        build_grid,
        candidate_ids,
        clamp,
        competition_tier,
        distance_km,
        load_stations,
        percentile,
        weighted_quantile,
    )
except ModuleNotFoundError:
    from scripts.generate_pricing_report import (
        COMPETITION_DECAY_KM,
        DATA2_PATH,
        FUEL_COLORS,
        FUEL_LABELS,
        FUELS,
        MAX_COMPETITORS,
        RADIUS_LONG_KM,
        RADIUS_SHORT_KM,
        RESEARCH_SOURCES,
        build_grid,
        candidate_ids,
        clamp,
        competition_tier,
        distance_km,
        load_stations,
        percentile,
        weighted_quantile,
    )

try:
    from generate_data import DateRange, _hourly_variation, _load_prices
except ModuleNotFoundError:
    from scripts.generate_data import DateRange, _hourly_variation, _load_prices


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "pricing_counterfactual"
EXTERNAL_DEMAND_PROXY_PATH = ROOT / "data" / "pricing_external_demand_proxies.csv"
DEFAULT_HISTORY_DAYS = 8
SUPPORTED_HISTORY_SOURCES = ("auto", "snapshot", "azure")

SIM_HOURS = tuple(list(range(12, 24)) + list(range(24, 36)))
SIM_CLOCK_HOURS = tuple(list(range(12, 24)) + list(range(0, 12)))
TIME_KEYS = tuple(
    f"{hour:02d}_00" if hour < 24 else f"nextday_{hour - 24:02d}_00"
    for hour in SIM_HOURS
)
TIME_LABELS = tuple(
    f"{hour:02d}:00" if hour < 24 else f"{hour - 24:02d}:00"
    for hour in SIM_HOURS
)


def checkpoint_halfhour_label(hour_index: int) -> str:
    if hour_index < 24:
        return f"{hour_index:02d}:30"
    return f"{hour_index - 24:02d}:30"

TEMPLATE_ANCHORS = {
    "competitive_ramp": {
        12: 0.00,
        14: 0.16,
        17: 0.39,
        20: 0.61,
        22: 0.74,
        24: 0.78,
        30: 0.83,
        32: 0.88,
        34: 0.95,
        35: 1.00,
    },
    "balanced_hold": {
        12: 0.00,
        14: 0.09,
        17: 0.25,
        20: 0.43,
        22: 0.55,
        24: 0.60,
        30: 0.70,
        32: 0.81,
        34: 0.93,
        35: 1.00,
    },
    "late_release": {
        12: 0.00,
        14: 0.04,
        17: 0.14,
        20: 0.28,
        22: 0.36,
        24: 0.39,
        30: 0.50,
        32: 0.67,
        34: 0.89,
        35: 1.00,
    },
    "overnight_plateau": {
        12: 0.00,
        14: 0.07,
        17: 0.21,
        20: 0.34,
        22: 0.42,
        24: 0.44,
        30: 0.47,
        32: 0.58,
        34: 0.84,
        35: 1.00,
    },
}

REFERENCE_TEMPLATE_BY_TIER = {
    "intense": "competitive_ramp",
    "medium": "balanced_hold",
    "relaxed": "late_release",
}

REFERENCE_RESIDUAL_SHARE = {
    "diesel": {"intense": 0.30, "medium": 0.44, "relaxed": 0.58},
    "e10": {"intense": 0.22, "medium": 0.36, "relaxed": 0.52},
    "e5": {"intense": 0.22, "medium": 0.36, "relaxed": 0.52},
}

ANCHOR_BASE_FACTOR = {"diesel": 0.84, "e10": 0.99, "e5": 0.98}
TIER_ANCHOR_FACTOR = {"intense": 0.94, "medium": 1.00, "relaxed": 1.08}

SENSITIVITY_BY_FUEL_TIER = {
    "diesel": {"intense": 0.56, "medium": 0.40, "relaxed": 0.26},
    "e10": {"intense": 0.72, "medium": 0.52, "relaxed": 0.35},
    "e5": {"intense": 0.70, "medium": 0.50, "relaxed": 0.34},
}

ANCHOR_MULTIPLIERS = np.array([0.70, 0.85, 1.00, 1.15, 1.30, 1.50], dtype=np.float64)
RESIDUAL_SHARES = np.array([0.15, 0.30, 0.45, 0.60, 0.75], dtype=np.float64)


@dataclass(frozen=True)
class FuelStats:
    station_id: str
    fuel: str
    span_cents: float
    minabs: float
    maxabs: float
    midprice: float
    hourly_dev_cents: tuple[float, ...]


@dataclass(frozen=True)
class Candidate:
    template: str
    anchor_multiplier: float
    residual_share: float
    margin_coeffs: np.ndarray


@dataclass(frozen=True)
class HistoryMeta:
    source: str
    analysis_start: date
    analysis_end: date
    analysis_days: int


@dataclass(frozen=True)
class DemandInputs:
    market_volume_index: np.ndarray
    outside_option_mass: np.ndarray
    effective_competitor_count: float
    quality_shift: float
    market_potential_index: float
    traffic_proxy_index: float
    external_proxy_index: float
    city_station_count: int
    postcode_station_count: int


def interpolate_template(anchor_map: dict[int, float]) -> dict[int, float]:
    points = sorted(anchor_map)
    result: dict[int, float] = {}
    for left, right in zip(points, points[1:]):
        left_value = anchor_map[left]
        right_value = anchor_map[right]
        width = right - left
        for hour_index in range(left, right + 1):
            share = 0.0 if width == 0 else (hour_index - left) / width
            result[hour_index] = left_value + (right_value - left_value) * share
    result[points[-1]] = anchor_map[points[-1]]
    return result


TEMPLATE_SHARES = {
    name: np.array(
        [interpolate_template(anchor_map)[hour] for hour in SIM_HOURS],
        dtype=np.float64,
    )
    for name, anchor_map in TEMPLATE_ANCHORS.items()
}


def build_candidates() -> list[Candidate]:
    candidates: list[Candidate] = []
    for template, shares in TEMPLATE_SHARES.items():
        for anchor_multiplier in ANCHOR_MULTIPLIERS:
            for residual_share in RESIDUAL_SHARES:
                margin_coeffs = anchor_multiplier * (1.0 - shares * (1.0 - residual_share))
                candidates.append(
                    Candidate(
                        template=template,
                        anchor_multiplier=float(anchor_multiplier),
                        residual_share=float(residual_share),
                        margin_coeffs=margin_coeffs,
                    )
                )
    return candidates


CANDIDATES = build_candidates()
CANDIDATE_MARGIN_MATRIX = np.vstack([candidate.margin_coeffs for candidate in CANDIDATES])


def fuel_path_to_station_id(path: Path) -> str:
    return "-".join(path.parts[-6:-1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history-days",
        type=int,
        default=DEFAULT_HISTORY_DAYS,
        help="Number of completed days to use for station-level price history statistics.",
    )
    parser.add_argument(
        "--history-source",
        choices=SUPPORTED_HISTORY_SOURCES,
        default="auto",
        help="Use repo snapshot, Azure raw prices, or auto-select based on the requested history window.",
    )
    return parser.parse_args()


def build_history_window(history_days: int) -> tuple[date, date, list[date], datetime, datetime]:
    if history_days < 2:
        raise SystemExit("--history-days must be at least 2.")
    today = date.today()
    analysis_end = today - timedelta(days=1)
    analysis_start = analysis_end - timedelta(days=history_days - 1)
    analysis_days = [
        analysis_start + timedelta(days=offset)
        for offset in range((analysis_end - analysis_start).days + 1)
    ]
    window_start = datetime.combine(analysis_start, datetime.min.time())
    window_end = datetime.combine(analysis_end, datetime.max.time())
    return analysis_start, analysis_end, analysis_days, window_start, window_end


def require_credentials() -> None:
    if os.environ.get("TK_USER") and os.environ.get("TK_PASS"):
        return
    raise SystemExit(
        "Azure-backed history loading requires TK_USER and TK_PASS. "
        "Either export those credentials or use --history-source snapshot."
    )


def latest_snapshot_meta() -> HistoryMeta:
    candidates = sorted(DATA2_PATH.rglob("management_boxplots.json"))
    if candidates:
        payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
        analysis_start = date.fromisoformat(payload["analysis_start"])
        analysis_end = date.fromisoformat(payload["analysis_end"])
        return HistoryMeta(
            source="snapshot",
            analysis_start=analysis_start,
            analysis_end=analysis_end,
            analysis_days=(analysis_end - analysis_start).days + 1,
        )

    analysis_start, analysis_end, _, _, _ = build_history_window(DEFAULT_HISTORY_DAYS)
    return HistoryMeta(
        source="snapshot",
        analysis_start=analysis_start,
        analysis_end=analysis_end,
        analysis_days=DEFAULT_HISTORY_DAYS,
    )


def load_snapshot_fuel_stats() -> dict[str, dict[str, FuelStats]]:
    stats_by_station: dict[str, dict[str, FuelStats]] = defaultdict(dict)
    for fuel in FUELS:
        for path in DATA2_PATH.rglob(f"{fuel}.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            hourly = payload.get("hourly")
            if not hourly or "minabs" not in payload or "maxabs" not in payload or "span" not in payload:
                continue
            hourly_map = {int(row["hour"]): float(row["price"]) * 100.0 for row in hourly}
            if len(hourly_map) < 20:
                continue
            minabs = float(payload["minabs"])
            maxabs = float(payload["maxabs"])
            if minabs <= 0.5 or maxabs <= 0.5:
                continue
            station_id = fuel_path_to_station_id(path)
            stats_by_station[station_id][fuel] = FuelStats(
                station_id=station_id,
                fuel=fuel,
                span_cents=round(float(payload["span"]) * 100.0, 2),
                minabs=minabs,
                maxabs=maxabs,
                midprice=(minabs + maxabs) / 2.0,
                hourly_dev_cents=tuple(hourly_map.get(hour, 0.0) for hour in range(24)),
            )
    return dict(stats_by_station)


def load_azure_fuel_stats(history_days: int) -> tuple[dict[str, dict[str, FuelStats]], HistoryMeta]:
    require_credentials()
    analysis_start, analysis_end, analysis_days, window_start, window_end = build_history_window(
        history_days
    )
    load_start = analysis_start - timedelta(days=1)
    prices = _load_prices(DateRange(load_start, analysis_end))
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices = prices.dropna(subset=["date", "station_uuid"]).sort_values(["station_uuid", "date"])

    stats_by_station: dict[str, dict[str, FuelStats]] = defaultdict(dict)
    for station_id, station_frame in prices.groupby("station_uuid", sort=False):
        station_frame = station_frame.sort_values("date")
        station_series = station_frame.set_index("date")
        for fuel in FUELS:
            if fuel not in station_series.columns:
                continue
            fuel_series = pd.to_numeric(station_series[fuel], errors="coerce").dropna()
            if fuel_series.empty:
                continue
            hourly, minabs, maxabs, used_days, _ = _hourly_variation(
                fuel_series,
                window_start,
                window_end,
                analysis_days,
            )
            if hourly.empty or len(hourly) < 20:
                continue
            if minabs <= 0.5 or maxabs <= 0.5:
                continue
            if used_days < max(5, int(0.8 * history_days)):
                continue
            hourly_map = {int(row.hour): float(row.price) * 100.0 for row in hourly.itertuples(index=False)}
            span_cents = (float(hourly["price"].max()) - float(hourly["price"].min())) * 100.0
            stats_by_station[station_id][fuel] = FuelStats(
                station_id=station_id,
                fuel=fuel,
                span_cents=round(span_cents, 2),
                minabs=float(minabs),
                maxabs=float(maxabs),
                midprice=(float(minabs) + float(maxabs)) / 2.0,
                hourly_dev_cents=tuple(hourly_map.get(hour, 0.0) for hour in range(24)),
            )

    return dict(stats_by_station), HistoryMeta(
        source="azure",
        analysis_start=analysis_start,
        analysis_end=analysis_end,
        analysis_days=history_days,
    )


def load_fuel_stats(
    history_days: int = DEFAULT_HISTORY_DAYS,
    history_source: str = "auto",
) -> tuple[dict[str, dict[str, FuelStats]], HistoryMeta]:
    snapshot_meta = latest_snapshot_meta()
    if history_source == "snapshot":
        return load_snapshot_fuel_stats(), snapshot_meta
    if history_source == "azure":
        return load_azure_fuel_stats(history_days)
    if history_source != "auto":
        raise SystemExit(f"Unsupported history source: {history_source}")
    if history_days == snapshot_meta.analysis_days:
        return load_snapshot_fuel_stats(), snapshot_meta
    if os.environ.get("TK_USER") and os.environ.get("TK_PASS"):
        return load_azure_fuel_stats(history_days)
    raise SystemExit(
        f"Requested {history_days} history days, but the local snapshot only covers "
        f"{snapshot_meta.analysis_days} days. Export TK_USER/TK_PASS or use "
        "--history-source snapshot."
    )


def build_market_hourly_profile(
    stats_by_station: dict[str, dict[str, FuelStats]]
) -> dict[str, dict[int, float]]:
    values: dict[str, dict[int, list[float]]] = {
        fuel: defaultdict(list) for fuel in FUELS
    }
    for fuel_map in stats_by_station.values():
        for fuel, stats in fuel_map.items():
            for hour, value in enumerate(stats.hourly_dev_cents):
                values[fuel][hour].append(value)

    profile: dict[str, dict[int, float]] = {fuel: {} for fuel in FUELS}
    for fuel in FUELS:
        for hour in range(24):
            hour_values = sorted(values[fuel][hour])
            profile[fuel][hour] = percentile(hour_values, 0.5)
    return profile


def build_demand_weights(
    market_profile: dict[str, dict[int, float]]
) -> dict[str, np.ndarray]:
    weights: dict[str, np.ndarray] = {}
    for fuel in FUELS:
        hours = [market_profile[fuel][hour] for hour in SIM_CLOCK_HOURS]
        weights[fuel] = np.array(
            [clamp(1.0 + 0.08 * value, 0.72, 1.52) for value in hours],
            dtype=np.float64,
        )
    return weights


def normalize_key(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def safe_ratio(value: float, baseline: float, lower: float, upper: float) -> float:
    if baseline <= 0:
        return 1.0
    return clamp(value / baseline, lower, upper)


def load_external_demand_proxies(stations: dict[str, object]) -> dict[str, float]:
    if not EXTERNAL_DEMAND_PROXY_PATH.exists():
        return {station_id: 1.0 for station_id in stations}

    by_station: dict[str, float] = {}
    by_postcode: dict[str, float] = {}
    by_city: dict[str, float] = {}
    with EXTERNAL_DEMAND_PROXY_PATH.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_value = row.get("traffic_index") or row.get("volume_index") or row.get("demand_index")
            if raw_value is None:
                continue
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if row.get("station_uuid"):
                by_station[row["station_uuid"].strip()] = value
            if row.get("post_code"):
                by_postcode[row["post_code"].strip()] = value
            if row.get("city"):
                by_city[normalize_key(row["city"])] = value

    proxies: dict[str, float] = {}
    for station_id, station in stations.items():
        proxies[station_id] = clamp(
            by_station.get(
                station_id,
                by_postcode.get(
                    (station.post_code or "").strip(),
                    by_city.get(normalize_key(station.city), 1.0),
                ),
            ),
            0.4,
            3.0,
        )
    return proxies


def build_station_demand_inputs(
    *,
    station: object,
    station_id: str,
    fuel: str,
    own: FuelStats,
    score: float,
    pressure: float,
    same_brand_share: float,
    gap_index: float,
    base_demand_weights: dict[str, np.ndarray],
    city_station_counts: Counter,
    postcode_station_counts: Counter,
    fuel_span_baselines: dict[str, float],
    city_log_baseline: float,
    postcode_log_baseline: float,
    external_proxy_map: dict[str, float],
) -> DemandInputs:
    city_count = city_station_counts[normalize_key(station.city)]
    postcode_count = postcode_station_counts[(station.post_code or "").strip()]
    city_scale = safe_ratio(math.log1p(city_count), city_log_baseline, 0.6, 1.9)
    postcode_scale = safe_ratio(math.log1p(postcode_count), postcode_log_baseline, 0.6, 1.8)
    span_scale = safe_ratio(own.span_cents, fuel_span_baselines[fuel], 0.65, 1.75)
    competition_scale = clamp(0.85 + pressure, 0.6, 1.85)
    external_proxy = clamp(external_proxy_map.get(station_id, 1.0), 0.4, 3.0)

    market_potential = clamp(
        (0.32 * city_scale)
        + (0.12 * postcode_scale)
        + (0.18 * span_scale)
        + (0.22 * competition_scale)
        + (0.06 * (1.0 + same_brand_share))
        + (0.10 * external_proxy),
        0.55,
        2.4,
    )
    traffic_proxy = clamp(
        0.60 * market_potential + 0.40 * external_proxy,
        0.55,
        2.6,
    )
    market_volume = np.clip(base_demand_weights[fuel] * traffic_proxy, 0.25, None)
    centered = market_volume - float(np.mean(market_volume))
    outside_option = np.clip(1.20 - (0.30 * centered), 0.25, 1.85)
    effective_competitors = clamp(max(score, 0.85) * (0.95 + (0.20 * pressure)), 1.0, 8.5)
    quality_shift = (
        (0.10 if (station.brand or "Unbekannt") != "Unbekannt" else 0.0)
        + (0.04 * same_brand_share)
        - (0.10 * max(gap_index, 0.0))
        + (0.05 * max(-gap_index, 0.0))
    )

    return DemandInputs(
        market_volume_index=market_volume.astype(np.float64),
        outside_option_mass=outside_option.astype(np.float64),
        effective_competitor_count=float(effective_competitors),
        quality_shift=float(quality_shift),
        market_potential_index=float(market_potential),
        traffic_proxy_index=float(traffic_proxy),
        external_proxy_index=float(external_proxy),
        city_station_count=int(city_count),
        postcode_station_count=int(postcode_count),
    )


def market_share_matrix(
    gaps: np.ndarray,
    sensitivity_lambda: float,
    demand_inputs: DemandInputs,
    sensitivity_multiplier: float = 1.0,
) -> np.ndarray:
    utility = demand_inputs.quality_shift - ((0.90 * sensitivity_lambda * sensitivity_multiplier) * gaps)
    own_mass = np.exp(np.clip(utility, -8.0, 8.0))
    competitor_mass = demand_inputs.effective_competitor_count
    return own_mass / (
        demand_inputs.outside_option_mass[np.newaxis, :] + competitor_mass + own_mass
    )


def hour_key(hour_index: int) -> str:
    if hour_index < 24:
        return f"{hour_index:02d}_00"
    return f"nextday_{hour_index - 24:02d}_00"


def hour_label(hour_index: int) -> str:
    if hour_index < 24:
        return f"{hour_index:02d}:00"
    return f"{hour_index - 24:02d}:00"


def reference_margin_curve(
    fuel: str,
    tier: str,
    reference_anchor_cents: float,
) -> tuple[str, float, np.ndarray]:
    template_name = REFERENCE_TEMPLATE_BY_TIER[tier]
    residual_share = REFERENCE_RESIDUAL_SHARE[fuel][tier]
    shares = TEMPLATE_SHARES[template_name]
    margins = reference_anchor_cents * (1.0 - shares * (1.0 - residual_share))
    return template_name, residual_share, margins


def strategy_family(template_name: str, anchor_gap_cents: float, residual_share: float) -> str:
    if template_name == "competitive_ramp":
        return "defensive_match"
    if template_name == "overnight_plateau":
        return "plateau_hold"
    if template_name == "late_release" and residual_share >= 0.60:
        return "margin_preserve"
    if anchor_gap_cents > 0.8 and residual_share >= 0.45:
        return "hold_high"
    return "balanced_release"


def render_chart(rows: list[dict[str, object]], chart_path: Path) -> None:
    x = np.arange(len(SIM_HOURS))
    fig, ax = plt.subplots(figsize=(18, 8.4))

    for fuel in FUELS:
        fuel_rows = [row for row in rows if row["fuel"] == fuel]
        medians: list[float] = []
        p25s: list[float] = []
        p75s: list[float] = []
        for key in TIME_KEYS:
            values = sorted(float(row[f"markdown_{key}_cents"]) for row in fuel_rows)
            medians.append(percentile(values, 0.5))
            p25s.append(percentile(values, 0.25))
            p75s.append(percentile(values, 0.75))

        color = FUEL_COLORS[fuel]
        ax.fill_between(x, p25s, p75s, color=color, alpha=0.15)
        ax.plot(x, medians, color=color, linewidth=2.4, label=FUEL_LABELS[fuel])

    ax.set_ylim(-10, 10)
    ax.invert_yaxis()

    adjustment_box = Rectangle(
        (-0.5, -8.0),
        0.5,
        8.0,
        facecolor="#e2e8f0",
        edgecolor="#475569",
        linewidth=1.4,
        hatch="//",
        alpha=0.55,
        zorder=1,
    )
    ax.add_patch(adjustment_box)
    ax.text(
        -0.25,
        -4.0,
        "Preisabstimmung",
        rotation=90,
        ha="center",
        va="center",
        color="#1e293b",
        fontsize=9,
        zorder=4,
    )

    ax.axvline(11.5, color="#475569", linestyle="--", linewidth=1.2, alpha=0.8)
    ax.text(
        5.5,
        1.08,
        "Gleicher Tag",
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="bottom",
        fontsize=10,
        color="#334155",
    )
    ax.text(
        17.5,
        1.08,
        "Folgetag",
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="bottom",
        fontsize=10,
        color="#334155",
    )

    tick_positions = list(range(len(SIM_HOURS)))
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(
        [checkpoint_halfhour_label(SIM_HOURS[pos]) for pos in tick_positions],
        fontsize=8,
    )
    ax.set_xlim(-0.5, len(SIM_HOURS) - 0.5)
    ax.tick_params(
        axis="x",
        pad=10,
        top=True,
        labeltop=True,
        bottom=False,
        labelbottom=False,
    )
    ax.xaxis.tick_top()
    y_ticks = [-10, -8, -6, -4, -2, 0, 2, 4, 6, 8, 10]
    y_tick_labels = ["", "", "", "", "", "0", "2", "4", "6", "8", "10"]
    ax.set_yticks(y_ticks)
    ax.set_yticklabels(y_tick_labels)
    ax.set_ylabel("Simulierte kumulierte Preissenkung seit 12:00 (ct/Liter)")
    ax.set_title("Simulierte optimale Preissenkungsleiter (Mittagsreset)")
    ax.grid(axis="y", color="#cbd5e1", linewidth=0.8, alpha=0.7)
    ax.legend(frameon=False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["top"].set_visible(True)

    note = (
        "Linie = Median der simulierten optimalen Strategie je Kraftstoff. "
        "Band = Interquartilsabstand über alle Tankstellen. "
        "Die Leiter verläuft stündlich, aber auf halbstündig versetzter Zeitachse "
        "von 12:30 Uhr bis 11:30 Uhr am Folgetag."
    )
    fig.subplots_adjust(left=0.07, right=0.98, top=0.72, bottom=0.16)
    fig.text(0.01, 0.075, note, fontsize=9, color="#475569")
    fig.text(0.01, 0.02, "@ProfVolz", fontsize=9, color="#334155", ha="left")
    fig.text(0.99, 0.02, "tankzeit.de", fontsize=9, color="#334155", ha="right")
    fig.savefig(chart_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def generate(
    history_days: int = DEFAULT_HISTORY_DAYS,
    history_source: str = "auto",
) -> tuple[Path, Path, Path]:
    stations = load_stations()
    stats_by_station, history_meta = load_fuel_stats(history_days=history_days, history_source=history_source)
    active_ids = sorted(station_id for station_id in stats_by_station if station_id in stations)
    active_stations = {station_id: stations[station_id] for station_id in active_ids}
    grid = build_grid(stations)

    market_profile = build_market_hourly_profile(stats_by_station)
    base_demand_weights = build_demand_weights(market_profile)
    external_proxy_map = load_external_demand_proxies(active_stations)
    city_station_counts = Counter(normalize_key(station.city) for station in active_stations.values())
    postcode_station_counts = Counter((station.post_code or "").strip() for station in active_stations.values())
    city_log_baseline = percentile(
        sorted(math.log1p(max(count, 1)) for count in city_station_counts.values()),
        0.5,
    )
    postcode_log_baseline = percentile(
        sorted(math.log1p(max(count, 1)) for count in postcode_station_counts.values()),
        0.5,
    )
    fuel_span_baselines = {
        fuel: percentile(
            sorted(
                stats.span_cents
                for fuel_map in stats_by_station.values()
                for current_fuel, stats in fuel_map.items()
                if current_fuel == fuel
            ),
            0.5,
        )
        for fuel in FUELS
    }

    competition_scores: list[float] = []
    competition_inputs: dict[str, dict[str, object]] = {}
    for station_id, station in active_stations.items():
        nearby: list[tuple[str, float]] = []
        count_2km = 0
        count_3km = 0
        same_brand = 0
        nearest_km = None
        score = 0.0

        for candidate_id in candidate_ids(station, grid):
            if candidate_id == station_id:
                continue
            candidate = stations[candidate_id]
            dist = distance_km(station, candidate)
            if nearest_km is None or dist < nearest_km:
                nearest_km = dist
            if dist <= RADIUS_SHORT_KM:
                count_2km += 1
            if dist <= RADIUS_LONG_KM:
                count_3km += 1
                nearby.append((candidate_id, dist))
                score += math.exp(-dist / COMPETITION_DECAY_KM)
                if candidate.brand == station.brand:
                    same_brand += 1

        nearby.sort(key=lambda item: item[1])
        competition_scores.append(score)
        competition_inputs[station_id] = {
            "nearby": nearby,
            "count_2km": count_2km,
            "count_3km": count_3km,
            "same_brand_share": (same_brand / count_3km) if count_3km else 0.0,
            "nearest_km": 999.0 if nearest_km is None else nearest_km,
            "score": score,
        }

    sorted_scores = sorted(competition_scores)
    q25 = percentile(sorted_scores, 0.25)
    q75 = percentile(sorted_scores, 0.75)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "station_counterfactual_strategies.csv"
    report_path = OUTPUT_DIR / "counterfactual_pricing_report.md"
    chart_path = OUTPUT_DIR / "counterfactual_market_ladder.png"

    competition_tiers = Counter()
    family_counts = Counter()
    rows: list[dict[str, object]] = []
    fuel_summary: dict[str, dict[str, list[float]]] = {
        fuel: defaultdict(list) for fuel in FUELS
    }

    for station_id in active_ids:
        station = active_stations[station_id]
        info = competition_inputs[station_id]
        nearby = info["nearby"]
        nearest_km = float(info["nearest_km"])
        count_2km = int(info["count_2km"])
        count_3km = int(info["count_3km"])
        same_brand_share = float(info["same_brand_share"])
        score = float(info["score"])
        pressure = clamp((score - q25) / max(q75 - q25, 0.001), 0.0, 1.0)
        tier = competition_tier(score, q25, q75, nearest_km, count_2km)
        competition_tiers[tier] += 1

        for fuel, own in stats_by_station[station_id].items():
            competitor_values: list[tuple[float, FuelStats]] = []
            for competitor_id, dist in nearby:
                competitor_stats = stats_by_station.get(competitor_id, {}).get(fuel)
                if competitor_stats is None:
                    continue
                weight = math.exp(-dist / COMPETITION_DECAY_KM)
                competitor_values.append((weight, competitor_stats))

            if competitor_values:
                weights = [weight for weight, _ in competitor_values]
                local_span_cents = weighted_quantile(
                    [stats.span_cents for _, stats in competitor_values], weights
                )
                local_midprice = weighted_quantile(
                    [stats.midprice for _, stats in competitor_values], weights
                )
                local_floor = weighted_quantile(
                    [stats.minabs for _, stats in competitor_values], weights, q=0.35
                )
            else:
                local_span_cents = own.span_cents
                local_midprice = own.midprice
                local_floor = own.minabs

            span_blend = (0.55 * own.span_cents) + (0.45 * local_span_cents)
            price_gap_cents = round((own.midprice - local_midprice) * 100.0, 2)
            gap_index = clamp(price_gap_cents / max(span_blend, 1.0), -1.0, 1.0)

            effective_floor = clamp(
                (0.68 * own.minabs) + (0.32 * local_floor),
                own.minabs - 0.02,
                own.minabs + 0.03,
            )
            reference_floor = (0.25 * own.minabs) + (0.75 * local_floor)

            anchor_base = clamp(
                span_blend
                * ANCHOR_BASE_FACTOR[fuel]
                * TIER_ANCHOR_FACTOR[tier]
                * (
                    1.0
                    - (0.16 * pressure)
                    + (0.08 * same_brand_share)
                    - (0.15 * max(gap_index, 0.0))
                    + (0.06 * max(-gap_index, 0.0))
                ),
                1.4,
                12.5,
            )

            reference_anchor = clamp(
                local_span_cents
                * ANCHOR_BASE_FACTOR[fuel]
                * {"intense": 0.97, "medium": 1.05, "relaxed": 1.13}[tier]
                * (1.0 - (0.08 * pressure) + (0.08 * same_brand_share)),
                1.2,
                13.0,
            )

            reference_template_name, reference_residual_share, reference_margin_curve_cents = (
                reference_margin_curve(fuel, tier, reference_anchor)
            )
            floor_gap_cents = (effective_floor - reference_floor) * 100.0
            demand_inputs = build_station_demand_inputs(
                station=station,
                station_id=station_id,
                fuel=fuel,
                own=own,
                score=score,
                pressure=pressure,
                same_brand_share=same_brand_share,
                gap_index=gap_index,
                base_demand_weights=base_demand_weights,
                city_station_counts=city_station_counts,
                postcode_station_counts=postcode_station_counts,
                fuel_span_baselines=fuel_span_baselines,
                city_log_baseline=city_log_baseline,
                postcode_log_baseline=postcode_log_baseline,
                external_proxy_map=external_proxy_map,
            )

            sensitivity = (
                SENSITIVITY_BY_FUEL_TIER[fuel][tier]
                * (1.0 - (0.18 * same_brand_share))
                * (1.0 + (0.10 * max(gap_index, 0.0)))
            )
            undercut_trigger_cents = clamp(
                1.05
                - (0.20 * same_brand_share)
                + (0.18 * (1.0 - pressure))
                + (0.10 if fuel == "diesel" else 0.0),
                0.65,
                1.35,
            )
            retaliation_penalty = 0.025 + (0.05 * pressure)

            own_margin_matrix = anchor_base * CANDIDATE_MARGIN_MATRIX
            gaps = floor_gap_cents + own_margin_matrix - reference_margin_curve_cents[np.newaxis, :]
            shares = market_share_matrix(gaps, sensitivity, demand_inputs)
            expected_volume = demand_inputs.market_volume_index[np.newaxis, :] * shares
            retaliation = retaliation_penalty * np.square(
                np.maximum(-gaps - undercut_trigger_cents, 0.0)
            )
            objective = np.sum(
                expected_volume * own_margin_matrix - retaliation,
                axis=1,
            )

            best_idx = int(np.argmax(objective))
            best_candidate = CANDIDATES[best_idx]
            best_margins = own_margin_matrix[best_idx]
            best_anchor_cents = anchor_base * best_candidate.anchor_multiplier
            best_markdowns = best_anchor_cents - best_margins
            best_end_markup_cents = float(best_margins[-1])
            best_expected_volume_index = float(np.sum(expected_volume[best_idx]))
            best_mean_share = float(np.mean(shares[best_idx]))
            simulated_noon_price = effective_floor + (best_anchor_cents / 100.0)
            simulated_end_price = effective_floor + (best_end_markup_cents / 100.0)
            reference_noon_price = reference_floor + (reference_anchor / 100.0)
            reference_end_price = reference_floor + (reference_margin_curve_cents[-1] / 100.0)
            anchor_gap_to_reference = simulated_noon_price - reference_noon_price

            family = strategy_family(
                best_candidate.template,
                anchor_gap_to_reference * 100.0,
                best_candidate.residual_share,
            )
            family_counts[family] += 1

            row = {
                "station_uuid": station_id,
                "name": station.name,
                "brand": station.brand,
                "city": station.city,
                "fuel": fuel,
                "scenario": "noon_reset_counterfactual",
                "competition_tier": tier,
                "competition_score": round(score, 2),
                "competitors_2km": count_2km,
                "competitors_3km": count_3km,
                "nearest_competitor_km": round(nearest_km, 2),
                "same_brand_share": round(same_brand_share, 2),
                "own_span_cents": round(own.span_cents, 1),
                "local_span_cents": round(local_span_cents, 1),
                "own_midprice_eur_l": round(own.midprice, 3),
                "local_midprice_eur_l": round(local_midprice, 3),
                "price_gap_to_local_cents": price_gap_cents,
                "effective_floor_eur_l": round(effective_floor, 3),
                "reference_floor_eur_l": round(reference_floor, 3),
                "simulated_noon_anchor_cents": round(best_anchor_cents, 1),
                "simulated_end_markup_cents": round(best_end_markup_cents, 1),
                "simulated_noon_price_eur_l": round(simulated_noon_price, 3),
                "simulated_nextday_11_00_price_eur_l": round(simulated_end_price, 3),
                "reference_noon_price_eur_l": round(reference_noon_price, 3),
                "reference_nextday_11_00_price_eur_l": round(reference_end_price, 3),
                "simulated_price_gap_to_reference_noon_cents": round(
                    (simulated_noon_price - reference_noon_price) * 100.0,
                    1,
                ),
                "simulated_price_gap_to_reference_20_00_cents": round(
                    (
                        effective_floor
                        + (best_margins[8] / 100.0)
                        - (reference_floor + (reference_margin_curve_cents[8] / 100.0))
                    )
                    * 100.0,
                    1,
                ),
                "expected_profit_index": round(float(objective[best_idx]), 2),
                "expected_volume_index": round(best_expected_volume_index, 2),
                "mean_choice_share": round(best_mean_share, 3),
                "market_potential_index": round(demand_inputs.market_potential_index, 3),
                "traffic_proxy_index": round(demand_inputs.traffic_proxy_index, 3),
                "external_proxy_index": round(demand_inputs.external_proxy_index, 3),
                "city_station_count": demand_inputs.city_station_count,
                "postcode_station_count": demand_inputs.postcode_station_count,
                "effective_competitor_count": round(demand_inputs.effective_competitor_count, 2),
                "demand_weight_07_00": round(float(demand_inputs.market_volume_index[19]), 2),
                "demand_weight_17_00": round(float(demand_inputs.market_volume_index[5]), 2),
                "sensitivity_lambda": round(float(sensitivity), 3),
                "retaliation_penalty": round(float(retaliation_penalty), 3),
                "undercut_trigger_cents": round(float(undercut_trigger_cents), 1),
                "strategy_family": family,
                "reference_strategy_family": reference_template_name,
                "selected_template": best_candidate.template,
                "selected_residual_share": round(best_candidate.residual_share, 2),
                "top_competitors": "|".join(
                    f"{competitor_id}:{dist:.2f}" for competitor_id, dist in nearby[:MAX_COMPETITORS]
                ),
            }
            for key, markdown in zip(TIME_KEYS, best_markdowns):
                row[f"markdown_{key}_cents"] = round(float(markdown), 1)
            rows.append(row)

            fuel_summary[fuel]["simulated_noon_anchor_cents"].append(best_anchor_cents)
            fuel_summary[fuel]["simulated_end_markup_cents"].append(best_end_markup_cents)
            fuel_summary[fuel]["markdown_20_00_cents"].append(float(best_markdowns[8]))
            fuel_summary[fuel]["markdown_22_00_cents"].append(float(best_markdowns[10]))
            fuel_summary[fuel]["markdown_nextday_11_00_cents"].append(float(best_markdowns[-1]))
            fuel_summary[fuel]["price_gap_noon_cents"].append(
                (simulated_noon_price - reference_noon_price) * 100.0
            )
            fuel_summary[fuel]["expected_profit_index"].append(float(objective[best_idx]))
            fuel_summary[fuel]["expected_volume_index"].append(best_expected_volume_index)

    fieldnames = list(rows[0].keys())
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    render_chart(rows, chart_path)

    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    station_count = len(active_ids)
    total_rows = len(rows)

    lines: list[str] = [
        "# Competitor-Aware Fuel Pricing Counterfactual Report",
        "",
        f"Generated: {generated_at}",
        "Scenario: noon reset with one permitted price increase at 12:00, then only flat or lower prices until the next noon.",
        (
            f"History window: {history_meta.analysis_start.isoformat()} to {history_meta.analysis_end.isoformat()} "
            f"({history_meta.analysis_days} completed days, source `{history_meta.source}`)."
        ),
        f"Coverage: {station_count} stations with usable strategy data, {total_rows} station-fuel simulations.",
        "",
        "## Executive Summary",
        "",
        "- This report is a forward-looking simulation under the new noon-reset rule, not a description of the historical old regime.",
        "- Nearby competitors are modeled explicitly, and the closest stations carry the highest weight in the reference market path.",
        "- The simulated optimum is materially flatter than the historical cycle: stations preserve more markup overnight because they cannot re-increase before the next noon.",
        "- Gasoline grades are optimally more aggressive than diesel at noon, especially in intense local markets.",
        "- The demand layer now uses a latent market-volume model with station exposure, city/postcode density, local competition, and an optional external traffic proxy file instead of a simple one-reference share curve.",
        "- Azure raw prices are necessary when the requested history window exceeds the local snapshot or when minute-level reaction calibration is needed.",
        "",
        "## What This Report Uses",
        "",
        "- `data/stations.json`: station metadata and coordinates.",
        (
            "- Historical station-level prices: "
            + (
                "`data2/<station>/<fuel>.json` snapshot statistics."
                if history_meta.source == "snapshot"
                else "Azure raw Tankerkönig prices aggregated into hourly station features."
            )
        ),
        f"- Local competition radius: {RADIUS_LONG_KM:.0f} km with exponential distance weighting (decay {COMPETITION_DECAY_KM:.1f} km).",
        "- Counterfactual constraint: one price increase at `12:00`, then only flat or decreasing prices until the next noon.",
        "- Demand model: latent hourly market volume multiplied by a multinomial-style choice share with outside option and effective competitor mass.",
        "- Optional external proxy input: `data/pricing_external_demand_proxies.csv` with `station_uuid`, `post_code`, or `city` plus `traffic_index` or `volume_index`.",
        "- Basic data hygiene: station-fuel rows with invalid absolute prices (<= 0.5 EUR/l) are excluded.",
        "",
        "## Scientific Basis",
        "",
    ]

    for title, url, finding in RESEARCH_SOURCES:
        lines.append(f"- [{title}]({url}): {finding}")

    lines.extend(
        [
            "",
            "## Counterfactual Market Structure Under Noon Reset",
            "",
            f"- Competition score quartiles: Q1={q25:.2f}, Q3={q75:.2f}.",
            f"- Competition tiers: intense={competition_tiers['intense']}, medium={competition_tiers['medium']}, relaxed={competition_tiers['relaxed']}.",
            f"- Strategy families: {', '.join(f'{name}={count}' for name, count in sorted(family_counts.items()))}.",
            "",
            "| Fuel | Median noon anchor (ct/l) | Median markup at next day 11:00 (ct/l) | Median markdown by 20:00 (ct/l) | Median markdown by 22:00 (ct/l) | Median markdown by next day 11:00 (ct/l) | Median noon gap to reference (ct/l) | Median profit index | Median volume index |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for fuel in FUELS:
        lines.append(
            "| "
            + FUEL_LABELS[fuel]
            + f" | {percentile(sorted(fuel_summary[fuel]['simulated_noon_anchor_cents']), 0.5):.1f}"
            + f" | {percentile(sorted(fuel_summary[fuel]['simulated_end_markup_cents']), 0.5):.1f}"
            + f" | {percentile(sorted(fuel_summary[fuel]['markdown_20_00_cents']), 0.5):.1f}"
            + f" | {percentile(sorted(fuel_summary[fuel]['markdown_22_00_cents']), 0.5):.1f}"
            + f" | {percentile(sorted(fuel_summary[fuel]['markdown_nextday_11_00_cents']), 0.5):.1f}"
            + f" | {percentile(sorted(fuel_summary[fuel]['price_gap_noon_cents']), 0.5):.1f}"
            + f" | {percentile(sorted(fuel_summary[fuel]['expected_profit_index']), 0.5):.1f}"
            + f" | {percentile(sorted(fuel_summary[fuel]['expected_volume_index']), 0.5):.1f} |"
        )

    lines.extend(
        [
            "",
            "## Strategy Logic",
            "",
            "For each station and fuel, the counterfactual model blends five ingredients:",
            "",
            "1. Own station shape: recent price span, recent minimum, and historical hour-of-day premium pattern.",
            "2. Nearby rivals: counts within 2 km and 3 km, weighted by distance.",
            "3. Relative local level: own recent midprice versus the local weighted competitor midprice and floor.",
            "4. Demand model: latent market volume from hourly market activity, local station density, competition pressure, and optional external traffic proxies.",
            "5. Counterfactual law: one noon anchor, then only monotone markdown paths are admissible.",
            "",
            "The strategy output for each station-fuel row is:",
            "",
            "- `simulated_noon_anchor_cents`: the recommended markup above the station's effective floor at 12:00.",
            "- `simulated_end_markup_cents`: how much markup the station should still keep at 11:00 the next day before the next noon reset.",
            "- `selected_template`: the optimal markdown family under the counterfactual law.",
            "- `markdown_12_00 ... markdown_nextday_11_00_cents`: cumulative simulated markdown since noon.",
            "- `simulated_price_gap_to_reference_noon_cents`: how far above or below the simulated local competitor reference the station should start.",
            "- `undercut_trigger_cents`: the local gap below reference where retaliation risk becomes material in the objective.",
            "- `expected_profit_index`: a relative objective score for ranking strategies within this model, not a euro profit forecast.",
            "- `expected_volume_index`: simulated captured market volume in model units, not liters.",
            "",
            "## When The Azure Raw Feed Becomes Necessary",
            "",
            "The repo snapshot is enough for an 8-day counterfactual baseline because it already captures local volatility, local levels, and spatial rivalry. The original Azure data becomes necessary when we want longer calibration windows or finer calibration:",
            "",
            "- history windows longer than the locally materialized snapshot",
            "- minute-level reaction studies after a competitor markdown",
            "- better calibration of how quickly nearby stations match price cuts",
            "- more precise consumer-response estimates for very small local price gaps",
            "- publication-grade validation of the simulated ladder timing",
            "",
            "## Outputs",
            "",
            f"- Markdown report: `{report_path}`",
            f"- Station-fuel strategy table: `{csv_path}`",
            f"- General-market visualization: `{chart_path}`",
        ]
    )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path, csv_path, chart_path


def main() -> None:
    args = parse_args()
    report_path, csv_path, chart_path = generate(
        history_days=args.history_days,
        history_source=args.history_source,
    )
    print(f"Wrote {report_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {chart_path}")


if __name__ == "__main__":
    main()
