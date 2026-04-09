import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.verify_live_generated_data import (
    NoonReference,
    cycle_anchor_delta,
    cycle_anchor_price,
    default_target_date,
    iter_delayed_noon_references,
    sample_references,
    station_json_path,
    verify_station_payload,
)


class VerifyLiveGeneratedDataTests(unittest.TestCase):
    def test_default_target_date_uses_berlin_yesterday(self) -> None:
        now = datetime.fromisoformat("2026-04-09T00:15:00+00:00")
        self.assertEqual(default_target_date(now), date(2026, 4, 8))

    def test_iter_delayed_noon_references_only_keeps_post_noon_increases(self) -> None:
        rows = [
            {
                "station_uuid": "station-a",
                "diesel": "2.449",
                "diesel_last_update": "2026-04-08T12:05:15+02:00",
                "diesel_selection_method": "increase",
                "e10": "2.179",
                "e10_last_update": "2026-04-08T12:00:00+02:00",
                "e10_selection_method": "increase",
                "e5": "2.239",
                "e5_last_update": "2026-04-08T11:58:00+02:00",
                "e5_selection_method": "fallback",
            },
            {
                "station_uuid": "station-b",
                "diesel": "2.419",
                "diesel_last_update": "2026-04-08T12:14:00+02:00",
                "diesel_selection_method": "increase",
                "e10": "2.149",
                "e10_last_update": "2026-04-08T12:16:00+02:00",
                "e10_selection_method": "increase",
                "e5": "2.209",
                "e5_last_update": "2026-04-08T12:10:00+02:00",
                "e5_selection_method": "increase",
            },
        ]

        references = iter_delayed_noon_references(rows)

        self.assertEqual(
            [(reference.station_uuid, reference.fuel) for reference in references],
            [
                ("station-b", "diesel"),
                ("station-a", "diesel"),
                ("station-b", "e10"),
                ("station-b", "e5"),
            ],
        )
        self.assertEqual(references[0].delay_minutes, 14)

    def test_sample_references_limits_each_fuel(self) -> None:
        references = [
            NoonReference("a", "diesel", 2.4, datetime.fromisoformat("2026-04-08T12:14:00+02:00"), "increase"),
            NoonReference("b", "diesel", 2.4, datetime.fromisoformat("2026-04-08T12:10:00+02:00"), "increase"),
            NoonReference("c", "e10", 2.2, datetime.fromisoformat("2026-04-08T12:12:00+02:00"), "increase"),
            NoonReference("d", "e10", 2.2, datetime.fromisoformat("2026-04-08T12:11:00+02:00"), "increase"),
        ]

        sampled = sample_references(references, per_fuel=1)

        self.assertEqual(
            [(reference.station_uuid, reference.fuel) for reference in sampled],
            [("a", "diesel"), ("c", "e10")],
        )

    def test_station_json_path_uses_uuid_segments(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = station_json_path(root, "4152d0cf-65a3-4656-b2de-d09e00d0bbda", "diesel")
            self.assertEqual(
                path,
                root / "data2" / "4152d0cf" / "65a3" / "4656" / "b2de" / "d09e00d0bbda" / "diesel.json",
            )

    def test_cycle_anchor_helpers_read_first_cycle_row(self) -> None:
        payload = {
            "cycle_hourly": [
                {
                    "price_median": 2.449,
                    "delta_median": 0.0,
                }
            ]
        }
        self.assertEqual(cycle_anchor_price(payload), 2.449)
        self.assertEqual(cycle_anchor_delta(payload), 0.0)

    def test_verify_station_payload_accepts_matching_anchor(self) -> None:
        reference = NoonReference(
            station_uuid="station-a",
            fuel="diesel",
            price=2.449,
            last_update=datetime.fromisoformat("2026-04-08T12:05:15+02:00"),
            selection_method="increase",
        )
        payload = {
            "cycle_hourly": [
                {
                    "price_median": 2.449,
                    "delta_median": 0.0,
                }
            ]
        }
        verify_station_payload(reference, payload, "test")

    def test_verify_station_payload_rejects_anchor_mismatch(self) -> None:
        reference = NoonReference(
            station_uuid="station-a",
            fuel="diesel",
            price=2.449,
            last_update=datetime.fromisoformat("2026-04-08T12:05:15+02:00"),
            selection_method="increase",
        )
        payload = {
            "cycle_hourly": [
                {
                    "price_median": 2.389,
                    "delta_median": 0.0,
                }
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "anchor mismatch"):
            verify_station_payload(reference, payload, "test")
