#!/usr/bin/env python3
"""Generate SEO station pages plus sitemap/robots for the root-served site."""

from __future__ import annotations

from collections import Counter
from datetime import date
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
SITE_NAME = "tankzeit.de"
SITE_LOGO_URL = f"{SITE_ORIGIN}/favicon-512.png"

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
LAW_RESET_DATE = date(2026, 4, 1)
POST_LAW_DAY_PROFILE_SHORT = (
    "00:00-11:59 ggü. Vortag 12:00, 12:00-23:59 ggü. Tages-12:00"
)
POST_LAW_DAY_PROFILE_LONG = (
    "00:00-11:59 relativ zu Vortag 12:00, 12:00-23:59 relativ zum Tagespreis um 12:00"
)
TOKEN_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")
EXACT_TOKEN_FALLBACKS = {
    "aeuussere": "äußere",
    "niederoefflingen": "niederöfflingen",
    "oeffingen": "öffingen",
}
TOKEN_FRAGMENT_FALLBACKS = (
    ("aecker", "äcker"),
    ("allgaeu", "allgäu"),
    ("boenn", "bönn"),
    ("braeu", "bräu"),
    ("broel", "bröl"),
    ("brueck", "brück"),
    ("buec", "büc"),
    ("caec", "cäc"),
    ("duehr", "dühr"),
    ("flaem", "fläm"),
    ("flueg", "flüg"),
    ("froschae", "fröschä"),
    ("gueter", "güter"),
    ("guetz", "gütz"),
    ("haeuer", "häuer"),
    ("haeus", "häus"),
    ("hoeld", "höld"),
    ("huett", "hütt"),
    ("koelsch", "kölsch"),
    ("koenig", "könig"),
    ("koes", "kös"),
    ("koeth", "köth"),
    ("koest", "köst"),
    ("kuepp", "küpp"),
    ("maeb", "mäb"),
    ("moench", "mönch"),
    ("muehl", "mühl"),
    ("muenst", "münst"),
    ("muer", "mür"),
    ("nuett", "nütt"),
    ("poess", "pöß"),
    ("roem", "röm"),
    ("roett", "rött"),
    ("schoef", "schöf"),
    ("schuett", "schütt"),
    ("strohgaeu", "strohgäu"),
    ("ueber", "über"),
    ("ueck", "ück"),
    ("voehl", "vöhl"),
)


def format_text(value: object) -> str:
    return html.escape(str(value or "").strip())


def normalize_german_token(token: str) -> str:
    return (
        token.replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .lower()
    )


def has_german_diacritic(token: str) -> bool:
    return any(char in token for char in "ÄÖÜäöüß")


def apply_token_case(preferred: str, pattern: str) -> str:
    lower = preferred.lower()
    if pattern.isupper():
        return lower.replace("ß", "ẞ").upper()
    if pattern.islower():
        return lower
    if len(pattern) > 1 and pattern[:1].isupper() and pattern[1:].islower():
        return lower[:1].upper() + lower[1:]
    return preferred


def build_display_token_corrections(
    stations: list[dict[str, object]],
) -> dict[str, str]:
    groups: dict[str, Counter[str]] = {}
    for station in stations:
        for field in ("name", "brand", "street", "city"):
            value = str(station.get(field) or "").strip()
            for token in TOKEN_RE.findall(value):
                normalized = normalize_german_token(token)
                groups.setdefault(normalized, Counter())[token] += 1

    corrections: dict[str, str] = {}
    for counter in groups.values():
        umlauted = Counter(
            {
                token: count
                for token, count in counter.items()
                if has_german_diacritic(token)
            }
        )
        ascii_variants = [token for token in counter if token.isascii()]
        if not umlauted or not ascii_variants:
            continue
        preferred = max(umlauted.items(), key=lambda item: (item[1], len(item[0])))[0]
        for token in ascii_variants:
            corrections[token] = apply_token_case(preferred, token)
    return corrections


