#!/usr/bin/env python3
"""Analyze the time-of-day distribution of positive price changes on selected days."""

from __future__ import annotations

import argparse
import csv
import os
from datetime import date, timedelta
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


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "output" / "april_increase_timing"
FUELS = ("diesel", "e5", "e10")
FUEL_LABELS = {"diesel": "Diesel", "e5": "E5", "e10": "E10"}
DAY_COLORS = ("#2563eb", "#dc2626", "#7c3aed", "#0f766e")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=date(2026, 4, 1),
        help="First local analysis day in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=date(2026, 4, 2),
        help="Last local analysis day in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--bucket-minutes",
        type=int,
        default=15,
        help="Bucket size for the time-of-day histogram.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for chart, CSV, and markdown outputs.",
    )
    parser.add_argument(
        "--prices-csv",
        type=Path,
        help="Optional raw prices CSV to reuse instead of downloading.",
    )
    parser.add_argument(
        "--cache-prices-csv",
        type=Path,
        help="Optional path to write the loaded raw price window as CSV.",
    )
    return parser.parse_args()


def require_credentials() -> None:
    if os.environ.get("TK_USER") and os.environ.get("TK_PASS"):
        return
    raise SystemExit("TK_USER and TK_PASS are required unless --prices-csv is provided.")


def validate_args(args: argparse.Namespace) -> None:
    if args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date.")
    if args.bucket_minutes < 1 or 1440 % args.bucket_minutes != 0:
        raise SystemExit("--bucket-minutes must be a positive divisor of 1440.")


def load_prices_window(
    start_day: date,
    end_day: date,
    prices_csv: Path | None,
    cache_prices_csv: Path | None,
) -> pd.DataFrame:
    if prices_csv is not None:
        print(f"Loading raw prices from {prices_csv}...")
        prices = pd.read_csv(prices_csv)
    else:
        require_credentials()
        load_start = start_day - timedelta(days=1)
        print(f"Downloading raw prices from {load_start} to {end_day}...")
        prices = _load_prices(DateRange(load_start, end_day))

    prices["date"] = pd.to_datetime(prices["date"], errors="coerce", utc=True)
    prices = prices.dropna(subset=["date", "station_uuid"]).sort_values(["station_uuid", "date"])

    if cache_prices_csv is not None:
        cache_prices_csv.parent.mkdir(parents=True, exist_ok=True)
        prices.to_csv(cache_prices_csv, index=False)
        print(f"Wrote raw price cache to {cache_prices_csv}")

    return prices


def _bucket_label(bucket_minute: int) -> str:
    hour, minute = divmod(int(bucket_minute), 60)
    return f"{hour:02d}:{minute:02d}"


