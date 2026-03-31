import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from scripts.generate_data import DateRange, _load_prices


class LoadPricesTests(unittest.TestCase):
    @patch("scripts.generate_data._read_csv_from_url")
    def test_load_prices_normalizes_mixed_dst_offsets_to_utc(self, mock_read_csv) -> None:
        mock_read_csv.return_value = pd.DataFrame(
            {
                "date": [
                    "2026-03-29T01:30:00+01:00",
                    "2026-03-29T03:30:00+02:00",
                ],
                "station_uuid": ["station-1", "station-1"],
            }
        )

        data = _load_prices(DateRange(date(2026, 3, 29), date(2026, 3, 29)))

        self.assertEqual(str(data["date"].dt.tz), "UTC")
        self.assertTrue(data["date"].is_monotonic_increasing)
        self.assertEqual(
            data["date"].dt.strftime("%Y-%m-%dT%H:%M:%S%z").tolist(),
            [
                "2026-03-29T00:30:00+0000",
                "2026-03-29T01:30:00+0000",
            ],
        )


if __name__ == "__main__":
    unittest.main()
