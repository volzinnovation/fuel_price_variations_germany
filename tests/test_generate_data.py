import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from scripts.generate_data import (
    DateRange,
    _daily_noon_reset_metrics,
    _load_prices,
    _noon_to_noon_markdown_profile,
)


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


class DailyNoonResetMetricTests(unittest.TestCase):
    def build_series(self) -> pd.Series:
        return pd.Series(
            [1.70, 1.68, 1.75, 1.72, 1.70, 1.69, 1.67, 1.74, 1.70, 1.68],
            index=pd.DatetimeIndex(
                [
                    "2026-03-31 23:40",
                    "2026-04-01 08:00",
                    "2026-04-01 12:00",
                    "2026-04-01 15:30",
                    "2026-04-01 18:00",
                    "2026-04-01 22:15",
                    "2026-04-02 10:00",
                    "2026-04-02 12:00",
                    "2026-04-02 16:00",
                    "2026-04-02 20:00",
                ],
                tz="Europe/Berlin",
            ),
        )

    def test_daily_metrics_carry_forward_midnight_price_and_track_minimum_window(self) -> None:
        series = self.build_series()

        daily, summary = _daily_noon_reset_metrics(
            series,
            [date(2026, 4, 1), date(2026, 4, 2)],
        )

        self.assertEqual(len(daily), 2)

        self.assertEqual(daily[0]["date"], "2026-04-01")
        self.assertEqual(daily[0]["midnight_price"], 1.7)
        self.assertEqual(daily[0]["noon_price"], 1.75)
        self.assertEqual(daily[0]["post_noon_decreases"], 3)
        self.assertEqual(daily[0]["min_time_text"], "08:00")
        self.assertEqual(daily[0]["min_duration_minutes"], 240)
        self.assertEqual(daily[0]["min_duration_text"], "4h")

        self.assertEqual(daily[1]["date"], "2026-04-02")
        self.assertEqual(daily[1]["midnight_price"], 1.69)
        self.assertEqual(daily[1]["noon_price"], 1.74)
        self.assertEqual(daily[1]["post_noon_decreases"], 2)
        self.assertEqual(daily[1]["min_time_text"], "10:00")
        self.assertEqual(daily[1]["min_duration_minutes"], 120)
        self.assertEqual(daily[1]["min_duration_text"], "2h")

        self.assertEqual(summary["days"], 2)
        self.assertEqual(summary["analysis_start"], "2026-04-01")
        self.assertEqual(summary["analysis_end"], "2026-04-02")
        self.assertEqual(summary["noon_price_avg"], 1.745)
        self.assertEqual(summary["post_noon_decreases_avg"], 2.5)
        self.assertEqual(summary["min_time_text"], "09:00")
        self.assertEqual(summary["min_duration_text"], "3h")

    def test_cycle_profile_tracks_markdown_from_previous_noon_across_24_hours(self) -> None:
        cycle_hourly, cycle_summary = _noon_to_noon_markdown_profile(
            self.build_series(),
            [date(2026, 4, 1), date(2026, 4, 2)],
        )

        self.assertEqual(cycle_summary["days"], 1)
        self.assertEqual(cycle_summary["cycle_start"], "2026-04-01")
        self.assertEqual(cycle_summary["cycle_end"], "2026-04-02")
        self.assertEqual(len(cycle_hourly), 25)

        first = cycle_hourly[0]
        late_afternoon = cycle_hourly[4]
        late_evening = cycle_hourly[11]
        next_morning = cycle_hourly[22]
        next_noon = cycle_hourly[24]

        self.assertEqual(first["label"], "12")
        self.assertEqual(first["markdown_median"], 0.0)
        self.assertEqual(late_afternoon["label"], "16")
        self.assertEqual(late_afternoon["markdown_median"], 0.03)
        self.assertEqual(late_evening["label"], "23")
        self.assertEqual(late_evening["markdown_median"], 0.06)
        self.assertEqual(next_morning["label"], "10")
        self.assertEqual(next_morning["markdown_median"], 0.08)
        self.assertEqual(next_noon["label"], "12")
        self.assertEqual(next_noon["markdown_median"], 0.01)


if __name__ == "__main__":
    unittest.main()
