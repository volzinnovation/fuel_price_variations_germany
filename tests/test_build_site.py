import unittest
from unittest.mock import patch

from scripts.build_site import build_station_page, has_noon_reset_stats


def _station() -> dict[str, object]:
    return {
        "uuid": "station-1",
        "name": "Beispiel Tankstelle",
        "brand": "Beispiel",
        "street": "Hauptstraße",
        "house_number": "1",
        "post_code": "12345",
        "city": "Berlin",
        "latitude": 52.5,
        "longitude": 13.4,
    }


def _generic_stats() -> dict[str, object]:
    return {
        "text": "18 - 24h",
        "besthours": [18, 19, 20, 21, 22, 23],
        "minabs": 2.139,
        "maxabs": 2.219,
        "span": 0.08,
        "law_effective_date": "2026-04-01",
        "analysis_start": "2026-04-08",
        "analysis_end": "2026-04-08",
        "analysis_days": 1,
        "summary": {
            "days": 0,
            "law_effective_date": "2026-04-01",
            "analysis_start": None,
            "analysis_end": None,
            "analysis_days": 1,
        },
    }


def _noon_reset_stats() -> dict[str, object]:
    return {
        "text": "12 - 24h",
        "besthours": list(range(12, 24)),
        "minabs": 2.109,
        "maxabs": 2.159,
        "span": 0.05,
        "law_effective_date": "2026-04-01",
        "analysis_start": "2026-04-05",
        "analysis_end": "2026-04-06",
        "analysis_days": 2,
        "summary": {
            "days": 1,
            "law_effective_date": "2026-04-01",
            "analysis_start": "2026-04-05",
            "analysis_end": "2026-04-05",
            "analysis_days": 2,
            "noon_price_median": 2.159,
            "post_noon_decreases_median": 0,
            "post_noon_increases_median": 0,
            "min_time_text": "12:00",
            "min_duration_text": "24h",
        },
    }


class BuildSiteTests(unittest.TestCase):
    def test_station_page_uses_large_social_card_metadata(self) -> None:
        stats_by_fuel = {
            "diesel": _generic_stats(),
            "e10": _generic_stats(),
            "e5": _generic_stats(),
        }

        with patch("scripts.build_site.load_station_stats", return_value=stats_by_fuel):
            _path, html = build_station_page(_station(), {})

        self.assertIn('<meta name="twitter:card" content="summary_large_image" />', html)
        self.assertIn('content="https://tankzeit.de/img/social-card.png"', html)
        self.assertIn(
            'content="Diesel im Tagesprofil meist günstig: 18 - 24h. Direktlinks zu Diesel-, E10- und E5-Charts auf tankzeit.de."',
            html,
        )
        self.assertIn('name="twitter:image:alt"', html)

    def test_metadata_only_summary_does_not_enable_noon_reset_copy(self) -> None:
        stats_by_fuel = {
            "diesel": _generic_stats(),
            "e10": _generic_stats(),
            "e5": _generic_stats(),
        }

        self.assertFalse(has_noon_reset_stats(stats_by_fuel["diesel"]))

        with patch("scripts.build_site.load_station_stats", return_value=stats_by_fuel):
            _path, html = build_station_page(_station(), {})

        self.assertIn("Diesel: 18 - 24h", html)
        self.assertIn("<strong>Beste Zeit im Tagesprofil:</strong> 18 - 24h", html)
        self.assertIn("Tagesprofil mit geteilter 12:00-Referenz", html)
        self.assertIn("Vor- und Nachmittag sind daher nicht direkt als ein gemeinsames Minimum vergleichbar.", html)
        self.assertIn("00:00-11:59 ggü. Vortag 12:00, 12:00-23:59 ggü. Tages-12:00", html)
        self.assertIn("00:00-11:59 relativ zu Vortag 12:00, 12:00-23:59 relativ zum Tagespreis um 12:00", html)
        self.assertNotIn("<strong>12:00-Referenz:</strong>", html)
        self.assertNotIn("Minimum 12:00-11:59", html)
        self.assertNotIn("historische Tagesprofile", html)

    def test_detailed_noon_reset_summary_keeps_noon_reset_copy(self) -> None:
        stats_by_fuel = {
            "diesel": _noon_reset_stats(),
            "e10": _noon_reset_stats(),
            "e5": _noon_reset_stats(),
        }

        self.assertTrue(has_noon_reset_stats(stats_by_fuel["diesel"]))

        with patch("scripts.build_site.load_station_stats", return_value=stats_by_fuel):
            _path, html = build_station_page(_station(), {})

        self.assertIn("12:00-Referenz", html)
        self.assertIn("Minimum 12:00-11:59", html)
        self.assertIn("Minimum meist 12:00 Uhr für 24h", html)
        self.assertIn("den 12:00-Referenzpreis", html)

    def test_station_page_offers_default_navigation_app_link(self) -> None:
        stats_by_fuel = {
            "diesel": _generic_stats(),
            "e10": _generic_stats(),
            "e5": _generic_stats(),
        }

        with patch("scripts.build_site.load_station_stats", return_value=stats_by_fuel):
            _path, html = build_station_page(_station(), {})

        self.assertIn(
            'href="geo:52.5,13.4?q=52.5,13.4(Beispiel%20Tankstelle)"',
            html,
        )
        self.assertIn(">Navigation-App</a>", html)
        self.assertIn(">Google Maps</a>", html)


if __name__ == "__main__":
    unittest.main()
