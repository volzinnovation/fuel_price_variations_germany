#!/usr/bin/env python3
"""Generate a market-wide minutely markdown report from Azure raw price data.

This script requires TK_USER and TK_PASS to be present in the environment.
It is intended to run locally with exported credentials or inside GitHub Actions.
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

try:
    from generate_data import DateRange, TZ, _load_prices
except ModuleNotFoundError:
    from scripts.generate_data import DateRange, TZ, _load_prices

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "pricing_research_minutely"

FUELS = ("diesel", "e10", "e5")
FUEL_LABELS = {"diesel": "Diesel", "e10": "E10", "e5": "E5"}
FUEL_COLORS = {"diesel": "#155e75", "e10": "#c2410c", "e5": "#7c2d12"}

START_CLOCK = time(12, 30)
END_CLOCK = time(11, 30)
WINDOW_MINUTES = 23 * 60
REPORT_CHECKPOINTS = (
    ("12:30", 0),
    ("14:30", 120),
    ("17:30", 300),
    ("20:30", 480),
    ("22:30", 600),
    ("06:30", 1080),
    ("08:30", 1200),
    ("10:30", 1320),
    ("11:30", 1380),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=8,
        help="Number of completed cycle start days to include.",
    )
    parser.add_argument(
        "--max-stations",
        type=int,
        default=0,
        help="Optional cap for debugging. 0 means all stations.",
    )
    return parser.parse_args()


def require_credentials() -> None:
    if os.environ.get("TK_USER") and os.environ.get("TK_PASS"):
        return
    raise SystemExit(
        "TK_USER and TK_PASS are required. Run this script in GitHub Actions "
        "or export the credentials locally."
    )


def cycle_start_days(completed_days: int) -> list[date]:
    if completed_days < 1:
        raise SystemExit("--days must be at least 1.")
    today = date.today()
    end_day = today - timedelta(days=1)
    start_day = end_day - timedelta(days=completed_days - 1)
    return [start_day + timedelta(days=offset) for offset in range(completed_days)]


def cycle_labels() -> list[str]:
    start = datetime(2000, 1, 1, START_CLOCK.hour, START_CLOCK.minute)
    labels: list[str] = []
    for minute_offset in range(WINDOW_MINUTES + 1):
        current = start + timedelta(minutes=minute_offset)
        labels.append(current.strftime("%H:%M"))
    return labels


def local_dt(day: date, clock: time) -> datetime:
    return TZ.localize(datetime.combine(day, clock))


def build_station_vectors(
    prices: pd.DataFrame,
    fuel: str,
    start_days: list[date],
    max_stations: int = 0,
) -> tuple[np.ndarray, int]:
    vectors: list[np.ndarray] = []
    stations_seen = 0

    global_start = local_dt(start_days[0], START_CLOCK)
    global_end = local_dt(start_days[-1] + timedelta(days=1), END_CLOCK)
    full_index = pd.date_range(start=global_start, end=global_end, freq="1min", tz=TZ)

    for _, station in prices.groupby("station_uuid", sort=False):
        if max_stations > 0 and stations_seen >= max_stations:
            break
        stations_seen += 1

        station_frame = station[["date", fuel]].copy()
        station_frame[fuel] = pd.to_numeric(station_frame[fuel], errors="coerce")
        station_frame = station_frame.dropna(subset=["date", fuel]).sort_values("date")
        if station_frame.empty:
            continue

        index = station_frame["date"]
        if getattr(index.dt, "tz", None) is None:
            localized = index.dt.tz_localize(TZ)
        else:
            localized = index.dt.tz_convert(TZ)

        series = pd.Series(
            station_frame[fuel].to_numpy(dtype=np.float64),
            index=localized,
        ).sort_index()
        series = series[~series.index.duplicated(keep="last")]
        series = series.loc[(series.index >= global_start - timedelta(days=1)) & (series.index <= global_end)]
        if series.empty:
            continue

        filled = series.reindex(full_index, method="ffill")
        if filled.dropna().empty:
            continue

        cycles: list[np.ndarray] = []
        for day in start_days:
            cycle_start = local_dt(day, START_CLOCK)
            cycle_end = local_dt(day + timedelta(days=1), END_CLOCK)
            cycle = filled.loc[cycle_start:cycle_end]
            if len(cycle) != WINDOW_MINUTES + 1:
                continue
            if cycle.isna().any():
                continue

            baseline = float(cycle.iloc[0])
            if baseline <= 0.5:
                continue

            markdown = (baseline - cycle.to_numpy(dtype=np.float64)) * 100.0
            cycles.append(markdown.astype(np.float32))

        if cycles:
            station_mean = np.mean(np.vstack(cycles), axis=0, dtype=np.float64)
            vectors.append(station_mean.astype(np.float32))

    if not vectors:
        raise SystemExit(f"No usable station vectors for fuel {fuel}.")
    return np.vstack(vectors), len(vectors)


def summarize_market(
    prices: pd.DataFrame,
    start_days: list[date],
    max_stations: int = 0,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    labels = cycle_labels()
    summary_rows: list[dict[str, object]] = []
    station_counts: dict[str, int] = {}

    for fuel in FUELS:
        print(f"Building minutely vectors for {fuel}...")
        matrix, station_count = build_station_vectors(
            prices,
            fuel,
            start_days,
            max_stations=max_stations,
        )
        station_counts[fuel] = station_count

        p25 = np.quantile(matrix, 0.25, axis=0)
        median = np.quantile(matrix, 0.50, axis=0)
        p75 = np.quantile(matrix, 0.75, axis=0)

        for minute_offset, label in enumerate(labels):
            summary_rows.append(
                {
                    "fuel": fuel,
                    "minute_offset": minute_offset,
                    "clock_label": label,
                    "p25_cents": round(float(p25[minute_offset]), 3),
                    "median_cents": round(float(median[minute_offset]), 3),
                    "p75_cents": round(float(p75[minute_offset]), 3),
                }
            )

    return summary_rows, station_counts


def render_chart(summary_rows: list[dict[str, object]], chart_path: Path) -> None:
    labels = cycle_labels()
    x = np.arange(WINDOW_MINUTES + 1)
    fig, ax = plt.subplots(figsize=(18, 8.4))

    for fuel in FUELS:
        fuel_rows = [row for row in summary_rows if row["fuel"] == fuel]
        p25 = np.array([row["p25_cents"] for row in fuel_rows], dtype=float)
        median = np.array([row["median_cents"] for row in fuel_rows], dtype=float)
        p75 = np.array([row["p75_cents"] for row in fuel_rows], dtype=float)
        color = FUEL_COLORS[fuel]
        ax.fill_between(x, p25, p75, color=color, alpha=0.16)
        ax.plot(x, median, color=color, linewidth=2.2, label=FUEL_LABELS[fuel])

    adjustment_box = Rectangle(
        (-30.0, -8.0),
        30.0,
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
        -15.0,
        -4.0,
        "Preisabstimmung",
        rotation=90,
        ha="center",
        va="center",
        color="#1e293b",
        fontsize=9,
        zorder=4,
    )

    ax.set_xlim(-30, WINDOW_MINUTES)
    ax.set_ylim(-10, 10)
    ax.invert_yaxis()
    ax.axvline(690, color="#475569", linestyle="--", linewidth=1.2, alpha=0.8)
    ax.text(
        330,
        1.08,
        "Gleicher Tag",
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="bottom",
        color="#334155",
        fontsize=10,
    )
    ax.text(
        1050,
        1.08,
        "Folgetag",
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="bottom",
        color="#334155",
        fontsize=10,
    )

    tick_positions = list(range(0, WINDOW_MINUTES + 1, 60))
    tick_labels = [labels[position] for position in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, fontsize=8)
    ax.tick_params(
        axis="x",
        top=True,
        labeltop=True,
        bottom=False,
        labelbottom=False,
        pad=10,
    )
    ax.xaxis.tick_top()

    y_ticks = [-10, -8, -6, -4, -2, 0, 2, 4, 6, 8, 10]
    y_tick_labels = ["", "", "", "", "", "0", "2", "4", "6", "8", "10"]
    ax.set_yticks(y_ticks)
    ax.set_yticklabels(y_tick_labels)
    ax.set_ylabel("Erwartete stündliche Preissenktung  (ct/Liter)")
    ax.set_title("Marktweite Preissenkungsleiter (minütlich, Azure)")
    ax.grid(axis="y", color="#cbd5e1", linewidth=0.8, alpha=0.7)
    ax.legend(frameon=False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["top"].set_visible(True)

    note = (
        "Linie = Median über alle Tankstellen, Band = Interquartilsabstand. "
        "Basis: Azure-Rohdaten, minutengenau ab 12:30 Uhr."
    )
    fig.subplots_adjust(left=0.07, right=0.98, top=0.72, bottom=0.16)
    fig.text(0.01, 0.075, note, fontsize=9, color="#475569")
    fig.text(0.01, 0.02, "@ProfVolz", fontsize=9, color="#334155", ha="left")
    fig.text(0.99, 0.02, "tankzeit.de", fontsize=9, color="#334155", ha="right")
    fig.savefig(chart_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def write_report(
    summary_rows: list[dict[str, object]],
    station_counts: dict[str, int],
    start_days: list[date],
    csv_path: Path,
    chart_path: Path,
    report_path: Path,
) -> None:
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    lines = [
        "# Minutely Market Report (Azure)",
        "",
        f"Generated: {generated_at}",
        f"Cycle days: {len(start_days)}",
        "Precision: minutely",
        "",
        "This report uses raw Tankerkönig Azure price data via `TK_USER` and `TK_PASS`.",
        "The minute ladder covers `12:30` to `11:30` and excludes cycle-days that do not map cleanly onto that fixed window, for example daylight-saving anomalies.",
        "",
        "| Kraftstoff | Verwendete Tankstellen | Median 12:30 | Median 14:30 | Median 17:30 | Median 20:30 | Median 22:30 | Median 06:30 | Median 08:30 | Median 10:30 | Median 11:30 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for fuel in FUELS:
        fuel_rows = [row for row in summary_rows if row["fuel"] == fuel]
        medians = {
            row["minute_offset"]: float(row["median_cents"])
            for row in fuel_rows
        }
        values = [f"{medians[offset]:.2f}" for _, offset in REPORT_CHECKPOINTS]
        lines.append(
            "| "
            + FUEL_LABELS[fuel]
            + f" | {station_counts.get(fuel, 0)}"
            + " | "
            + " | ".join(values)
            + " |"
        )

    lines.extend(
        [
            "",
            f"- CSV: `{csv_path}`",
            f"- Chart: `{chart_path}`",
        ]
    )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_market_report(
    days: int = 8,
    max_stations: int = 0,
    output_dir: Path | None = None,
) -> tuple[Path, Path, Path]:
    require_credentials()

    output_dir = output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    start_days = cycle_start_days(days)
    load_start = start_days[0] - timedelta(days=1)
    load_end = start_days[-1] + timedelta(days=1)

    print(f"Loading Azure raw prices from {load_start} to {load_end}...")
    prices = _load_prices(DateRange(load_start, load_end))
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices = prices.dropna(subset=["date", "station_uuid"]).sort_values("date")

    summary_rows, station_counts = summarize_market(
        prices,
        start_days,
        max_stations=max_stations,
    )

    csv_path = output_dir / "minutely_market_quantiles.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "fuel",
                "minute_offset",
                "clock_label",
                "p25_cents",
                "median_cents",
                "p75_cents",
            ],
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    chart_path = output_dir / "market_markdown_ladder_minutely.png"
    render_chart(summary_rows, chart_path)

    report_path = output_dir / "minutely_market_report.md"
    write_report(
        summary_rows,
        station_counts,
        start_days,
        csv_path,
        chart_path,
        report_path,
    )

    return report_path, csv_path, chart_path


def main() -> None:
    args = parse_args()
    report_path, csv_path, chart_path = generate_market_report(
        days=args.days,
        max_stations=args.max_stations,
    )
    print(f"Wrote {csv_path}")
    print(f"Wrote {chart_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
