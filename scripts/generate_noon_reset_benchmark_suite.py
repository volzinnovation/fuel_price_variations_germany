#!/usr/bin/env python3
"""Benchmark counterfactual fuel-pricing models under a noon-reset rule."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    from generate_noon_reset_counterfactual_report import (
        ANCHOR_MULTIPLIERS,
        CANDIDATES,
        CANDIDATE_MARGIN_MATRIX,
        COMPETITION_DECAY_KM,
        DEFAULT_HISTORY_DAYS,
        MAX_COMPETITORS,
        RESEARCH_SOURCES,
        RADIUS_LONG_KM,
        RADIUS_SHORT_KM,
        SIM_HOURS,
        TIME_KEYS,
        FUEL_LABELS,
        FUELS,
        FUEL_COLORS,
        build_demand_weights,
        build_market_hourly_profile,
        build_station_demand_inputs,
        candidate_ids,
        clamp,
        competition_tier,
        distance_km,
        load_fuel_stats,
        load_external_demand_proxies,
        load_stations,
        market_share_matrix,
        normalize_key,
        percentile,
        reference_margin_curve,
        strategy_family,
        weighted_quantile,
        build_grid,
    )
except ModuleNotFoundError:
    from scripts.generate_noon_reset_counterfactual_report import (
        ANCHOR_MULTIPLIERS,
        CANDIDATES,
        CANDIDATE_MARGIN_MATRIX,
        COMPETITION_DECAY_KM,
        DEFAULT_HISTORY_DAYS,
        MAX_COMPETITORS,
        RESEARCH_SOURCES,
        RADIUS_LONG_KM,
        RADIUS_SHORT_KM,
        SIM_HOURS,
        TIME_KEYS,
        FUEL_LABELS,
        FUELS,
        FUEL_COLORS,
        build_demand_weights,
        build_market_hourly_profile,
        build_station_demand_inputs,
        candidate_ids,
        clamp,
        competition_tier,
        distance_km,
        load_fuel_stats,
        load_external_demand_proxies,
        load_stations,
        market_share_matrix,
        normalize_key,
        percentile,
        reference_margin_curve,
        strategy_family,
        weighted_quantile,
        build_grid,
    )


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "pricing_benchmarks"
REACTION_CALIBRATION_PATH = ROOT / "output" / "reaction_timing_calibration" / "reaction_timing_summary.csv"
MODEL_ORDER = (
    "flat_hold",
    "uniform_ladder",
    "tier_heuristic",
    "local_follower",
    "station_best_response",
    "robust_best_response",
    "equilibrium_best_response",
)
MODEL_LABELS = {
    "flat_hold": "Flat Hold",
    "uniform_ladder": "Uniform Ladder",
    "tier_heuristic": "Tier Heuristic",
    "local_follower": "Local Follower",
    "station_best_response": "Best Response",
    "robust_best_response": "Robust Best Response",
    "equilibrium_best_response": "Equilibrium",
}
FLAT_HOLD_MULTIPLIERS = np.array([0.70, 0.85, 1.00, 1.15, 1.30, 1.50], dtype=np.float64)
ROBUST_SCENARIOS = (
    {"name": "calm", "sensitivity": 0.85, "retaliation": 0.70, "demand_flatten": 0.50, "weight": 0.25},
    {"name": "base", "sensitivity": 1.00, "retaliation": 1.00, "demand_flatten": 1.00, "weight": 0.50},
    {"name": "aggressive", "sensitivity": 1.20, "retaliation": 1.35, "demand_flatten": 1.20, "weight": 0.25},
)
SENSITIVITY_SCENARIOS = (
    ("calm", 0.85, 0.70, 0.50),
    ("base", 1.00, 1.00, 1.00),
    ("aggressive", 1.20, 1.35, 1.20),
)
BOOTSTRAP_DRAWS = 120
BOOTSTRAP_SEED = 42
EQUILIBRIUM_MAX_ITERS = 8
EQUILIBRIUM_CONVERGENCE_SHARE = 0.01
DEFAULT_COMPOSITE_WEIGHTS = {
    "profit": 0.40,
    "volume": 0.15,
    "downside": 0.20,
    "robustness": 0.15,
    "equilibrium": 0.10,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history-days",
        type=int,
        default=DEFAULT_HISTORY_DAYS,
        help="Number of completed days used for station-level history features.",
    )
    parser.add_argument(
        "--history-source",
        choices=("auto", "snapshot", "azure"),
        default="auto",
        help="Use repo snapshot, Azure raw prices, or auto-select based on history length.",
    )
    return parser.parse_args()


@dataclass
class Context:
    row_id: int
    station_id: str
    name: str
    brand: str
    city: str
    fuel: str
    competition_tier: str
    competition_score: float
    competitors_2km: int
    competitors_3km: int
    nearest_competitor_km: float
    same_brand_share: float
    own_span_cents: float
    local_span_cents: float
    own_midprice_eur_l: float
    local_midprice_eur_l: float
    price_gap_to_local_cents: float
    effective_floor_cents: float
    reference_floor_cents: float
    anchor_base: float
    reference_anchor_cents: float
    static_reference_abs_cents: np.ndarray
    sensitivity_lambda: float
    retaliation_penalty: float
    undercut_trigger_cents: float
    market_volume_index: np.ndarray
    outside_option_mass: np.ndarray
    effective_competitor_count: float
    quality_shift: float
    market_potential_index: float
    traffic_proxy_index: float
    external_proxy_index: float
    city_station_count: int
    postcode_station_count: int
    top_competitors: str
    neighbor_ids: list[str]
    neighbor_weights: list[float]


@dataclass
class Strategy:
    model: str
    model_label: str
    candidate_name: str
    template: str
    anchor_cents: float
    end_markup_cents: float
    markdowns: np.ndarray
    margins: np.ndarray
    abs_prices_cents: np.ndarray
    reference_abs_cents: np.ndarray
    expected_profit_index: float
    expected_volume_index: float
    mean_choice_share: float
    simulated_gap_noon_cents: float
    simulated_gap_20_cents: float
    residual_share: float
    convergence_iteration: int = 0


def demand_scenario(weights: np.ndarray, flatten: float) -> np.ndarray:
    baseline = np.mean(weights)
    return baseline + flatten * (weights - baseline)


def load_reaction_multipliers() -> dict[tuple[str, str], float]:
    if not REACTION_CALIBRATION_PATH.exists():
        return {}
    rows = list(csv.DictReader(REACTION_CALIBRATION_PATH.open(encoding="utf-8")))
    return {
        (row["fuel"], row["competition_tier"]): float(row["retaliation_multiplier"])
        for row in rows
    }


def candidate_objective(
    context: Context,
    reference_abs_cents: np.ndarray,
    candidate_matrix: np.ndarray = CANDIDATE_MARGIN_MATRIX,
    sensitivity_multiplier: float = 1.0,
    retaliation_multiplier: float = 1.0,
    demand_flatten: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    own_margin_matrix = context.anchor_base * candidate_matrix
    own_abs_matrix = context.effective_floor_cents + own_margin_matrix
    gaps = own_abs_matrix - reference_abs_cents[np.newaxis, :]
    shares = market_share_matrix(
        gaps,
        context.sensitivity_lambda,
        type("DemandInputsProxy", (), {
            "outside_option_mass": context.outside_option_mass,
            "effective_competitor_count": context.effective_competitor_count,
            "quality_shift": context.quality_shift,
        })(),
        sensitivity_multiplier=sensitivity_multiplier,
    )
    retaliation = (
        context.retaliation_penalty
        * retaliation_multiplier
        * np.square(np.maximum(-gaps - context.undercut_trigger_cents, 0.0))
    )
    market_volume = demand_scenario(context.market_volume_index, demand_flatten)
    expected_volume = market_volume[np.newaxis, :] * shares
    objective = np.sum(expected_volume * own_margin_matrix - retaliation, axis=1)
    return objective, own_margin_matrix, own_abs_matrix, shares, expected_volume


def flat_hold_objective(
    context: Context,
    reference_abs_cents: np.ndarray,
    sensitivity_multiplier: float = 1.0,
    retaliation_multiplier: float = 1.0,
    demand_flatten: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    own_margin_matrix = np.outer(context.anchor_base * FLAT_HOLD_MULTIPLIERS, np.ones(len(SIM_HOURS)))
    own_abs_matrix = context.effective_floor_cents + own_margin_matrix
    gaps = own_abs_matrix - reference_abs_cents[np.newaxis, :]
    shares = market_share_matrix(
        gaps,
        context.sensitivity_lambda,
        type("DemandInputsProxy", (), {
            "outside_option_mass": context.outside_option_mass,
            "effective_competitor_count": context.effective_competitor_count,
            "quality_shift": context.quality_shift,
        })(),
        sensitivity_multiplier=sensitivity_multiplier,
    )
    retaliation = (
        context.retaliation_penalty
        * retaliation_multiplier
        * np.square(np.maximum(-gaps - context.undercut_trigger_cents, 0.0))
    )
    market_volume = demand_scenario(context.market_volume_index, demand_flatten)
    expected_volume = market_volume[np.newaxis, :] * shares
    objective = np.sum(expected_volume * own_margin_matrix - retaliation, axis=1)
    return objective, own_margin_matrix, own_abs_matrix, shares, expected_volume


def build_contexts(
    history_days: int = DEFAULT_HISTORY_DAYS,
    history_source: str = "auto",
) -> tuple[list[Context], dict[str, object], dict[str, list[float]], Counter]:
    reaction_multipliers = load_reaction_multipliers()
    stations = load_stations()
    stats_by_station, history_meta = load_fuel_stats(
        history_days=history_days,
        history_source=history_source,
    )
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

    contexts: list[Context] = []
    tier_counts = Counter()
    family_inputs: dict[str, list[float]] = defaultdict(list)
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
        tier_counts[tier] += 1

        for fuel, own in stats_by_station[station_id].items():
            competitor_values: list[tuple[float, object]] = []
            neighbor_ids: list[str] = []
            neighbor_weights: list[float] = []
            for competitor_id, dist in nearby:
                competitor_stats = stats_by_station.get(competitor_id, {}).get(fuel)
                if competitor_stats is None:
                    continue
                weight = math.exp(-dist / COMPETITION_DECAY_KM)
                competitor_values.append((weight, competitor_stats))
                neighbor_ids.append(competitor_id)
                neighbor_weights.append(weight)

            if competitor_values:
                weights = [weight for weight, _ in competitor_values]
                local_span_cents = weighted_quantile(
                    [stats.span_cents for _, stats in competitor_values],
                    weights,
                )
                local_midprice = weighted_quantile(
                    [stats.midprice for _, stats in competitor_values],
                    weights,
                )
                local_floor = weighted_quantile(
                    [stats.minabs for _, stats in competitor_values],
                    weights,
                    q=0.35,
                )
            else:
                local_span_cents = own.span_cents
                local_midprice = own.midprice
                local_floor = own.minabs

            span_blend = (0.55 * own.span_cents) + (0.45 * local_span_cents)
            price_gap_cents = (own.midprice - local_midprice) * 100.0
            gap_index = clamp(price_gap_cents / max(span_blend, 1.0), -1.0, 1.0)
            effective_floor = clamp(
                (0.68 * own.minabs) + (0.32 * local_floor),
                own.minabs - 0.02,
                own.minabs + 0.03,
            )
            reference_floor = (0.25 * own.minabs) + (0.75 * local_floor)

            anchor_base = clamp(
                span_blend
                * {"diesel": 0.84, "e10": 0.99, "e5": 0.98}[fuel]
                * {"intense": 0.94, "medium": 1.00, "relaxed": 1.08}[tier]
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
                * {"diesel": 0.84, "e10": 0.99, "e5": 0.98}[fuel]
                * {"intense": 0.97, "medium": 1.05, "relaxed": 1.13}[tier]
                * (1.0 - (0.08 * pressure) + (0.08 * same_brand_share)),
                1.2,
                13.0,
            )
            _, _, reference_margins = reference_margin_curve(fuel, tier, reference_anchor)
            reference_abs_cents = (reference_floor * 100.0) + reference_margins
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
                {"diesel": {"intense": 0.56, "medium": 0.40, "relaxed": 0.26},
                 "e10": {"intense": 0.72, "medium": 0.52, "relaxed": 0.35},
                 "e5": {"intense": 0.70, "medium": 0.50, "relaxed": 0.34}}[fuel][tier]
                * (1.0 - (0.18 * same_brand_share))
                * (1.0 + (0.10 * max(gap_index, 0.0)))
            )
            undercut_trigger = clamp(
                1.05
                - (0.20 * same_brand_share)
                + (0.18 * (1.0 - pressure))
                + (0.10 if fuel == "diesel" else 0.0),
                0.65,
                1.35,
            )
            retaliation_penalty = (
                0.025 + (0.05 * pressure)
            ) * reaction_multipliers.get((fuel, tier), 1.0)

            contexts.append(
                Context(
                    row_id=len(contexts),
                    station_id=station_id,
                    name=station.name,
                    brand=station.brand,
                    city=station.city,
                    fuel=fuel,
                    competition_tier=tier,
                    competition_score=round(score, 2),
                    competitors_2km=count_2km,
                    competitors_3km=count_3km,
                    nearest_competitor_km=round(nearest_km, 2),
                    same_brand_share=round(same_brand_share, 2),
                    own_span_cents=round(own.span_cents, 1),
                    local_span_cents=round(local_span_cents, 1),
                    own_midprice_eur_l=round(own.midprice, 3),
                    local_midprice_eur_l=round(local_midprice, 3),
                    price_gap_to_local_cents=round(price_gap_cents, 2),
                    effective_floor_cents=round(effective_floor * 100.0, 3),
                    reference_floor_cents=round(reference_floor * 100.0, 3),
                    anchor_base=float(anchor_base),
                    reference_anchor_cents=float(reference_anchor),
                    static_reference_abs_cents=reference_abs_cents.astype(np.float64),
                    sensitivity_lambda=float(sensitivity),
                    retaliation_penalty=float(retaliation_penalty),
                    undercut_trigger_cents=float(undercut_trigger),
                    market_volume_index=demand_inputs.market_volume_index.astype(np.float64),
                    outside_option_mass=demand_inputs.outside_option_mass.astype(np.float64),
                    effective_competitor_count=float(demand_inputs.effective_competitor_count),
                    quality_shift=float(demand_inputs.quality_shift),
                    market_potential_index=float(demand_inputs.market_potential_index),
                    traffic_proxy_index=float(demand_inputs.traffic_proxy_index),
                    external_proxy_index=float(demand_inputs.external_proxy_index),
                    city_station_count=int(demand_inputs.city_station_count),
                    postcode_station_count=int(demand_inputs.postcode_station_count),
                    top_competitors="|".join(
                        f"{competitor_id}:{dist:.2f}" for competitor_id, dist in nearby[:MAX_COMPETITORS]
                    ),
                    neighbor_ids=neighbor_ids,
                    neighbor_weights=neighbor_weights,
                )
            )
            family_inputs[fuel].append(anchor_base)

    meta = {
        "q25": q25,
        "q75": q75,
        "history_meta": history_meta,
    }
    return contexts, meta, family_inputs, tier_counts


def candidate_to_strategy(
    context: Context,
    model: str,
    candidate_idx: int,
    reference_abs_cents: np.ndarray,
    share_curve: np.ndarray,
    volume_curve: np.ndarray,
    objective: float,
    convergence_iteration: int = 0,
) -> Strategy:
    candidate = CANDIDATES[candidate_idx]
    margins = context.anchor_base * candidate.margin_coeffs
    abs_prices = context.effective_floor_cents + margins
    anchor_cents = float(margins[0])
    markdowns = anchor_cents - margins
    return Strategy(
        model=model,
        model_label=MODEL_LABELS[model],
        candidate_name=f"{candidate.template}:{candidate.anchor_multiplier:.2f}:{candidate.residual_share:.2f}",
        template=candidate.template,
        anchor_cents=round(anchor_cents, 3),
        end_markup_cents=round(float(margins[-1]), 3),
        markdowns=markdowns.astype(np.float64),
        margins=margins.astype(np.float64),
        abs_prices_cents=abs_prices.astype(np.float64),
        reference_abs_cents=reference_abs_cents.astype(np.float64),
        expected_profit_index=round(float(objective), 4),
        expected_volume_index=round(float(np.sum(volume_curve)), 4),
        mean_choice_share=round(float(np.mean(share_curve)), 4),
        simulated_gap_noon_cents=round(float(abs_prices[0] - reference_abs_cents[0]), 3),
        simulated_gap_20_cents=round(float(abs_prices[8] - reference_abs_cents[8]), 3),
        residual_share=float(candidate.residual_share),
        convergence_iteration=convergence_iteration,
    )


def flat_hold_strategy(
    context: Context,
    model: str,
    reference_abs_cents: np.ndarray,
    convergence_iteration: int = 0,
) -> Strategy:
    objective, margin_matrix, abs_matrix, shares, expected_volume = flat_hold_objective(
        context,
        reference_abs_cents,
    )
    best_idx = int(np.argmax(objective))
    margins = margin_matrix[best_idx]
    abs_prices = abs_matrix[best_idx]
    anchor_cents = float(margins[0])
    markdowns = anchor_cents - margins
    multiplier = FLAT_HOLD_MULTIPLIERS[best_idx]
    return Strategy(
        model=model,
        model_label=MODEL_LABELS[model],
        candidate_name=f"flat:{multiplier:.2f}",
        template="flat_hold",
        anchor_cents=round(anchor_cents, 3),
        end_markup_cents=round(float(margins[-1]), 3),
        markdowns=markdowns.astype(np.float64),
        margins=margins.astype(np.float64),
        abs_prices_cents=abs_prices.astype(np.float64),
        reference_abs_cents=reference_abs_cents.astype(np.float64),
        expected_profit_index=round(float(objective[best_idx]), 4),
        expected_volume_index=round(float(np.sum(expected_volume[best_idx])), 4),
        mean_choice_share=round(float(np.mean(shares[best_idx])), 4),
        simulated_gap_noon_cents=round(float(abs_prices[0] - reference_abs_cents[0]), 3),
        simulated_gap_20_cents=round(float(abs_prices[8] - reference_abs_cents[8]), 3),
        residual_share=1.0,
        convergence_iteration=convergence_iteration,
    )


def local_follower_strategy(context: Context, model: str) -> Strategy:
    reference_abs_cents = context.static_reference_abs_cents
    abs_prices = np.maximum(reference_abs_cents, context.effective_floor_cents)
    margins = abs_prices - context.effective_floor_cents
    anchor_cents = float(margins[0])
    markdowns = anchor_cents - margins
    gaps = abs_prices - reference_abs_cents
    shares = market_share_matrix(
        gaps[np.newaxis, :],
        context.sensitivity_lambda,
        type("DemandInputsProxy", (), {
            "outside_option_mass": context.outside_option_mass,
            "effective_competitor_count": context.effective_competitor_count,
            "quality_shift": context.quality_shift,
        })(),
    )[0]
    expected_volume = context.market_volume_index * shares
    retaliation = context.retaliation_penalty * np.square(
        np.maximum(-gaps - context.undercut_trigger_cents, 0.0)
    )
    objective = float(np.sum(expected_volume * margins - retaliation))
    return Strategy(
        model=model,
        model_label=MODEL_LABELS[model],
        candidate_name="reference_follow",
        template="reference_follow",
        anchor_cents=round(anchor_cents, 3),
        end_markup_cents=round(float(margins[-1]), 3),
        markdowns=markdowns.astype(np.float64),
        margins=margins.astype(np.float64),
        abs_prices_cents=abs_prices.astype(np.float64),
        reference_abs_cents=reference_abs_cents.astype(np.float64),
        expected_profit_index=round(objective, 4),
        expected_volume_index=round(float(np.sum(expected_volume)), 4),
        mean_choice_share=round(float(np.mean(shares)), 4),
        simulated_gap_noon_cents=round(float(abs_prices[0] - reference_abs_cents[0]), 3),
        simulated_gap_20_cents=round(float(abs_prices[8] - reference_abs_cents[8]), 3),
        residual_share=float(margins[-1] / anchor_cents) if anchor_cents > 0 else 0.0,
    )


def select_group_candidates(
    contexts: list[Context],
    group_key_fn,
) -> dict[tuple[str, ...], int]:
    grouped_scores: dict[tuple[str, ...], np.ndarray] = {}
    for context in contexts:
        key = group_key_fn(context)
        objective, _, _, _, _ = candidate_objective(context, context.static_reference_abs_cents)
        grouped_scores[key] = grouped_scores.get(key, np.zeros(len(CANDIDATES))) + objective
    return {key: int(np.argmax(scores)) for key, scores in grouped_scores.items()}


def robust_station_strategy(context: Context) -> Strategy:
    scenario_scores: list[np.ndarray] = []
    for scenario in ROBUST_SCENARIOS:
        objective, _, _, _, _ = candidate_objective(
            context,
            context.static_reference_abs_cents,
            sensitivity_multiplier=scenario["sensitivity"],
            retaliation_multiplier=scenario["retaliation"],
            demand_flatten=scenario["demand_flatten"],
        )
        scenario_scores.append(objective)
    stacked = np.vstack(scenario_scores)
    weights = np.array([scenario["weight"] for scenario in ROBUST_SCENARIOS], dtype=np.float64)[:, np.newaxis]
    mean_score = np.sum(weights * stacked, axis=0)
    downside = np.min(stacked, axis=0)
    robust_score = mean_score * 0.7 + downside * 0.3
    best_idx = int(np.argmax(robust_score))
    base_objective, _, _, base_shares, base_volume = candidate_objective(
        context,
        context.static_reference_abs_cents,
    )
    return candidate_to_strategy(
        context,
        "robust_best_response",
        best_idx,
        context.static_reference_abs_cents,
        base_shares[best_idx],
        base_volume[best_idx],
        base_objective[best_idx],
    )


def evaluate_model_rows(
    contexts: list[Context],
    model: str,
) -> list[Strategy]:
    results: list[Strategy] = []
    if model == "flat_hold":
        return [flat_hold_strategy(context, model, context.static_reference_abs_cents) for context in contexts]
    if model == "uniform_ladder":
        group_map = select_group_candidates(contexts, lambda context: (context.fuel,))
        for context in contexts:
            idx = group_map[(context.fuel,)]
            objective, _, _, shares, volume = candidate_objective(context, context.static_reference_abs_cents)
            results.append(
                candidate_to_strategy(
                    context,
                    model,
                    idx,
                    context.static_reference_abs_cents,
                    shares[idx],
                    volume[idx],
                    objective[idx],
                )
            )
        return results
    if model == "tier_heuristic":
        group_map = select_group_candidates(contexts, lambda context: (context.fuel, context.competition_tier))
        for context in contexts:
            idx = group_map[(context.fuel, context.competition_tier)]
            objective, _, _, shares, volume = candidate_objective(context, context.static_reference_abs_cents)
            results.append(
                candidate_to_strategy(
                    context,
                    model,
                    idx,
                    context.static_reference_abs_cents,
                    shares[idx],
                    volume[idx],
                    objective[idx],
                )
            )
        return results
    if model == "local_follower":
        return [local_follower_strategy(context, model) for context in contexts]
    if model == "station_best_response":
        for context in contexts:
            objective, _, _, shares, volume = candidate_objective(context, context.static_reference_abs_cents)
            best_idx = int(np.argmax(objective))
            results.append(
                candidate_to_strategy(
                    context,
                    model,
                    best_idx,
                    context.static_reference_abs_cents,
                    shares[best_idx],
                    volume[best_idx],
                    objective[best_idx],
                )
            )
        return results
    if model == "robust_best_response":
        return [robust_station_strategy(context) for context in contexts]
    raise ValueError(f"Unsupported model: {model}")


def equilibrium_best_response(contexts: list[Context], initial: list[Strategy]) -> tuple[list[Strategy], list[dict[str, object]]]:
    context_by_id = {context.row_id: context for context in contexts}
    row_by_station_fuel = {(context.station_id, context.fuel): context.row_id for context in contexts}
    current_strategies = {context.row_id: strategy for context, strategy in zip(contexts, initial)}
    current_abs = {row_id: strategy.abs_prices_cents.copy() for row_id, strategy in current_strategies.items()}
    diagnostics: list[dict[str, object]] = []

    for iteration in range(1, EQUILIBRIUM_MAX_ITERS + 1):
        changed = 0
        total_change = 0.0
        for context in contexts:
            neighbor_row_ids = [
                row_by_station_fuel[(neighbor_id, context.fuel)]
                for neighbor_id in context.neighbor_ids
                if (neighbor_id, context.fuel) in row_by_station_fuel
            ]
            if neighbor_row_ids:
                weights = np.array(
                    [
                        weight
                        for neighbor_id, weight in zip(context.neighbor_ids, context.neighbor_weights)
                        if (neighbor_id, context.fuel) in row_by_station_fuel
                    ],
                    dtype=np.float64,
                )
                curves = np.vstack([current_abs[row_id] for row_id in neighbor_row_ids])
                reference_abs = np.average(curves, axis=0, weights=weights)
            else:
                reference_abs = context.static_reference_abs_cents

            objective, _, _, shares, volume = candidate_objective(context, reference_abs)
            best_idx = int(np.argmax(objective))
            next_strategy = candidate_to_strategy(
                context,
                "equilibrium_best_response",
                best_idx,
                reference_abs,
                shares[best_idx],
                volume[best_idx],
                objective[best_idx],
                convergence_iteration=iteration,
            )
            old_curve = current_abs[context.row_id]
            curve_change = float(np.mean(np.abs(next_strategy.abs_prices_cents - old_curve)))
            total_change += curve_change
            if curve_change > 0.01:
                changed += 1
            current_strategies[context.row_id] = next_strategy
            current_abs[context.row_id] = next_strategy.abs_prices_cents.copy()

        diagnostics.append(
            {
                "iteration": iteration,
                "changed_share": changed / len(contexts),
                "mean_curve_change_cents": total_change / len(contexts),
            }
        )
        if diagnostics[-1]["changed_share"] <= EQUILIBRIUM_CONVERGENCE_SHARE:
            break

    ordered = [current_strategies[context.row_id] for context in contexts]
    return ordered, diagnostics


def station_rows_for_output(
    contexts: list[Context],
    strategies_by_model: dict[str, list[Strategy]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for model, strategies in strategies_by_model.items():
        for context, strategy in zip(contexts, strategies):
            row = {
                "model": model,
                "model_label": strategy.model_label,
                "station_uuid": context.station_id,
                "name": context.name,
                "brand": context.brand,
                "city": context.city,
                "fuel": context.fuel,
                "competition_tier": context.competition_tier,
                "competition_score": context.competition_score,
                "competitors_2km": context.competitors_2km,
                "competitors_3km": context.competitors_3km,
                "nearest_competitor_km": context.nearest_competitor_km,
                "same_brand_share": context.same_brand_share,
                "own_span_cents": context.own_span_cents,
                "local_span_cents": context.local_span_cents,
                "own_midprice_eur_l": context.own_midprice_eur_l,
                "local_midprice_eur_l": context.local_midprice_eur_l,
                "price_gap_to_local_cents": context.price_gap_to_local_cents,
                "effective_floor_eur_l": round(context.effective_floor_cents / 100.0, 3),
                "reference_floor_eur_l": round(context.reference_floor_cents / 100.0, 3),
                "strategy_family": strategy_family(
                    strategy.template,
                    strategy.simulated_gap_noon_cents,
                    strategy.residual_share,
                ),
                "selected_template": strategy.template,
                "candidate_name": strategy.candidate_name,
                "simulated_noon_anchor_cents": round(strategy.anchor_cents, 1),
                "simulated_end_markup_cents": round(strategy.end_markup_cents, 1),
                "simulated_noon_price_eur_l": round(strategy.abs_prices_cents[0] / 100.0, 3),
                "simulated_nextday_11_00_price_eur_l": round(strategy.abs_prices_cents[-1] / 100.0, 3),
                "simulated_price_gap_to_reference_noon_cents": round(strategy.simulated_gap_noon_cents, 1),
                "simulated_price_gap_to_reference_20_00_cents": round(strategy.simulated_gap_20_cents, 1),
                "expected_profit_index": round(strategy.expected_profit_index, 2),
                "expected_volume_index": round(strategy.expected_volume_index, 2),
                "mean_choice_share": round(strategy.mean_choice_share, 3),
                "market_potential_index": round(context.market_potential_index, 3),
                "traffic_proxy_index": round(context.traffic_proxy_index, 3),
                "external_proxy_index": round(context.external_proxy_index, 3),
                "city_station_count": context.city_station_count,
                "postcode_station_count": context.postcode_station_count,
                "effective_competitor_count": round(context.effective_competitor_count, 2),
                "sensitivity_lambda": round(context.sensitivity_lambda, 3),
                "retaliation_penalty": round(context.retaliation_penalty, 3),
                "undercut_trigger_cents": round(context.undercut_trigger_cents, 1),
                "convergence_iteration": strategy.convergence_iteration,
                "top_competitors": context.top_competitors,
            }
            for key, markdown in zip(TIME_KEYS, strategy.markdowns):
                row[f"markdown_{key}_cents"] = round(float(markdown), 1)
            rows.append(row)
    return rows


def bootstrap_summary(model_rows: list[dict[str, object]]) -> dict[tuple[str, str], dict[str, float]]:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    by_group: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in model_rows:
        by_group[(row["model"], row["fuel"])].append(row)

    result: dict[tuple[str, str], dict[str, float]] = {}
    for key, rows in by_group.items():
        anchor = np.array([float(row["simulated_noon_anchor_cents"]) for row in rows], dtype=np.float64)
        profit = np.array([float(row["expected_profit_index"]) for row in rows], dtype=np.float64)
        volume = np.array([float(row["expected_volume_index"]) for row in rows], dtype=np.float64)
        markdown22 = np.array([float(row["markdown_22_00_cents"]) for row in rows], dtype=np.float64)
        anchor_samples: list[float] = []
        profit_samples: list[float] = []
        volume_samples: list[float] = []
        markdown_samples: list[float] = []
        n = len(rows)
        for _ in range(BOOTSTRAP_DRAWS):
            idx = rng.integers(0, n, size=n)
            anchor_samples.append(float(np.median(anchor[idx])))
            profit_samples.append(float(np.mean(profit[idx])))
            volume_samples.append(float(np.mean(volume[idx])))
            markdown_samples.append(float(np.median(markdown22[idx])))
        result[key] = {
            "anchor_ci_lo": percentile(sorted(anchor_samples), 0.05),
            "anchor_ci_hi": percentile(sorted(anchor_samples), 0.95),
            "profit_ci_lo": percentile(sorted(profit_samples), 0.05),
            "profit_ci_hi": percentile(sorted(profit_samples), 0.95),
            "volume_ci_lo": percentile(sorted(volume_samples), 0.05),
            "volume_ci_hi": percentile(sorted(volume_samples), 0.95),
            "markdown22_ci_lo": percentile(sorted(markdown_samples), 0.05),
            "markdown22_ci_hi": percentile(sorted(markdown_samples), 0.95),
        }
    return result


def scenario_summary(
    contexts: list[Context],
    strategies_by_model: dict[str, list[Strategy]],
) -> tuple[dict[str, dict[str, Counter]], dict[tuple[str, str], dict[str, float]]]:
    ranking_counts: dict[str, dict[str, Counter]] = {
        fuel: {"top1": Counter(), "top2": Counter()} for fuel in FUELS
    }
    summary: dict[tuple[str, str], dict[str, float]] = {}

    for fuel in FUELS:
        model_values_by_scenario: dict[str, dict[str, list[float]]] = {
            scenario_name: {model: [] for model in MODEL_ORDER}
            for scenario_name, _, _, _ in SENSITIVITY_SCENARIOS
        }
        rank_history: dict[str, list[int]] = {model: [] for model in MODEL_ORDER}

        for model, strategies in strategies_by_model.items():
            for context, strategy in zip(contexts, strategies):
                if context.fuel != fuel:
                    continue
                margins = strategy.margins[np.newaxis, :]
                own_abs = context.effective_floor_cents + margins
                gaps = own_abs - strategy.reference_abs_cents[np.newaxis, :]
                for scenario_name, sensitivity_mult, retaliation_mult, demand_flatten in SENSITIVITY_SCENARIOS:
                    shares = market_share_matrix(
                        gaps,
                        context.sensitivity_lambda,
                        type("DemandInputsProxy", (), {
                            "outside_option_mass": context.outside_option_mass,
                            "effective_competitor_count": context.effective_competitor_count,
                            "quality_shift": context.quality_shift,
                        })(),
                        sensitivity_multiplier=sensitivity_mult,
                    )
                    retaliation = (
                        context.retaliation_penalty
                        * retaliation_mult
                        * np.square(np.maximum(-gaps - context.undercut_trigger_cents, 0.0))
                    )
                    market_volume = demand_scenario(context.market_volume_index, demand_flatten)
                    objective = float(
                        np.sum((market_volume[np.newaxis, :] * shares) * margins - retaliation, axis=1)[0]
                    )
                    model_values_by_scenario[scenario_name][model].append(objective)

        for scenario_name, _, _, _ in SENSITIVITY_SCENARIOS:
            ranking = sorted(
                (
                    (model, float(np.mean(values)))
                    for model, values in model_values_by_scenario[scenario_name].items()
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            ranking_counts[fuel]["top1"][ranking[0][0]] += 1
            ranking_counts[fuel]["top2"][ranking[0][0]] += 1
            ranking_counts[fuel]["top2"][ranking[1][0]] += 1
            for rank, (model, _) in enumerate(ranking, start=1):
                rank_history[model].append(rank)

        for model in MODEL_ORDER:
            scenario_means = [
                float(np.mean(model_values_by_scenario[scenario_name][model]))
                for scenario_name, _, _, _ in SENSITIVITY_SCENARIOS
            ]
            summary[(model, fuel)] = {
                "scenario_mean_profit_index": float(np.mean(scenario_means)),
                "scenario_worst_profit_index": float(np.min(scenario_means)),
                "scenario_best_profit_index": float(np.max(scenario_means)),
                "scenario_avg_rank": float(np.mean(rank_history[model])),
                "scenario_top1_share": ranking_counts[fuel]["top1"][model] / len(SENSITIVITY_SCENARIOS),
                "scenario_top2_share": ranking_counts[fuel]["top2"][model] / len(SENSITIVITY_SCENARIOS),
            }
    return ranking_counts, summary


def equilibrium_distance_summary(
    contexts: list[Context],
    strategies_by_model: dict[str, list[Strategy]],
) -> dict[tuple[str, str], float]:
    equilibrium = strategies_by_model["equilibrium_best_response"]
    result: dict[tuple[str, str], float] = {}
    for model, strategies in strategies_by_model.items():
        for fuel in FUELS:
            distances = [
                float(np.mean(np.abs(strategy.markdowns - eq_strategy.markdowns)))
                for context, strategy, eq_strategy in zip(contexts, strategies, equilibrium)
                if context.fuel == fuel
            ]
            result[(model, fuel)] = float(np.mean(distances)) if distances else 0.0
    return result


def zscore_map(values_by_model: dict[str, float], higher_is_better: bool = True) -> dict[str, float]:
    values = np.array(list(values_by_model.values()), dtype=np.float64)
    if len(values) == 0 or float(np.std(values)) < 1e-9:
        return {model: 0.0 for model in values_by_model}
    mean = float(np.mean(values))
    std = float(np.std(values))
    direction = 1.0 if higher_is_better else -1.0
    return {
        model: direction * ((value - mean) / std)
        for model, value in values_by_model.items()
    }


def apply_multi_criteria_scores(summary_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    by_fuel: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in summary_rows:
        by_fuel[row["fuel"]].append(row)

    for fuel, rows in by_fuel.items():
        profit_scores = zscore_map({row["model"]: float(row["mean_profit_index"]) for row in rows}, True)
        volume_scores = zscore_map({row["model"]: float(row["mean_expected_volume_index"]) for row in rows}, True)
        downside_scores = zscore_map({row["model"]: float(row["profit_ci_lo"]) for row in rows}, True)
        robustness_scores = zscore_map({row["model"]: float(row["scenario_top2_share"]) for row in rows}, True)
        equilibrium_scores = zscore_map({row["model"]: float(row["equilibrium_distance_cents"]) for row in rows}, False)

        ranked = []
        for row in rows:
            model = row["model"]
            composite = (
                DEFAULT_COMPOSITE_WEIGHTS["profit"] * profit_scores[model]
                + DEFAULT_COMPOSITE_WEIGHTS["volume"] * volume_scores[model]
                + DEFAULT_COMPOSITE_WEIGHTS["downside"] * downside_scores[model]
                + DEFAULT_COMPOSITE_WEIGHTS["robustness"] * robustness_scores[model]
                + DEFAULT_COMPOSITE_WEIGHTS["equilibrium"] * equilibrium_scores[model]
            )
            row["composite_score"] = round(float(composite), 4)
            ranked.append((model, composite))

        for rank, (model, _) in enumerate(sorted(ranked, key=lambda item: item[1], reverse=True), start=1):
            for row in rows:
                if row["model"] == model:
                    row["fuel_composite_rank"] = rank

    by_model: dict[str, list[float]] = defaultdict(list)
    for row in summary_rows:
        by_model[row["model"]].append(float(row["composite_score"]))
    overall_rank = sorted(
        ((model, float(np.mean(scores))) for model, scores in by_model.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    overall_order = {model: rank for rank, (model, _) in enumerate(overall_rank, start=1)}
    overall_scores = {model: score for model, score in overall_rank}
    for row in summary_rows:
        row["overall_composite_rank"] = overall_order[row["model"]]
        row["overall_composite_score"] = round(overall_scores[row["model"]], 4)
    return summary_rows


def render_score_chart(
    summary_rows: list[dict[str, object]],
    chart_path: Path,
    metric: str,
    title: str,
    xlabel: str,
) -> None:
    fig, axes = plt.subplots(1, len(FUELS), figsize=(16, 5.6), sharey=True)
    if len(FUELS) == 1:
        axes = [axes]
    for ax, fuel in zip(axes, FUELS):
        fuel_rows = [row for row in summary_rows if row["fuel"] == fuel]
        fuel_rows.sort(key=lambda row: float(row[metric]), reverse=True)
        labels = [MODEL_LABELS[row["model"]] for row in fuel_rows]
        values = [float(row[metric]) for row in fuel_rows]
        colors = [FUEL_COLORS[fuel] for _ in fuel_rows]
        y = np.arange(len(labels))
        ax.barh(y, values, color=colors, alpha=0.75)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=8)
        ax.invert_yaxis()
        ax.set_title(FUEL_LABELS[fuel])
        ax.grid(axis="x", color="#cbd5e1", linewidth=0.8, alpha=0.7)
    axes[0].set_xlabel(xlabel)
    fig.suptitle(title)
    fig.subplots_adjust(left=0.20, right=0.98, top=0.86, bottom=0.12, wspace=0.22)
    fig.savefig(chart_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def generate(
    history_days: int = DEFAULT_HISTORY_DAYS,
    history_source: str = "auto",
) -> tuple[Path, Path, Path, Path]:
    contexts, meta, _, tier_counts = build_contexts(
        history_days=history_days,
        history_source=history_source,
    )
    reaction_calibrated = REACTION_CALIBRATION_PATH.exists()

    strategies_by_model: dict[str, list[Strategy]] = {}
    for model in MODEL_ORDER[:-1]:
        strategies_by_model[model] = evaluate_model_rows(contexts, model)

    equilibrium_strategies, equilibrium_diagnostics = equilibrium_best_response(
        contexts,
        strategies_by_model["tier_heuristic"],
    )
    strategies_by_model["equilibrium_best_response"] = equilibrium_strategies

    model_rows = station_rows_for_output(contexts, strategies_by_model)
    bootstrap = bootstrap_summary(model_rows)
    sensitivity, scenario_metrics = scenario_summary(contexts, strategies_by_model)
    equilibrium_distance = equilibrium_distance_summary(contexts, strategies_by_model)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    station_csv_path = OUTPUT_DIR / "station_model_benchmarks.csv"
    summary_csv_path = OUTPUT_DIR / "model_summary.csv"
    report_path = OUTPUT_DIR / "noon_reset_benchmark_report.md"
    chart_path = OUTPUT_DIR / "benchmark_composite_score.png"

    fieldnames = list(model_rows[0].keys())
    with station_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(model_rows)

    summary_rows: list[dict[str, object]] = []
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in model_rows:
        grouped[(row["model"], row["fuel"])].append(row)

    for model in MODEL_ORDER:
        for fuel in FUELS:
            rows = grouped[(model, fuel)]
            anchor_vals = sorted(float(row["simulated_noon_anchor_cents"]) for row in rows)
            end_vals = sorted(float(row["simulated_end_markup_cents"]) for row in rows)
            markdown20_vals = sorted(float(row["markdown_20_00_cents"]) for row in rows)
            markdown22_vals = sorted(float(row["markdown_22_00_cents"]) for row in rows)
            markdown11_vals = sorted(float(row["markdown_nextday_11_00_cents"]) for row in rows)
            profit_vals = [float(row["expected_profit_index"]) for row in rows]
            volume_vals = [float(row["expected_volume_index"]) for row in rows]
            gap_vals = sorted(float(row["simulated_price_gap_to_reference_noon_cents"]) for row in rows)
            summary_rows.append(
                {
                    "model": model,
                    "model_label": MODEL_LABELS[model],
                    "fuel": fuel,
                    "row_count": len(rows),
                    "median_noon_anchor_cents": round(percentile(anchor_vals, 0.5), 2),
                    "median_end_markup_cents": round(percentile(end_vals, 0.5), 2),
                    "median_markdown_20_00_cents": round(percentile(markdown20_vals, 0.5), 2),
                    "median_markdown_22_00_cents": round(percentile(markdown22_vals, 0.5), 2),
                    "median_markdown_nextday_11_00_cents": round(percentile(markdown11_vals, 0.5), 2),
                    "median_gap_noon_cents": round(percentile(gap_vals, 0.5), 2),
                    "mean_profit_index": round(float(np.mean(profit_vals)), 3),
                    "mean_expected_volume_index": round(float(np.mean(volume_vals)), 3),
                    "anchor_ci_lo": round(bootstrap[(model, fuel)]["anchor_ci_lo"], 2),
                    "anchor_ci_hi": round(bootstrap[(model, fuel)]["anchor_ci_hi"], 2),
                    "profit_ci_lo": round(bootstrap[(model, fuel)]["profit_ci_lo"], 2),
                    "profit_ci_hi": round(bootstrap[(model, fuel)]["profit_ci_hi"], 2),
                    "volume_ci_lo": round(bootstrap[(model, fuel)]["volume_ci_lo"], 2),
                    "volume_ci_hi": round(bootstrap[(model, fuel)]["volume_ci_hi"], 2),
                    "markdown22_ci_lo": round(bootstrap[(model, fuel)]["markdown22_ci_lo"], 2),
                    "markdown22_ci_hi": round(bootstrap[(model, fuel)]["markdown22_ci_hi"], 2),
                    "scenario_mean_profit_index": round(
                        scenario_metrics[(model, fuel)]["scenario_mean_profit_index"],
                        3,
                    ),
                    "scenario_worst_profit_index": round(
                        scenario_metrics[(model, fuel)]["scenario_worst_profit_index"],
                        3,
                    ),
                    "scenario_best_profit_index": round(
                        scenario_metrics[(model, fuel)]["scenario_best_profit_index"],
                        3,
                    ),
                    "scenario_avg_rank": round(
                        scenario_metrics[(model, fuel)]["scenario_avg_rank"],
                        2,
                    ),
                    "scenario_top1_share": round(
                        scenario_metrics[(model, fuel)]["scenario_top1_share"],
                        3,
                    ),
                    "scenario_top2_share": round(
                        scenario_metrics[(model, fuel)]["scenario_top2_share"],
                        3,
                    ),
                    "equilibrium_distance_cents": round(equilibrium_distance[(model, fuel)], 3),
                }
            )

    summary_rows = apply_multi_criteria_scores(summary_rows)

    with summary_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    render_score_chart(
        summary_rows,
        chart_path,
        "composite_score",
        "Mehrkriterien-Benchmark der Modellklassen unter Mittagsreset",
        "Mehrkriterien-Score",
    )

    overall_rank = sorted(
        (
            (
                model,
                np.mean([row["overall_composite_score"] for row in summary_rows if row["model"] == model]),
            )
            for model in MODEL_ORDER
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    history_meta = meta["history_meta"]

    lines: list[str] = [
        "# Noon-Reset Benchmark Suite",
        "",
        f"Generated: {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        (
            f"History window: {history_meta.analysis_start.isoformat()} to {history_meta.analysis_end.isoformat()} "
            f"({history_meta.analysis_days} completed days, source `{history_meta.source}`)."
        ),
        f"Coverage: {len({context.station_id for context in contexts})} stations, {len(contexts)} station-fuel contexts.",
        "",
        "## Executive Summary",
        "",
        "- This benchmark suite compares multiple pricing models under the noon-reset law rather than relying on a single counterfactual.",
        "- There is a formal benchmark in the literature: [Angerer (2020)](https://www.sciencedirect.com/science/article/abs/pii/S1544612319308487) compares several retail gasoline regulations in an experimental spatial model, including the decrease-after-fixing rule.",
        "- The strongest operational benchmark inside this repo is the iterated multi-agent equilibrium, which closes the model by repeatedly updating local best responses.",
        "- The ranking now defaults to a multi-criteria score that blends mean profit, expected volume capture, downside uncertainty, scenario robustness, and distance to the equilibrium benchmark.",
        "- The demand layer no longer relies on a one-reference logistic share alone; it uses a latent market-volume model with outside option and effective competitor mass.",
        (
            f"- Reaction timing calibration: loaded from `{REACTION_CALIBRATION_PATH}`."
            if reaction_calibrated
            else "- Reaction timing calibration: no Azure-based calibration file found, so the suite uses default retaliation heuristics."
        ),
        "",
        "## Benchmark Models",
        "",
        "- `flat_hold`: one noon reset, then no markdowns until the next noon.",
        "- `uniform_ladder`: one common ladder per fuel across all stations.",
        "- `tier_heuristic`: one common ladder per fuel and competition tier.",
        "- `local_follower`: match the local competitor reference path.",
        "- `station_best_response`: station-level best response to the static local reference path.",
        "- `robust_best_response`: station-level best response under multiple reaction scenarios.",
        "- `equilibrium_best_response`: iterated local best responses until approximate convergence.",
        "",
        "## Demand Model",
        "",
        "- Hourly market volume starts from the historical market-wide daypart curve and is scaled by station exposure, city/postcode station density, local competition pressure, and optional external traffic proxies.",
        "- Choice share is computed against an effective competitor mass with an outside option, which is closer to a local multinomial demand system than the earlier binary share curve.",
        "- Profit remains an index because liters sold are not observed in the repo, but the benchmark now carries a separate `expected_volume_index` alongside profit.",
        "",
        "## Scientific Basis",
        "",
    ]

    for title, url, finding in RESEARCH_SOURCES:
        lines.append(f"- [{title}]({url}): {finding}")
    lines.extend(
        [
            f"- [Angerer (2020), Regulation of retail gasoline prices](https://www.sciencedirect.com/science/article/abs/pii/S1544612319308487): experimental benchmark comparing unregulated markets, one-day fixing, decrease-after-fixing, and maximum-markup rules.",
            f"- [Obradovits (2014), Austrian-style gasoline price regulation: How it may backfire](https://www.sciencedirect.com/science/article/abs/pii/S0167718713000994): theoretical warning that Austrian-style increase restrictions can distort intertemporal pricing and harm consumers.",
            "",
            "## Market Structure",
            "",
            f"- Competition score quartiles: Q1={meta['q25']:.2f}, Q3={meta['q75']:.2f}.",
            f"- Competition tiers: intense={tier_counts['intense']}, medium={tier_counts['medium']}, relaxed={tier_counts['relaxed']}.",
            "",
            "## Model Ranking",
            "",
            "| Rank | Model | Composite score | Mean profit index | Mean volume index | Mean scenario top-2 share | Mean equilibrium distance (ct/l) |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for rank, (model, score) in enumerate(overall_rank, start=1):
        model_rows = [row for row in summary_rows if row["model"] == model]
        lines.append(
            f"| {rank} | {MODEL_LABELS[model]} | {score:.2f}"
            + f" | {np.mean([row['mean_profit_index'] for row in model_rows]):.2f}"
            + f" | {np.mean([row['mean_expected_volume_index'] for row in model_rows]):.2f}"
            + f" | {np.mean([row['scenario_top2_share'] for row in model_rows]):.2f}"
            + f" | {np.mean([row['equilibrium_distance_cents'] for row in model_rows]):.2f} |"
        )

    lines.extend(
        [
            "",
            "## Fuel-Level Summary",
            "",
            "| Fuel | Model | Fuel rank | Composite score | Mean profit index | Mean volume index | Profit 90% CI | Volume 90% CI | Scenario top-2 share | Eq. distance (ct/l) |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: |",
        ]
    )

    for fuel in FUELS:
        fuel_rows = [row for row in summary_rows if row["fuel"] == fuel]
        fuel_rows.sort(key=lambda row: row["fuel_composite_rank"])
        for row in fuel_rows:
            lines.append(
                "| "
                + FUEL_LABELS[fuel]
                + f" | {row['model_label']}"
                + f" | {row['fuel_composite_rank']}"
                + f" | {row['composite_score']:.2f}"
                + f" | {row['mean_profit_index']:.1f}"
                + f" | {row['mean_expected_volume_index']:.1f}"
                + f" | [{row['profit_ci_lo']:.1f}, {row['profit_ci_hi']:.1f}]"
                + f" | [{row['volume_ci_lo']:.1f}, {row['volume_ci_hi']:.1f}]"
                + f" | {100.0 * row['scenario_top2_share']:.0f}%"
                + f" | {row['equilibrium_distance_cents']:.2f} |"
            )

    lines.extend(
        [
            "",
            "## Equilibrium Closure",
            "",
            "The equilibrium model starts from the tier heuristic and then repeatedly recomputes each station's best response to the current weighted average strategy of nearby competitors in the same fuel market.",
            "",
            "| Iteration | Share of stations materially changing | Mean curve change (ct/l) |",
            "| ---: | ---: | ---: |",
        ]
    )
    for row in equilibrium_diagnostics:
        lines.append(
            f"| {row['iteration']} | {100.0 * row['changed_share']:.2f}% | {row['mean_curve_change_cents']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Uncertainty Analysis",
            "",
            "- Sampling uncertainty is measured with 120 bootstrap draws for model-level anchors, markdowns, profit indices, and expected volume indices.",
            "- Parameter uncertainty is approximated with three scenarios: calm reaction, base reaction, and aggressive reaction.",
            "",
            "| Fuel | Scenario stability: top-1 winners | Scenario stability: top-2 winners |",
            "| --- | --- | --- |",
        ]
    )
    for fuel in FUELS:
        top1 = ", ".join(
            f"{MODEL_LABELS[model]}={count}"
            for model, count in sensitivity[fuel]["top1"].most_common()
        )
        top2 = ", ".join(
            f"{MODEL_LABELS[model]}={count}"
            for model, count in sensitivity[fuel]["top2"].most_common()
        )
        lines.append(f"| {FUEL_LABELS[fuel]} | {top1} | {top2} |")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- If the equilibrium model meaningfully outperforms the static benchmarks, the local strategic interaction matters enough that one-shot heuristics are leaving money on the table.",
            "- If robust best response stays near the top across the aggressive-reaction scenario, the recommended strategy is less fragile to fast local retaliation.",
            "- If simple models such as `uniform_ladder` or `tier_heuristic` remain close to the best-response models even after the richer demand layer, the market may be simple enough to operationalize with lower-complexity rules.",
            "- The multi-criteria score penalizes strategies that look good only on mean profit but are fragile in uncertainty scenarios or far from the equilibrium benchmark.",
            "- Because the demand layer still estimates volume in index units rather than liters, the ranking is materially stronger than the old share-only benchmark but still not a final structural demand model.",
            "",
            "## Outputs",
            "",
            f"- Benchmark report: `{report_path}`",
            f"- Station-model benchmark table: `{station_csv_path}`",
            f"- Model summary table: `{summary_csv_path}`",
            f"- Composite ranking chart: `{chart_path}`",
        ]
    )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path, station_csv_path, summary_csv_path, chart_path


def main() -> None:
    args = parse_args()
    report_path, station_csv_path, summary_csv_path, chart_path = generate(
        history_days=args.history_days,
        history_source=args.history_source,
    )
    print(f"Wrote {report_path}")
    print(f"Wrote {station_csv_path}")
    print(f"Wrote {summary_csv_path}")
    print(f"Wrote {chart_path}")


if __name__ == "__main__":
    main()
