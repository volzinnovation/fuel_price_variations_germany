#!/usr/bin/env python3
"""Generate a Germany-wide Diesel daily-average profitability chart.

This is a one-off artifact generator. It reads raw MTS-K price events from the
Tankerkönig data repository and computes a station-time-weighted daily average
gross Diesel price across all active German fuel stations.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import os
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import certifi
import matplotlib

matplotlib.use("Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import pytz
import requests
from matplotlib.ticker import FuncFormatter, MultipleLocator
from tqdm import tqdm


ROOT = Path(__file__).resolve().parents[1]
TZ = pytz.timezone("Europe/Berlin")
TANKER_BASE = (
    "https://data.tankerkoenig.de/"
    "tankerkoenig-organization/tankerkoenig-data/raw/branch/master"
)
FRED_BRENT_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"
ECB_FX_FULL_XML_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
LITERS_PER_BARREL = 42 * 3.785411784

VAT_RATE = 0.19
DIESEL_ENERGY_TAX_EUR_PER_LITER = 0.4704
DIESEL_TEMP_ENERGY_TAX_EUR_PER_LITER = 0.33
TEMP_TAX_START = date(2026, 5, 1)
TEMP_TAX_END = date(2026, 6, 30)
CO2_PRICE_EUR_PER_TON = 65
DIESEL_CO2_KG_PER_LITER = 2.627
EBV_EUR_PER_TON = 3.56
DIESEL_DENSITY_TON_PER_M3 = 0.845

TOKENS = {
    "surface": "#FCFCFD",
    "panel": "#FFFFFF",
    "ink": "#1F2430",
    "muted": "#6F768A",
    "grid": "#E6E8F0",
    "axis": "#D7DBE7",
}
COLORS = {
    "station": "#5477C4",
    "tax": "#CC6F47",
    "tax_dark": "#804126",
    "brent": "#71B436",
    "tax_band": "#FFF4C2",
    "neutral_dark": "#464C55",
    "neutral_mid": "#7A828F",
}
FONT_FAMILY = ["Aptos", "Inter", "Segoe UI", "DejaVu Sans", "Arial", "sans-serif"]


@dataclass(frozen=True)
class DailyMarketAverage:
    day: date
    gross_price_eur_l: float | None
    active_station_equivalent: float
    event_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmup-start", type=date.fromisoformat, default=date(2026, 1, 25))
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 2, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 6, 30))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    return parser.parse_args()


def days_between(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def local_datetime(day: date, hour: int = 0, minute: int = 0) -> datetime:
    return TZ.localize(datetime.combine(day, time(hour=hour, minute=minute)))


def data_path(prefix: str, day: date) -> str:
    return f"{prefix}/{day:%Y}/{day:%m}/{day:%Y-%m-%d}-{prefix}.csv"


def require_credentials() -> tuple[str, str]:
    user = os.environ.get("TK_USER")
    password = os.environ.get("TK_PASS")
    if not user or not password:
        raise SystemExit("TK_USER and TK_PASS are required for raw Tankerkönig data.")
    return user, password


def read_csv_from_url(url: str, *, label: str, auth: tuple[str, str]) -> pd.DataFrame:
    response = requests.get(url, timeout=120, verify=certifi.where(), auth=auth)
    response.raise_for_status()
    text = response.text.lstrip()
    if text.startswith("<") or text.startswith("{"):
        raise ValueError(f"Unexpected response payload for {label}.")
    return pd.read_csv(io.StringIO(text))


def read_text_from_url(url: str, *, label: str) -> str:
    response = requests.get(url, timeout=120, verify=certifi.where())
    response.raise_for_status()
    text = response.text
    if not text.strip():
        raise ValueError(f"{label} returned an empty response.")
    return text


def load_diesel_events(day: date, auth: tuple[str, str]) -> pd.DataFrame:
    url = f"{TANKER_BASE}/{data_path('prices', day)}"
    frame = read_csv_from_url(url, label=f"prices {day:%Y-%m-%d}", auth=auth)
    expected = {"station_uuid", "date", "diesel"}
    missing = expected - set(frame.columns)
    if missing:
        raise ValueError(f"{url} misses required columns: {', '.join(sorted(missing))}")

    events = frame[["station_uuid", "date", "diesel"]].copy()
    events["station_uuid"] = events["station_uuid"].astype(str)
    events["date"] = pd.to_datetime(events["date"], errors="coerce", utc=True)
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


def integrate_interval(
    start: datetime,
    end: datetime,
    *,
    total_price: float,
    active_count: int,
    accumulator: dict[str, float],
) -> None:
    if end <= start or active_count <= 0:
        return
    seconds = (end - start).total_seconds()
    accumulator["price_seconds"] += total_price * seconds
    accumulator["station_seconds"] += active_count * seconds


def process_day(
    day: date,
    events: pd.DataFrame,
    state_prices: dict[str, float],
    *,
    emit_result: bool,
) -> DailyMarketAverage | None:
    day_start = local_datetime(day, 0, 0)
    day_end = local_datetime(day + timedelta(days=1), 0, 0)

    total_price = float(sum(state_prices.values()))
    active_count = len(state_prices)
    current_time = day_start
    accumulator = {"price_seconds": 0.0, "station_seconds": 0.0}

    for timestamp, group in events.groupby("local_ts", sort=True):
        ts = timestamp.to_pydatetime()
        if ts < day_start:
            continue
        if ts > day_end:
            break

        if emit_result:
            integrate_interval(
                current_time,
                min(ts, day_end),
                total_price=total_price,
                active_count=active_count,
                accumulator=accumulator,
            )
        current_time = min(ts, day_end)

        if ts >= day_end:
            break

        for row in group.itertuples(index=False):
            station_id = str(row.station_uuid)
            new_price = float(row.diesel)
            old_price = state_prices.get(station_id)
            if old_price is None:
                active_count += 1
                total_price += new_price
            else:
                total_price += new_price - old_price
            state_prices[station_id] = new_price

    if emit_result:
        integrate_interval(
            current_time,
            day_end,
            total_price=total_price,
            active_count=active_count,
            accumulator=accumulator,
        )
        station_seconds = accumulator["station_seconds"]
        return DailyMarketAverage(
            day=day,
            gross_price_eur_l=(
                accumulator["price_seconds"] / station_seconds if station_seconds > 0 else None
            ),
            active_station_equivalent=station_seconds / (24 * 60 * 60),
            event_count=int(len(events)),
        )

    return None


def latest_on_or_before(values_by_day: dict[date, float], target_day: date) -> tuple[date, float]:
    keys = sorted(values_by_day)
    idx = bisect_right(keys, target_day) - 1
    if idx < 0:
        raise ValueError(f"No value available on or before {target_day}")
    selected_day = keys[idx]
    return selected_day, values_by_day[selected_day]


def load_daily_brent_values(start_day: date, end_day: date) -> dict[date, dict[str, object]]:
    brent_text = read_text_from_url(FRED_BRENT_CSV_URL, label="FRED Brent CSV").lstrip("\ufeff")
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

    fx_xml = read_text_from_url(ECB_FX_FULL_XML_URL, label="ECB FX history").lstrip()
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
        if usd_cube is not None and usd_cube.attrib.get("rate"):
            fx_by_day[date.fromisoformat(day_text)] = float(usd_cube.attrib["rate"])
    if not fx_by_day:
        raise ValueError("ECB XML contained no USD exchange rates.")

    daily: dict[date, dict[str, object]] = {}
    for target_day in days_between(start_day, end_day):
        brent_day, brent_usd_per_barrel = latest_on_or_before(brent_by_day, target_day)
        fx_day, usd_per_eur = latest_on_or_before(fx_by_day, target_day)
        daily[target_day] = {
            "value": (brent_usd_per_barrel / usd_per_eur) / LITERS_PER_BARREL,
            "brent_as_of": brent_day,
            "fx_as_of": fx_day,
        }
    return daily


def energy_tax_for_day(day: date) -> float:
    if TEMP_TAX_START <= day <= TEMP_TAX_END:
        return DIESEL_TEMP_ENERGY_TAX_EUR_PER_LITER
    return DIESEL_ENERGY_TAX_EUR_PER_LITER


def tax_levies_for_gross(day: date, gross_price: float | None) -> float | None:
    if gross_price is None or not math.isfinite(gross_price):
        return None
    vat = gross_price - gross_price / (1 + VAT_RATE)
    co2 = (DIESEL_CO2_KG_PER_LITER * CO2_PRICE_EUR_PER_TON) / 1000
    ebv = (EBV_EUR_PER_TON * DIESEL_DENSITY_TON_PER_M3) / 1000
    return energy_tax_for_day(day) + co2 + ebv + vat


def build_chart_frame(market_rows: list[DailyMarketAverage], start_day: date, end_day: date) -> pd.DataFrame:
    brent_daily = load_daily_brent_values(start_day, end_day)
    market_by_day = {row.day: row for row in market_rows}
    rows: list[dict[str, object]] = []
    for day in days_between(start_day, end_day):
        market = market_by_day.get(day)
        gross = market.gross_price_eur_l if market else None
        taxes = tax_levies_for_gross(day, gross)
        brent = float(brent_daily[day]["value"])
        rows.append(
            {
                "datum": day.isoformat(),
                "day": pd.Timestamp(day),
                "tagesdurchschnitt_bruttopreis_eur_l": gross,
                "steuern_abgaben_eur_l": taxes,
                "brent_eur_l": brent,
                "betriebskosten_gewinn_eur_l": (
                    gross - taxes - brent if gross is not None and taxes is not None else None
                ),
                "aktive_tankstellen_aequivalent": (
                    market.active_station_equivalent if market else 0.0
                ),
                "preisereignisse": market.event_count if market else 0,
                "brent_as_of": brent_daily[day]["brent_as_of"],
                "fx_as_of": brent_daily[day]["fx_as_of"],
            }
        )
    return pd.DataFrame(rows)


def add_event_marker(ax, when: date, label: str, *, color: str, xoffset_days: float = 1.0, ypos: float = 0.98):
    ts = pd.Timestamp(when)
    ax.axvline(ts, color=color, linestyle="-", linewidth=1.0, alpha=0.72, zorder=2)
    if label:
        ax.text(
            ts + pd.Timedelta(days=xoffset_days),
            ypos,
            label,
            transform=ax.get_xaxis_transform(),
            rotation=90,
            ha="left",
            va="top",
            fontsize=8.4,
            color=color,
            linespacing=1.1,
            zorder=4,
        )


def euro_tick(value: float, _pos: object) -> str:
    return f"{value:.2f} €/l"


def render_chart(df: pd.DataFrame, output_png: Path, start_day: date, end_day: date) -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": TOKENS["surface"],
            "savefig.facecolor": TOKENS["surface"],
            "axes.facecolor": TOKENS["panel"],
            "axes.edgecolor": TOKENS["axis"],
            "axes.labelcolor": TOKENS["ink"],
            "xtick.color": TOKENS["muted"],
            "ytick.color": TOKENS["muted"],
            "grid.color": TOKENS["grid"],
            "font.family": "sans-serif",
            "font.sans-serif": FONT_FAMILY,
        }
    )

    fig, ax = plt.subplots(figsize=(16, 9), dpi=180)
    fig.subplots_adjust(left=0.075, right=0.965, bottom=0.145, top=0.775)

    ax.axvspan(
        pd.Timestamp(TEMP_TAX_START),
        pd.Timestamp(TEMP_TAX_END),
        color=COLORS["tax_band"],
        alpha=0.45,
        zorder=0,
    )
    ax.text(
        0.50,
        0.53,
        "tankzeit.de",
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=98,
        fontweight="bold",
        color=COLORS["neutral_mid"],
        alpha=0.13,
        zorder=1,
    )

    line_station, = ax.plot(
        df["day"],
        df["betriebskosten_gewinn_eur_l"],
        color=COLORS["station"],
        linewidth=2.5,
        linestyle="-",
        label="Tagesdurchschnittspreis Deutschland: Betriebskosten und Gewinn",
        zorder=3.6,
    )
    line_tax, = ax.plot(
        df["day"],
        df["steuern_abgaben_eur_l"],
        color=COLORS["tax"],
        linewidth=2.45,
        linestyle="-",
        label="Tagesdurchschnittspreis Deutschland: Steuern und Abgaben inkl. CO2 + MwSt. 19%",
        zorder=3.4,
    )
    line_brent, = ax.plot(
        df["day"],
        df["brent_eur_l"],
        color=COLORS["brent"],
        linewidth=2.45,
        linestyle="-",
        label="Brent Crude Oil Kurs",
        zorder=3.2,
    )

    add_event_marker(ax, date(2026, 2, 28), "Beginn\nIran-Krieg", color=COLORS["neutral_dark"], xoffset_days=1.1, ypos=0.98)
    add_event_marker(ax, date(2026, 4, 1), "1. April\n12-Uhr-Regel", color=COLORS["neutral_dark"], xoffset_days=1.1, ypos=0.98)
    add_event_marker(ax, TEMP_TAX_START, "Steuersenkung\nStart", color=COLORS["tax_dark"], xoffset_days=1.0, ypos=0.97)
    add_event_marker(ax, TEMP_TAX_END, "", color=COLORS["tax_dark"])
    ax.text(
        pd.Timestamp(date(2026, 5, 31)),
        0.065,
        "Steuersenkung Mai/Juni",
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="bottom",
        fontsize=9.2,
        color=COLORS["tax_dark"],
        zorder=4,
    )

    ax.set_xlim(pd.Timestamp(start_day), pd.Timestamp(end_day))
    values = pd.concat(
        [
            df["betriebskosten_gewinn_eur_l"],
            df["steuern_abgaben_eur_l"],
            df["brent_eur_l"],
        ]
    ).dropna()
    ax.set_ylim(
        max(0.0, math.floor((values.min() - 0.08) * 10) / 10),
        math.ceil((values.max() + 0.10) * 10) / 10,
    )
    ax.set_ylabel("Euro pro Liter", fontsize=10.5, color=TOKENS["ink"], labelpad=10)
    ax.set_xlabel("Tag", fontsize=10.5, color=TOKENS["ink"], labelpad=10)
    ax.yaxis.set_major_formatter(FuncFormatter(euro_tick))
    ax.yaxis.set_major_locator(MultipleLocator(0.10))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m."))
    ax.tick_params(axis="x", which="major", labelrotation=0, labelsize=8.5, length=0, pad=8)
    ax.tick_params(axis="y", labelsize=8.7, length=0, pad=6)
    ax.grid(True, axis="y", linestyle="-", linewidth=0.8, color=TOKENS["grid"], zorder=0.2)
    ax.grid(True, axis="x", linestyle="-", linewidth=0.35, color=TOKENS["grid"], alpha=0.36, zorder=0.2)
    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)
    for side in ["left", "bottom"]:
        ax.spines[side].set_color(TOKENS["axis"])
        ax.spines[side].set_linewidth(1.0)

    fig.text(
        0.075,
        0.962,
        "Profitabilität und Steuereinnahmen",
        ha="left",
        va="top",
        fontsize=20.5,
        fontweight="semibold",
        color=TOKENS["ink"],
    )
    fig.text(
        0.075,
        0.925,
        "Auswirkung der staatliche Eingriffe in den deutschen Kraftstoffmarkt",
        ha="left",
        va="top",
        fontsize=12.1,
        color=TOKENS["muted"],
    )
    fig.text(
        0.075,
        0.895,
        "Alle Tankstellen in Deutschland - Diesel-Tagesdurchschnittspreis",
        ha="left",
        va="top",
        fontsize=8.9,
        color=COLORS["neutral_dark"],
    )
    fig.legend(
        handles=[line_station, line_tax, line_brent],
        loc="upper left",
        bbox_to_anchor=(0.075, 0.872),
        ncol=2,
        frameon=False,
        handlelength=2.8,
        handletextpad=0.62,
        columnspacing=1.35,
        labelspacing=0.65,
        fontsize=9.0,
        borderaxespad=0,
    )
    fig.text(
        0.075,
        0.060,
        "Quelle: tankzeit.de / Brent Crude Oil Kurs / MTS-K Daten",
        ha="left",
        va="bottom",
        fontsize=8.8,
        color=TOKENS["muted"],
    )
    fig.text(
        0.965,
        0.060,
        f"Tagesdurchschnittspreis, {start_day:%d.%m.}-{end_day:%d.%m.%Y}",
        ha="right",
        va="bottom",
        fontsize=8.8,
        color=TOKENS["muted"],
    )

    fig.savefig(output_png, dpi=180, bbox_inches="tight", pad_inches=0.18)
    plt.close(fig)


def write_outputs(df: pd.DataFrame, args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = "profitabilitaet_steuereinnahmen_deutschland_tagesdurchschnitt"
    output_png = args.output_dir / f"{stem}.png"
    output_csv = args.output_dir / f"{stem}.csv"
    output_md = args.output_dir / f"{stem}.md"

    export_df = df.drop(columns=["day"]).copy()
    export_df.to_csv(output_csv, index=False, quoting=csv.QUOTE_MINIMAL)
    render_chart(df, output_png, args.start_date, args.end_date)

    usable = df.dropna(subset=["tagesdurchschnitt_bruttopreis_eur_l"])
    output_md.write_text(
        "\n".join(
            [
                "# Profitabilität und Steuereinnahmen",
                "",
                f"- Zeitraum: {args.start_date:%Y-%m-%d} bis {args.end_date:%Y-%m-%d}",
                "- Kraftstoff: Diesel",
                "- Preisbasis: station-time-gewichteter Tagesdurchschnittspreis über alle aktiven MTS-K-Tankstellen in Deutschland",
                "- Berechnung: Bruttopreis minus Steuern/Abgaben, Brent-Rohölpreis, CO2, EBV und 19% MwSt. entsprechend der Preisaufschlüsselung",
                f"- Tage mit Preisbasis: {len(usable)} von {len(df)}",
                f"- Durchschnitt aktive Tankstellenäquivalente: {usable['aktive_tankstellen_aequivalent'].mean():.0f}",
                f"- Bruttopreis-Spanne: {usable['tagesdurchschnitt_bruttopreis_eur_l'].min():.3f} bis {usable['tagesdurchschnitt_bruttopreis_eur_l'].max():.3f} EUR/l",
                f"- Betriebskosten/Gewinn-Spanne: {usable['betriebskosten_gewinn_eur_l'].min():.3f} bis {usable['betriebskosten_gewinn_eur_l'].max():.3f} EUR/l",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(output_png)
    print(output_csv)
    print(output_md)


def main() -> None:
    args = parse_args()
    if args.warmup_start > args.start_date:
        raise SystemExit("--warmup-start must be on or before --start-date")
    if args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")

    auth = require_credentials()
    state_prices: dict[str, float] = {}
    market_rows: list[DailyMarketAverage] = []
    for day in tqdm(
        list(days_between(args.warmup_start, args.end_date)),
        desc="Processing raw price days",
        unit="day",
    ):
        events = load_diesel_events(day, auth)
        result = process_day(
            day,
            events,
            state_prices,
            emit_result=args.start_date <= day <= args.end_date,
        )
        if result is not None:
            market_rows.append(result)

    df = build_chart_frame(market_rows, args.start_date, args.end_date)
    write_outputs(df, args)


if __name__ == "__main__":
    main()