def build_increase_events(
    prices: pd.DataFrame,
    start_day: date,
    end_day: date,
    bucket_minutes: int,
) -> pd.DataFrame:
    target_days = {start_day + timedelta(days=offset) for offset in range((end_day - start_day).days + 1)}
    event_frames: list[pd.DataFrame] = []

    for fuel in FUELS:
        if fuel not in prices.columns:
            continue
        frame = prices[["station_uuid", "date", fuel]].copy()
        frame[fuel] = pd.to_numeric(frame[fuel], errors="coerce")
        frame = frame.dropna(subset=[fuel]).drop_duplicates(subset=["station_uuid", "date"], keep="last")
        frame = frame.sort_values(["station_uuid", "date"])
        previous = frame.groupby("station_uuid")[fuel].shift(1)
        frame["delta_cents"] = (frame[fuel] - previous) * 100.0
        frame["date_local"] = frame["date"].dt.tz_convert(TZ)
        frame["local_day"] = frame["date_local"].dt.date

        increases = frame[(frame["delta_cents"] > 0) & (frame["local_day"].isin(target_days))].copy()
        if increases.empty:
            continue

        minutes_of_day = increases["date_local"].dt.hour * 60 + increases["date_local"].dt.minute
        increases["bucket_minute"] = (minutes_of_day // bucket_minutes) * bucket_minutes
        increases["bucket_label"] = increases["bucket_minute"].map(_bucket_label)
        increases["fuel"] = fuel
        event_frames.append(
            increases[
                [
                    "fuel",
                    "station_uuid",
                    "date",
                    "date_local",
                    "local_day",
                    "delta_cents",
                    "bucket_minute",
                    "bucket_label",
                ]
            ].copy()
        )

    if not event_frames:
        return pd.DataFrame(
            columns=[
                "fuel",
                "station_uuid",
                "date",
                "date_local",
                "local_day",
                "delta_cents",
                "bucket_minute",
                "bucket_label",
            ]
        )
    return pd.concat(event_frames, ignore_index=True)


def build_bucket_rows(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(
            columns=[
                "fuel",
                "local_day",
                "bucket_minute",
                "bucket_label",
                "increase_events",
                "stations",
                "delta_cents_sum",
                "delta_cents_median",
                "delta_cents_mean",
            ]
        )

    grouped = (
        events.groupby(["fuel", "local_day", "bucket_minute", "bucket_label"], as_index=False)
        .agg(
            increase_events=("station_uuid", "size"),
            stations=("station_uuid", "nunique"),
            delta_cents_sum=("delta_cents", "sum"),
            delta_cents_median=("delta_cents", "median"),
            delta_cents_mean=("delta_cents", "mean"),
        )
        .sort_values(["fuel", "local_day", "bucket_minute"])
        .reset_index(drop=True)
    )
    for column in ("delta_cents_sum", "delta_cents_median", "delta_cents_mean"):
        grouped[column] = grouped[column].map(lambda value: round(float(value), 3))
    return grouped


def build_summary_rows(events: pd.DataFrame, bucket_rows: pd.DataFrame) -> pd.DataFrame:
    summary_rows: list[dict[str, object]] = []
    if events.empty:
        return pd.DataFrame(
            columns=[
                "fuel",
                "local_day",
                "increase_events",
                "stations",
                "median_delta_cents",
                "mean_delta_cents",
                "max_delta_cents",
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
        for local_day in sorted(fuel_events["local_day"].unique()):
            day_events = fuel_events.loc[fuel_events["local_day"] == local_day].copy()
            day_buckets = bucket_rows.loc[
                (bucket_rows["fuel"] == fuel) & (bucket_rows["local_day"] == local_day)
            ].copy()
            peak = day_buckets.sort_values(
                ["increase_events", "stations", "bucket_minute"], ascending=[False, False, True]
            ).iloc[0]
            summary_rows.append(
                {
                    "fuel": fuel,
                    "local_day": local_day.isoformat(),
                    "increase_events": int(len(day_events)),
                    "stations": int(day_events["station_uuid"].nunique()),
                    "median_delta_cents": round(float(day_events["delta_cents"].median()), 3),
                    "mean_delta_cents": round(float(day_events["delta_cents"].mean()), 3),
                    "max_delta_cents": round(float(day_events["delta_cents"].max()), 3),
                    "peak_bucket_label": str(peak["bucket_label"]),
                    "peak_bucket_events": int(peak["increase_events"]),
                    "peak_bucket_share": round(float(peak["increase_events"]) / float(len(day_events)), 4),
                    "first_increase_timestamp": day_events["date_local"].min().isoformat(),
                    "last_increase_timestamp": day_events["date_local"].max().isoformat(),
                }
            )

    return pd.DataFrame(summary_rows).sort_values(["fuel", "local_day"]).reset_index(drop=True)


def render_chart(
    bucket_rows: pd.DataFrame,
    start_day: date,
    end_day: date,
    bucket_minutes: int,
    chart_path: Path,
) -> None:
    target_days = [start_day + timedelta(days=offset) for offset in range((end_day - start_day).days + 1)]
    bucket_index = np.arange(0, 24 * 60, bucket_minutes)
    fig, axes = plt.subplots(len(FUELS), 1, figsize=(16, 10), sharex=True, constrained_layout=True)
    if len(FUELS) == 1:
        axes = [axes]

    for axis, fuel in zip(axes, FUELS):
        fuel_rows = bucket_rows.loc[bucket_rows["fuel"] == fuel].copy()
        max_count = 0
        for day_index, local_day in enumerate(target_days):
            day_rows = fuel_rows.loc[fuel_rows["local_day"] == local_day].copy()
            if day_rows.empty:
                values = np.zeros_like(bucket_index, dtype=float)
            else:
                day_map = {int(row.bucket_minute): int(row.increase_events) for row in day_rows.itertuples(index=False)}
                values = np.array([day_map.get(int(minute), 0) for minute in bucket_index], dtype=float)
            max_count = max(max_count, int(values.max()) if len(values) else 0)
            color = DAY_COLORS[day_index % len(DAY_COLORS)]
            axis.plot(
                bucket_index,
                values,
                label=str(local_day),
                color=color,
                linewidth=2.2,
                alpha=0.95,
            )
            axis.fill_between(bucket_index, values, 0, color=color, alpha=0.10)

        axis.set_title(f"{FUEL_LABELS[fuel]}: positive Preisänderungen je {bucket_minutes}-Minuten-Bin")
        axis.set_ylabel("Anzahl Erhöhungen")
        axis.grid(axis="y", alpha=0.25)
        axis.set_xlim(0, 24 * 60 - bucket_minutes)
        axis.set_ylim(0, max(10, max_count * 1.15 if max_count else 10))

    tick_minutes = np.arange(0, 24 * 60 + 1, 120)
    axes[-1].set_xticks(tick_minutes)
    axes[-1].set_xticklabels([_bucket_label(minute % (24 * 60)) for minute in tick_minutes], rotation=0)
    axes[-1].set_xlabel("Lokale Uhrzeit (Europe/Berlin)")
    axes[0].legend(frameon=False, ncols=min(4, len(target_days)), loc="upper right")
    fig.suptitle(
        f"Zeitliche Verteilung von Erhöhungen\n{start_day.isoformat()} bis {end_day.isoformat()}",
        fontsize=16,
    )
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(chart_path, dpi=160)
    plt.close(fig)


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_report(
    summary_rows: pd.DataFrame,
    start_day: date,
    end_day: date,
    bucket_minutes: int,
    chart_path: Path,
    bucket_csv_path: Path,
    summary_csv_path: Path,
) -> str:
    lines = [
        "# Analyse der zeitlichen Verteilung von Erhöhungen",
        "",
        f"- Zeitraum: `{start_day.isoformat()}` bis `{end_day.isoformat()}`",
        f"- Methodik: positive Preisänderungen (`delta_cents > 0`) aus Tankerkönig-Rohdaten, aggregiert in `{bucket_minutes}`-Minuten-Bins nach lokaler Zeit `Europe/Berlin`.",
        f"- Artefakte: `{chart_path.name}`, `{bucket_csv_path.name}`, `{summary_csv_path.name}`",
        "",
        "## Überblick",
        "",
        "| Fuel | Tag | Erhöhungen | Stationen | Median Delta (ct/l) | Peak-Bin | Peak-Anteil | Erste Erhöhung | Letzte Erhöhung |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | --- | --- |",
    ]

    if summary_rows.empty:
        lines.extend(
            [
                "| - | - | 0 | 0 | - | - | - | - | - |",
                "",
                "Keine positiven Preisänderungen im gewählten Zeitraum gefunden.",
            ]
        )
        return "\n".join(lines) + "\n"

    for row in summary_rows.itertuples(index=False):
        lines.append(
            "| "
            + " | ".join(
                [
                    FUEL_LABELS.get(str(row.fuel), str(row.fuel)),
                    str(row.local_day),
                    f"{int(row.increase_events):,}".replace(",", "."),
                    f"{int(row.stations):,}".replace(",", "."),
                    f"{float(row.median_delta_cents):.3f}",
                    f"{row.peak_bucket_label} ({int(row.peak_bucket_events):,})".replace(",", "."),
                    f"{float(row.peak_bucket_share) * 100:.1f} %",
                    str(row.first_increase_timestamp)[11:16],
                    str(row.last_increase_timestamp)[11:16],
                ]
            )
            + " |"
        )

    lines.extend(["", "## Kurzbefunde", ""])
    for fuel in FUELS:
        fuel_rows = summary_rows.loc[summary_rows["fuel"] == fuel].copy()
        if fuel_rows.empty:
            continue
        strongest = fuel_rows.sort_values(
            ["peak_bucket_share", "increase_events"], ascending=[False, False]
        ).iloc[0]
        busiest = fuel_rows.sort_values("increase_events", ascending=False).iloc[0]
        lines.append(
            f"- {FUEL_LABELS[fuel]}: stärkster Zeitcluster am `{strongest['local_day']}` um `{strongest['peak_bucket_label']}` "
            f"mit `{int(strongest['peak_bucket_events']):,}` Erhöhungen "
            f"({float(strongest['peak_bucket_share']) * 100:.1f} % des Tagesvolumens).".replace(",", ".")
        )
        if strongest["local_day"] != busiest["local_day"]:
            lines.append(
                f"- {FUEL_LABELS[fuel]}: das höchste Tagesvolumen lag am `{busiest['local_day']}` "
                f"mit insgesamt `{int(busiest['increase_events']):,}` positiven Preisänderungen.".replace(",", ".")
            )
    return "\n".join(lines) + "\n"


def generate(
    start_day: date,
    end_day: date,
    bucket_minutes: int,
    output_dir: Path,
    prices_csv: Path | None = None,
    cache_prices_csv: Path | None = None,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    prices = load_prices_window(start_day, end_day, prices_csv, cache_prices_csv)
    events = build_increase_events(prices, start_day, end_day, bucket_minutes)
    bucket_rows = build_bucket_rows(events)
    summary_rows = build_summary_rows(events, bucket_rows)

    chart_path = output_dir / "increase_timing_distribution.png"
    summary_csv_path = output_dir / "increase_timing_summary.csv"
    bucket_csv_path = output_dir / "increase_events_by_bucket.csv"
    report_path = output_dir / "increase_timing_report.md"

    render_chart(bucket_rows, start_day, end_day, bucket_minutes, chart_path)
    write_csv(summary_csv_path, summary_rows.to_dict(orient="records"), list(summary_rows.columns))
    write_csv(bucket_csv_path, bucket_rows.to_dict(orient="records"), list(bucket_rows.columns))
    report_path.write_text(
        build_report(
            summary_rows,
            start_day,
            end_day,
            bucket_minutes,
            chart_path,
            bucket_csv_path,
            summary_csv_path,
        ),
        encoding="utf-8",
    )

    print(f"Wrote chart to {chart_path}")
    print(f"Wrote summary CSV to {summary_csv_path}")
    print(f"Wrote bucket CSV to {bucket_csv_path}")
    print(f"Wrote report to {report_path}")
    return chart_path, summary_csv_path, report_path


def main() -> None:
    args = parse_args()
    validate_args(args)
    generate(
        start_day=args.start_date,
        end_day=args.end_date,
        bucket_minutes=args.bucket_minutes,
        output_dir=args.output_dir,
        prices_csv=args.prices_csv,
        cache_prices_csv=args.cache_prices_csv,
    )


if __name__ == "__main__":
    main()
