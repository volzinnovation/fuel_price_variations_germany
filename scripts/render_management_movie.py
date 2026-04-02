#!/usr/bin/env python3
"""Render management chart movies from dated management_boxplots.json files."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter, MaxNLocator


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data2"
DEFAULT_START = date(2026, 2, 4)
DEFAULT_END = date.today() - timedelta(days=1)
DEFAULT_FUEL = "diesel"
DEFAULT_DATASET = "hourly-delta"
DEFAULT_STATIC_FPS = 6
HOURLY_VALUE_SCALE = 100.0
FOOTER_FONT_SIZE = 11
BRAND_X_MIN = 1.5
BRAND_X_MAX = 3.0

FUEL_LABELS = {
    "diesel": "Diesel",
    "e10": "E10",
    "e5": "E5",
}


@dataclass(frozen=True)
class DayPayload:
    day: date
    path: Path
    payload: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end-date", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--fuel", choices=tuple(FUEL_LABELS), default=DEFAULT_FUEL)
    parser.add_argument(
        "--dataset",
        choices=("hourly-delta", "brand-noon"),
        default=DEFAULT_DATASET,
        help="Render hourly markdown boxplots or brand-based noon price boxplots.",
    )
    parser.add_argument("--fps", type=int, default=DEFAULT_STATIC_FPS)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=ROOT / "tmp" / "management_movie_fixedscale",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output MP4 path. Defaults to output/management_movies/.",
    )
    args = parser.parse_args()
    if args.end_date < args.start_date:
        parser.error("--end-date must be on or after --start-date.")
    return args


def iter_days(start: date, end: date) -> list[date]:
    current = start
    days: list[date] = []
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def day_path(data_root: Path, day: date) -> Path:
    return data_root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / "management_boxplots.json"


def load_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def collect_payloads(data_root: Path, start: date, end: date) -> tuple[list[DayPayload], list[str]]:
    payloads: list[DayPayload] = []
    missing_dates: list[str] = []
    for day in iter_days(start, end):
        path = day_path(data_root, day)
        payload = load_payload(path)
        if payload is None:
            missing_dates.append(day.isoformat())
            continue
        payloads.append(DayPayload(day=day, path=path, payload=payload))
    if not payloads:
        raise SystemExit("No management_boxplots.json files found in the selected range.")
    return payloads, missing_dates


def fuel_rows(payload: dict[str, Any], fuel: str) -> list[dict[str, Any]]:
    rows = payload.get("fuels", {}).get(fuel) or []
    return sorted(rows, key=lambda row: int(row.get("hour", row.get("cycle_hour", 0))))


def brand_rows(payload: dict[str, Any], fuel: str) -> list[dict[str, Any]]:
    rows = payload.get("brand_distributions", {}).get(fuel) or []
    return [row for row in rows if int(row.get("count", 0)) > 0]


def hourly_range(payloads: list[DayPayload], fuel: str) -> tuple[float, float]:
    mins: list[float] = []
    maxs: list[float] = []
    for item in payloads:
        for row in fuel_rows(item.payload, fuel):
            if int(row.get("count", 0)) <= 0:
                continue
            mins.append(float(row["min"]) * HOURLY_VALUE_SCALE)
            maxs.append(float(row["max"]) * HOURLY_VALUE_SCALE)
    if not mins or not maxs:
        raise SystemExit(f"No usable {fuel} hourly-delta data found in the selected range.")
    y_min = min(mins)
    y_max = max(maxs)
    if y_min == y_max:
        y_min -= 1.0
        y_max += 1.0
    return y_min, y_max


def brand_range(payloads: list[DayPayload], fuel: str) -> tuple[float, float]:
    if not any(brand_rows(item.payload, fuel) for item in payloads):
        raise SystemExit(f"No usable {fuel} brand-noon data found in the selected range.")
    return BRAND_X_MIN, BRAND_X_MAX


def brand_order(payloads: list[DayPayload], fuel: str) -> list[str]:
    totals: dict[str, int] = {}
    for item in payloads:
        for row in brand_rows(item.payload, fuel):
            brand = str(row.get("brand") or "").strip() or "UNBEKANNT"
            totals[brand] = totals.get(brand, 0) + int(row.get("count", 0))

    def sort_key(brand: str) -> tuple[int, int, str]:
        if brand == "Gesamtmarkt":
            return (0, 0, brand)
        if brand == "MISC":
            return (2, 0, brand)
        return (1, -totals.get(brand, 0), brand)

    return [brand for brand in sorted(totals, key=sort_key) if brand != "UNBEKANNT"]


def display_brand_name(brand: str) -> str:
    if brand == "MISC":
        return "Andere/Freie"
    return brand


def add_footer(fig: plt.Figure) -> None:
    fig.text(
        0.08,
        0.045,
        "@ProfVolz",
        fontsize=FOOTER_FONT_SIZE,
        fontweight="bold",
        color="#111827",
        ha="left",
        va="bottom",
    )
    fig.text(
        0.985,
        0.045,
        "tankzeit.de",
        fontsize=FOOTER_FONT_SIZE,
        fontweight="bold",
        color="#111827",
        ha="right",
        va="bottom",
    )


def style_hourly_boxplot(boxplot: dict[str, Any]) -> None:
    edge_color = "#0f766e"
    fill_color = "#cde8e4"
    for box in boxplot["boxes"]:
        box.set(facecolor=fill_color, edgecolor=edge_color, linewidth=1.6)
    for whisker in boxplot["whiskers"]:
        whisker.set(color=edge_color, linewidth=1.6)
    for cap in boxplot["caps"]:
        cap.set(color=edge_color, linewidth=1.6)
    for median in boxplot["medians"]:
        median.set(color=edge_color, linewidth=2.0)


def hourly_boxplot_stat(row: dict[str, Any]) -> dict[str, Any]:
    label = str(row.get("label") or f"{int(row.get('hour', 0)):02d}")
    return {
        "label": label,
        "med": float(row["median"]) * HOURLY_VALUE_SCALE,
        "q1": float(row["q1"]) * HOURLY_VALUE_SCALE,
        "q3": float(row["q3"]) * HOURLY_VALUE_SCALE,
        "whislo": float(row["min"]) * HOURLY_VALUE_SCALE,
        "whishi": float(row["max"]) * HOURLY_VALUE_SCALE,
        "fliers": [],
    }


def render_hourly_frame(item: DayPayload, fuel: str, y_min: float, y_max: float, out_path: Path) -> None:
    rows = fuel_rows(item.payload, fuel)
    stats = [hourly_boxplot_stat(row) for row in rows]
    labels = [stat["label"] for stat in stats]
    positions = list(range(len(stats)))

    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor("#f7f4ee")
    ax.set_facecolor("white")

    boxplot = ax.bxp(
        stats,
        positions=positions,
        widths=0.62,
        showfliers=False,
        patch_artist=True,
        manage_ticks=False,
    )
    style_hourly_boxplot(boxplot)

    ax.set_xlim(-0.75, max(len(stats) - 0.25, 23.75))
    ax.set_ylim(y_min, y_max)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Stunde", fontsize=13, color="#374151", labelpad=10)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.1f}"))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.grid(axis="y", color="#e7e0d6", linewidth=1.0)
    ax.axhline(0.0, color="#0f766e", linewidth=1.5, alpha=0.35)
    ax.tick_params(axis="x", labelsize=11, colors="#6b7280")
    ax.tick_params(axis="y", labelsize=11, colors="#6b7280")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d8d2c8")
    ax.spines["bottom"].set_color("#d8d2c8")

    fig.suptitle(
        f"{FUEL_LABELS[fuel]} - Untertägige Preisabweichungen (ct/Liter)",
        fontsize=24,
        fontweight="bold",
        color="#111827",
        y=0.95,
    )
    ax.set_title(
        item.day.strftime("%d.%m.%Y"),
        fontsize=16,
        color="#6b7280",
        pad=18,
    )

    add_footer(fig)
    fig.subplots_adjust(left=0.08, right=0.985, top=0.82, bottom=0.18)
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def box_colors(brand: str) -> tuple[str, str]:
    if brand == "Gesamtmarkt":
        return "#b8ddd6", "#0f766e"
    if brand == "MISC":
        return "#ece7dd", "#6b7280"
    return "#cde8e4", "#0f766e"


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def draw_brand_box(ax: plt.Axes, row: dict[str, Any], y_pos: float, x_min: float, x_max: float) -> None:
    min_value = float(row["min"])
    q1 = float(row["q1"])
    median = float(row["median"])
    q3 = float(row["q3"])
    max_value = float(row["max"])
    avg = float(row.get("avg", median))
    brand = str(row.get("brand") or "")
    face_color, edge_color = box_colors(brand)
    box_height = 0.62
    cap_half_height = box_height * 0.18

    visible_min = clamp(min_value, x_min, x_max)
    visible_q1 = clamp(q1, x_min, x_max)
    visible_median = clamp(median, x_min, x_max)
    visible_q3 = clamp(q3, x_min, x_max)
    visible_max = clamp(max_value, x_min, x_max)
    visible_avg = clamp(avg, x_min, x_max)

    ax.hlines(y_pos, visible_min, visible_max, color=edge_color, linewidth=1.5, zorder=2)
    if x_min <= min_value <= x_max:
        ax.vlines(min_value, y_pos - cap_half_height, y_pos + cap_half_height, color=edge_color, linewidth=1.5, zorder=2)
    if x_min <= max_value <= x_max:
        ax.vlines(max_value, y_pos - cap_half_height, y_pos + cap_half_height, color=edge_color, linewidth=1.5, zorder=2)

    if visible_q3 > visible_q1:
        rect = Rectangle(
            (visible_q1, y_pos - (box_height / 2)),
            visible_q3 - visible_q1,
            box_height,
            facecolor=face_color,
            edgecolor=edge_color,
            linewidth=1.6,
            zorder=3,
        )
        ax.add_patch(rect)
    if x_min <= median <= x_max:
        ax.vlines(visible_median, y_pos - (box_height / 2), y_pos + (box_height / 2), color=edge_color, linewidth=2.0, zorder=4)
    if x_min <= avg <= x_max:
        ax.scatter([visible_avg], [y_pos], color=edge_color, s=18, alpha=0.85, zorder=5)


def snapshot_datetime(item: DayPayload) -> datetime | None:
    raw = item.payload.get("brand_snapshot_timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def render_brand_frame(
    item: DayPayload,
    fuel: str,
    order: list[str],
    x_min: float,
    x_max: float,
    out_path: Path,
) -> None:
    row_map = {
        str(row.get("brand") or "").strip() or "UNBEKANNT": row
        for row in brand_rows(item.payload, fuel)
    }
    positions = list(range(len(order)))

    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor("#f7f4ee")
    ax.set_facecolor("white")

    for y_pos, brand in zip(positions, order):
        row = row_map.get(brand)
        if row is None:
            continue
        draw_brand_box(ax, row, y_pos, x_min, x_max)

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-0.75, len(order) - 0.25)
    ax.set_yticks(positions)
    ax.set_yticklabels([display_brand_name(brand) for brand in order])
    ax.invert_yaxis()
    ax.set_xlabel("Preis (€/Liter)", fontsize=13, color="#374151", labelpad=10)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.2f}"))
    ax.xaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.grid(axis="x", color="#e7e0d6", linewidth=1.0)
    ax.tick_params(axis="x", labelsize=11, colors="#6b7280")
    ax.tick_params(axis="y", labelsize=11, colors="#374151")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d8d2c8")
    ax.spines["bottom"].set_color("#d8d2c8")

    fig.suptitle(
        f"Tanz der Spritpreise - {FUEL_LABELS[fuel]}",
        fontsize=24,
        fontweight="bold",
        color="#111827",
        y=0.95,
    )
    snapshot = snapshot_datetime(item)
    subtitle = snapshot.strftime("%d.%m.%Y %H:%M") if snapshot else item.day.strftime("%d.%m.%Y")
    ax.set_title(subtitle, fontsize=16, color="#6b7280", pad=18)

    add_footer(fig)
    fig.subplots_adjust(left=0.22, right=0.985, top=0.82, bottom=0.18)
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def encode_video(frames_dir: Path, fps: int, output_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(frames_dir / "frame-%04d.png"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def default_output_path(dataset: str, fuel: str, start: date, end: date) -> Path:
    out_dir = ROOT / "output" / "management_movies"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{dataset}_{fuel}_{start.isoformat()}_{end.isoformat()}.mp4"


def write_summary(
    summary_path: Path,
    requested_start: date,
    requested_end: date,
    payloads: list[DayPayload],
    missing_dates: list[str],
    dataset: str,
    fuel: str,
    fps: int,
    frame_count: int,
    axis_min: float,
    axis_max: float,
    output_path: Path,
    brand_order_rows: list[str] | None = None,
) -> None:
    summary = {
        "dataset": dataset,
        "fuel": fuel,
        "title": (
            f"Tanz der Spritpreise - {FUEL_LABELS[fuel]}"
            if dataset == "brand-noon"
            else f"{FUEL_LABELS[fuel]} - Untertägige Preisabweichungen (ct/Liter)"
        ),
        "fps": fps,
        "requested_start": requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "available_start": payloads[0].day.isoformat(),
        "available_end": payloads[-1].day.isoformat(),
        "frame_count": frame_count,
        "missing_dates": missing_dates,
        "video_path": str(output_path),
    }
    if dataset == "brand-noon":
        summary["global_min_eur_per_l"] = axis_min
        summary["global_max_eur_per_l"] = axis_max
        summary["brand_order"] = [display_brand_name(brand) for brand in (brand_order_rows or [])]
    else:
        summary["global_min_cent_per_l"] = axis_min
        summary["global_max_cent_per_l"] = axis_max
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    payloads, missing_dates = collect_payloads(args.data_root, args.start_date, args.end_date)
    output_path = args.output or default_output_path(args.dataset, args.fuel, args.start_date, args.end_date)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    work_dir = args.work_dir / f"{args.dataset}_{args.fuel}_{args.start_date}_{args.end_date}"
    frames_dir = work_dir / "frames"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    if args.dataset == "brand-noon":
        axis_min, axis_max = brand_range(payloads, args.fuel)
        order = brand_order(payloads, args.fuel)
        for index, item in enumerate(payloads):
            render_brand_frame(
                item=item,
                fuel=args.fuel,
                order=order,
                x_min=axis_min,
                x_max=axis_max,
                out_path=frames_dir / f"frame-{index:04d}.png",
            )
        brand_order_rows: list[str] | None = order
    else:
        axis_min, axis_max = hourly_range(payloads, args.fuel)
        for index, item in enumerate(payloads):
            render_hourly_frame(
                item=item,
                fuel=args.fuel,
                y_min=axis_min,
                y_max=axis_max,
                out_path=frames_dir / f"frame-{index:04d}.png",
            )
        brand_order_rows = None

    encode_video(frames_dir, args.fps, output_path)
    summary_path = output_path.with_suffix(".json")
    write_summary(
        summary_path=summary_path,
        requested_start=args.start_date,
        requested_end=args.end_date,
        payloads=payloads,
        missing_dates=missing_dates,
        dataset=args.dataset,
        fuel=args.fuel,
        fps=args.fps,
        frame_count=len(payloads),
        axis_min=axis_min,
        axis_max=axis_max,
        output_path=output_path,
        brand_order_rows=brand_order_rows,
    )
    print(output_path)
    print(summary_path)


if __name__ == "__main__":
    main()
