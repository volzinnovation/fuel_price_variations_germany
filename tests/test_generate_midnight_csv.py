import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from scripts.generate_midnight_csv import build_midnight_snapshot, generate_midnight_csv


class MidnightSnapshotTests(unittest.TestCase):
    def test_build_midnight_snapshot_uses_last_price_valid_at_midnight(self) -> None:
        prices = pd.DataFrame(
            {
                "station_uuid": [
                    "station-1",
                    "station-1",
                    "station-1",
                    "station-2",
                    "station-2",
                    "station-2",
                    "station-3",
                ],
                "date": pd.to_datetime(
                    [
                        "2026-03-30T21:30:00Z",
                        "2026-03-30T22:00:00Z",
                        "2026-03-31T00:15:00Z",
                        "2026-03-30T21:59:00Z",
                        "2026-03-30T22:00:00Z",
                        "2026-03-31T01:00:00Z",
                        "2026-03-30T18:00:00Z",
                    ],
                    utc=True,
                ),
                "diesel": [1.60, 1.59, 1.58, 1.70, 1.69, 1.68, None],
                "e5": [1.70, 1.69, 1.68, 1.80, None, 1.78, 1.90],
                "e10": [1.65, 1.64, 1.63, None, 1.74, 1.73, 1.85],
            }
        )

        snapshot = build_midnight_snapshot(
            prices,
            ["station-3", "station-2", "station-1"],
            date(2026, 3, 31),
        )

        self.assertEqual(snapshot["station_uuid"].tolist(), ["station-1", "station-2"])
        self.assertEqual(snapshot.loc[0, "diesel"], 1.59)
        self.assertEqual(snapshot.loc[0, "e5"], 1.69)
        self.assertEqual(snapshot.loc[0, "e10"], 1.64)
        self.assertEqual(snapshot.loc[1, "diesel"], 1.69)
        self.assertEqual(snapshot.loc[1, "e5"], 1.8)
        self.assertEqual(snapshot.loc[1, "e10"], 1.74)
        self.assertEqual(len(snapshot), 2)

    @patch("scripts.generate_midnight_csv._load_price_window")
    @patch("scripts.generate_midnight_csv._load_station_ids")
    def test_generate_midnight_csv_drops_rows_with_missing_or_zero_prices(
        self,
        mock_load_station_ids,
        mock_load_price_window,
    ) -> None:
        mock_load_station_ids.return_value = ["station-3", "station-2", "station-1"]
        mock_load_price_window.return_value = pd.DataFrame(
            {
                "station_uuid": ["station-1", "station-2", "station-3"],
                "date": pd.to_datetime(
                    ["2026-03-30T22:00:00Z", "2026-03-30T22:00:00Z", "2026-03-30T22:00:00Z"],
                    utc=True,
                ),
                "diesel": [1.599, 1.699, 0.0],
                "e5": [1.699, 1.799, 1.899],
                "e10": [1.649, None, 1.849],
            }
        )

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "midnight.csv"
            generate_midnight_csv(output_path, target_day=date(2026, 3, 31))
            written = output_path.read_text(encoding="utf-8").strip().splitlines()

        self.assertEqual(written[0], "station_uuid,diesel,e5,e10")
        self.assertEqual(written[1], "station-1,1.599,1.699,1.649")
        self.assertEqual(len(written), 2)


if __name__ == "__main__":
    unittest.main()
