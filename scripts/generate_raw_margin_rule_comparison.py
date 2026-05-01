#!/usr/bin/env python3
"""Generate a raw-source Diesel margin comparison around the 2026 noon rule."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FuncFormatter

try:
    from .generate_data import (
        ECB_FX_90D_XML_URL,
        FRED_BRENT_CSV_URL,
        LITERS_PER_BARREL,
        TANKER_BASE,
        TZ,
        _data_path,
        _parse_dates_utc,
        _read_csv_from_url,
        _read_text_from_url,
    )
except ImportError:  # pragma: no cover
    from generate_data import (
        ECB_FX_90D_XML_URL,
        FRED_BRENT_CSV_URL,
        LITERS_PER_BARREL,
        TANKER_BASE,
        TZ,
        _data_path,
        _parse_dates_utc,
        _read_csv_from_url,
        _read_text_from_url,
    )


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "raw_margin_rule_comparison"

VAT_RATE = 0.19
DIESEL_ENERGY_TAX_EUR_PER_LITER = 0.4704
CO2_PRICE_EUR_PER_TON = 65
DIESEL_CO2_KG_PER_LITER = 2.627
EBV_EUR_PER_TON = 3.56
DIESEL_DENSITY_TON_PER_M3 = 0.845


@dataclass(frozen=True)
class DailyBrent:
    value_eur_per_liter: float
    brent_as_of: date
    fx_as_of: date


@dataclass(frozen=True)
class DayResult:
    day: date
    brent_eur_per_liter: float
    brent_as_of: date
    fx_as_of: date
    march_hourly_max_gross: float | None
    march_hourly_min_gross: float | None
    april_noon_anchor_gross: float | None
    april_11h_mean_gross: float | None
    line_high_gross: float | None
    line_low_gross: float | None
    line_high_margin: float | None
    line_low_margin: float | None
    stations_at_noon: int
    station_hours: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmup-start", type=date.fromisoformat, default=date(2026, 2, 22))
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 3, 1))
    parser.add_argument("--cutover-date", type=date.fromisoformat, default=date(2026, 4, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 4, 30))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def require_tankerkoenig_credentials() -> None:
    if os.environ.get("TK_USER") and os.environ.get("TK_PASS"):
        return
    raise SystemExit("TK_USER and TK_PASS are required for raw Tankerkönig Azure data.")


def days_between(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def local_datetime(day: date, hour: int = 0, minute: int = 0) -> datetime:
    return TZ.localize(datetime.combine(day, time(hour=hour, minute=minute)))


def local_hour_buckets(day: date) -> list[tuple[datetime, datetime, int]]:
    start = local_datetime(day, 0, 0)
    end = local_datetime(day + timedelta(days=1), 0, 0)
    boundaries = pd.date_range(start=start, end=end, freq="1h", tz=TZ).to_pydatetime()
    return [
        (boundaries[index], boundaries[index + 1], boundaries[index].hour)
        for index in range(len(boundaries) - 1)
    ]


def load_diesel_events(day: date) -> pd.DataFrame:
    url = f"{TANKER_BASE}/{_data_path('prices', day)}"
    frame = _read_csv_from_url(url, label=f"prices {day:%Y-%m-%d}", show=True)
    expected = {"station_uuid", "date", "diesel"}
    missing = expected - set(frame.columns)
    if missing:
        raise ValueError(f"{url} misses required columns: {', '.join(sorted(missing))}")

    events = frame[["station_uuid", "date", "diesel"]].copy()
    events["station_uuid"] = events["station_uuid"].astype(str)
    events["date"] = _parse_dates_utc(events["date"])
    events["diesel"] = pd.to_numeric(events["diesel"], errors="coerce")
    events = events.dropna(subset=["station_uuid", "date", "diesel"])
    events = events.loc[events["diesel"].gt(0)].copy()
    if events.empty:
        return pd.DataFrame(columns=["station_uuid", "local_ts", "diesel"])

    events["local_ts"] = events["date"].dt.tz_convert(TZ)
    events = events.loc[events["local_ts"].dt.date.eq(day)].copy()
    if events.empty:
        return pd.DataFrame(columns=["station_uuid", "local_ts", "diesel"])

    events = (
        events.sort_values(["local_ts", "station_uuid"])
        .drop_duplicates(subset=["station_uuid", "local_ts"], keep="last")
        .reset_index(drop=True)
    )
    return events[["station_uuid", "local_ts", "diesel"]]


def add_interval(
    start: datetime,
    end: datetime,
    total_price: float,
    active_count: int,
    sum_price_seconds: list[float],
    count_seconds: list[float],
    buckets: list[tuple[datetime, datetime, int]],
) -> None:
    if end <= start or active_count <= 0:
        return

    for bucket_start, bucket_end, hour in buckets:
        if bucket_end <= start:
            continue
        if bucket_start >= end:
            break
        overlap_start = max(start, bucket_start)
        overlap_end = min(end, bucket_end)
        seconds = (overlap_end - overlap_start).total_seconds()
        if seconds <= 0:
            continue
        sum_price_seconds[hour] += total_price * seconds
        count_seconds[hour] += active_count * seconds


def mean_or_none(values: Iterable[float]) -> float | None:
    usable = [value for value in values if value is not None and math.isfinite(value)]
    if not usable:
        return None
    return float(sum(usable) / len(usable))


def diesel_margin(gross_price: float | None, brent_eur_per_liter: float) -> float | None:
    if gross_price is None or not math.isfinite(gross_price):
        return None
    vat = gross_price - gross_price / (1 + VAT_RATE)
    co2 = (DIESEL_CO2_KG_PER_LITER * CO2_PRICE_EUR_PER_TON) / 1000
    ebv = (EBV_EUR_PER_TON * DIESEL_DENSITY_TON_PER_M3) / 1000
    regulated = DIESEL_ENERGY_TAX_EUR_PER_LITER + co2 + ebv + vat
    return gross_price - regulated - brent_eur_per_liter


def latest_on_or_before(values_by_day: dict[date, float], target_day: date) -> tuple[date, float]:
    candidates = [day for day in values_by_day if day <= target_day]
    if not candidates:
        raise ValueError(f"No value available on or before {target_day}")
    selected_day = max(candidates)
    return selected_day, values_by_day[selected_day]


def load_daily_brent_values(start_day: date, end_day: date) -> dict[date, DailyBrent]:
    brent_text = _read_text_from_url(
        FRED_BRENT_CSV_URL,
        label="Brent crude history (FRED/EIA)",
        show=True,
    ).lstrip("\ufeff")
    brent_frame = pd.read_csv(io.StringIO(brent_text))
    if brent_frame.shape[1] < 2:
        raise ValueError("Unexpected Brent CSV format.")
    date_col, value_col = brent_frame.columns[:2]
    brent_frame[date_col] = pd.to_datetime(brent_frame[date_col], errors="coerce").dt.date
    brent_frame[value_col] = pd.to_numeric(brent_frame[value_col], errors="coerce")
    brent_by_day = {
        row[date_col]: float(row[value_col])
        for _, row in brent_frame.dropna(subset=[date_col, value_col]).iterrows()
    }
    if not brent_by_day:
        raise ValueError("Brent CSV contained no usable rows.")

    fx_xml = _read_text_from_url(
        ECB_FX_90D_XML_URL,
        label="ECB FX history",
        show=True,
    ).lstrip()
    root = ET.fromstring(fx_xml)
    ns = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
    fx_by_day: dict[date, float] = {}
    for day_cube in root.findall(".//ecb:Cube[@time]", ns):
        day_text = day_cube.attrib.get("time")
        if not day_text:
            continue
        usd_cube = next(
            (
                cube
                for cube in day_cube.findall("ecb:Cube", ns)
                if cube.attrib.get("currency") == "USD"
            ),
            None,
        )
        if usd_cube is None or not usd_cube.attrib.get("rate"):
            continue
        fx_by_day[date.fromisoformat(day_text)] = float(usd_cube.attrib["rate"])
    if not fx_by_day:
        raise ValueError("ECB XML contained no USD exchange rates.")

    daily: dict[date, DailyBrent] = {}
    for target_day in days_between(start_day, end_day):
        brent_day, brent_usd_per_barrel = latest_on_or_before(brent_by_day, target_day)
        fx_day, usd_per_eur = latest_on_or_before(fx_by_day, target_day)
        daily[target_day] = DailyBrent(
            value_eur_per_liter=(brent_usd_per_barrel / usd_per_eur) / LITERS_PER_BARREL,
            brent_as_of=brent_day,
            fx_as_of=fx_day,
        )
    return daily


def process_day(
    day: date,
    events: pd.DataFrame,
    state_prices: dict[str, float],
    brent: DailyBrent | None,
    start_date: date,
    cutover_date: date,
) -> DayResult | None:
    day_start = local_datetime(day, 0, 0)
    day_end = local_datetime(day + timedelta(days=1), 0, 0)
    buckets = local_hour_buckets(day)
    noon = local_datetime(day, 12, 0)
    noon_cutoff = noon + timedelta(minutes=15)

    total_price = float(sum(state_prices.values()))
    active_count = len(state_prices)
    current_time = day_start
    sum_price_seconds = [0.0] * 24
    count_seconds = [0.0] * 24
    first_noon_increase: dict[str, float] = {}
    noon_fallback_prices: dict[str, float] | None = None

    def capture_noon_if_needed() -> None:
        nonlocal noon_fallback_prices
        if noon_fallback_prices is None:
            noon_fallback_prices = dict(state_prices)

    for timestamp, group in events.groupby("local_ts", sort=True):
        ts = timestamp.to_pydatetime()
        if ts < day_start:
            continue
        if ts > day_end:
            break

        if noon_fallback_prices is None and ts > noon:
            capture_noon_if_needed()

        add_interval(
            current_time,
            min(ts, day_end),
            total_price,
            active_count,
            sum_price_seconds,
            count_seconds,
            buckets,
        )
        current_time = min(ts, day_end)

        if ts >= day_end:
            break

        for row in group.itertuples(index=False):
            station_id = str(row.station_uuid)
            new_price = float(row.diesel)
            old_price = state_prices.get(station_id)

            if noon <= ts <= noon_cutoff and old_price is not None:
                if new_price > old_price + 1e-9 and station_id not in first_noon_increase:
                    first_noon_increase[station_id] = new_price

            if old_price is None:
                active_count += 1
                total_price += new_price
            else:
                total_price += new_price - old_price
            state_prices[station_id] = new_price

        if noon_fallback_prices is None and ts == noon:
            capture_noon_if_needed()

    if noon_fallback_prices is None:
        capture_noon_if_needed()

    add_interval(
        current_time,
        day_end,
        total_price,
        active_count,
        sum_price_seconds,
        count_seconds,
        buckets,
    )

    if day < start_date:
        return None
    if brent is None:
        raise ValueError(f"Missing Brent value for {day}")

    hourly_means = [
        (sum_price_seconds[hour] / count_seconds[hour] if count_seconds[hour] > 0 else None)
        for hour in range(24)
    ]
    hourly_values = [value for value in hourly_means if value is not None and math.isfinite(value)]
    station_hours = sum(count_seconds) / 3600

    march_hourly_max = max(hourly_values) if day < cutover_date and hourly_values else None
    march_hourly_min = min(hourly_values) if day < cutover_date and hourly_values else None

    april_noon_anchor = None
    april_11h_mean = None
    if day >= cutover_date:
        noon_prices = dict(noon_fallback_prices or {})
        noon_prices.update(first_noon_increase)
        april_noon_anchor = mean_or_none(noon_prices.values())
        april_11h_mean = hourly_means[11]

    line_high_gross = march_hourly_max if day < cutover_date else april_noon_anchor
    line_low_gross = march_hourly_min if day < cutover_date else april_11h_mean

    return DayResult(
        day=day,
        brent_eur_per_liter=brent.value_eur_per_liter,
        brent_as_of=brent.brent_as_of,
        fx_as_of=brent.fx_as_of,
        march_hourly_max_gross=march_hourly_max,
        march_hourly_min_gross=march_hourly_min,
        april_noon_anchor_gross=april_noon_anchor,
        april_11h_mean_gross=april_11h_mean,
        line_high_gross=line_high_gross,
        line_low_gross=line_low_gross,
        line_high_margin=diesel_margin(line_high_gross, brent.value_eur_per_liter),
        line_low_margin=diesel_margin(line_low_gross, brent.value_eur_per_liter),
        stations_at_noon=len(noon_fallback_prices or {}),
        station_hours=station_hours,
    )


def write_csv(rows: list[DayResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "datum",
        "brent_eur_l",
        "brent_as_of",
        "fx_as_of",
        "maerz_tagesmaximum_stundenmittel_bruttopreis_eur_l",
        "maerz_tagesminimum_stundenmittel_bruttopreis_eur_l",
        "april_12uhr_anker_bruttopreis_eur_l",
        "april_11uhr_stundenmittel_bruttopreis_eur_l",
        "linie_oben_bruttopreis_eur_l",
        "linie_unten_bruttopreis_eur_l",
        "linie_oben_betriebskosten_marge_eur_l",
        "linie_unten_betriebskosten_marge_eur_l",
        "stationen_um_12uhr",
        "stationsstunden",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "datum": row.day.isoformat(),
                    "brent_eur_l": row.brent_eur_per_liter,
                    "brent_as_of": row.brent_as_of.isoformat(),
                    "fx_as_of": row.fx_as_of.isoformat(),
                    "maerz_tagesmaximum_stundenmittel_bruttopreis_eur_l": row.march_hourly_max_gross,
                    "maerz_tagesminimum_stundenmittel_bruttopreis_eur_l": row.march_hourly_min_gross,
                    "april_12uhr_anker_bruttopreis_eur_l": row.april_noon_anchor_gross,
                    "april_11uhr_stundenmittel_bruttopreis_eur_l": row.april_11h_mean_gross,
                    "linie_oben_bruttopreis_eur_l": row.line_high_gross,
                    "linie_unten_bruttopreis_eur_l": row.line_low_gross,
                    "linie_oben_betriebskosten_marge_eur_l": row.line_high_margin,
                    "linie_unten_betriebskosten_marge_eur_l": row.line_low_margin,
                    "stationen_um_12uhr": row.stations_at_noon,
                    "stationsstunden": row.station_hours,
                }
            )


def render_chart(rows: list[DayResult], output_path: Path) -> None:
    x_values = [row.day for row in rows]
    high_values = [row.line_high_margin for row in rows]
    low_values = [row.line_low_margin for row in rows]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.titleweight": "bold",
            "axes.labelsize": 12,
            "axes.titlesize": 17,
            "xtick.labelsize": 9.5,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
        }
    )
    fig, ax = plt.subplots(figsize=(15, 8.3), dpi=150)
    fig.patch.set_facecolor("#f5f7fa")
    ax.set_facecolor("white")

    ax.plot(
        x_values,
        high_values,
        color="#0f766e",
        marker="o",
        markersize=3.8,
        linewidth=2.3,
        label="März: Tagesmaximum der Stundenmittel / April: 12:00-Anker",
    )
    ax.plot(
        x_values,
        low_values,
        color="#2563eb",
        marker="s",
        markersize=3.6,
        linewidth=2.3,
        label="März: Tagesminimum der Stundenmittel / April: 11:00-Mittel",
    )

    cutover = date(2026, 4, 1)
    ax.axvline(cutover, color="#c2410c", linewidth=2, linestyle="--")
    ax.text(cutover + timedelta(days=0.4), ax.get_ylim()[1], "01.04. Regelwechsel", color="#9a3412", va="top")

    ax.set_title("Diesel: Betriebskosten und Marge vor/nach 12:00-Regel")
    ax.set_ylabel("Betriebskosten und Marge (€/l)")
    ax.set_xlabel("Datum")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:.2f}".replace(".", ",")))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m."))
    ax.set_xlim(min(x_values) - timedelta(days=1), max(x_values) + timedelta(days=1))
    ax.grid(axis="y", color="#cfd6df", linewidth=0.8)
    ax.grid(axis="x", color="#e7ebf0", linewidth=0.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#9aa9bd")
    ax.spines["bottom"].set_color("#9aa9bd")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#cbd5e1", framealpha=0.95)

    fig.text(
        0.06,
        0.052,
        "Quelle: rohe Tankerkönig-Azure-Preisdaten; Berechnung: Bruttopreis minus MwSt., Energiesteuer, CO₂-Abgabe, EBV und täglichem Brent-Rohölpreis.",
        fontsize=9.2,
        color="#475569",
    )
    fig.text(
        0.06,
        0.027,
        "Brent und USD/EUR werden je Kalendertag aus der letzten verfügbaren Beobachtung fortgeschrieben.",
        fontsize=9.2,
        color="#475569",
    )
    fig.tight_layout(rect=(0.035, 0.09, 0.98, 0.94))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def write_summary(rows: list[DayResult], output_path: Path) -> None:
    high_values = [row.line_high_margin for row in rows if row.line_high_margin is not None]
    low_values = [row.line_low_margin for row in rows if row.line_low_margin is not None]
    brent_values = [row.brent_eur_per_liter for row in rows]
    output_path.write_text(
        "\n".join(
            [
                "# Diesel Betriebskosten und Marge: Rohdaten-Vergleich",
                "",
                f"- Zeitraum: {rows[0].day.isoformat()} bis {rows[-1].day.isoformat()}",
                "- Linie 1: März Tagesmaximum der Stundenmittel, ab April 12:00-Anker",
                "- Linie 2: März Tagesminimum der Stundenmittel, ab April 11:00-Stundenmittel",
                "- Brent: täglicher FRED/EIA-Wert in EUR/l, mit letzter verfügbarer Beobachtung fortgeschrieben",
                f"- Brent-Spanne: {min(brent_values):.6f} bis {max(brent_values):.6f} €/l",
                f"- Linie 1 Spanne: {min(high_values):.3f} bis {max(high_values):.3f} €/l",
                f"- Linie 2 Spanne: {min(low_values):.3f} bis {max(low_values):.3f} €/l",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    require_tankerkoenig_credentials()
    daily_brent = load_daily_brent_values(args.start_date, args.end_date)

    if args.warmup_start > args.start_date:
        raise SystemExit("--warmup-start must be on or before --start-date.")
    if not (args.start_date < args.cutover_date <= args.end_date):
        raise SystemExit("Expected start_date < cutover_date <= end_date.")

    state_prices: dict[str, float] = {}
    results: list[DayResult] = []
    for day in days_between(args.warmup_start, args.end_date):
        events = load_diesel_events(day)
        result = process_day(
            day,
            events,
            state_prices,
            daily_brent.get(day),
            args.start_date,
            args.cutover_date,
        )
        if result is not None:
            results.append(result)

    if not results:
        raise SystemExit("No result rows were generated.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "diesel_betriebskosten_marge_rohdatenvergleich.csv"
    png_path = args.output_dir / "diesel_betriebskosten_marge_rohdatenvergleich.png"
    summary_path = args.output_dir / "diesel_betriebskosten_marge_rohdatenvergleich.md"
    write_csv(results, csv_path)
    render_chart(results, png_path)
    write_summary(results, summary_path)
    print(f"Wrote {csv_path}")
    print(f"Wrote {png_path}")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
