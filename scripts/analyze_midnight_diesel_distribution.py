#!/usr/bin/env python3
"""Analyze diesel midnight prices overall and by operator."""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

MISC_LABEL = "MISC"
NON_BRAND_LABELS = {"UNBEKANNT"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("latest-noon", "midnight-csv"),
        default="latest-noon",
        help="Snapshot source. Defaults to the latest noon high from data2 station payloads.",
    )
    parser.add_argument(
        "--fuel",
        default="diesel",
        help="Fuel column / payload name to analyze. Defaults to diesel.",
    )
    parser.add_argument(
        "--midnight-csv",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "midnight.csv",
    )
    parser.add_argument(
        "--data2-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data2",
    )
    parser.add_argument(
        "--stations-json",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "stations.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "output",
    )
    parser.add_argument(
        "--target-date",
        type=str,
        help="Snapshot day in YYYY-MM-DD. For latest-noon, defaults to the latest available noon date in data2.",
    )
    parser.add_argument(
        "--top-n-brands",
        type=int,
        default=9,
        help="Keep the top N active brands separate and aggregate the rest into the misc bucket.",
    )
    return parser.parse_args()


def _parse_price_column(series: pd.Series) -> pd.Series:
    values = series.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(values, errors="coerce")


def load_midnight(path: Path, fuel: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    if fuel not in df.columns:
        raise SystemExit(f"Fuel column {fuel!r} not found in {path}.")
    df[fuel] = _parse_price_column(df[fuel])
    return df.dropna(subset=["station_uuid", fuel]).copy()


def _parse_target_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return date.fromisoformat(raw)


def _positive_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _station_uuid_from_data2_path(path: Path, data2_dir: Path) -> str:
    relative = path.relative_to(data2_dir)
    if len(relative.parts) < 6:
        raise ValueError(f"Unexpected data2 path layout: {path}")
    return "-".join(relative.parts[:5])


def _extract_noon_row(payload: dict[str, object], target_iso: str) -> tuple[float | None, str | None]:
    daily_rows = payload.get("daily") or []
    for row in daily_rows:
        if str(row.get("date")) != target_iso:
            continue
        price = _positive_float(row.get("max_price"))
        if price is None:
            price = _positive_float(row.get("noon_price"))
        if price is None:
            continue
        timestamp = row.get("window_start_timestamp")
        return price, str(timestamp) if timestamp else None
    return None, None


def _latest_available_noon_date(data2_dir: Path, fuel: str) -> str:
    latest_date: str | None = None
    for path in data2_dir.rglob(f"{fuel}.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in payload.get("daily") or []:
            day = row.get("date")
            price = _positive_float(row.get("max_price"))
            if price is None:
                price = _positive_float(row.get("noon_price"))
            if not day or price is None:
                continue
            day = str(day)
            if latest_date is None or day > latest_date:
                latest_date = day
    if latest_date is None:
        raise SystemExit(f"No usable noon snapshot rows found in {data2_dir} for fuel {fuel!r}.")
    return latest_date


def load_latest_noon_snapshot(data2_dir: Path, fuel: str, target_day: date | None) -> tuple[pd.DataFrame, dict[str, str]]:
    if not data2_dir.exists():
        raise SystemExit(f"{data2_dir} not found. Run generate_data.py first.")

    target_iso = target_day.isoformat() if target_day else _latest_available_noon_date(data2_dir, fuel)
    rows: list[dict[str, object]] = []
    first_timestamp: str | None = None
    for path in data2_dir.rglob(f"{fuel}.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        price, timestamp = _extract_noon_row(payload, target_iso)
        if price is None:
            continue
        if first_timestamp is None and timestamp:
            first_timestamp = timestamp
        rows.append(
            {
                "station_uuid": _station_uuid_from_data2_path(path, data2_dir),
                fuel: price,
            }
        )

    if not rows:
        raise SystemExit(f"No noon snapshot rows found for {target_iso} in {data2_dir}.")

    snapshot = pd.DataFrame(rows).drop_duplicates(subset=["station_uuid"], keep="last")
    metadata = {
        "source": "latest-noon",
        "snapshot_date": target_iso,
        "snapshot_timestamp": first_timestamp or f"{target_iso}T12:00",
        "title_short": f"{target_iso} 12:00",
        "title_long": f"{target_iso} 12:00 (Mittagshoch)",
        "output_prefix": f"latest_noon_{fuel}",
        "value_label": "12:00-Hochpreis",
        "report_title": f"{fuel.capitalize()}-Latest-Noon-Distribution",
    }
    return snapshot, metadata


def load_station_metadata(path: Path) -> pd.DataFrame:
    stations = pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
    stations["operator"] = stations["brand"].fillna("").astype(str).str.strip().str.upper()
    stations["operator"] = stations["operator"].replace({"": "UNBEKANNT"})
    return stations[["uuid", "operator"]].copy()


def prepare_joined(midnight: pd.DataFrame, stations: pd.DataFrame, top_n_brands: int) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    joined = midnight.merge(stations, left_on="station_uuid", right_on="uuid", how="left")
    joined["operator"] = joined["operator"].fillna("UNBEKANNT")
    counts = joined["operator"].value_counts()
    branded_counts = counts.loc[~counts.index.isin(NON_BRAND_LABELS)]
    focus_operators = branded_counts.head(top_n_brands).index.tolist()
    focus_set = set(focus_operators)
    joined["operator_group"] = joined["operator"].map(lambda value: value if value in focus_set else MISC_LABEL)
    return joined, counts, focus_operators + [MISC_LABEL]


def fit_normal(values: pd.Series) -> tuple[float, float]:
    array = values.to_numpy(dtype=float)
    return float(array.mean()), float(array.std(ddof=0))


def fit_shifted_lognormal(values: pd.Series) -> tuple[float, float, float]:
    lower_step = 0.001
    shift = float(values.min()) - lower_step
    positive = values - shift
    logs = np.log(positive.to_numpy(dtype=float))
    return shift, float(logs.mean()), float(logs.std(ddof=0))


def normal_pdf(x: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return np.zeros_like(x)
    coeff = 1.0 / (sigma * math.sqrt(2.0 * math.pi))
    return coeff * np.exp(-0.5 * ((x - mu) / sigma) ** 2)


def shifted_lognormal_pdf(x: np.ndarray, shift: float, mu_log: float, sigma_log: float) -> np.ndarray:
    if sigma_log <= 0:
        return np.zeros_like(x)
    shifted = x - shift
    pdf = np.zeros_like(x)
    mask = shifted > 0
    valid = shifted[mask]
    coeff = 1.0 / (valid * sigma_log * math.sqrt(2.0 * math.pi))
    exponent = -0.5 * ((np.log(valid) - mu_log) / sigma_log) ** 2
    pdf[mask] = coeff * np.exp(exponent)
    return pdf


def summarize_distribution(values: pd.Series) -> dict[str, float]:
    mu_norm, sigma_norm = fit_normal(values)
    shift, mu_log, sigma_log = fit_shifted_lognormal(values)
    return {
        "station_count": int(values.size),
        "mean": float(values.mean()),
        "std": float(values.std(ddof=1)),
        "median": float(values.median()),
        "q05": float(values.quantile(0.05)),
        "q25": float(values.quantile(0.25)),
        "q75": float(values.quantile(0.75)),
        "q95": float(values.quantile(0.95)),
        "skew": float(values.skew()),
        "kurtosis_excess": float(values.kurt()),
        "normal_mu": mu_norm,
        "normal_sigma": sigma_norm,
        "shifted_lognormal_shift": shift,
        "shifted_lognormal_mu": mu_log,
        "shifted_lognormal_sigma": sigma_log,
    }


def plot_distribution(ax: plt.Axes, values: pd.Series, title: str, x_limits: tuple[float, float] | None = None) -> None:
    mu_norm, sigma_norm = fit_normal(values)
    shift, mu_log, sigma_log = fit_shifted_lognormal(values)
    bins = max(20, min(50, int(math.sqrt(values.size))))
    ax.hist(
        values,
        bins=bins,
        density=True,
        color="#155e75",
        edgecolor="white",
        linewidth=0.5,
        alpha=0.8,
    )
    left = x_limits[0] if x_limits else float(values.min())
    right = x_limits[1] if x_limits else float(values.max())
    x_grid = np.linspace(left, right, 500)
    ax.plot(x_grid, normal_pdf(x_grid, mu_norm, sigma_norm), color="#c2410c", linewidth=2, label="Normal-Fit")
    ax.plot(
        x_grid,
        shifted_lognormal_pdf(x_grid, shift, mu_log, sigma_log),
        color="#7c2d12",
        linewidth=2,
        linestyle="--",
        label="Shifted-Lognormal-Fit",
    )
    ax.axvline(values.median(), color="#111827", linewidth=1.5, alpha=0.8, label="Median")
    ax.set_title(title)
    ax.set_xlabel("Dieselpreis (EUR/l)")
    ax.set_ylabel("Dichte")
    ax.grid(axis="y", color="#d1d5db", linewidth=0.8, alpha=0.7)
    if x_limits:
        ax.set_xlim(*x_limits)


def write_report(
    out_path: Path,
    snapshot_meta: dict[str, str],
    fuel: str,
    overall_summary: dict[str, float],
    operator_summary: pd.DataFrame,
    misc_members: pd.Series,
    focus_operators: list[str],
) -> None:
    focus_rows = operator_summary.set_index("operator_group")
    lines = [
        f"# {snapshot_meta['report_title']}",
        "",
        f"Zeitpunkt: {snapshot_meta['title_long']} Europe/Berlin.",
        "",
        f"Stichprobe: {int(overall_summary['station_count']):,} Tankstellen.".replace(",", "."),
        (
            f"Gesamtmarkt ({fuel}): Mittelwert {overall_summary['mean']:.3f} EUR/l, "
            f"Median {overall_summary['median']:.3f}, Standardabweichung {overall_summary['std']:.3f}, "
            f"Schiefe {overall_summary['skew']:.3f}."
        ),
        "",
        f"## Top {len(focus_operators) - 1} Marken + Misc",
        "",
    ]
    for operator in focus_operators:
        if operator not in focus_rows.index:
            continue
        row = focus_rows.loc[operator]
        lines.append(
            (
                f"- {operator}: n={int(row['station_count'])}, Mittelwert {row['mean']:.3f}, "
                f"Median {row['median']:.3f}, Std {row['std']:.3f}, "
                f"Q05 {row['q05']:.3f}, Q95 {row['q95']:.3f}, Schiefe {row['skew']:.3f}."
            )
        )
    if not misc_members.empty:
        lines.extend(
            [
                "",
                "## Misc",
                "",
                (
                    f"Zusammengefasst: {int(misc_members.sum())} Stationen über "
                    f"{int(misc_members.size)} Betreiber."
                ),
                "Größte Mitglieder dieser Sammelgruppe:",
            ]
        )
        for operator, count in misc_members.head(15).items():
            lines.append(f"- {operator}: {int(count)}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target_day = _parse_target_date(args.target_date)

    if args.source == "latest-noon":
        snapshot, snapshot_meta = load_latest_noon_snapshot(args.data2_dir, args.fuel, target_day)
    else:
        snapshot = load_midnight(args.midnight_csv, args.fuel)
        snapshot_meta = {
            "source": "midnight-csv",
            "snapshot_date": target_day.isoformat() if target_day else "latest",
            "snapshot_timestamp": (target_day.isoformat() + "T00:00") if target_day else "Mitternacht",
            "title_short": (target_day.isoformat() + " 00:00") if target_day else "Mitternacht",
            "title_long": (target_day.isoformat() + " 00:00 (Mitternacht)") if target_day else "Mitternacht",
            "output_prefix": f"midnight_{args.fuel}",
            "value_label": "Mitternachtspreis",
            "report_title": f"{args.fuel.capitalize()}-Midnight-Distribution",
        }
    stations = load_station_metadata(args.stations_json)
    joined, raw_operator_counts, focus_operators = prepare_joined(snapshot, stations, args.top_n_brands)

    overall = summarize_distribution(joined[args.fuel])

    grouped = []
    grouped_counts = joined["operator_group"].value_counts()
    for operator_group in grouped_counts.index:
        values = joined.loc[joined["operator_group"] == operator_group, args.fuel].dropna()
        summary = summarize_distribution(values)
        summary["operator_group"] = operator_group
        grouped.append(summary)
    operator_summary = pd.DataFrame(grouped)
    operator_summary = operator_summary.sort_values(["station_count", "operator_group"], ascending=[False, True])
    operator_summary.to_csv(
        args.output_dir / f"{snapshot_meta['output_prefix']}_operator_summary.csv",
        index=False,
        float_format="%.6f",
    )

    focus_set = set(focus_operators[:-1])
    misc_members = raw_operator_counts.loc[~raw_operator_counts.index.isin(focus_set)].sort_values(ascending=False)
    misc_members.to_csv(
        args.output_dir / f"{snapshot_meta['output_prefix']}_misc_operators.csv",
        header=["station_count"],
    )

    x_limits = (
        float(joined[args.fuel].quantile(0.001)),
        float(joined[args.fuel].quantile(0.995)),
    )

    fig, ax = plt.subplots(figsize=(12, 7), dpi=180)
    plot_distribution(
        ax,
        joined[args.fuel],
        f"{args.fuel.capitalize()}preise {snapshot_meta['title_short']}: Gesamtmarkt",
        x_limits=x_limits,
    )
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(args.output_dir / f"{snapshot_meta['output_prefix']}_distribution_fit.png", bbox_inches="tight")
    plt.close(fig)

    stacked_panels = [("Gesamtmarkt", joined[args.fuel])] + [
        (operator, joined.loc[joined["operator_group"] == operator, args.fuel].dropna())
        for operator in focus_operators
    ]
    fig, axes = plt.subplots(
        len(stacked_panels),
        1,
        figsize=(12, max(3.8 * len(stacked_panels), 9)),
        dpi=180,
        sharex=True,
        sharey=True,
    )
    axes_array = np.atleast_1d(axes).flatten()
    for ax, (label, values) in zip(axes_array, stacked_panels):
        if values.empty:
            ax.set_visible(False)
            continue
        title = label if label == "Gesamtmarkt" else f"{label} (n={len(values)})"
        plot_distribution(ax, values, title, x_limits=x_limits)
    for ax in axes_array[len(stacked_panels):]:
        ax.set_visible(False)
    handles, labels = axes_array[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False)
    fig.suptitle(
        f"{args.fuel.capitalize()}preise {snapshot_meta['title_long']}: Gesamtmarkt und Top {args.top_n_brands} Marken + Misc",
        y=0.995,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.99))
    fig.savefig(args.output_dir / f"{snapshot_meta['output_prefix']}_all_charts_tall.png", bbox_inches="tight")
    plt.close(fig)

    n_panels = len(focus_operators)
    fig, axes = plt.subplots(
        n_panels,
        1,
        figsize=(12, max(3.6 * n_panels, 8)),
        dpi=180,
        sharex=True,
        sharey=True,
    )
    axes_array = np.atleast_1d(axes).flatten()
    for ax, operator in zip(axes_array, focus_operators):
        values = joined.loc[joined["operator_group"] == operator, args.fuel].dropna()
        if values.empty:
            ax.set_visible(False)
            continue
        plot_distribution(ax, values, f"{operator} (n={len(values)})", x_limits=x_limits)
    handles, labels = axes_array[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False)
    fig.suptitle(
        f"{args.fuel.capitalize()}preise {snapshot_meta['title_long']}: Top {args.top_n_brands} Marken + Misc",
        y=0.995,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.99))
    fig.savefig(args.output_dir / f"{snapshot_meta['output_prefix']}_operator_fit.png", bbox_inches="tight")
    plt.close(fig)

    write_report(
        args.output_dir / f"{snapshot_meta['output_prefix']}_distribution_report.md",
        snapshot_meta=snapshot_meta,
        fuel=args.fuel,
        overall_summary=overall,
        operator_summary=operator_summary,
        misc_members=misc_members,
        focus_operators=focus_operators,
    )

    print(args.output_dir / f"{snapshot_meta['output_prefix']}_distribution_fit.png")
    print(args.output_dir / f"{snapshot_meta['output_prefix']}_all_charts_tall.png")
    print(args.output_dir / f"{snapshot_meta['output_prefix']}_operator_fit.png")
    print(args.output_dir / f"{snapshot_meta['output_prefix']}_operator_summary.csv")
    print(args.output_dir / f"{snapshot_meta['output_prefix']}_distribution_report.md")


if __name__ == "__main__":
    main()
