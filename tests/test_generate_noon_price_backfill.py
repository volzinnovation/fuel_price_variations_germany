import unittest
from datetime import date

import pandas as pd

from scripts.generate_noon_price_backfill import build_noon_snapshot


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


if __name__ == "__main__":
    unittest.main()
