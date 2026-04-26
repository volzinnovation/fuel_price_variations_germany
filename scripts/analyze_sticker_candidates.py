#!/usr/bin/env python3
"""Assess consumer-friendly station sticker candidates from April noon snapshots.

The primary shortlist rule implemented here is:

1. the station is always the cheapest station inside a 3x3 km area around it.

The separate idea "same price all day" needs intraday day-level traces. The
repo currently contains only sparse derived intraday coverage for that rule, so
the report surfaces the coverage gap explicitly instead of treating noon-price
stability as a proxy.

Because "cheapest" can be interpreted either as tied-lowest or uniquely lowest,
the report includes both variants. The initial ranking is based on a "cheap
first" lens: the selected fuel is sorted by average noon price over the month.

Neighborhood note:
The 3x3 km area is approximated as a latitude/longitude bounding box of
plus/minus 1.5 km around each station.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


FUELS: tuple[str, ...] = ("diesel", "e10", "e5")
DEFAULT_MONTH = "2026-04"
DEFAULT_LOW_PERCENTILE = 0.10


@dataclass(frozen=True)
class MonthConfig:
    label: str
    days: tuple[pd.Timestamp, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--month",
        default=DEFAULT_MONTH,
        help="Month to analyze in YYYY-MM format (default: %(default)s).",
    )
    parser.add_argument(
        "--fuel",
        choices=FUELS,
        default="e10",
        help="Fuel used for the ranked candidate list (default: %(default)s).",
    )
    parser.add_argument(
        "--low-percentile",
        type=float,
        default=DEFAULT_LOW_PERCENTILE,
        help="Cheap-first cutoff as a fraction between 0 and 1 (default: %(default)s).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "sticker_candidates",
        help="Directory for generated CSV and Markdown output.",
    )
    return parser.parse_args()


def month_days(month_label: str) -> MonthConfig:
    start = pd.Timestamp(f"{month_label}-01")
    if pd.isna(start):
        raise ValueError(f"Invalid month: {month_label}")
    end = start + pd.offsets.MonthEnd(0)
    days = tuple(pd.date_range(start=start, end=end, freq="D"))
    return MonthConfig(label=month_label, days=days)


def load_station_meta(root: Path) -> pd.DataFrame:
    stations = pd.DataFrame(json.loads((root / "data" / "stations.json").read_text()))
    stations = stations.dropna(subset=["uuid", "latitude", "longitude"]).copy()
    stations["uuid"] = stations["uuid"].astype(str)
    stations = stations.drop_duplicates(subset=["uuid"]).reset_index(drop=True)
    columns = [
        "name",
        "brand",
        "street",
        "house_number",
        "post_code",
        "city",
        "latitude",
        "longitude",
    ]
    return stations.set_index("uuid")[columns]


def iter_month_snapshot_paths(root: Path, config: MonthConfig) -> Iterable[tuple[pd.Timestamp, Path]]:
    for day in config.days:
        path = root / "data2" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / "noon.csv"
        if path.exists():
            yield day, path


def load_noon_snapshots(root: Path, config: MonthConfig, fuels: Iterable[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    usecols = ["station_uuid", *fuels]
    for day, path in iter_month_snapshot_paths(root, config):
        frame = pd.read_csv(path, usecols=usecols)
        frame["date"] = day
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No noon snapshots found for {config.label}")
    result = pd.concat(frames, ignore_index=True)
    result["station_uuid"] = result["station_uuid"].astype(str)
    for fuel in fuels:
        result[fuel] = pd.to_numeric(result[fuel], errors="coerce")
    return result


def build_neighbors(meta: pd.DataFrame, area_km: float = 3.0) -> dict[str, tuple[str, ...]]:
    half_km = area_km / 2.0
    cell_lat = 0.02
    cell_lon = 0.03

    rows = meta.reset_index().rename(columns={"index": "station_uuid"})
    rows["lat_bin"] = (rows["latitude"] / cell_lat).apply(math.floor)
    rows["lon_bin"] = (rows["longitude"] / cell_lon).apply(math.floor)

    cell_map: dict[tuple[int, int], list[object]] = defaultdict(list)
    for row in rows.itertuples(index=False):
        cell_map[(row.lat_bin, row.lon_bin)].append(row)

    neighbors: dict[str, tuple[str, ...]] = {}
    for row in rows.itertuples(index=False):
        lat_delta = half_km / 111.32
        lon_delta = half_km / (111.32 * max(math.cos(math.radians(row.latitude)), 0.2))
        found: list[str] = []
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                for other in cell_map.get((row.lat_bin + dlat, row.lon_bin + dlon), []):
                    if other.uuid == row.uuid:
                        continue
                    if (
                        abs(other.latitude - row.latitude) <= lat_delta
                        and abs(other.longitude - row.longitude) <= lon_delta
                    ):
                        found.append(other.uuid)
        neighbors[row.uuid] = tuple(sorted(set(found)))
    return neighbors


def load_intraday_daily_flags(root: Path, config: MonthConfig, fuel: str) -> pd.DataFrame:
    valid_dates = {f"{day:%Y-%m-%d}" for day in config.days}
    rows: list[dict[str, object]] = []
    for path in (root / "data2").glob(f"*/*/*/*/*/{fuel}.json"):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        daily = payload.get("daily") or []
        if not daily:
            continue
        matching_days = [row for row in daily if row.get("date") in valid_dates]
        if not matching_days:
            continue
        station_uuid = "-".join(path.parts[-6:-1])
        observed_dates = sorted(str(row.get("date")) for row in matching_days if row.get("date"))
        flat_rows = [
            row
            for row in matching_days
            if float(row.get("daily_range") or 0.0) <= 1e-9
        ]
        rows.append(
            {
                "station_uuid": station_uuid,
                "intraday_observed_days": len(matching_days),
                "intraday_flat_observed_days": len(flat_rows),
                "intraday_all_day_constant_observed": len(flat_rows) == len(matching_days),
                "intraday_full_month_coverage": len(matching_days) == len(config.days),
                "intraday_observed_dates": ",".join(observed_dates),
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "station_uuid",
                "intraday_observed_days",
                "intraday_flat_observed_days",
                "intraday_all_day_constant_observed",
                "intraday_full_month_coverage",
                "intraday_observed_dates",
            ]
        )
    return pd.DataFrame(rows)


def fuel_stats(
    noon: pd.DataFrame,
    meta: pd.DataFrame,
    neighbors: dict[str, tuple[str, ...]],
    fuel: str,
    expected_days: int,
) -> pd.DataFrame:
    fuel_df = noon[["station_uuid", "date", fuel]].dropna().rename(columns={fuel: "price"})
    fuel_df = fuel_df.loc[fuel_df["price"] > 0].copy()
    if fuel_df.empty:
        return pd.DataFrame()

    daily_map = {
        day.date(): dict(zip(group["station_uuid"], group["price"]))
        for day, group in fuel_df.groupby("date", sort=True)
    }
    ordered_days = tuple(sorted(daily_map))

    stats = fuel_df.groupby("station_uuid").agg(
        days=("price", "count"),
        mean_price=("price", "mean"),
        min_price=("price", "min"),
        max_price=("price", "max"),
        distinct_prices=("price", lambda s: round(s, 3).nunique()),
    )
    stats["price_span"] = stats["max_price"] - stats["min_price"]
    stats["constant_noon_price"] = (stats["days"] == expected_days) & (stats["price_span"] < 0.0005)

    rows: list[dict[str, object]] = []
    for station_id in stats.index:
        station_neighbors = neighbors.get(station_id, ())
        eligible_days = 0
        cheapest_days = 0
        strict_cheapest_days = 0
        tied_days = 0
        day_competitors: list[int] = []
        daily_prices: list[float] = []

        for day in ordered_days:
            prices = daily_map.get(day, {})
            station_price = prices.get(station_id)
            if station_price is None:
                continue
            competitor_prices = [prices[other] for other in station_neighbors if other in prices]
            if not competitor_prices:
                continue

            eligible_days += 1
            day_competitors.append(len(competitor_prices))
            daily_prices.append(float(station_price))
            competitor_min = min(competitor_prices)
            if station_price <= competitor_min + 1e-9:
                cheapest_days += 1
                if any(abs(station_price - other_price) < 1e-9 for other_price in competitor_prices):
                    tied_days += 1
                else:
                    strict_cheapest_days += 1

        rows.append(
            {
                "station_uuid": station_id,
                "eligible_days": eligible_days,
                "cheapest_days": cheapest_days,
                "strict_cheapest_days": strict_cheapest_days,
                "tied_days": tied_days,
                "min_competitors_daily": min(day_competitors) if day_competitors else 0,
                "mean_competitors": (
                    sum(day_competitors) / len(day_competitors) if day_competitors else 0.0
                ),
                "max_competitors": max(day_competitors) if day_competitors else 0,
                "has_neighbor_station": bool(station_neighbors),
                "always_local_cheapest": eligible_days == expected_days and cheapest_days == expected_days,
                "always_strict_local_cheapest": (
                    eligible_days == expected_days and strict_cheapest_days == expected_days
                ),
                "daily_price_signature": ",".join(f"{price:.3f}" for price in daily_prices),
            }
        )

    local = pd.DataFrame(rows).set_index("station_uuid")
    stats = stats.join(local, how="left").join(meta, how="left")
    stats["fuel"] = fuel
    stats["qualifies_loose"] = stats["always_local_cheapest"]
    stats["qualifies_strict"] = stats["always_strict_local_cheapest"]
    full = stats.loc[stats["days"] == expected_days].copy()
    if not full.empty:
        full["mean_price_percentile"] = full["mean_price"].rank(method="min", pct=True)
    else:
        full["mean_price_percentile"] = pd.NA
    stats = stats.join(full[["mean_price_percentile"]], how="left")
    return stats.reset_index().rename(columns={"index": "station_uuid"})


def fuel_summary(stats: pd.DataFrame, low_percentile: float) -> dict[str, object]:
    full = stats.loc[stats["days"] == stats["days"].max()].copy()
    if full.empty:
        return {
            "fuel": str(stats["fuel"].iloc[0]) if not stats.empty else "",
            "stations_with_full_history": 0,
            "always_local_cheapest_count": 0,
            "always_strict_local_cheapest_count": 0,
            "union_loose_count": 0,
            "union_strict_count": 0,
            "cheap_cutoff": float("nan"),
            "cheap_station_count": 0,
            "cheap_loose_count": 0,
            "cheap_strict_count": 0,
            "intraday_coverage_station_count": 0,
            "intraday_full_month_station_count": 0,
            "intraday_full_month_flat_count": 0,
            "intraday_partial_flat_station_count": 0,
        }

    cutoff = float(full["mean_price"].quantile(low_percentile))
    cheap = full.loc[full["mean_price"] <= cutoff]
    intraday_observed = stats.loc[stats["intraday_observed_days"].fillna(0).gt(0)]
    intraday_full_month = intraday_observed.loc[intraday_observed["intraday_full_month_coverage"]]
    intraday_partial_flat = intraday_observed.loc[
        intraday_observed["intraday_all_day_constant_observed"]
        & ~intraday_observed["intraday_full_month_coverage"]
    ]
    return {
        "fuel": str(full["fuel"].iloc[0]),
        "stations_with_full_history": int(len(full)),
        "always_local_cheapest_count": int(full["always_local_cheapest"].sum()),
        "always_strict_local_cheapest_count": int(full["always_strict_local_cheapest"].sum()),
        "union_loose_count": int(full["qualifies_loose"].sum()),
        "union_strict_count": int(full["qualifies_strict"].sum()),
        "cheap_cutoff": cutoff,
        "cheap_station_count": int(len(cheap)),
        "cheap_loose_count": int(cheap["qualifies_loose"].sum()),
        "cheap_strict_count": int(cheap["qualifies_strict"].sum()),
        "cheap_strict_2plus_count": int(
            (
                cheap["always_strict_local_cheapest"]
                & cheap["min_competitors_daily"].ge(2)
            ).sum()
        ),
        "cheap_strict_3plus_count": int(
            (
                cheap["always_strict_local_cheapest"]
                & cheap["min_competitors_daily"].ge(3)
            ).sum()
        ),
        "intraday_coverage_station_count": int(len(intraday_observed)),
        "intraday_full_month_station_count": int(len(intraday_full_month)),
        "intraday_full_month_flat_count": int(
            intraday_full_month["intraday_all_day_constant_observed"].sum()
        ),
        "intraday_partial_flat_station_count": int(len(intraday_partial_flat)),
    }


def category_label(row: pd.Series) -> str:
    if bool(row["always_strict_local_cheapest"]):
        return "strict_local_cheapest"
    if bool(row["always_local_cheapest"]):
        return "local_cheapest"
    return "other"


def format_markdown_value(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"

    columns = [str(column) for column in frame.columns]
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for row in frame.itertuples(index=False, name=None):
        values = [format_markdown_value(value) for value in row]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, separator, *rows])


def render_markdown(
    config: MonthConfig,
    fuel: str,
    summary_rows: pd.DataFrame,
    selected_stats: pd.DataFrame,
    low_percentile: float,
) -> str:
    full = selected_stats.loc[selected_stats["days"] == len(config.days)].copy()
    cutoff = float(full["mean_price"].quantile(low_percentile))
    recommended = full.loc[
        full["always_strict_local_cheapest"] & full["min_competitors_daily"].ge(2)
    ].sort_values(["mean_price", "city", "name"])
    cheap_recommended = recommended.loc[recommended["mean_price"] <= cutoff].head(20)
    intraday_observed = selected_stats.loc[
        selected_stats["intraday_observed_days"].fillna(0).gt(0)
    ].copy()
    observed_dates = sorted(
        {
            item
            for value in intraday_observed["intraday_observed_dates"].dropna().tolist()
            for item in str(value).split(",")
            if item
        }
    )
    coverage_lines = [
        f"- Stationen mit intraday `daily`-Daten im Repo: `{int(len(intraday_observed))}`",
        f"- Stationen mit voller Intraday-Abdeckung für alle `{len(config.days)}` April-Tage: `{int(intraday_observed['intraday_full_month_coverage'].sum())}`",
        f"- Stationen mit flachem Preis auf allen beobachteten Intraday-Tagen, aber nur Teilabdeckung: `{int((intraday_observed['intraday_all_day_constant_observed'] & ~intraday_observed['intraday_full_month_coverage']).sum())}`",
    ]
    if observed_dates:
        coverage_lines.append(
            f"- Beobachtete Intraday-Tage im aktuellen Repo: `{', '.join(observed_dates)}`"
        )

    summary_table = markdown_table(summary_rows)
    recommended_table = markdown_table(
        cheap_recommended[
            [
                "station_uuid",
                "name",
                "brand",
                "city",
                "mean_price",
                "min_competitors_daily",
                "mean_competitors",
                "price_span",
            ]
        ]
    )

    return "\n".join(
        [
            f"# Sticker Candidate Assessment {config.label}",
            "",
            f"- Ranked fuel: `{fuel}`",
            f"- Analyzed noon snapshots: `{config.days[0]:%Y-%m-%d}` to `{config.days[-1]:%Y-%m-%d}`",
            f"- Cheap-first cutoff: bottom `{int(low_percentile * 100)}`% by average noon price for `{fuel}`",
            "- Local competition rule: 3x3 km box around each station, approximated as +/- 1.5 km in both directions",
            "- Important: this shortlist uses the local-cheapest rule only. The repo does not currently contain enough April intraday traces to verify the separate 'same price all day' rule nationwide.",
            "",
            "## Fuel Summary",
            "",
            summary_table,
            "",
            "## All-Day Constant Coverage",
            "",
            *coverage_lines,
            "",
            f"## Recommended `{fuel}` Shortlist",
            "",
            "Strictly cheapest every analyzed day, with at least two competitors present every day,",
            "then ranked by lowest average noon price.",
            "",
            recommended_table,
            "",
            "## Map View",
            "",
            "- `map.html` in the same output directory renders the shortlisted stations on OpenStreetMap tiles.",
            "",
        ]
    )


def build_map_records(stats: pd.DataFrame, cheap_cutoff: float) -> list[dict[str, object]]:
    candidates = stats.loc[stats["qualifies_loose"]].copy()
    candidates["recommended"] = (
        candidates["always_strict_local_cheapest"]
        & candidates["min_competitors_daily"].ge(2)
        & candidates["mean_price"].le(cheap_cutoff)
    )
    candidates["cheap"] = candidates["mean_price"].le(cheap_cutoff)
    candidates = candidates.sort_values(["recommended", "mean_price"], ascending=[False, True])

    records: list[dict[str, object]] = []
    for row in candidates.itertuples(index=False):
        street = "" if pd.isna(row.street) else str(row.street).strip()
        house_number = "" if pd.isna(row.house_number) else str(row.house_number).strip()
        street_label = " ".join(part for part in [street, house_number] if part).strip()
        records.append(
            {
                "station_uuid": row.station_uuid,
                "name": row.name,
                "brand": "" if pd.isna(row.brand) else str(row.brand),
                "city": row.city,
                "street": street_label,
                "post_code": "" if pd.isna(row.post_code) else str(row.post_code),
                "latitude": round(float(row.latitude), 6),
                "longitude": round(float(row.longitude), 6),
                "mean_price": round(float(row.mean_price), 3),
                "price_span": round(float(row.price_span), 3),
                "category": row.category,
                "cheap": bool(row.cheap),
                "recommended": bool(row.recommended),
                "always_local_cheapest": bool(row.always_local_cheapest),
                "always_strict_local_cheapest": bool(row.always_strict_local_cheapest),
                "min_competitors_daily": int(row.min_competitors_daily),
                "mean_competitors": round(float(row.mean_competitors), 2),
            }
        )
    return records


def render_map_html(
    config: MonthConfig,
    fuel: str,
    summary_rows: pd.DataFrame,
    map_records: list[dict[str, object]],
    low_percentile: float,
) -> str:
    summary_lookup = {row["fuel"]: row for row in summary_rows.to_dict(orient="records")}
    summary = summary_lookup[fuel]
    map_json = json.dumps(map_records, ensure_ascii=False).replace("</", "<\\/")

    top_records = [record for record in map_records if record["recommended"]][:12]
    if top_records:
        shortlist_items = "\n".join(
            "<li>"
            f"<strong>{html.escape(record['name'])}</strong><br>"
            f"{html.escape(str(record['post_code']))} {html.escape(record['city'])} · "
            f"{record['mean_price']:.3f} €/L"
            "</li>"
            for record in top_records
        )
    else:
        shortlist_items = "<li>Keine Station erfüllt den strikten Cheap-First-Filter.</li>"

    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sticker Candidates {config.label} {fuel.upper()}</title>
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
    crossorigin=""
  >
  <style>
    :root {{
      --bg: #f4f1e8;
      --panel: rgba(252, 250, 245, 0.95);
      --ink: #1f2a1f;
      --muted: #5b6656;
      --accent: #2f6f57;
      --accent-strong: #1f4b3b;
      --warn: #d98b2b;
      --soft: #7f8c8d;
      --line: rgba(31, 42, 31, 0.14);
    }}
    html, body {{
      margin: 0;
      height: 100%;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(47, 111, 87, 0.14), transparent 26rem),
        linear-gradient(180deg, #f7f4ec 0%, #ece7dc 100%);
    }}
    .layout {{
      display: grid;
      grid-template-columns: 24rem 1fr;
      height: 100vh;
    }}
    .sidebar {{
      padding: 1.25rem 1.1rem 1.1rem;
      background: var(--panel);
      border-right: 1px solid var(--line);
      overflow-y: auto;
      backdrop-filter: blur(10px);
    }}
    .eyebrow {{
      font-size: 0.78rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent-strong);
      margin-bottom: 0.4rem;
    }}
    h1 {{
      margin: 0 0 0.55rem;
      font-size: 1.9rem;
      line-height: 1.05;
    }}
    .lede {{
      margin: 0 0 1rem;
      color: var(--muted);
      line-height: 1.45;
    }}
    .chips {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.55rem;
      margin-bottom: 1rem;
    }}
    .chip {{
      padding: 0.75rem 0.8rem;
      background: white;
      border: 1px solid var(--line);
      border-radius: 0.85rem;
    }}
    .chip strong {{
      display: block;
      font-size: 1.15rem;
      margin-bottom: 0.15rem;
    }}
    .chip span {{
      color: var(--muted);
      font-size: 0.84rem;
    }}
    .legend,
    .shortlist {{
      margin-top: 1rem;
      padding-top: 1rem;
      border-top: 1px solid var(--line);
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 0.6rem;
      margin-bottom: 0.55rem;
      color: var(--muted);
    }}
    .dot {{
      width: 0.9rem;
      height: 0.9rem;
      border-radius: 999px;
      border: 2px solid rgba(0, 0, 0, 0.2);
      flex: none;
    }}
    .dot.recommended {{ background: var(--accent); }}
    .dot.strict {{ background: #4d8f76; }}
    .dot.local {{ background: var(--warn); }}
    .shortlist ol {{
      margin: 0.7rem 0 0;
      padding-left: 1.15rem;
    }}
    .shortlist li {{
      margin-bottom: 0.8rem;
      color: var(--muted);
      line-height: 1.35;
    }}
    .map-wrap {{
      position: relative;
    }}
    #map {{
      height: 100%;
      width: 100%;
    }}
    .leaflet-popup-content {{
      min-width: 16rem;
      line-height: 1.45;
    }}
    .popup-title {{
      font-weight: 700;
      margin-bottom: 0.2rem;
    }}
    .popup-meta {{
      color: var(--muted);
      margin-bottom: 0.45rem;
    }}
    .popup-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.3rem 0.7rem;
      font-size: 0.92rem;
    }}
    .popup-grid span {{
      color: var(--muted);
    }}
    @media (max-width: 980px) {{
      .layout {{
        grid-template-columns: 1fr;
        grid-template-rows: auto 1fr;
      }}
      .sidebar {{
        max-height: 45vh;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="eyebrow">tankzeit.de sticker analysis</div>
      <h1>{html.escape(config.label)} · {html.escape(fuel.upper())}</h1>
      <p class="lede">
        Cheap-first cutoff: bottom {int(low_percentile * 100)}% by average noon price.
        Recommended markers are strictly cheapest every analyzed day and face at least two competitors daily.
      </p>
      <section class="chips">
        <div class="chip"><strong>{int(summary['union_loose_count'])}</strong><span>qualifying candidates</span></div>
        <div class="chip"><strong>{int(summary['union_strict_count'])}</strong><span>strict local winners</span></div>
        <div class="chip"><strong>{summary['cheap_cutoff']:.3f} €</strong><span>cheap-first cutoff</span></div>
        <div class="chip"><strong>{int(summary['cheap_strict_2plus_count'])}</strong><span>recommended shortlist</span></div>
      </section>
      <section class="legend">
        <div class="legend-item"><span class="dot recommended"></span>Recommended shortlist</div>
        <div class="legend-item"><span class="dot strict"></span>Strictly cheapest locally</div>
        <div class="legend-item"><span class="dot local"></span>Tied-lowest locally</div>
      </section>
      <section class="shortlist">
        <strong>Top shortlist stations</strong>
        <ol>
          {shortlist_items}
        </ol>
      </section>
    </aside>
    <main class="map-wrap">
      <div id="map"></div>
    </main>
  </div>
  <script
    src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin=""
  ></script>
  <script>
    const records = {map_json};
    const map = L.map('map', {{ zoomControl: true }});
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    }}).addTo(map);

    const colors = {{
      recommended: '#2f6f57',
      strict_local_cheapest: '#4d8f76',
      local_cheapest: '#d98b2b'
    }};

    const groups = {{
      recommended: L.layerGroup(),
      strict: L.layerGroup(),
      local: L.layerGroup()
    }};

    const bounds = [];

    function popupHtml(record) {{
      const address = [record.street, [record.post_code, record.city].filter(Boolean).join(' ')].filter(Boolean).join('<br>');
      return `
        <div class="popup-title">${{record.name}}</div>
        <div class="popup-meta">${{record.brand || 'Unbranded'}} · ${{record.station_uuid}}</div>
        <div class="popup-meta">${{address || 'Keine Adressdaten'}}</div>
        <div class="popup-grid">
          <div><span>Mean noon</span><br>${{record.mean_price.toFixed(3)}} €/L</div>
          <div><span>Range</span><br>${{record.price_span.toFixed(3)}} €/L</div>
          <div><span>Rule</span><br>${{record.category}}</div>
          <div><span>Competitors/day</span><br>${{record.min_competitors_daily}} min · ${{record.mean_competitors.toFixed(2)}} avg</div>
        </div>
      `;
    }}

    for (const record of records) {{
      const color = colors[record.category] || colors.local_cheapest;
      const marker = L.circleMarker([record.latitude, record.longitude], {{
        radius: record.recommended ? 8.5 : 6.5,
        color,
        weight: record.recommended ? 3 : 2,
        fillColor: color,
        fillOpacity: record.recommended ? 0.9 : 0.74
      }}).bindPopup(popupHtml(record));
      bounds.push([record.latitude, record.longitude]);

      if (record.recommended) {{
        marker.addTo(groups.recommended);
      }} else if (record.category === 'strict_local_cheapest') {{
        marker.addTo(groups.strict);
      }} else {{
        marker.addTo(groups.local);
      }}
    }}

    groups.recommended.addTo(map);
    groups.strict.addTo(map);
    groups.local.addTo(map);

    L.control.layers(null, {{
      'Recommended shortlist': groups.recommended,
      'Strict local winners': groups.strict,
      'Tied-lowest local winners': groups.local
    }}, {{ collapsed: false }}).addTo(map);

    if (bounds.length) {{
      map.fitBounds(bounds, {{ padding: [22, 22] }});
    }} else {{
      map.setView([51.1657, 10.4515], 6);
    }}
  </script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    requested_config = month_days(args.month)
    if not 0 < args.low_percentile < 1:
        raise ValueError("--low-percentile must be between 0 and 1")

    meta = load_station_meta(root)
    noon = load_noon_snapshots(root, requested_config, FUELS).merge(
        meta, left_on="station_uuid", right_index=True, how="inner"
    )
    available_days = tuple(sorted(pd.Timestamp(day) for day in noon["date"].drop_duplicates().tolist()))
    config = MonthConfig(label=requested_config.label, days=available_days)
    neighbors = build_neighbors(meta)

    stats_by_fuel: dict[str, pd.DataFrame] = {}
    summary_rows: list[dict[str, object]] = []
    for fuel in FUELS:
        stats = fuel_stats(noon, meta, neighbors, fuel=fuel, expected_days=len(config.days))
        if stats.empty:
            continue
        intraday_flags = load_intraday_daily_flags(root, config, fuel)
        if intraday_flags.empty:
            stats["intraday_observed_days"] = 0
            stats["intraday_flat_observed_days"] = 0
            stats["intraday_all_day_constant_observed"] = False
            stats["intraday_full_month_coverage"] = False
            stats["intraday_observed_dates"] = ""
        else:
            stats = stats.merge(intraday_flags, on="station_uuid", how="left")
            stats["intraday_observed_days"] = stats["intraday_observed_days"].fillna(0).astype(int)
            stats["intraday_flat_observed_days"] = (
                stats["intraday_flat_observed_days"].fillna(0).astype(int)
            )
            stats["intraday_all_day_constant_observed"] = (
                stats["intraday_all_day_constant_observed"]
                .where(stats["intraday_all_day_constant_observed"].notna(), False)
                .astype(bool)
            )
            stats["intraday_full_month_coverage"] = (
                stats["intraday_full_month_coverage"]
                .where(stats["intraday_full_month_coverage"].notna(), False)
                .astype(bool)
            )
            stats["intraday_observed_dates"] = (
                stats["intraday_observed_dates"].fillna("").astype(str)
            )
        stats["category"] = stats.apply(category_label, axis=1)
        stats_by_fuel[fuel] = stats
        summary_rows.append(fuel_summary(stats, args.low_percentile))

    if args.fuel not in stats_by_fuel:
        raise ValueError(f"No data for ranked fuel: {args.fuel}")

    summary_df = pd.DataFrame(summary_rows).sort_values("fuel").reset_index(drop=True)
    selected_stats = stats_by_fuel[args.fuel].copy()
    full = selected_stats.loc[selected_stats["days"] == len(config.days)].copy()
    cheap_cutoff = float(full["mean_price"].quantile(args.low_percentile))

    candidates = selected_stats.loc[selected_stats["qualifies_loose"]].copy()
    candidates = candidates.sort_values(["mean_price", "category", "city", "name"]).reset_index(drop=True)

    recommended = full.loc[
        full["always_strict_local_cheapest"]
        & full["min_competitors_daily"].ge(2)
        & full["mean_price"].le(cheap_cutoff)
    ].copy()
    recommended = recommended.sort_values(["mean_price", "city", "name"]).reset_index(drop=True)

    output_dir = args.output_dir / config.label / args.fuel
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates_path = output_dir / "candidates.csv"
    recommended_path = output_dir / "recommended.csv"
    summary_path = output_dir / "summary.csv"
    report_path = output_dir / "report.md"
    map_path = output_dir / "map.html"

    candidates.to_csv(candidates_path, index=False, float_format="%.3f")
    recommended.to_csv(recommended_path, index=False, float_format="%.3f")
    summary_df.to_csv(summary_path, index=False, float_format="%.3f")
    report_path.write_text(
        render_markdown(config, args.fuel, summary_df, selected_stats, args.low_percentile)
    )
    map_path.write_text(
        render_map_html(
            config=config,
            fuel=args.fuel,
            summary_rows=summary_df,
            map_records=build_map_records(selected_stats, cheap_cutoff),
            low_percentile=args.low_percentile,
        )
    )

    print(f"Analyzed {config.label} noon snapshots for {len(config.days)} days.")
    print(f"Ranked fuel: {args.fuel}")
    print(summary_df.to_string(index=False))
    print()
    print(f"Wrote candidate list: {candidates_path}")
    print(f"Wrote recommended shortlist: {recommended_path}")
    print(f"Wrote summary: {summary_path}")
    print(f"Wrote report: {report_path}")
    print(f"Wrote map: {map_path}")


if __name__ == "__main__":
    main()
