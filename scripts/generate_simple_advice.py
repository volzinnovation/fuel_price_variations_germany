#!/usr/bin/env python3
"""Build the compact 11:00 web-advice summary from generated station profiles."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import median
from typing import Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
BERLIN = ZoneInfo("Europe/Berlin")
FUELS = ("diesel", "e10", "e5")
FUEL_LABELS = {"diesel": "Diesel", "e10": "E10", "e5": "E5"}
PRICE_MIN_EUR = 0.50
PRICE_MAX_EUR = 4.00
PRICE_TOLERANCE = 0.0005


@dataclass(frozen=True)
class ProfileObservation:
    station_uuid: str
    fuel: str
    analysis_date: date
    anchor_price: float
    advice_price: float
    raw_saving: float
    confirmed: bool
    method: str
    best_hour: int | None


def station_profile_paths(data2_dir: Path) -> Iterable[Path]:
    for fuel in FUELS:
        for path in data2_dir.rglob(f"{fuel}.json"):
            relative = path.relative_to(data2_dir)
            if len(relative.parts) == 6:
                yield path


def station_uuid_from_path(data2_dir: Path, path: Path) -> str:
    return "-".join(path.relative_to(data2_dir).parts[:-1])


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_price(value: float | None) -> bool:
    return value is not None and PRICE_MIN_EUR <= value <= PRICE_MAX_EUR


def _profile_date(payload: dict[str, object]) -> date | None:
    raw = payload.get("analysis_end")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def extract_observation(
    payload: dict[str, object],
    *,
    station_uuid: str,
    fuel: str,
    target_date: date,
) -> tuple[ProfileObservation | None, str | None]:
    analysis_date = _profile_date(payload)
    if analysis_date != target_date:
        return None, "stale_profile"

    raw_rows = payload.get("cycle_hourly")
    rows = raw_rows if isinstance(raw_rows, list) else []
    cycle_rows: dict[int, tuple[int, float]] = {}
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        cycle_hour = _number(raw_row.get("cycle_hour"))
        clock_hour = _number(raw_row.get("clock_hour"))
        price = _number(raw_row.get("price_median"))
        if cycle_hour is None or clock_hour is None or price is None:
            continue
        rounded_cycle_hour = int(round(cycle_hour))
        if 0 <= rounded_cycle_hour < 24:
            cycle_rows[rounded_cycle_hour] = (int(round(clock_hour)) % 24, price)

    if 0 in cycle_rows and 23 in cycle_rows:
        prices = [price for _clock_hour, price in cycle_rows.values()]
        if len(cycle_rows) < 24:
            return None, "incomplete_cycle"
        if not all(_valid_price(price) for price in prices):
            return None, "invalid_price"

        anchor_price = cycle_rows[0][1]
        advice_price = cycle_rows[23][1]
        minimum_price = min(prices)
        best_cycle_hour = next(
            cycle_hour
            for cycle_hour, (_clock_hour, price) in sorted(cycle_rows.items())
            if abs(price - minimum_price) <= PRICE_TOLERANCE
        )
        return (
            ProfileObservation(
                station_uuid=station_uuid,
                fuel=fuel,
                analysis_date=target_date,
                anchor_price=anchor_price,
                advice_price=advice_price,
                raw_saving=anchor_price - advice_price,
                confirmed=advice_price <= minimum_price + PRICE_TOLERANCE,
                method="cycle_11_start",
                best_hour=cycle_rows[best_cycle_hour][0],
            ),
            None,
        )

    span = _number(payload.get("span"))
    minimum = _number(payload.get("minabs"))
    maximum = _number(payload.get("maxabs"))
    if (
        span is not None
        and abs(span) <= PRICE_TOLERANCE
        and _valid_price(minimum)
        and _valid_price(maximum)
    ):
        flat_price = float(minimum)
        return (
            ProfileObservation(
                station_uuid=station_uuid,
                fuel=fuel,
                analysis_date=target_date,
                anchor_price=flat_price,
                advice_price=flat_price,
                raw_saving=0.0,
                confirmed=True,
                method="flat_profile_inferred",
                best_hour=None,
            ),
            None,
        )

    return None, "missing_cycle"


def load_station_lookup(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return {}
    return {
        str(row.get("uuid")): row
        for row in payload
        if isinstance(row, dict) and row.get("uuid")
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def summarize_observations(
    observations: list[ProfileObservation],
    excluded: Counter[str],
) -> dict[str, object]:
    consumer_savings = [max(0.0, row.raw_saving) for row in observations]
    confirmed = sum(1 for row in observations if row.confirmed)
    flat = sum(1 for row in observations if row.method == "flat_profile_inferred")
    eligible = len(observations)
    confirmation_rate = confirmed / eligible if eligible else 0.0

    return {
        "eligible_station_fuels": eligible,
        "confirmed_station_fuels": confirmed,
        "exception_station_fuels": eligible - confirmed,
        "confirmation_rate": round(confirmation_rate, 6),
        "flat_profiles_inferred": flat,
        "excluded_station_fuels": int(sum(excluded.values())),
        "excluded_by_reason": dict(sorted(excluded.items())),
        "savings_eur_per_liter": {
            "minimum": round(min(consumer_savings), 3) if consumer_savings else None,
            "average": round(sum(consumer_savings) / len(consumer_savings), 3)
            if consumer_savings
            else None,
            "median": round(float(median(consumer_savings)), 3)
            if consumer_savings
            else None,
            "p95": round(float(percentile(consumer_savings, 0.95)), 3)
            if consumer_savings
            else None,
            "maximum": round(max(consumer_savings), 3) if consumer_savings else None,
        },
    }


def build_station_savings_distribution(
    observations: list[ProfileObservation],
    *,
    bin_width_eur: float = 0.05,
) -> dict[str, object]:
    savings_by_station: dict[str, list[float]] = {}
    for row in observations:
        savings_by_station.setdefault(row.station_uuid, []).append(
            max(0.0, row.raw_saving)
        )

    station_savings = [
        float(median(values)) for values in savings_by_station.values() if values
    ]
    if not station_savings:
        return {
            "grain": "station",
            "aggregation": "median consumer saving across available fuels",
            "station_count": 0,
            "bin_width_eur_per_liter": bin_width_eur,
            "bins": [],
            "savings_eur_per_liter": {
                "minimum": None,
                "average": None,
                "median": None,
                "p95": None,
                "maximum": None,
            },
        }

    maximum = max(station_savings)
    bin_count = max(1, math.ceil(maximum / bin_width_eur))
    counts = [0] * bin_count
    for saving in station_savings:
        index = min(int(saving / bin_width_eur), bin_count - 1)
        counts[index] += 1

    bins = [
        {
            "lower": round(index * bin_width_eur, 3),
            "upper": round((index + 1) * bin_width_eur, 3),
            "count": count,
            "includes_upper": index == bin_count - 1,
        }
        for index, count in enumerate(counts)
    ]
    return {
        "grain": "station",
        "aggregation": "median consumer saving across available fuels",
        "station_count": len(station_savings),
        "bin_width_eur_per_liter": bin_width_eur,
        "bins": bins,
        "savings_eur_per_liter": {
            "minimum": round(min(station_savings), 3),
            "average": round(sum(station_savings) / len(station_savings), 3),
            "median": round(float(median(station_savings)), 3),
            "p95": round(float(percentile(station_savings, 0.95)), 3),
            "maximum": round(maximum, 3),
        },
    }


def build_summary(
    observations: list[ProfileObservation],
    exclusions: dict[str, Counter[str]],
    target_date: date,
    station_lookup: dict[str, dict[str, object]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    station_lookup = station_lookup or {}
    generated_at = generated_at or datetime.now(BERLIN)
    overall_excluded: Counter[str] = Counter()
    for counter in exclusions.values():
        overall_excluded.update(counter)

    eligible = len(observations)
    confirmed = sum(1 for row in observations if row.confirmed)
    confirmation_rate = confirmed / eligible if eligible else 0.0
    if eligible == 0:
        status = "unavailable"
    elif confirmed == eligible:
        status = "confirmed"
    elif confirmation_rate >= 0.995:
        status = "strong_signal"
    else:
        status = "exceptions"

    top_savings = []
    for row in sorted(observations, key=lambda item: item.raw_saving, reverse=True)[:10]:
        station = station_lookup.get(row.station_uuid, {})
        top_savings.append(
            {
                "station_uuid": row.station_uuid,
                "name": station.get("name") or "Tankstelle",
                "brand": station.get("brand") or "",
                "city": station.get("city") or "",
                "fuel": row.fuel,
                "fuel_label": FUEL_LABELS[row.fuel],
                "saving_eur_per_liter": round(max(0.0, row.raw_saving), 3),
            }
        )

    exceptions = []
    for row in sorted(
        (item for item in observations if not item.confirmed),
        key=lambda item: item.advice_price
        - min(item.anchor_price, item.advice_price),
        reverse=True,
    )[:100]:
        exceptions.append(
            {
                "station_uuid": row.station_uuid,
                "fuel": row.fuel,
                "raw_saving_eur_per_liter": round(row.raw_saving, 3),
                "best_hour": row.best_hour,
            }
        )

    cycle_start = datetime.combine(
        target_date - timedelta(days=1),
        time(hour=12),
        tzinfo=BERLIN,
    )
    cycle_end = datetime.combine(
        target_date,
        time(hour=11, minute=59, second=59),
        tzinfo=BERLIN,
    )

    return {
        "schema_version": 1,
        "status": status,
        "analysis_date": target_date.isoformat(),
        "cycle": {
            "start": cycle_start.isoformat(),
            "end": cycle_end.isoformat(),
            "timezone": "Europe/Berlin",
        },
        "advice_window": {
            "start": "11:00",
            "end": "11:59",
            "label": "11:00–11:59 Uhr",
        },
        "generated_at": generated_at.astimezone(BERLIN).isoformat(timespec="seconds"),
        "overall": summarize_observations(observations, overall_excluded),
        "station_savings_distribution": build_station_savings_distribution(
            observations
        ),
        "fuels": {
            fuel: summarize_observations(
                [row for row in observations if row.fuel == fuel],
                exclusions.get(fuel, Counter()),
            )
            for fuel in FUELS
        },
        "top_savings": top_savings,
        "exceptions": exceptions,
        "methodology": {
            "grain": "station_fuel",
            "reference": "median price from 12:00-12:59 on the prior local day",
            "advice_price": "median price from 11:00-11:59 on the completed local day",
            "consumer_saving": "max(0, prior_noon_price - advice_price)",
            "station_distribution": "median consumer saving across available fuels per station",
            "confirmation": "11:00 price is a strict or tied minimum of the 12:00-to-11:59 cycle",
            "flat_profiles": "positive zero-span profiles are included as inferred unchanged prices",
        },
    }


def discover_target_date(data2_dir: Path) -> date:
    latest: date | None = None
    for path in station_profile_paths(data2_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        profile_date = _profile_date(payload)
        if profile_date is not None and (latest is None or profile_date > latest):
            latest = profile_date
    if latest is None:
        raise RuntimeError("No dated station profiles were found.")
    return latest


def collect_observations(
    data2_dir: Path,
    target_date: date,
) -> tuple[list[ProfileObservation], dict[str, Counter[str]]]:
    observations: list[ProfileObservation] = []
    exclusions = {fuel: Counter() for fuel in FUELS}
    for path in station_profile_paths(data2_dir):
        fuel = path.stem
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            exclusions[fuel]["unreadable_profile"] += 1
            continue
        if not isinstance(payload, dict):
            exclusions[fuel]["unreadable_profile"] += 1
            continue
        observation, reason = extract_observation(
            payload,
            station_uuid=station_uuid_from_path(data2_dir, path),
            fuel=fuel,
            target_date=target_date,
        )
        if observation is not None:
            observations.append(observation)
        elif reason:
            exclusions[fuel][reason] += 1
    return observations, exclusions


def _history_row(summary: dict[str, object], fuel: str) -> dict[str, object]:
    stats = summary["overall"] if fuel == "all" else summary["fuels"][fuel]
    savings = stats["savings_eur_per_liter"]
    return {
        "date": summary["analysis_date"],
        "fuel": fuel,
        "eligible_station_fuels": stats["eligible_station_fuels"],
        "confirmed_station_fuels": stats["confirmed_station_fuels"],
        "confirmation_rate": stats["confirmation_rate"],
        "minimum_saving_eur_per_liter": savings["minimum"],
        "average_saving_eur_per_liter": savings["average"],
        "median_saving_eur_per_liter": savings["median"],
        "p95_saving_eur_per_liter": savings["p95"],
        "maximum_saving_eur_per_liter": savings["maximum"],
        "flat_profiles_inferred": stats["flat_profiles_inferred"],
        "excluded_station_fuels": stats["excluded_station_fuels"],
    }


def write_outputs(summary: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    (output_dir / "latest.json").write_text(serialized, encoding="utf-8")
    dated_dir = output_dir / str(summary["analysis_date"]).replace("-", "/")
    dated_dir.mkdir(parents=True, exist_ok=True)
    (dated_dir / "advice.json").write_text(serialized, encoding="utf-8")

    history_path = output_dir / "history.csv"
    rows: list[dict[str, str]] = []
    if history_path.exists():
        with history_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    new_rows = [_history_row(summary, fuel) for fuel in ("all", *FUELS)]
    keys_to_replace = {(row["date"], row["fuel"]) for row in new_rows}
    rows = [
        row
        for row in rows
        if (str(row.get("date")), str(row.get("fuel"))) not in keys_to_replace
    ]
    rows.extend(new_rows)
    rows.sort(key=lambda row: (str(row["date"]), str(row["fuel"])))
    fieldnames = list(new_rows[0].keys())
    with history_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--date", type=date.fromisoformat)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data2_dir = args.repo_root / "data2"
    target_date = args.date or discover_target_date(data2_dir)
    observations, exclusions = collect_observations(data2_dir, target_date)
    summary = build_summary(
        observations,
        exclusions,
        target_date,
        load_station_lookup(args.repo_root / "data" / "stations.json"),
    )
    write_outputs(summary, args.repo_root / "data" / "simple")
    overall = summary["overall"]
    print(
        "Wrote web advice for "
        f"{target_date}: {overall['confirmed_station_fuels']}/"
        f"{overall['eligible_station_fuels']} profiles confirm 11:00."
    )


if __name__ == "__main__":
    main()
