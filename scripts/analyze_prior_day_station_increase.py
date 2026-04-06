#!/usr/bin/env python3
"""Audit prior-day first price increases against the daily noon snapshot."""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pytz

try:
    from generate_data import DateRange, TZ, _load_prices
except ModuleNotFoundError:
    from scripts.generate_data import DateRange, TZ, _load_prices


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "output" / "prior_day_station_increase"
FUELS = ("diesel", "e5", "e10")
FUEL_LABELS = {"diesel": "Diesel", "e5": "E5", "e10": "E10"}
FUEL_COLORS = {"diesel": "#2563eb", "e5": "#ea580c", "e10": "#16a34a"}
PRICE_TOLERANCE = 5e-4
DEFAULT_BUCKET_MINUTES = 5
DEFAULT_FOCUS_START = "11:45"
DEFAULT_FOCUS_END = "12:15"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-date",
        type=date.fromisoformat,
        default=None,
        help="Local analysis day in YYYY-MM-DD format. Defaults to yesterday.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for CSV and markdown outputs.",
    )
    parser.add_argument(
        "--prices-csv",
        type=Path,
        help="Optional combined raw price CSV covering the target day and its previous day.",
    )
    parser.add_argument(
        "--cache-prices-csv",
        type=Path,
        help="Optional path to write the loaded raw price window as CSV.",
    )
    parser.add_argument(
        "--noon-csv",
        type=Path,
        default=None,
        help="Optional noon snapshot CSV. Defaults to the dated data2/YYYY/MM/DD/noon.csv path.",
    )
    parser.add_argument(
        "--bucket-minutes",
        type=int,
        default=DEFAULT_BUCKET_MINUTES,
        help=f"Histogram bucket size in minutes. Defaults to {DEFAULT_BUCKET_MINUTES}.",
    )
    parser.add_argument(
        "--focus-start",
        type=str,
        default=None,
        help="Optional chart focus start time in HH:MM local time.",
    )
    parser.add_argument(
        "--focus-end",
        type=str,
        default=None,
        help="Optional chart focus end time in HH:MM local time.",
    )
    return parser.parse_args()


def _default_target_day() -> date:
    return date.today() - timedelta(days=1)


def validate_args(args: argparse.Namespace) -> None:
    if args.bucket_minutes < 1 or 1440 % args.bucket_minutes != 0:
        raise SystemExit("--bucket-minutes must be a positive divisor of 1440.")
    if (args.focus_start is None) != (args.focus_end is None):
        raise SystemExit("--focus-start and --focus-end must be provided together.")
    if args.focus_start is not None:
        start_minute = _parse_clock_minute(args.focus_start)
        end_minute = _parse_clock_minute(args.focus_end)
        if start_minute > end_minute:
            raise SystemExit("--focus-start must be on or before --focus-end.")


def require_credentials() -> None:
    if os.environ.get("TK_USER") and os.environ.get("TK_PASS"):
        return
    raise SystemExit("TK_USER and TK_PASS are required unless --prices-csv is provided.")


def load_prices_window(
    target_day: date,
    prices_csv: Path | None,
    cache_prices_csv: Path | None,
) -> pd.DataFrame:
    if prices_csv is not None:
        print(f"Loading raw prices from {prices_csv}...")
        prices = pd.read_csv(prices_csv)
    else:
        require_credentials()
        load_start = target_day - timedelta(days=1)
        print(f"Downloading raw prices from {load_start} to {target_day}...")
        prices = _load_prices(DateRange(load_start, target_day))

    prices["date"] = pd.to_datetime(prices["date"], errors="coerce", utc=True)
    prices = prices.dropna(subset=["date", "station_uuid"]).sort_values(["station_uuid", "date"])

    if cache_prices_csv is not None:
        cache_prices_csv.parent.mkdir(parents=True, exist_ok=True)
        prices.to_csv(cache_prices_csv, index=False)
        print(f"Wrote raw price cache to {cache_prices_csv}")

    return prices


