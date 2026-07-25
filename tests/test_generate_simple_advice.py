from __future__ import annotations

import json
import unittest
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from scripts.generate_simple_advice import (
    ProfileObservation,
    build_summary,
    collect_observations,
    extract_observation,
    write_outputs,
)


TARGET_DATE = date(2026, 7, 24)


def cycle_payload(prices: list[float]) -> dict[str, object]:
    return {
        "analysis_end": TARGET_DATE.isoformat(),
        "span": max(prices) - min(prices),
        "minabs": min(prices),
        "maxabs": max(prices),
        "cycle_hourly": [
            {
                "cycle_hour": cycle_hour,
                "clock_hour": (12 + cycle_hour) % 24,
                "price_median": price,
            }
            for cycle_hour, price in enumerate(prices)
        ],
    }


class SimpleAdviceTests(unittest.TestCase):
    def test_extracts_confirmed_11_oclock_minimum(self) -> None:
        prices = [2.20 - min(hour, 20) * 0.01 for hour in range(24)]
        prices[23] = 1.98

        observation, reason = extract_observation(
            cycle_payload(prices),
            station_uuid="station-1",
            fuel="diesel",
            target_date=TARGET_DATE,
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(observation)
        self.assertTrue(observation.confirmed)
        self.assertAlmostEqual(observation.raw_saving, 0.22)

    def test_flags_real_exception_without_hiding_negative_saving(self) -> None:
        prices = [2.00] * 24
        prices[8] = 1.90
        prices[23] = 2.05

        observation, reason = extract_observation(
            cycle_payload(prices),
            station_uuid="station-2",
            fuel="e10",
            target_date=TARGET_DATE,
        )

        self.assertIsNone(reason)
        self.assertFalse(observation.confirmed)
        self.assertAlmostEqual(observation.raw_saving, -0.05)
        self.assertEqual(observation.best_hour, 20)

    def test_includes_positive_flat_profile_as_zero_saving(self) -> None:
        payload = {
            "analysis_end": TARGET_DATE.isoformat(),
            "span": 0.0,
            "minabs": 1.989,
            "maxabs": 1.989,
            "cycle_hourly": [],
        }

        observation, reason = extract_observation(
            payload,
            station_uuid="trigema",
            fuel="diesel",
            target_date=TARGET_DATE,
        )

        self.assertIsNone(reason)
        self.assertTrue(observation.confirmed)
        self.assertEqual(observation.raw_saving, 0.0)
        self.assertEqual(observation.method, "flat_profile_inferred")

    def test_excludes_cycle_with_invalid_zero_price(self) -> None:
        prices = [2.00] * 24
        prices[4] = 0.0

        observation, reason = extract_observation(
            cycle_payload(prices),
            station_uuid="station-3",
            fuel="e5",
            target_date=TARGET_DATE,
        )

        self.assertIsNone(observation)
        self.assertEqual(reason, "invalid_price")

    def test_builds_guarded_consumer_savings_summary(self) -> None:
        observations = [
            ProfileObservation(
                "a",
                "diesel",
                TARGET_DATE,
                2.20,
                2.00,
                0.20,
                True,
                "cycle_11_start",
                11,
            ),
            ProfileObservation(
                "b",
                "diesel",
                TARGET_DATE,
                2.00,
                2.00,
                0.0,
                True,
                "flat_profile_inferred",
                None,
            ),
            ProfileObservation(
                "c",
                "e10",
                TARGET_DATE,
                2.00,
                2.05,
                -0.05,
                False,
                "cycle_11_start",
                20,
            ),
        ]
        exclusions = {fuel: Counter() for fuel in ("diesel", "e10", "e5")}
        exclusions["e5"]["invalid_price"] = 2

        summary = build_summary(
            observations,
            exclusions,
            TARGET_DATE,
            generated_at=datetime(2026, 7, 25, 2, 0, tzinfo=ZoneInfo("Europe/Berlin")),
        )

        self.assertEqual(summary["status"], "exceptions")
        self.assertEqual(summary["overall"]["eligible_station_fuels"], 3)
        self.assertEqual(summary["overall"]["confirmed_station_fuels"], 2)
        self.assertEqual(summary["overall"]["flat_profiles_inferred"], 1)
        self.assertEqual(
            summary["overall"]["savings_eur_per_liter"]["minimum"],
            0.0,
        )
        self.assertEqual(
            summary["overall"]["savings_eur_per_liter"]["maximum"],
            0.2,
        )
        self.assertEqual(summary["overall"]["excluded_station_fuels"], 2)
        distribution = summary["station_savings_distribution"]
        self.assertEqual(distribution["station_count"], 3)
        self.assertEqual(
            sum(bin_row["count"] for bin_row in distribution["bins"]),
            3,
        )
        self.assertEqual(
            distribution["savings_eur_per_liter"]["median"],
            0.0,
        )
        self.assertEqual(summary["cycle"]["start"], "2026-07-23T12:00:00+02:00")
        self.assertEqual(summary["cycle"]["end"], "2026-07-24T11:59:59+02:00")

    def test_collects_station_paths_and_writes_idempotent_history(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            station_dir = root / "data2" / "aaaaaaaa" / "bbbb" / "cccc" / "dddd" / "eeeeeeeeeeee"
            station_dir.mkdir(parents=True)
            payload = cycle_payload([2.10] * 23 + [1.90])
            (station_dir / "diesel.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )

            observations, exclusions = collect_observations(
                root / "data2",
                TARGET_DATE,
            )
            summary = build_summary(
                observations,
                exclusions,
                TARGET_DATE,
                generated_at=datetime(
                    2026,
                    7,
                    25,
                    2,
                    0,
                    tzinfo=ZoneInfo("Europe/Berlin"),
                ),
            )
            output_dir = root / "data" / "simple"
            write_outputs(summary, output_dir)
            write_outputs(summary, output_dir)

            self.assertTrue((output_dir / "latest.json").exists())
            self.assertTrue(
                (output_dir / "2026" / "07" / "24" / "advice.json").exists()
            )
            history_lines = (output_dir / "history.csv").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(history_lines), 5)


if __name__ == "__main__":
    unittest.main()