def restore_german_spelling(value: object, corrections: dict[str, str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in corrections:
            return corrections[token]
        lowered = token.lower()
        if lowered in EXACT_TOKEN_FALLBACKS:
            return apply_token_case(EXACT_TOKEN_FALLBACKS[lowered], token)
        transliterated = lowered
        for source, target in TOKEN_FRAGMENT_FALLBACKS:
            transliterated = transliterated.replace(source, target)
        if transliterated != lowered:
            return apply_token_case(transliterated, token)
        if lowered.endswith("strasse"):
            stem = lowered[:-7]
            return apply_token_case(f"{stem}straße", token)
        return token

    return TOKEN_RE.sub(
        replace_token,
        text,
    )


def absolute_url(path: str) -> str:
    clean = path.lstrip("/")
    if not clean:
        return f"{SITE_ORIGIN}/"
    return f"{SITE_ORIGIN}/{clean}"


def station_page_path(station_id: str) -> str:
    return f"station/{station_id}.html"


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "tankstelle"


def legacy_station_page_path(name: str, station_id: str) -> str:
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


def has_noon_reset_stats(stats: dict[str, object] | None) -> bool:
    summary = fuel_summary(stats)
    if not summary:
        return False
    for key in (
        "noon_price_avg",
        "noon_price_median",
        "post_noon_decreases_avg",
        "post_noon_decreases_median",
        "post_noon_increases_avg",
        "post_noon_increases_median",
        "min_time_text",
        "min_duration_text",
    ):
        if key not in summary:
            continue
        value = summary.get(key)
        if isinstance(value, str):
            if value.strip():
                return True
            continue
        if value is not None:
            return True
    return False


def has_noon_reference_day_profile(stats: dict[str, object] | None) -> bool:
    if not stats or has_noon_reset_stats(stats):
        return False
    for key in ("analysis_end", "analysis_start"):
        value = stats.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            if date.fromisoformat(value) >= LAW_RESET_DATE:
                return True
        except ValueError:
            continue
    return False


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
    increases = _first_number(
        summary.get("post_noon_increases_avg"),
        summary.get("post_noon_increases_median"),
    )
    parts: list[str] = []
    if noon_price is not None:
        parts.append(f"12:00 {noon_price:.3f} €/l")
    if decreases is not None:
        parts.append(f"{decreases} Senkungen ab 12 Uhr")
    if increases is not None and increases > 0.05:
        parts.append(f"{_format_count(increases)} Erhöhungen ab 12 Uhr")
    return " · ".join(parts) if parts else "Kein Tagesprofil hinterlegt."


def fuel_minimum_text(stats: dict[str, object] | None) -> str:
    summary = fuel_summary(stats)
    if not summary:
        return "Kein Minimum im 12:00-11:59-Fenster hinterlegt."
    time_text = str(summary.get("min_time_text") or "").strip()
    duration_text = str(summary.get("min_duration_text") or "").strip()
    if time_text and duration_text:
        return f"{time_text} Uhr für {duration_text}"
    if time_text:
        return f"{time_text} Uhr"
    if duration_text:
        return duration_text
    return "Kein Minimum im 12:00-11:59-Fenster hinterlegt."


def fuel_chip_text(stats: dict[str, object] | None) -> str:
    summary = fuel_summary(stats)
    if has_noon_reset_stats(stats) and summary and summary.get("min_time_text"):
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
        if has_noon_reset_stats(stats):
            profile = fuel_profile_text(stats)
            minimum = fuel_minimum_text(stats)
            snippets.append(
                f"{fuel_label}: {profile}, Minimum im 12:00-11:59-Fenster meist {minimum}"
            )
        elif has_noon_reference_day_profile(stats):
            snippets.append(
                f"{fuel_label}: beste Stunden im Tagesprofil {fuel_best_text(stats)} ({POST_LAW_DAY_PROFILE_SHORT})"
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


def attribute_url(url: str) -> str:
    return html.escape(url, quote=True)


def build_legacy_alias_page(canonical_url: str) -> str:
    canonical_href = attribute_url(canonical_url)
    return f"""<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8" />
    <meta http-equiv="refresh" content="0; url={canonical_href}" />
    <link rel="canonical" href="{canonical_href}" />
    <meta name="robots" content="noindex,follow" />
    <title>Weiterleitung | tankzeit.de</title>
    <script>
      location.replace({json.dumps(canonical_url)});
    </script>
  </head>
  <body>
    <p>
      Diese Seite ist umgezogen.
      <a href="{canonical_href}">Zur aktuellen URL</a>
    </p>
  </body>
</html>
"""


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
        chart_url = attribute_url(
            fuel_chart_url(station_id, fuel, name, latitude, longitude)
        )
        if has_noon_reset_stats(stats):
            cards.append(
                "<article class=\"station-fuel-card\">"
                f"<h3>{FUEL_LABELS[fuel]}</h3>"
                f"<p><strong>12:00-Referenz:</strong> {format_text(fuel_profile_text(stats))}</p>"
                f"<p><strong>Minimum 12:00-11:59:</strong> {format_text(fuel_minimum_text(stats))}</p>"
                f"<p><strong>Historische Spanne:</strong> {format_text(fuel_range_text(stats))}</p>"
                f"<a class=\"link-btn secondary-link\" href=\"{chart_url}\">Chart öffnen</a>"
                "</article>"
            )
            continue
        if has_noon_reference_day_profile(stats):
            cards.append(
                "<article class=\"station-fuel-card\">"
                f"<h3>{FUEL_LABELS[fuel]}</h3>"
                f"<p><strong>Beste Zeit im Tagesprofil:</strong> {format_text(fuel_best_text(stats))}</p>"
                f"<p><strong>Referenz:</strong> {POST_LAW_DAY_PROFILE_LONG}</p>"
                f"<p><strong>Historische Spanne:</strong> {format_text(fuel_range_text(stats))}</p>"
                f"<a class=\"link-btn secondary-link\" href=\"{chart_url}\">Chart öffnen</a>"
                "</article>"
            )
            continue
        cards.append(
            "<article class=\"station-fuel-card\">"
            f"<h3>{FUEL_LABELS[fuel]}</h3>"
            f"<p><strong>Beste Zeit:</strong> {format_text(fuel_best_text(stats))}</p>"
            f"<p><strong>Historische Spanne:</strong> {format_text(fuel_range_text(stats))}</p>"
            f"<a class=\"link-btn secondary-link\" href=\"{chart_url}\">Chart öffnen</a>"
            "</article>"
        )
    return "".join(cards)


def build_station_page(
    station: dict[str, object],
    corrections: dict[str, str],
) -> tuple[str, str]:
    station_id = str(station.get("uuid") or "").strip()
    name = restore_german_spelling(station.get("name") or "Tankstelle", corrections)
    brand = restore_german_spelling(station.get("brand") or "", corrections)
    street = restore_german_spelling(
        " ".join(
            part
            for part in (
                str(station.get("street") or "").strip(),
                str(station.get("house_number") or "").strip(),
            )
            if part
        ).strip(),
        corrections,
    )
    postcode = str(station.get("post_code") or "").strip()
    city = restore_german_spelling(station.get("city") or "", corrections)
    latitude = float(station.get("latitude") or 0)
    longitude = float(station.get("longitude") or 0)
    canonical_path = station_page_path(station_id)
    canonical_url = absolute_url(canonical_path)
    google_maps_url = f"https://www.google.com/maps/dir/?api=1&destination={latitude},{longitude}"
    canonical_href = attribute_url(canonical_url)
    google_maps_href = attribute_url(google_maps_url)
    stats_by_fuel = load_station_stats(station_id)
    description = build_station_description(name, city, street, stats_by_fuel)
    brand_line = f"{brand} · " if brand else ""
    city_line = " ".join(part for part in (postcode, city) if part).strip()
    address_html = "<br />".join(
        part for part in (format_text(street), format_text(city_line)) if part
    )
    city_title = city or postcode or "Deutschland"
    diesel_chart_url = attribute_url(
        fuel_chart_url(station_id, "diesel", name, latitude, longitude)
    )
    e10_chart_url = attribute_url(
        fuel_chart_url(station_id, "e10", name, latitude, longitude)
    )
    e5_chart_url = attribute_url(
        fuel_chart_url(station_id, "e5", name, latitude, longitude)
    )
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
    structured_data["image"] = SITE_LOGO_URL
    has_noon_reset_station_stats = any(
        has_noon_reset_stats(stats_by_fuel.get(fuel)) for fuel in FUELS
    )
    has_noon_reference_day_station_stats = any(
        has_noon_reference_day_profile(stats_by_fuel.get(fuel)) for fuel in FUELS
    )

    page_html = f"""<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1" />
    <meta name="application-name" content="{SITE_NAME}" />
    <meta name="apple-mobile-web-app-title" content="Tankzeit" />
    <meta name="mobile-web-app-capable" content="yes" />
    <title>{format_text(name)} in {format_text(city_title)} | Tagesprofile für Diesel, E10 und E5 | tankzeit.de</title>
    <meta name="description" content="{format_text(description)}" />
    <link rel="canonical" href="{canonical_href}" />
    <link rel="icon" href="/favicon.ico" sizes="any" />
    <link rel="icon" type="image/png" sizes="192x192" href="/favicon-192.png" />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
    <link rel="manifest" href="/site.webmanifest" />
    <meta property="og:type" content="website" />
    <meta property="og:locale" content="de_DE" />
    <meta property="og:site_name" content="{SITE_NAME}" />
    <meta property="og:title" content="{format_text(name)} in {format_text(city_title)} | tankzeit.de" />
    <meta property="og:description" content="{format_text(description)}" />
    <meta property="og:url" content="{canonical_href}" />
    <meta property="og:image" content="{SITE_LOGO_URL}" />
    <meta property="og:image:width" content="512" />
    <meta property="og:image:height" content="512" />
    <meta property="og:image:alt" content="Tankzeit Logo" />
    <meta name="twitter:card" content="summary" />
    <meta name="twitter:title" content="{format_text(name)} in {format_text(city_title)} | tankzeit.de" />
    <meta name="twitter:description" content="{format_text(description)}" />
    <meta name="twitter:image" content="{SITE_LOGO_URL}" />
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
          {"Tankzeit zeigt für diese Tankstelle den 12:00-Referenzpreis, die Preisbewegungen im 12:00-11:59-Fenster und das dortige Minimum auf Basis der veröffentlichten Tankerkönig-Daten." if has_noon_reset_station_stats else "Tankzeit zeigt für diese Tankstelle das Tagesprofil mit geteilter 12:00-Referenz: 00:00-11:59 relativ zu Vortag 12:00, 12:00-23:59 relativ zum Tagespreis um 12:00. Vor- und Nachmittag sind daher nicht direkt als ein gemeinsames Minimum vergleichbar." if has_noon_reference_day_station_stats else "Tankzeit zeigt für diese Tankstelle historische Tagesprofile aus den veröffentlichten Tankerkönig-Daten. So findest du schneller die typischen Zeitfenster mit günstigeren Preisen."}
        </p>
        <div class="station-actions">
          <a class="link-btn" href="{diesel_chart_url}">Diesel-Chart</a>
          <a class="link-btn secondary-link" href="{e10_chart_url}">E10-Chart</a>
          <a class="link-btn secondary-link" href="{e5_chart_url}">E5-Chart</a>
          <a class="link-btn secondary-link" href="{google_maps_href}" target="_blank" rel="noopener noreferrer">Navigation</a>
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
    corrections = build_display_token_corrections(
        [station for station in stations if isinstance(station, dict)]
    )
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
        page_path, page_html = build_station_page(station, corrections)
        target = ROOT / page_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page_html, encoding="utf-8")
        legacy_path = legacy_station_page_path(name, station_id)
        if legacy_path != page_path:
            legacy_target = ROOT / legacy_path
            legacy_target.write_text(
                build_legacy_alias_page(absolute_url(page_path)),
                encoding="utf-8",
            )
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
