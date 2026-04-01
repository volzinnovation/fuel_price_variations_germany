#!/usr/bin/env python3
"""Generate SEO station pages plus sitemap/robots for the root-served site."""

from __future__ import annotations

import html
import json
import re
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
STATIONS_PATH = ROOT / "data" / "stations.json"
DATA2_DIR = ROOT / "data2"
STATION_DIR = ROOT / "station"
SITEMAP_PATH = ROOT / "sitemap.xml"
ROBOTS_PATH = ROOT / "robots.txt"
SITE_ORIGIN = "https://tankzeit.de"

FUELS = ("diesel", "e10", "e5")
FUEL_LABELS = {
    "diesel": "Diesel",
    "e10": "E10",
    "e5": "E5",
}
ROOT_URLS = [
    "",
    "e10.html",
    "management.html",
    "info.html",
    "privacy.html",
    "imprint.html",
]


def format_text(value: object) -> str:
    return html.escape(str(value or "").strip())


def absolute_url(path: str) -> str:
    clean = path.lstrip("/")
    if not clean:
        return f"{SITE_ORIGIN}/"
    return f"{SITE_ORIGIN}/{clean}"


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "tankstelle"


def station_page_path(name: str, station_id: str) -> str:
    return f"station/{slugify(name)}-{station_id}.html"


def has_valid_coordinates(station: dict[str, object]) -> bool:
    try:
        latitude = float(station.get("latitude") or 0)
        longitude = float(station.get("longitude") or 0)
    except (TypeError, ValueError):
        return False
    return 47.0 <= latitude <= 56.0 and 5.0 <= longitude <= 16.0


def station_address(station: dict[str, object]) -> str:
    parts = [
        str(station.get("street") or "").strip(),
        str(station.get("house_number") or "").strip(),
    ]
    street = " ".join(part for part in parts if part).strip()
    city_line = " ".join(
        part
        for part in (
            str(station.get("post_code") or "").strip(),
            str(station.get("city") or "").strip(),
        )
        if part
    ).strip()
    return "<br />".join(filter(None, (format_text(street), format_text(city_line))))


def load_station_stats(station_id: str) -> dict[str, dict[str, object]]:
    normalized_id = station_id.replace("-", "/")
    stats_by_fuel: dict[str, dict[str, object]] = {}
    for fuel in FUELS:
        path = DATA2_DIR / normalized_id / f"{fuel}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        stats_by_fuel[fuel] = payload
    return stats_by_fuel


def fuel_range_text(stats: dict[str, object] | None) -> str:
    if not stats:
        return "Keine historische Spanne verfügbar."
    minimum = stats.get("minabs")
    maximum = stats.get("maxabs")
    try:
        return f"{float(minimum):.3f} - {float(maximum):.3f} €/l"
    except (TypeError, ValueError):
        return "Keine historische Spanne verfügbar."


def fuel_best_text(stats: dict[str, object] | None) -> str:
    if not stats:
        return "Keine Bestzeit hinterlegt."
    text = str(stats.get("text") or "").strip()
    return text or "Keine Bestzeit hinterlegt."


def _first_number(*values: object) -> float | None:
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _format_count(*values: object) -> str | None:
    number = _first_number(*values)
    if number is None:
        return None
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.1f}"


def fuel_summary(stats: dict[str, object] | None) -> dict[str, object] | None:
    if not stats:
        return None
    summary = stats.get("summary")
    return summary if isinstance(summary, dict) else None


def fuel_profile_text(stats: dict[str, object] | None) -> str:
    summary = fuel_summary(stats)
    if not summary:
        return "Kein Tagesprofil hinterlegt."
    noon_price = _first_number(
        summary.get("noon_price_median"),
        summary.get("noon_price_avg"),
    )
    decreases = _format_count(
        summary.get("post_noon_decreases_avg"),
        summary.get("post_noon_decreases_median"),
    )
    parts: list[str] = []
    if noon_price is not None:
        parts.append(f"12:00 {noon_price:.3f} €/l")
    if decreases is not None:
        parts.append(f"{decreases} Senkungen nach 12 Uhr")
    return " · ".join(parts) if parts else "Kein Tagesprofil hinterlegt."


def fuel_minimum_text(stats: dict[str, object] | None) -> str:
    summary = fuel_summary(stats)
    if not summary:
        return "Kein Tagesminimum hinterlegt."
    time_text = str(summary.get("min_time_text") or "").strip()
    duration_text = str(summary.get("min_duration_text") or "").strip()
    if time_text and duration_text:
        return f"{time_text} Uhr für {duration_text}"
    if time_text:
        return f"{time_text} Uhr"
    if duration_text:
        return duration_text
    return "Kein Tagesminimum hinterlegt."


def fuel_chip_text(stats: dict[str, object] | None) -> str:
    summary = fuel_summary(stats)
    if summary and summary.get("min_time_text"):
        return f"Minimum meist {fuel_minimum_text(stats)}"
    return fuel_best_text(stats)


