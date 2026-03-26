#!/usr/bin/env python3
"""Generate pricing research outputs.

Supported modes:
- `hourly`: local competitor-aware report from the repo snapshot
- `minutely`: Azure-backed market ladder report via `TK_USER` / `TK_PASS`

Hourly mode uses:
- `data/stations.json` for station metadata and locations
- `data2/<station>/<fuel>.json` for historical price-shape statistics

Hourly outputs:
- `output/pricing_research/pricing_research_report.md`
- `output/pricing_research/station_pricing_strategies.csv`
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[1]
STATIONS_PATH = ROOT / "data" / "stations.json"
DATA2_PATH = ROOT / "data2"
OUTPUT_DIR = ROOT / "output" / "pricing_research"

FUELS = ("diesel", "e10", "e5")
FUEL_LABELS = {"diesel": "Diesel", "e10": "E10", "e5": "E5"}
FUEL_COLORS = {"diesel": "#155e75", "e10": "#c2410c", "e5": "#7c2d12"}
SUPPORTED_TIME_PRECISIONS = ("hourly", "minutely")

RADIUS_SHORT_KM = 2.0
RADIUS_LONG_KM = 3.0
MAX_COMPETITORS = 6
COMPETITION_DECAY_KM = 1.5

LAT_KM = 111.0
LON_KM = 111.0 * math.cos(math.radians(51.0))
GRID_KM = RADIUS_LONG_KM

FUEL_PARAMS = {
    "diesel": {
        "drop_share_base": 0.70,
        "base_quantile": 0.35,
        "undercut_trigger_cents": 1.2,
    },
    "e10": {
        "drop_share_base": 0.82,
        "base_quantile": 0.25,
        "undercut_trigger_cents": 0.9,
    },
    "e5": {
        "drop_share_base": 0.82,
        "base_quantile": 0.25,
        "undercut_trigger_cents": 0.9,
    },
}

LADDER_HOURS = tuple(list(range(12, 24)) + list(range(24, 36)))


def checkpoint_key(hour_index: int) -> str:
    clock_hour = hour_index if hour_index < 24 else hour_index - 24
    if hour_index < 24:
        return f"{clock_hour:02d}_00"
    return f"nextday_{clock_hour:02d}_00"


def checkpoint_label(hour_index: int) -> str:
    clock_hour = hour_index if hour_index < 24 else hour_index - 24
    return f"{clock_hour:02d}:00"


def checkpoint_halfhour_label(hour_index: int) -> str:
    clock_hour = hour_index if hour_index < 24 else hour_index - 24
    return f"{clock_hour:02d}:30"


LADDER_CHECKPOINTS = tuple(
    (checkpoint_key(hour_index), checkpoint_label(hour_index), hour_index)
    for hour_index in LADDER_HOURS
)

CADENCE_ANCHORS_BY_TIER = {
    "intense": {
        12: 0.00,
        14: 0.24,
        17: 0.43,
        20: 0.58,
        22: 0.72,
        24: 0.76,
        30: 0.86,
        32: 0.91,
        34: 0.97,
        35: 1.00,
    },
    "medium": {
        12: 0.00,
        14: 0.18,
        17: 0.35,
        20: 0.51,
        22: 0.66,
        24: 0.71,
        30: 0.82,
        32: 0.88,
        34: 0.96,
        35: 1.00,
    },
    "relaxed": {
        12: 0.00,
        14: 0.12,
        17: 0.27,
        20: 0.44,
        22: 0.60,
        24: 0.65,
        30: 0.77,
        32: 0.84,
        34: 0.94,
        35: 1.00,
    },
}


def interpolate_cadence(anchor_map: dict[int, float]) -> dict[int, float]:
    points = sorted(anchor_map)
    result: dict[int, float] = {}
    for left, right in zip(points, points[1:]):
        left_value = anchor_map[left]
        right_value = anchor_map[right]
        width = right - left
        for hour_index in range(left, right + 1):
            if width == 0:
                result[hour_index] = right_value
                continue
            share = (hour_index - left) / width
            result[hour_index] = left_value + (right_value - left_value) * share
    result[points[-1]] = anchor_map[points[-1]]
    return result


CADENCE_BY_TIER = {
    tier: interpolate_cadence(anchor_map)
    for tier, anchor_map in CADENCE_ANCHORS_BY_TIER.items()
}

RESEARCH_SOURCES = [
    (
        "Taylor & Muehlegger (2025), The Effects of Competition in the Retail Gasoline Industry",
        "https://www.nber.org/papers/w33569",
        "New nearby entry lowers incumbent prices by about 2.5 cents and the effect dissipates with distance.",
    ),
    (
        "Frondel et al. (2020), Empirical investigation of retail fuel pricing",
        "https://www.sciencedirect.com/science/article/pii/S0140988320302164",
        "Spatial interaction, radius-level competition, low-cost rivals, and same-brand presence all matter for station pricing.",
    ),
    (
        "Doyle et al. (2010), A simple spatial model for Edgeworth cycles",
        "https://www.sciencedirect.com/science/article/pii/S0165176510002284",
        "Retail fuel cycles can arise from spatial competition and repeated strategic interaction.",
    ),
    (
        "Borenstein & Shepard (1993/1996), Dynamic Pricing in Retail Gasoline Markets",
        "https://www.nber.org/papers/w4489",
        "Retail margins move with expected future demand and cost conditions, supporting dynamic pricing logic rather than static markup rules.",
    ),
    (
        "Dewenter et al. (2021), Price Effects of the Austrian Fuel Price Fixing Act",
        "https://www.sciencedirect.com/science/article/abs/pii/S0140988321001122",
        "A once-daily adjustment regime had stronger effects for gasoline than diesel, consistent with higher consumer search sensitivity in gasoline grades.",
    ),
    (
        "Clemenz & Gugler (2013), Spatial clustering and market power",
        "https://www.sciencedirect.com/science/article/pii/S0166046213000409",
        "Spatial clustering raises prices by reducing effective local competition.",
    ),
]


@dataclass(frozen=True)
class Station:
    uuid: str
    name: str
    brand: str
    city: str
    street: str
    house_number: str
    post_code: str
    latitude: float
    longitude: float
    x_km: float
    y_km: float


@dataclass(frozen=True)
class FuelStats:
    station_id: str
    fuel: str
    span_cents: float
    minabs: float
    maxabs: float
    midprice: float
    noon_dev_cents: float
    hour22_dev_cents: float
    peak_hour: int
    trough_hour: int


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    idx = q * (len(values) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return values[lo]
    frac = idx - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def project(latitude: float, longitude: float) -> tuple[float, float]:
    return longitude * LON_KM, latitude * LAT_KM


def distance_km(left: Station, right: Station) -> float:
    dx = left.x_km - right.x_km
    dy = left.y_km - right.y_km
    return math.hypot(dx, dy)


def weighted_quantile(values: list[float], weights: list[float], q: float = 0.5) -> float:
    if not values or not weights or len(values) != len(weights):
        return 0.0
    pairs = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        return 0.0
    threshold = q * total
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return pairs[-1][0]


def load_stations() -> dict[str, Station]:
    raw = json.loads(STATIONS_PATH.read_text(encoding="utf-8"))
    stations: dict[str, Station] = {}
    for item in raw:
        brand = (item.get("brand") or "").strip() or "Unbekannt"
        x_km, y_km = project(float(item["latitude"]), float(item["longitude"]))
        stations[item["uuid"]] = Station(
            uuid=item["uuid"],
            name=item.get("name") or "",
            brand=brand,
            city=item.get("city") or "",
            street=item.get("street") or "",
            house_number=item.get("house_number") or "",
            post_code=item.get("post_code") or "",
            latitude=float(item["latitude"]),
            longitude=float(item["longitude"]),
            x_km=x_km,
            y_km=y_km,
        )
    return stations


def fuel_path_to_station_id(path: Path) -> str:
    return "-".join(path.parts[-6:-1])


def load_fuel_stats(stations: dict[str, Station]) -> dict[str, dict[str, FuelStats]]:
    stats_by_station: dict[str, dict[str, FuelStats]] = defaultdict(dict)
    for fuel in FUELS:
        for path in DATA2_PATH.rglob(f"{fuel}.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if "hourly" not in payload or "minabs" not in payload or "span" not in payload:
                continue
            station_id = fuel_path_to_station_id(path)
            if station_id not in stations:
                continue
            hourly = {int(row["hour"]): float(row["price"]) for row in payload["hourly"]}
            if len(hourly) != 24:
                continue
            minabs = float(payload["minabs"])
            maxabs = float(payload["maxabs"])
            if minabs <= 0.5 or maxabs <= 0.5:
                continue
            peak_value = max(hourly.values())
            trough_value = min(hourly.values())
            peak_hour = min(hour for hour, value in hourly.items() if value == peak_value)
            trough_hour = min(hour for hour, value in hourly.items() if value == trough_value)
            stats_by_station[station_id][fuel] = FuelStats(
                station_id=station_id,
                fuel=fuel,
                span_cents=round(float(payload["span"]) * 100, 2),
                minabs=minabs,
                maxabs=maxabs,
                midprice=(minabs + maxabs) / 2,
                noon_dev_cents=round(hourly[12] * 100, 2),
                hour22_dev_cents=round(hourly[22] * 100, 2),
                peak_hour=peak_hour,
                trough_hour=trough_hour,
            )
    return dict(stats_by_station)


def build_grid(stations: dict[str, Station]) -> dict[tuple[int, int], list[str]]:
    grid: dict[tuple[int, int], list[str]] = defaultdict(list)
    for station in stations.values():
        cell = (int(station.x_km // GRID_KM), int(station.y_km // GRID_KM))
        grid[cell].append(station.uuid)
    return grid


def candidate_ids(station: Station, grid: dict[tuple[int, int], list[str]]) -> list[str]:
    base_x = int(station.x_km // GRID_KM)
    base_y = int(station.y_km // GRID_KM)
    result: list[str] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            result.extend(grid.get((base_x + dx, base_y + dy), []))
    return result


def competition_tier(score: float, q25: float, q75: float, nearest_km: float, count_2km: int) -> str:
    if score >= q75 or nearest_km <= 0.5 or count_2km >= 5:
        return "intense"
    if score >= q25 or nearest_km <= 1.0 or count_2km >= 2:
        return "medium"
    return "relaxed"


def strategy_family(tier: str, same_brand_share: float, price_gap_cents: float) -> str:
    if tier == "intense" or price_gap_cents > 1.0:
        return "defensive_volume"
    if tier == "relaxed" and same_brand_share >= 0.3 and price_gap_cents <= 0:
        return "margin_harvest"
    return "balanced_ladder"


def price_band_label(quantile: float) -> str:
    if quantile <= 0.25:
        return "lower_quartile"
    if quantile <= 0.40:
        return "lower_third"
    if quantile <= 0.55:
        return "market_middle"
    return "upper_middle"


def assert_supported_time_precision(time_precision: str) -> None:
    if time_precision in SUPPORTED_TIME_PRECISIONS:
        return
    raise SystemExit(f"Unsupported time precision: {time_precision}")


def render_market_ladder_chart(rows: list[dict[str, object]], time_precision: str) -> Path:
    chart_path = OUTPUT_DIR / "market_markdown_ladder.png"
    x = list(range(len(LADDER_CHECKPOINTS)))
    labels = [checkpoint_halfhour_label(hour_index) for _, _, hour_index in LADDER_CHECKPOINTS]

    fig, ax = plt.subplots(figsize=(18, 8.4))

    for fuel in FUELS:
        fuel_rows = [row for row in rows if row["fuel"] == fuel]
        medians: list[float] = []
        p25s: list[float] = []
        p75s: list[float] = []

        for key, _, _ in LADDER_CHECKPOINTS:
            values = sorted(float(row[f"markdown_{key}_cents"]) for row in fuel_rows)
            medians.append(percentile(values, 0.5))
            p25s.append(percentile(values, 0.25))
            p75s.append(percentile(values, 0.75))

        color = FUEL_COLORS[fuel]
        ax.fill_between(x, p25s, p75s, color=color, alpha=0.16)
        ax.plot(x, medians, color=color, linewidth=2.5, label=FUEL_LABELS[fuel])
        ax.scatter(x, medians, color=color, s=28, zorder=3)

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
        color="#334155",
        fontsize=10,
    )
    ax.text(
        17.5,
        1.08,
        "Folgetag",
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="bottom",
        color="#334155",
        fontsize=10,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=0, ha="center", fontsize=8)
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
    ax.set_ylabel("Erwartete stündliche Preissenktung  (ct/Liter)")
    ax.set_title(
        "Marktweite Preissenkungsleiter (stündlich)"
        if time_precision == "hourly"
        else "Marktweite Preissenkungsleiter"
    )
    ax.set_xlim(-0.5, len(x) - 0.5)
    ax.grid(axis="y", color="#cbd5e1", linewidth=0.8, alpha=0.7)
    ax.legend(frameon=False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["top"].set_visible(True)

    note = (
        "Linie = Median über alle Tankstellen, Band = Interquartilsabstand. "
        "Die Leiter verläuft stündlich, aber auf halbstündig versetzter Zeitachse "
        "von 12:30 Uhr bis 11:30 Uhr am Folgetag."
    )
    fig.subplots_adjust(left=0.07, right=0.98, top=0.72, bottom=0.16)
    fig.text(0.01, 0.075, note, fontsize=9, color="#475569")
    fig.text(0.01, 0.02, "@ProfVolz", fontsize=9, color="#334155", ha="left")
    fig.text(0.99, 0.02, "tankzeit.de", fontsize=9, color="#334155", ha="right")
    fig.savefig(chart_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return chart_path


def generate(time_precision: str = "hourly") -> tuple[Path, Path, Path]:
    assert_supported_time_precision(time_precision)
    if time_precision == "minutely":
        try:
            from generate_minutely_market_report import generate_market_report
        except ModuleNotFoundError:
            from scripts.generate_minutely_market_report import generate_market_report

        return generate_market_report()

    stations = load_stations()
    stats_by_station = load_fuel_stats(stations)
    active_ids = sorted(stats_by_station.keys())
    active_stations = {station_id: stations[station_id] for station_id in active_ids}

    grid = build_grid(stations)

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
    csv_path = OUTPUT_DIR / "station_pricing_strategies.csv"

    competition_tiers = Counter()
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
                local_noon_dev = weighted_quantile(
                    [stats.noon_dev_cents for _, stats in competitor_values], weights
                )
            else:
                local_span_cents = own.span_cents
                local_midprice = own.midprice
                local_noon_dev = own.noon_dev_cents

            pressure = clamp((score - q25) / max(q75 - q25, 0.001), 0.0, 1.0)
            span_blend = (0.55 * own.span_cents) + (0.45 * local_span_cents)
            price_gap_cents = round((own.midprice - local_midprice) * 100, 2)
            gap_index = clamp(price_gap_cents / max(span_blend, 1.0), -1.0, 1.0)

            anchor_multiplier = clamp(
                1.00
                - (0.15 * pressure)
                + (0.10 * same_brand_share)
                - (0.10 * max(gap_index, 0.0))
                + (0.05 * max(-gap_index, 0.0)),
                0.75,
                1.15,
            )
            noon_anchor_over_floor_cents = round(span_blend * anchor_multiplier, 1)

            drop_share = clamp(
                FUEL_PARAMS[fuel]["drop_share_base"]
                + (0.10 * pressure)
                - (0.05 * same_brand_share),
                0.55,
                0.95,
            )
            full_cycle_share = clamp(
                drop_share + 0.18 + (0.05 * pressure) - (0.05 * same_brand_share),
                0.75,
                1.00,
            )
            markdown_11_00_cents = round(
                noon_anchor_over_floor_cents * full_cycle_share,
                1,
            )

            quantile = clamp(
                FUEL_PARAMS[fuel]["base_quantile"]
                + (0.10 * same_brand_share)
                + (0.05 if tier == "relaxed" else 0.0)
                - (0.10 if tier == "intense" else 0.0)
                - (0.10 * max(gap_index, 0.0)),
                0.10,
                0.60,
            )
            trigger_cents = round(
                FUEL_PARAMS[fuel]["undercut_trigger_cents"]
                * (1.0 - (0.20 * same_brand_share) + (0.10 * (1.0 - pressure))),
                1,
            )
            family = strategy_family(tier, same_brand_share, price_gap_cents)
            cadence = CADENCE_BY_TIER[tier]
            ladder_values = {
                key: round(markdown_11_00_cents * cadence[hour_index], 1)
                for key, _, hour_index in LADDER_CHECKPOINTS
            }

            row = {
                "station_uuid": station_id,
                "name": station.name,
                "brand": station.brand,
                "city": station.city,
                "fuel": fuel,
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
                "own_peak_hour": own.peak_hour,
                "own_trough_hour": own.trough_hour,
                "local_noon_dev_cents": round(local_noon_dev, 1),
                "strategy_family": family,
                "target_local_quantile": round(quantile, 2),
                "target_local_band": price_band_label(quantile),
                "noon_anchor_over_recent_floor_cents": noon_anchor_over_floor_cents,
                "undercut_trigger_cents": trigger_cents,
                "top_competitors": "|".join(
                    f"{competitor_id}:{dist:.2f}" for competitor_id, dist in nearby[:MAX_COMPETITORS]
                ),
            }
            for key, _, _ in LADDER_CHECKPOINTS:
                row[f"markdown_{key}_cents"] = ladder_values[key]
            rows.append(row)

            fuel_summary[fuel]["own_span_cents"].append(row["own_span_cents"])
            fuel_summary[fuel]["markdown_22_00_cents"].append(
                row["markdown_22_00_cents"]
            )
            fuel_summary[fuel]["markdown_nextday_11_00_cents"].append(
                markdown_11_00_cents
            )
            fuel_summary[fuel]["noon_anchor_over_recent_floor_cents"].append(
                noon_anchor_over_floor_cents
            )
            fuel_summary[fuel]["target_local_quantile"].append(quantile)

    fieldnames = list(rows[0].keys())
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    chart_path = render_market_ladder_chart(rows, time_precision)

    report_path = OUTPUT_DIR / "pricing_research_report.md"
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    station_count = len(active_ids)
    total_rows = len(rows)

    report_lines: list[str] = [
        "# Competitor-Aware Fuel Pricing Research Report",
        "",
        f"Generated: {generated_at}",
        f"Time precision: {time_precision}",
        f"Repo snapshot: {station_count} stations with usable strategy data, {total_rows} station-fuel rows.",
        "",
        "## Executive Summary",
        "",
        "- The repo snapshot is already sufficient for a first scientific pricing report and a per-station strategy layer.",
        "- Nearby competitors must be modeled explicitly. The literature and the local data both point to strong distance decay: the closest stations matter most.",
        "- The current repo data supports local competition mapping, fuel-specific volatility estimates, and station-level markdown ladders.",
        "- The most informative general-market visualization is a markdown ladder fan chart: one line per fuel with interquartile ribbons across the full hourly cycle from 12:00 to 11:00 on the next day.",
        "- Minute-level precision is not possible from the current repo snapshot alone; that requires the raw Tankerkönig Azure price-event feed.",
        "",
        "## What This Report Uses",
        "",
        "- `data/stations.json`: station metadata and coordinates.",
        "- `data2/<station>/<fuel>.json`: hourly price deviations, recent min/max levels, and price-span statistics.",
        f"- Local competition radius: {RADIUS_LONG_KM:.0f} km with exponential distance weighting (decay {COMPETITION_DECAY_KM:.1f} km).",
        "- Basic data hygiene: station-fuel rows with invalid absolute prices (<= 0.5 EUR/l) are excluded from the strategy layer.",
        "",
        "## Scientific Basis",
        "",
    ]

    for title, url, finding in RESEARCH_SOURCES:
        report_lines.append(f"- [{title}]({url}): {finding}")

    report_lines.extend(
        [
            "",
            "## Market Structure From The Current Snapshot",
            "",
            f"- Competition score quartiles: Q1={q25:.2f}, Q3={q75:.2f}.",
            f"- Competition tiers: intense={competition_tiers['intense']}, medium={competition_tiers['medium']}, relaxed={competition_tiers['relaxed']}.",
            "",
            "| Fuel | Median own span (ct/l) | Median noon anchor over floor (ct/l) | Median markdown by 22:00 (ct/l) | Median markdown by next day 11:00 (ct/l) | Median target local quantile |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for fuel in FUELS:
        spans = sorted(fuel_summary[fuel]["own_span_cents"])
        anchors = sorted(fuel_summary[fuel]["noon_anchor_over_recent_floor_cents"])
        drops_22 = sorted(fuel_summary[fuel]["markdown_22_00_cents"])
        drops_11_00 = sorted(fuel_summary[fuel]["markdown_nextday_11_00_cents"])
        quantiles = sorted(fuel_summary[fuel]["target_local_quantile"])
        report_lines.append(
            "| "
            + FUEL_LABELS[fuel]
            + f" | {percentile(spans, 0.5):.1f}"
            + f" | {percentile(anchors, 0.5):.1f}"
            + f" | {percentile(drops_22, 0.5):.1f}"
            + f" | {percentile(drops_11_00, 0.5):.1f}"
            + f" | {percentile(quantiles, 0.5):.2f} |"
        )

    report_lines.extend(
        [
            "",
            "## Strategy Logic",
            "",
            "For each station and fuel, the model blends four ingredients:",
            "",
            "1. Own station shape: recent intraday span and current historical peak/trough timing.",
            "2. Nearby rivals: counts within 2 km and 3 km, weighted by distance.",
            "3. Relative local level: own recent midprice versus the local weighted competitor midprice.",
            "4. Fuel sensitivity: gasoline grades receive faster markdown ladders than diesel.",
            "",
            "The strategy output for each station-fuel row is:",
            "",
            "- `target_local_quantile`: where the station should sit in the local price distribution at the noon reset.",
            "- `noon_anchor_over_recent_floor_cents`: how far the noon reset can sit above the recent floor.",
            "- `markdown_12_00 ... markdown_23_00_cents`: the hourly cumulative markdown ladder on the same day.",
            "- `markdown_nextday_00_00 ... markdown_nextday_11_00_cents`: the continuation of the hourly ladder through the next day before the next noon reset.",
            "- `undercut_trigger_cents`: how much local undercutting should trigger a response.",
            "- `strategy_family`: `defensive_volume`, `balanced_ladder`, or `margin_harvest`.",
            "",
            "## When The Azure Raw Feed Becomes Necessary",
            "",
            "The repo snapshot is enough for a first hourly report because it already captures local volatility, local levels, and spatial rivalry. The original Azure data becomes necessary for minute-level precision:",
            "",
            "- the current repo stores only hourly aggregates; minute-level ladders cannot be reconstructed from it",
            "- `scripts/generate_data.py` already forward-fills raw changes to a 1-minute grid, but then aggregates to hourly output and discards the minute series",
            "- therefore we need a new Azure-backed export path that preserves per-minute market ladders or per-minute local-competition summaries",
            "- GitHub Actions secrets like `TK_USER` and `TK_PASS` can be used inside workflows, but their plaintext values cannot be read back locally from GitHub",
            "- the practical path is a dedicated GitHub Actions job that fetches Azure raw data with those secrets and writes minute-level artifacts",
            "- this repo now includes `.github/workflows/minutely_market_report.yml` plus `scripts/generate_minutely_market_report.py` for that path",
            "- to keep size under control, minute-level output should start as market-wide and competition-tier summaries, not full per-station minute JSON for all stations",
            "",
            "In short: repo data is enough for hourly strategy design; Azure data is needed for minute-level precision, causal validation, and fine-grained simulation.",
            "",
            "## Outputs",
            "",
            f"- Markdown report: `{report_path}`",
            f"- Station-fuel strategy table: `{csv_path}`",
            f"- General-market visualization: `{chart_path}`",
        ]
    )

    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return report_path, csv_path, chart_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--time-precision",
        choices=SUPPORTED_TIME_PRECISIONS,
        default="hourly",
        help="Time precision for the pricing ladder output.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_path, csv_path, chart_path = generate(time_precision=args.time_precision)
    print(f"Wrote {report_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {chart_path}")


if __name__ == "__main__":
    main()
