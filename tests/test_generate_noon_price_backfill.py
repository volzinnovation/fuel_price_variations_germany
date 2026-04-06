import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from scripts.generate_noon_price_backfill import (
    build_noon_snapshot,
    _collect_history_rows,
    _merge_history_rows,
    _write_history_files,
)


class NoonReferenceSnapshotTests(unittest.TestCase):
    def test_build_noon_snapshot_prefers_first_daily_increase_timestamp_and_prices(self) -> None:
        prices = pd.DataFrame(
            {
                "station_uuid": [
                    "station-1",
                    "station-1",
                    "station-1",
                    "station-1",
                ],
                "date": pd.to_datetime(
                    [
                        "2026-04-04T21:30:00Z",
                        "2026-04-05T10:00:00Z",
                        "2026-04-05T10:01:43Z",
                        "2026-04-05T10:04:45Z",
                    ],
                    utc=True,
                ),
                "diesel": [1.70, 1.70, 1.77, 1.76],
                "e5": [1.80, 1.80, 1.87, 1.86],
                "e10": [1.75, 1.75, 1.82, 1.81],
            }
        )

        snapshot = build_noon_snapshot(prices, ["station-1"], date(2026, 4, 5))

        self.assertEqual(snapshot["station_uuid"].tolist(), ["station-1"])
        self.assertEqual(snapshot.loc[0, "diesel"], 1.77)
        self.assertEqual(snapshot.loc[0, "e5"], 1.87)
        self.assertEqual(snapshot.loc[0, "e10"], 1.82)
        self.assertEqual(snapshot.loc[0, "last_update"], "2026-04-05T12:01:43+02:00")

    def test_build_noon_snapshot_falls_back_to_price_valid_at_noon_and_sets_timestamp_to_1200(self) -> None:
        prices = pd.DataFrame(
            {
                "station_uuid": [
                    "station-1",
                    "station-1",
                    "station-1",
                ],
                "date": pd.to_datetime(
                    [
                        "2026-04-04T21:30:00Z",
                        "2026-04-05T09:20:00Z",
                        "2026-04-05T11:10:00Z",
                    ],
                    utc=True,
                ),
                "diesel": [1.70, 1.68, 1.66],
                "e5": [1.80, 1.78, 1.76],
                "e10": [1.75, 1.73, 1.71],
            }
        )

        snapshot = build_noon_snapshot(prices, ["station-1"], date(2026, 4, 5))

        self.assertEqual(snapshot["station_uuid"].tolist(), ["station-1"])
        self.assertEqual(snapshot.loc[0, "diesel"], 1.68)
        self.assertEqual(snapshot.loc[0, "e5"], 1.78)
        self.assertEqual(snapshot.loc[0, "e10"], 1.73)
        self.assertEqual(snapshot.loc[0, "last_update"], "2026-04-05T12:00:00+02:00")


class NoonHistoryTests(unittest.TestCase):
    def test_collect_and_write_history_files_per_station_and_fuel(self) -> None:
        rows_by_file: dict[tuple[str, str], list[dict[str, object]]] = {}
        snapshot = pd.DataFrame(
            {
                "station_uuid": ["00006210-0037-4444-8888-acdc00006210"],
                "diesel": [2.429],
                "e5": [2.229],
                "e10": [2.169],
                "last_update": ["2026-04-05T07:47:18+02:00"],
            }
        )

        _collect_history_rows(
            rows_by_file,
            snapshot,
            target_day=date(2026, 4, 5),
            history_start_date=date(2026, 4, 1),
        )

        with TemporaryDirectory() as tmpdir:
            written_paths = _write_history_files(Path(tmpdir), rows_by_file)
            diesel_history = (
                Path(tmpdir)
                / "data2"
                / "00006210"
                / "0037"
                / "4444"
                / "8888"
                / "acdc00006210"
                / "diesel"
                / "history.csv"
            )

            self.assertIn(diesel_history, written_paths)
            history = pd.read_csv(diesel_history)
            self.assertEqual(history.to_dict(orient="records"), [
                {
                    "date": "2026-04-05",
                    "price": 2.429,
                    "last_update": "2026-04-05T07:47:18+02:00",
                }
            ])

    def test_collect_history_rows_skips_days_before_history_start_date(self) -> None:
        rows_by_file: dict[tuple[str, str], list[dict[str, object]]] = {}
        snapshot = pd.DataFrame(
            {
                "station_uuid": ["station-1"],
                "diesel": [1.77],
                "e5": [1.87],
                "e10": [1.82],
                "last_update": ["2026-03-31T12:00:00+02:00"],
            }
        )

        _collect_history_rows(
            rows_by_file,
            snapshot,
            target_day=date(2026, 3, 31),
            history_start_date=date(2026, 4, 1),
        )

        self.assertEqual(rows_by_file, {})

    def test_merge_history_rows_replaces_existing_day_with_latest_row(self) -> None:
        existing = pd.DataFrame(
            [
                {
                    "date": "2026-04-04",
                    "price": 2.119,
                    "last_update": "2026-04-04T12:00:00+02:00",
                },
                {
                    "date": "2026-04-05",
                    "price": 2.129,
                    "last_update": "2026-04-05T12:00:00+02:00",
                },
            ]
        )
        additions = pd.DataFrame(
            [
                {
                    "date": "2026-04-05",
                    "price": 2.139,
                    "last_update": "2026-04-05T12:01:43+02:00",
                },
                {
                    "date": "2026-04-06",
                    "price": 2.149,
                    "last_update": "2026-04-06T12:00:00+02:00",
                },
            ]
        )

        merged = _merge_history_rows(existing, additions)

        self.assertEqual(merged.to_dict(orient="records"), [
            {
                "date": "2026-04-04",
                "price": 2.119,
                "last_update": "2026-04-04T12:00:00+02:00",
            },
            {
                "date": "2026-04-05",
                "price": 2.139,
                "last_update": "2026-04-05T12:01:43+02:00",
            },
            {
                "date": "2026-04-06",
                "price": 2.149,
                "last_update": "2026-04-06T12:00:00+02:00",
            },
        ])


if __name__ == "__main__":
    unittest.main()