def resolve_noon_csv_path(target_day: date, explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        return explicit_path
    return ROOT / "data2" / f"{target_day:%Y}" / f"{target_day:%m}" / f"{target_day:%d}" / "noon.csv"


def load_noon_snapshot(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["station_uuid", *FUELS, "last_update"])

    snapshot = pd.read_csv(path, dtype={"station_uuid": "string"})
    if "station_uuid" in snapshot.columns:
        snapshot["station_uuid"] = snapshot["station_uuid"].astype(str)
    for fuel in FUELS:
        if fuel in snapshot.columns:
            snapshot[fuel] = pd.to_numeric(snapshot[fuel], errors="coerce")
    if "last_update" in snapshot.columns:
        snapshot["last_update_ts"] = pd.to_datetime(snapshot["last_update"], errors="coerce", utc=True)
    else:
        snapshot["last_update_ts"] = pd.NaT
        snapshot["last_update"] = pd.NA
    return snapshot


def build_first_daily_increase_events(prices: pd.DataFrame, target_day: date) -> pd.DataFrame:
    noon_local = TZ.localize(datetime.combine(target_day, datetime.min.time()) + timedelta(hours=12))
    event_frames: list[pd.DataFrame] = []

    for fuel in FUELS:
        if fuel not in prices.columns:
            continue
        frame = prices[["station_uuid", "date", fuel]].copy()
        frame[fuel] = pd.to_numeric(frame[fuel], errors="coerce")
        frame = frame.dropna(subset=[fuel]).drop_duplicates(subset=["station_uuid", "date"], keep="last")
        frame = frame.sort_values(["station_uuid", "date"])
        frame["prior_price"] = frame.groupby("station_uuid")[fuel].shift(1)
        frame["delta_cents"] = (frame[fuel] - frame["prior_price"]) * 100.0
        frame["date_local"] = frame["date"].dt.tz_convert(TZ)
        frame["local_day"] = frame["date_local"].dt.date

        increases = frame[
            (frame["local_day"] == target_day)
            & frame["prior_price"].notna()
            & (frame["delta_cents"] > 1e-9)
        ].copy()
        if increases.empty:
            continue

        firsts = (
            increases.sort_values(["station_uuid", "date"])
            .groupby("station_uuid", sort=False)
            .head(1)
            .copy()
        )
        firsts["fuel"] = fuel
        firsts["target_date"] = target_day.isoformat()
        firsts["increase_before_or_at_noon"] = firsts["date_local"] <= noon_local
        firsts["increase_time_local"] = firsts["date_local"].map(lambda ts: ts.isoformat())
        firsts["increase_time_utc"] = firsts["date"].map(lambda ts: ts.isoformat())
        firsts["increase_clock_time"] = firsts["date_local"].dt.strftime("%H:%M:%S")
        firsts["increase_price"] = firsts[fuel].round(3)
        firsts["prior_price"] = firsts["prior_price"].round(3)
        firsts["delta_cents"] = firsts["delta_cents"].round(3)
        event_frames.append(
            firsts[
                [
                    "target_date",
                    "station_uuid",
                    "fuel",
                    "increase_time_local",
                    "increase_time_utc",
                    "increase_clock_time",
                    "increase_before_or_at_noon",
                    "prior_price",
                    "increase_price",
                    "delta_cents",
                ]
            ]
        )

    if not event_frames:
        return pd.DataFrame(
            columns=[
                "target_date",
                "station_uuid",
                "fuel",
                "increase_time_local",
                "increase_time_utc",
                "increase_clock_time",
                "increase_before_or_at_noon",
                "prior_price",
                "increase_price",
                "delta_cents",
            ]
        )

    return pd.concat(event_frames, ignore_index=True).sort_values(
        ["fuel", "station_uuid"]
    ).reset_index(drop=True)


def build_raw_noon_snapshot(prices: pd.DataFrame, target_day: date) -> pd.DataFrame:
    cutoff = pd.Timestamp(
        TZ.localize(datetime.combine(target_day, datetime.min.time()) + timedelta(hours=12)).astimezone(
            pytz.UTC
        )
    )
    available = [fuel for fuel in FUELS if fuel in prices.columns]
    subset = prices.loc[prices["date"] <= cutoff, ["station_uuid", "date", *available]].copy()
    if subset.empty:
        return pd.DataFrame(columns=["station_uuid", *FUELS, "last_update", "last_update_ts"])

    for fuel in available:
        subset[fuel] = pd.to_numeric(subset[fuel], errors="coerce")
    subset = subset.sort_values(["station_uuid", "date"]).groupby("station_uuid", sort=False).tail(1).copy()
    for fuel in FUELS:
        if fuel not in subset.columns:
            subset[fuel] = pd.NA
    subset["last_update_ts"] = subset["date"]
    subset["last_update"] = subset["date"].map(lambda ts: ts.tz_convert(TZ).isoformat())
    return subset[["station_uuid", *FUELS, "last_update", "last_update_ts"]].reset_index(drop=True)


def _merge_snapshot(snapshot: pd.DataFrame, prefix: str) -> pd.DataFrame:
    renamed = snapshot.copy()
    rename_map = {}
    for fuel in FUELS:
        if fuel in renamed.columns:
            rename_map[fuel] = f"{prefix}_{fuel}"
    if "last_update" in renamed.columns:
        rename_map["last_update"] = f"{prefix}_last_update"
    if "last_update_ts" in renamed.columns:
        rename_map["last_update_ts"] = f"{prefix}_last_update_ts"
    return renamed.rename(columns=rename_map)


def _price_matches(left: object, right: object) -> bool:
    if pd.isna(left) or pd.isna(right):
        return False
    return abs(float(left) - float(right)) <= PRICE_TOLERANCE


def _timestamp_matches(left: object, right: object) -> bool:
    if pd.isna(left) or pd.isna(right):
        return False
    left_ts = pd.Timestamp(left)
    right_ts = pd.Timestamp(right)
    return left_ts == right_ts


def _snapshot_status(
    noon_available: bool,
    raw_last_update: object,
    noon_last_update: object,
    raw_price: object,
    noon_price: object,
) -> str:
    if not noon_available:
        return "noon_csv_unavailable"
    if pd.isna(noon_last_update):
        return "missing_station_row"
    if pd.isna(raw_last_update):
        return "missing_raw_noon_snapshot"
    price_match = _price_matches(raw_price, noon_price)
    timestamp_match = _timestamp_matches(raw_last_update, noon_last_update)
    if price_match and timestamp_match:
        return "match"
    if price_match:
        return "timestamp_mismatch"
    if timestamp_match:
        return "price_mismatch"
    return "price_and_timestamp_mismatch"


def _event_status(
    noon_available: bool,
    increase_before_or_at_noon: bool,
    raw_last_update: object,
    raw_price: object,
    noon_last_update: object,
    noon_price: object,
    event_time: object,
    event_price: object,
) -> str:
    if not increase_before_or_at_noon:
        return "after_noon"
    if not noon_available:
        return "noon_csv_unavailable"
    if pd.isna(noon_last_update):
        return "missing_station_row"
    if _timestamp_matches(noon_last_update, event_time) and _price_matches(noon_price, event_price):
        return "exact_event_match"
    if pd.isna(raw_last_update):
        return "missing_raw_noon_snapshot"
    if _timestamp_matches(raw_last_update, event_time) and _price_matches(raw_price, event_price):
        return "noon_csv_mismatch"
    if pd.Timestamp(raw_last_update) > pd.Timestamp(event_time):
        if _price_matches(raw_price, event_price):
            return "later_update_same_price"
        return "overwritten_before_noon"
    if _price_matches(noon_price, event_price):
        return "price_match_only"
    return "not_recoverable_in_noon_csv"


def build_validation_rows(
    events: pd.DataFrame,
    raw_noon_snapshot: pd.DataFrame,
    noon_snapshot: pd.DataFrame,
    noon_available: bool,
) -> pd.DataFrame:
    merged = events.copy()
    raw_prefixed = _merge_snapshot(raw_noon_snapshot, "raw_noon")
    noon_prefixed = _merge_snapshot(noon_snapshot, "noon_csv")
    merged = merged.merge(raw_prefixed, on="station_uuid", how="left")
    merged = merged.merge(noon_prefixed, on="station_uuid", how="left")

    for prefix in ("raw_noon", "noon_csv"):
        if f"{prefix}_last_update_ts" not in merged.columns:
            merged[f"{prefix}_last_update_ts"] = pd.NaT
        if f"{prefix}_last_update" not in merged.columns:
            merged[f"{prefix}_last_update"] = pd.NA
        for fuel in FUELS:
            column = f"{prefix}_{fuel}"
            if column not in merged.columns:
                merged[column] = pd.NA

    snapshot_statuses: list[str] = []
    event_statuses: list[str] = []
    raw_exact_matches: list[bool] = []
    noon_price_matches: list[bool] = []
    noon_timestamp_matches: list[bool] = []
    noon_exact_matches: list[bool] = []
    noon_matches_raw: list[bool] = []

    for row in merged.itertuples(index=False):
        raw_price = getattr(row, f"raw_noon_{row.fuel}")
        noon_price = getattr(row, f"noon_csv_{row.fuel}")
        raw_last_update_ts = row.raw_noon_last_update_ts
        noon_last_update_ts = row.noon_csv_last_update_ts

        snapshot_status = _snapshot_status(
            noon_available,
            raw_last_update_ts,
            noon_last_update_ts,
            raw_price,
            noon_price,
        )
        event_status = _event_status(
            noon_available,
            bool(row.increase_before_or_at_noon),
            raw_last_update_ts,
            raw_price,
            noon_last_update_ts,
            noon_price,
            pd.Timestamp(row.increase_time_utc),
            row.increase_price,
        )
        raw_exact = _timestamp_matches(raw_last_update_ts, pd.Timestamp(row.increase_time_utc)) and _price_matches(
            raw_price, row.increase_price
        )
        noon_price_match = _price_matches(noon_price, row.increase_price)
        noon_timestamp_match = _timestamp_matches(noon_last_update_ts, pd.Timestamp(row.increase_time_utc))
        noon_exact = noon_price_match and noon_timestamp_match
        matches_raw = _price_matches(noon_price, raw_price) and _timestamp_matches(
            noon_last_update_ts, raw_last_update_ts
        )

        snapshot_statuses.append(snapshot_status)
        event_statuses.append(event_status)
        raw_exact_matches.append(raw_exact)
        noon_price_matches.append(noon_price_match)
        noon_timestamp_matches.append(noon_timestamp_match)
        noon_exact_matches.append(noon_exact)
        noon_matches_raw.append(matches_raw)

    merged["raw_noon_matches_exact_event"] = raw_exact_matches
    merged["noon_csv_matches_event_price"] = noon_price_matches
    merged["noon_csv_matches_event_timestamp"] = noon_timestamp_matches
    merged["noon_csv_matches_exact_event"] = noon_exact_matches
    merged["noon_csv_matches_raw_noon_snapshot"] = noon_matches_raw
    merged["noon_csv_snapshot_status"] = snapshot_statuses
    merged["event_visibility_status"] = event_statuses

    return merged[
        [
            "target_date",
            "station_uuid",
            "fuel",
            "increase_time_local",
            "increase_time_utc",
            "increase_clock_time",
            "increase_before_or_at_noon",
            "prior_price",
            "increase_price",
            "delta_cents",
            "raw_noon_diesel",
            "raw_noon_e5",
            "raw_noon_e10",
            "raw_noon_last_update",
            "noon_csv_diesel",
            "noon_csv_e5",
            "noon_csv_e10",
            "noon_csv_last_update",
            "raw_noon_matches_exact_event",
            "noon_csv_matches_event_price",
            "noon_csv_matches_event_timestamp",
            "noon_csv_matches_exact_event",
            "noon_csv_matches_raw_noon_snapshot",
            "noon_csv_snapshot_status",
            "event_visibility_status",
        ]
    ].sort_values(["fuel", "station_uuid"]).reset_index(drop=True)


def build_station_coverage(prices: pd.DataFrame, target_day: date) -> dict[str, int]:
    coverage: dict[str, int] = {fuel: 0 for fuel in FUELS}
    local_day = prices["date"].dt.tz_convert(TZ).dt.date
    for fuel in FUELS:
        if fuel not in prices.columns:
            continue
        values = pd.to_numeric(prices[fuel], errors="coerce")
        mask = (local_day == target_day) & values.notna()
        coverage[fuel] = int(prices.loc[mask, "station_uuid"].astype(str).nunique())
    return coverage


def _share(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def build_summary_rows(validation_rows: pd.DataFrame, coverage: dict[str, int]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for fuel in FUELS:
        fuel_rows = validation_rows.loc[validation_rows["fuel"] == fuel].copy()
        tracked = int(coverage.get(fuel, 0))
        increases = int(len(fuel_rows))
        by_noon = int(fuel_rows["increase_before_or_at_noon"].sum()) if increases else 0
        raw_exact = int(fuel_rows["raw_noon_matches_exact_event"].sum()) if increases else 0
        noon_exact = int(fuel_rows["noon_csv_matches_exact_event"].sum()) if increases else 0
        noon_raw_match = int(fuel_rows["noon_csv_matches_raw_noon_snapshot"].sum()) if increases else 0
        rows.append(
            {
                "fuel": fuel,
                "tracked_stations": tracked,
                "stations_with_first_increase": increases,
                "stations_without_first_increase": max(tracked - increases, 0),
                "first_increase_by_noon": by_noon,
                "first_increase_by_noon_share": _share(by_noon, increases),
                "raw_noon_exact_event": raw_exact,
                "raw_noon_exact_event_share": _share(raw_exact, increases),
                "noon_csv_exact_event": noon_exact,
                "noon_csv_exact_event_share": _share(noon_exact, increases),
                "noon_csv_matches_raw_noon_snapshot": noon_raw_match,
                "noon_csv_matches_raw_noon_snapshot_share": _share(noon_raw_match, increases),
                "median_delta_cents": round(float(fuel_rows["delta_cents"].median()), 3) if increases else 0.0,
                "earliest_increase_time": fuel_rows["increase_clock_time"].min() if increases else "",
                "latest_increase_time": fuel_rows["increase_clock_time"].max() if increases else "",
            }
        )
    return pd.DataFrame(rows)


def _bucket_label(bucket_minute: int) -> str:
    hour, minute = divmod(int(bucket_minute), 60)
    return f"{hour:02d}:{minute:02d}"


def _parse_clock_minute(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise SystemExit(f"Invalid clock time {value!r}; expected HH:MM.") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SystemExit(f"Invalid clock time {value!r}; expected HH:MM within a local day.")
    return hour * 60 + minute


def build_histogram_rows(events: pd.DataFrame, bucket_minutes: int) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(
            columns=[
                "fuel",
                "bucket_minute",
                "bucket_label",
                "increase_events",
                "stations",
                "median_delta_cents",
                "mean_delta_cents",
            ]
        )

    rows = events.copy()
    rows["increase_time_local_ts"] = pd.to_datetime(rows["increase_time_local"], utc=True).dt.tz_convert(TZ)
    minutes_of_day = rows["increase_time_local_ts"].dt.hour * 60 + rows["increase_time_local_ts"].dt.minute
    rows["bucket_minute"] = (minutes_of_day // bucket_minutes) * bucket_minutes
    rows["bucket_label"] = rows["bucket_minute"].map(_bucket_label)

    grouped = (
        rows.groupby(["fuel", "bucket_minute", "bucket_label"], as_index=False)
        .agg(
            increase_events=("station_uuid", "size"),
            stations=("station_uuid", "nunique"),
            median_delta_cents=("delta_cents", "median"),
            mean_delta_cents=("delta_cents", "mean"),
        )
        .sort_values(["fuel", "bucket_minute"])
        .reset_index(drop=True)
    )
    grouped["median_delta_cents"] = grouped["median_delta_cents"].map(lambda value: round(float(value), 3))
    grouped["mean_delta_cents"] = grouped["mean_delta_cents"].map(lambda value: round(float(value), 3))
    return grouped


def filter_histogram_rows(
    histogram_rows: pd.DataFrame,
    focus_start_minute: int | None,
    focus_end_minute: int | None,
) -> pd.DataFrame:
    if focus_start_minute is None or focus_end_minute is None or histogram_rows.empty:
        return histogram_rows.copy()
    return histogram_rows.loc[
        (histogram_rows["bucket_minute"] >= focus_start_minute)
        & (histogram_rows["bucket_minute"] <= focus_end_minute)
    ].copy()


def build_histogram_summary_rows(events: pd.DataFrame, histogram_rows: pd.DataFrame) -> pd.DataFrame:
    summary_rows: list[dict[str, object]] = []
    if events.empty:
        return pd.DataFrame(
            columns=[
                "fuel",
                "increase_events",
                "stations",
                "median_delta_cents",
                "mean_delta_cents",
                "peak_bucket_label",
                "peak_bucket_events",
                "peak_bucket_share",
                "first_increase_timestamp",
                "last_increase_timestamp",
            ]
        )

    for fuel in FUELS:
        fuel_events = events.loc[events["fuel"] == fuel].copy()
        if fuel_events.empty:
            continue
        fuel_histogram = histogram_rows.loc[histogram_rows["fuel"] == fuel].copy()
        peak = fuel_histogram.sort_values(
            ["increase_events", "stations", "bucket_minute"], ascending=[False, False, True]
        ).iloc[0]
        summary_rows.append(
            {
                "fuel": fuel,
                "increase_events": int(len(fuel_events)),
                "stations": int(fuel_events["station_uuid"].nunique()),
                "median_delta_cents": round(float(fuel_events["delta_cents"].median()), 3),
                "mean_delta_cents": round(float(fuel_events["delta_cents"].mean()), 3),
                "peak_bucket_label": str(peak["bucket_label"]),
                "peak_bucket_events": int(peak["increase_events"]),
                "peak_bucket_share": _share(int(peak["increase_events"]), int(len(fuel_events))),
                "first_increase_timestamp": str(fuel_events["increase_time_local"].min()),
                "last_increase_timestamp": str(fuel_events["increase_time_local"].max()),
            }
        )

    return pd.DataFrame(summary_rows).sort_values(["fuel"]).reset_index(drop=True)


def render_histogram_chart(
    histogram_rows: pd.DataFrame,
    bucket_minutes: int,
    chart_path: Path,
    target_day: date,
    focus_start_minute: int | None = None,
    focus_end_minute: int | None = None,
) -> None:
    start_minute = 0 if focus_start_minute is None else focus_start_minute
    end_minute = (24 * 60) - bucket_minutes if focus_end_minute is None else focus_end_minute
    bucket_index = list(range(start_minute, end_minute + 1, bucket_minutes))
    fig, axes = plt.subplots(len(FUELS), 1, figsize=(16, 10), sharex=True, constrained_layout=True)
    if len(FUELS) == 1:
        axes = [axes]

    for axis, fuel in zip(axes, FUELS):
        fuel_rows = histogram_rows.loc[histogram_rows["fuel"] == fuel].copy()
        value_map = {int(row.bucket_minute): int(row.increase_events) for row in fuel_rows.itertuples(index=False)}
        values = [value_map.get(minute, 0) for minute in bucket_index]
        axis.bar(
            bucket_index,
            values,
            width=max(bucket_minutes * 0.92, 1),
            align="edge",
            color=FUEL_COLORS[fuel],
            alpha=0.82,
            edgecolor="white",
            linewidth=0.2,
        )
        axis.axvline(12 * 60, color="#111827", linewidth=1.2, linestyle="--", alpha=0.7)
        axis.set_title(
            f"{FUEL_LABELS[fuel]}: erste tägliche Erhöhung je {bucket_minutes}-Minuten-Bin"
        )
        axis.set_ylabel("Stationen")
        axis.grid(axis="y", alpha=0.25)
        axis.set_xlim(start_minute, end_minute + bucket_minutes)
        axis.set_ylim(0, max(10, int(max(values) * 1.15) if values else 10))

    tick_step = 5 if bucket_minutes == 1 and focus_start_minute is not None else max(60, bucket_minutes)
    tick_minutes = list(range(start_minute, end_minute + tick_step, tick_step))
    axes[-1].set_xticks(tick_minutes)
    axes[-1].set_xticklabels([_bucket_label(minute % (24 * 60)) for minute in tick_minutes])
    axes[-1].set_xlabel("Lokale Uhrzeit (Europe/Berlin)")
    focus_text = ""
    if focus_start_minute is not None and focus_end_minute is not None:
        focus_text = f"\nFokusfenster {_bucket_label(focus_start_minute)} bis {_bucket_label(focus_end_minute)}"
    fig.suptitle(
        f"Zeitstempel der ersten täglichen Preiserhöhung pro Station\n{target_day.isoformat()}{focus_text}",
        fontsize=16,
    )
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(chart_path, dpi=160)
    plt.close(fig)


def write_report(
    path: Path,
    target_day: date,
    noon_csv_path: Path,
    noon_available: bool,
    summary_rows: pd.DataFrame,
    validation_rows: pd.DataFrame,
    histogram_summary_rows: pd.DataFrame,
    histogram_csv_path: Path,
    histogram_chart_path: Path,
    bucket_minutes: int,
) -> None:
    lines = [
        f"# Prior-day station increase audit ({target_day.isoformat()})",
        "",
        f"- Daily noon snapshot path: `{noon_csv_path}`",
        f"- Daily noon snapshot available: `{'yes' if noon_available else 'no'}`",
        "- Snapshot schema check: `noon.csv` contains prices plus `last_update`, but no dedicated first-increase timestamp column.",
        f"- Histogram: exact first-increase timestamps aggregated in `{bucket_minutes}`-minute buckets.",
        f"- Additional artifacts: `{histogram_chart_path.name}`, `{histogram_csv_path.name}`",
        "",
    ]

    if validation_rows.empty:
        lines.extend(
            [
                "No positive price changes were detected for the target day in the available raw window.",
                "",
            ]
        )
    else:
        total_events = len(validation_rows)
        by_noon = int(validation_rows["increase_before_or_at_noon"].sum())
        noon_exact = int(validation_rows["noon_csv_matches_exact_event"].sum())
        raw_exact = int(validation_rows["raw_noon_matches_exact_event"].sum())
        noon_raw_match = int(validation_rows["noon_csv_matches_raw_noon_snapshot"].sum())
        lines.extend(
            [
                f"- Station-fuel first-increase rows: `{total_events:,}`".replace(",", "."),
                f"- First increases by or before 12:00: `{by_noon:,}` ({_share(by_noon, total_events):.1%})".replace(
                    ",", "."
                ),
                f"- Exact event still visible in raw noon snapshot: `{raw_exact:,}` ({_share(raw_exact, total_events):.1%})".replace(
                    ",", "."
                ),
                f"- Exact event recoverable from `noon.csv`: `{noon_exact:,}` ({_share(noon_exact, total_events):.1%})".replace(
                    ",", "."
                ),
                f"- `noon.csv` matches the raw noon snapshot: `{noon_raw_match:,}` ({_share(noon_raw_match, total_events):.1%})".replace(
                    ",", "."
                ),
                "",
                "## Per fuel",
                "",
                "| Fuel | Tracked stations | First increases | By noon | Raw noon exact | noon.csv exact | noon.csv vs raw | Median delta (ct/L) |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in summary_rows.itertuples(index=False):
            lines.append(
                "| "
                + " | ".join(
                    [
                        row.fuel,
                        f"{int(row.tracked_stations):,}".replace(",", "."),
                        f"{int(row.stations_with_first_increase):,}".replace(",", "."),
                        f"{int(row.first_increase_by_noon):,} ({float(row.first_increase_by_noon_share):.1%})".replace(
                            ",", "."
                        ),
                        f"{int(row.raw_noon_exact_event):,} ({float(row.raw_noon_exact_event_share):.1%})".replace(
                            ",", "."
                        ),
                        f"{int(row.noon_csv_exact_event):,} ({float(row.noon_csv_exact_event_share):.1%})".replace(
                            ",", "."
                        ),
                        f"{int(row.noon_csv_matches_raw_noon_snapshot):,} ({float(row.noon_csv_matches_raw_noon_snapshot_share):.1%})".replace(
                            ",", "."
                        ),
                        f"{float(row.median_delta_cents):.3f}",
                    ]
                )
                + " |"
            )

        top_status = (
            validation_rows["event_visibility_status"].value_counts().head(6).reset_index(name="count")
        )
        lines.extend(["", "## Event visibility status", "", "| Status | Rows |", "| --- | ---: |"])
        for row in top_status.itertuples(index=False):
            lines.append(f"| {row.event_visibility_status} | {int(row.count):,} |".replace(",", "."))
        lines.extend(["", "## Timestamp histogram", "", "| Fuel | Erste Erhöhungen | Peak-Bucket | Peak-Anteil | Erste Uhrzeit | Letzte Uhrzeit |", "| --- | ---: | --- | ---: | --- | --- |"])
        for row in histogram_summary_rows.itertuples(index=False):
            lines.append(
                "| "
                + " | ".join(
                    [
                        FUEL_LABELS.get(str(row.fuel), str(row.fuel)),
                        f"{int(row.increase_events):,}".replace(",", "."),
                        f"{row.peak_bucket_label} ({int(row.peak_bucket_events):,})".replace(",", "."),
                        f"{float(row.peak_bucket_share) * 100:.1f} %",
                        str(row.first_increase_timestamp)[11:16],
                        str(row.last_increase_timestamp)[11:16],
                    ]
                )
                + " |"
            )
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    validate_args(args)
    target_day = args.target_date or _default_target_day()
    focus_start_minute = _parse_clock_minute(args.focus_start) if args.focus_start is not None else None
    focus_end_minute = _parse_clock_minute(args.focus_end) if args.focus_end is not None else None
    prices = load_prices_window(target_day, args.prices_csv, args.cache_prices_csv)
    noon_csv_path = resolve_noon_csv_path(target_day, args.noon_csv)
    noon_snapshot = load_noon_snapshot(noon_csv_path)
    raw_noon_snapshot = build_raw_noon_snapshot(prices, target_day)
    events = build_first_daily_increase_events(prices, target_day)
    validation_rows = build_validation_rows(
        events,
        raw_noon_snapshot=raw_noon_snapshot,
        noon_snapshot=noon_snapshot,
        noon_available=noon_csv_path.exists(),
    )
    coverage = build_station_coverage(prices, target_day)
    summary_rows = build_summary_rows(validation_rows, coverage)
    histogram_rows = build_histogram_rows(events, args.bucket_minutes)
    histogram_summary_rows = build_histogram_summary_rows(events, histogram_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_csv_path = args.output_dir / "station_increase_summary.csv"
    detail_csv_path = args.output_dir / "station_first_increase_validation.csv"
    histogram_csv_path = args.output_dir / "first_increase_timestamp_histogram.csv"
    histogram_chart_path = args.output_dir / "first_increase_timestamp_histogram.png"
    report_path = args.output_dir / "prior_day_station_increase_report.md"
    focused_histogram_csv_path = None
    focused_histogram_chart_path = None

    summary_rows.to_csv(summary_csv_path, index=False)
    validation_rows.to_csv(detail_csv_path, index=False)
    histogram_rows.to_csv(histogram_csv_path, index=False)
    render_histogram_chart(
        histogram_rows,
        args.bucket_minutes,
        histogram_chart_path,
        target_day,
        focus_start_minute=focus_start_minute,
        focus_end_minute=focus_end_minute,
    )
    if focus_start_minute is not None and focus_end_minute is not None:
        focused_histogram_rows = filter_histogram_rows(histogram_rows, focus_start_minute, focus_end_minute)
        focus_suffix = f"{args.focus_start.replace(':', '')}_{args.focus_end.replace(':', '')}"
        focused_histogram_csv_path = args.output_dir / f"first_increase_timestamp_histogram_{focus_suffix}.csv"
        focused_histogram_chart_path = args.output_dir / f"first_increase_timestamp_histogram_{focus_suffix}.png"
        focused_histogram_rows.to_csv(focused_histogram_csv_path, index=False)
        render_histogram_chart(
            focused_histogram_rows,
            args.bucket_minutes,
            focused_histogram_chart_path,
            target_day,
            focus_start_minute=focus_start_minute,
            focus_end_minute=focus_end_minute,
        )
    write_report(
        report_path,
        target_day=target_day,
        noon_csv_path=noon_csv_path,
        noon_available=noon_csv_path.exists(),
        summary_rows=summary_rows,
        validation_rows=validation_rows,
        histogram_summary_rows=histogram_summary_rows,
        histogram_csv_path=histogram_csv_path,
        histogram_chart_path=histogram_chart_path,
        bucket_minutes=args.bucket_minutes,
    )

    print(f"Wrote {summary_csv_path}")
    print(f"Wrote {detail_csv_path}")
    print(f"Wrote {histogram_csv_path}")
    print(f"Wrote {histogram_chart_path}")
    if focused_histogram_csv_path is not None and focused_histogram_chart_path is not None:
        print(f"Wrote {focused_histogram_csv_path}")
        print(f"Wrote {focused_histogram_chart_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