def build_station_description(
    name: str,
    city: str,
    street: str,
    stats_by_fuel: dict[str, dict[str, object]],
) -> str:
    snippets: list[str] = []
    for fuel in ("diesel", "e10", "e5"):
        stats = stats_by_fuel.get(fuel)
        if not stats:
            continue
        fuel_label = FUEL_LABELS[fuel]
        summary = fuel_summary(stats)
        if summary:
            profile = fuel_profile_text(stats)
            minimum = fuel_minimum_text(stats)
            snippets.append(
                f"{fuel_label}: {profile}, Tagesminimum meist {minimum}"
            )
        else:
            snippets.append(f"{fuel_label}: beste Tankzeit {fuel_best_text(stats)}")
    best_summary = (
        "; ".join(snippets[:3]) if snippets else "mit historischen Tagesprofilen"
    )
    place = ", ".join(part for part in (street, city) if part)
    if place:
        return f"{name} in {place}. {best_summary}. Direktlinks für Diesel, E10 und E5 auf tankzeit.de."
    return f"{name}. {best_summary}. Direktlinks für Diesel, E10 und E5 auf tankzeit.de."


def fuel_chart_url(station_id: str, fuel: str, name: str, latitude: object, longitude: object) -> str:
    return (
        f"/chart.html?id={quote(station_id)}"
        f"&fuel={quote(fuel)}"
        f"&name={quote(name)}"
        f"&lat={quote(str(latitude))}"
        f"&lng={quote(str(longitude))}"
    )


def build_fuel_cards(
    station_id: str,
    name: str,
    latitude: object,
    longitude: object,
    stats_by_fuel: dict[str, dict[str, object]],
) -> str:
    cards: list[str] = []
    for fuel in FUELS:
        stats = stats_by_fuel.get(fuel)
        if fuel_summary(stats):
            cards.append(
                "<article class=\"station-fuel-card\">"
                f"<h3>{FUEL_LABELS[fuel]}</h3>"
                f"<p><strong>Mittagsmaximum:</strong> {format_text(fuel_profile_text(stats))}</p>"
                f"<p><strong>Tagesminimum:</strong> {format_text(fuel_minimum_text(stats))}</p>"
                f"<p><strong>Historische Spanne:</strong> {format_text(fuel_range_text(stats))}</p>"
                f"<a class=\"link-btn secondary-link\" href=\"{fuel_chart_url(station_id, fuel, name, latitude, longitude)}\">Chart öffnen</a>"
                "</article>"
            )
            continue
        cards.append(
            "<article class=\"station-fuel-card\">"
            f"<h3>{FUEL_LABELS[fuel]}</h3>"
            f"<p><strong>Beste Zeit:</strong> {format_text(fuel_best_text(stats))}</p>"
            f"<p><strong>Historische Spanne:</strong> {format_text(fuel_range_text(stats))}</p>"
            f"<a class=\"link-btn secondary-link\" href=\"{fuel_chart_url(station_id, fuel, name, latitude, longitude)}\">Chart öffnen</a>"
            "</article>"
        )
    return "".join(cards)


def build_station_page(station: dict[str, object]) -> tuple[str, str]:
    station_id = str(station.get("uuid") or "").strip()
    name = str(station.get("name") or "Tankstelle").strip()
    brand = str(station.get("brand") or "").strip()
    street = " ".join(
        part
        for part in (
            str(station.get("street") or "").strip(),
            str(station.get("house_number") or "").strip(),
        )
        if part
    ).strip()
    postcode = str(station.get("post_code") or "").strip()
    city = str(station.get("city") or "").strip()
    latitude = float(station.get("latitude") or 0)
    longitude = float(station.get("longitude") or 0)
    canonical_path = station_page_path(name, station_id)
    canonical_url = absolute_url(canonical_path)
    google_maps_url = f"https://www.google.com/maps/dir/?api=1&destination={latitude},{longitude}"
    stats_by_fuel = load_station_stats(station_id)
    has_noon_reset_stats = any(fuel_summary(stats_by_fuel.get(fuel)) for fuel in FUELS)
    description = build_station_description(name, city, street, stats_by_fuel)
    brand_line = f"{brand} · " if brand else ""
    address_html = station_address(station)
    city_title = city or postcode or "Deutschland"
    structured_data = {
        "@context": "https://schema.org",
        "@type": "GasStation",
        "name": name,
        "brand": {"@type": "Brand", "name": brand} if brand else None,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": street,
            "postalCode": postcode,
            "addressLocality": city,
            "addressCountry": "DE",
        },
        "geo": {
            "@type": "GeoCoordinates",
            "latitude": latitude,
            "longitude": longitude,
        },
        "url": canonical_url,
    }
    structured_data = {key: value for key, value in structured_data.items() if value}

    page_html = f"""<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <title>{format_text(name)} in {format_text(city_title)} | Tagesprofile für Diesel, E10 und E5 | tankzeit.de</title>
    <meta name="description" content="{format_text(description)}" />
    <link rel="canonical" href="{canonical_url}" />
    <meta property="og:type" content="website" />
    <meta property="og:title" content="{format_text(name)} in {format_text(city_title)} | tankzeit.de" />
    <meta property="og:description" content="{format_text(description)}" />
    <meta property="og:url" content="{canonical_url}" />
    <meta name="theme-color" content="#0f766e" />
    <link rel="stylesheet" href="/styles.css" />
    <script type="application/ld+json">{json.dumps(structured_data, ensure_ascii=False)}</script>
  </head>
  <body class="station-page">
    <main class="station-shell">
      <a class="legal-back" href="/index.html">← Zur App</a>

      <section class="station-hero">
        <p class="legal-kicker">Tankzeit Station</p>
        <h1>{format_text(name)}</h1>
        <p class="station-summary">{brand_line}{address_html}</p>
        <div class="station-chip-row">
          <span class="station-chip">Diesel: {format_text(fuel_chip_text(stats_by_fuel.get("diesel")))}</span>
          <span class="station-chip">E10: {format_text(fuel_chip_text(stats_by_fuel.get("e10")))}</span>
          <span class="station-chip">E5: {format_text(fuel_chip_text(stats_by_fuel.get("e5")))}</span>
        </div>
        <p class="station-summary">
          {"Tankzeit zeigt für diese Tankstelle das typische 12:00-Maximum, die Senkungen nach 12 Uhr und das Tagesminimum aus den veröffentlichten Tankerkönig-Daten seit dem Mittagsreset vom 1. April 2026." if has_noon_reset_stats else "Tankzeit zeigt für diese Tankstelle historische Tagesprofile aus den veröffentlichten Tankerkönig-Daten. So findest du schneller die typischen Zeitfenster mit günstigeren Preisen."}
        </p>
        <div class="station-actions">
          <a class="link-btn" href="{fuel_chart_url(station_id, 'diesel', name, latitude, longitude)}">Diesel-Chart</a>
          <a class="link-btn secondary-link" href="{fuel_chart_url(station_id, 'e10', name, latitude, longitude)}">E10-Chart</a>
          <a class="link-btn secondary-link" href="{fuel_chart_url(station_id, 'e5', name, latitude, longitude)}">E5-Chart</a>
          <a class="link-btn secondary-link" href="{google_maps_url}" target="_blank" rel="noopener noreferrer">Navigation</a>
        </div>
      </section>

      <section class="legal-card">
        <h2>Tankstelle im Überblick</h2>
        <p class="legal-intro">
          Diese statische Seite dient als Direktlink für Suchmaschinen und Verweise auf eine einzelne Tankstelle.
          Die interaktiven Detailcharts öffnest du über die Kraftstoffkarten oben.
        </p>

        <h3>Adresse</h3>
        <p>{address_html}</p>

        <h3>Preisprofile je Kraftstoff</h3>
        <div class="station-fuel-grid">
          {build_fuel_cards(station_id, name, latitude, longitude, stats_by_fuel)}
        </div>

        <p class="station-note">
          Datenquellen: Tankerkönig / MTS-K, tankzeit.de Auswertungspipeline. Koordinaten und Stationsstammdaten stammen aus dem veröffentlichten Stationskatalog.
        </p>
      </section>
    </main>
  </body>
</html>
"""
    return canonical_path, page_html


def write_station_pages() -> list[str]:
    stations = json.loads(STATIONS_PATH.read_text(encoding="utf-8"))
    if STATION_DIR.exists():
        shutil.rmtree(STATION_DIR)
    STATION_DIR.mkdir(parents=True, exist_ok=True)

    page_paths: list[str] = []
    for station in stations:
        if not isinstance(station, dict):
            continue
        station_id = str(station.get("uuid") or "").strip()
        name = str(station.get("name") or "").strip()
        if not station_id or not name or not has_valid_coordinates(station):
            continue
        page_path, page_html = build_station_page(station)
        target = ROOT / page_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page_html, encoding="utf-8")
        page_paths.append(page_path)
    return page_paths


def write_sitemap(page_paths: list[str]) -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path in ROOT_URLS + page_paths:
        lines.append("  <url>")
        lines.append(f"    <loc>{html.escape(absolute_url(path))}</loc>")
        lines.append("  </url>")
    lines.append("</urlset>")
    SITEMAP_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_robots() -> None:
    ROBOTS_PATH.write_text(
        "User-agent: *\n"
        "Allow: /\n\n"
        f"Sitemap: {absolute_url('sitemap.xml')}\n",
        encoding="utf-8",
    )


def main() -> None:
    page_paths = write_station_pages()
    write_sitemap(page_paths)
    write_robots()
    print(f"Generated {len(page_paths)} station pages.")


if __name__ == "__main__":
    main()
