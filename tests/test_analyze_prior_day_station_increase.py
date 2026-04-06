import unittest
from datetime import date

import pandas as pd

from scripts.analyze_prior_day_station_increase import (
    build_first_daily_increase_events,
    build_raw_noon_snapshot,
    build_validation_rows,
)


class PriorDayIncreaseEventTests(unittest.TestCase):
    def test_build_first_daily_increase_events_uses_previous_day_baseline_and_first_positive_change(self) -> None:
        prices = pd.DataFrame(
            {
                "station_uuid": [
                    "station-1",
                    "station-1",
                    "station-1",
                    "station-1",
                    "station-2",
                    "station-2",
                    "station-2",
                ],
                "date": pd.to_datetime(
                    [
                        "2026-04-04T21:30:00Z",
                        "2026-04-05T07:00:00Z",
                        "2026-04-05T08:15:00Z",
                        "2026-04-05T11:45:00Z",
                        "2026-04-04T21:40:00Z",
                        "2026-04-05T10:00:00Z",
                        "2026-04-05T11:55:00Z",
                    ],
                    utc=True,
                ),
                "diesel": [1.50, 1.50, 1.53, 1.51, 1.60, 1.60, 1.60],
                "e5": [1.60, 1.60, 1.60, 1.64, 1.70, 1.72, 1.73],
                "e10": [1.55, 1.55, 1.58, 1.58, 1.65, 1.64, 1.66],
            }
        )

        events = build_first_daily_increase_events(prices, date(2026, 4, 5))

        diesel = events.loc[(events["station_uuid"] == "station-1") & (events["fuel"] == "diesel")].iloc[0]
        self.assertEqual(diesel["increase_time_local"], "2026-04-05T10:15:00+02:00")
        self.assertEqual(diesel["prior_price"], 1.50)
        self.assertEqual(diesel["increase_price"], 1.53)
        self.assertEqual(diesel["delta_cents"], 3.0)
        self.assertTrue(diesel["increase_before_or_at_noon"])

        e5 = events.loc[(events["station_uuid"] == "station-2") & (events["fuel"] == "e5")].iloc[0]
        self.assertEqual(e5["increase_time_local"], "2026-04-05T12:00:00+02:00")
        self.assertTrue(e5["increase_before_or_at_noon"])

        self.assertFalse(
            ((events["station_uuid"] == "station-2") & (events["fuel"] == "diesel")).any()
        )


class PriorDayIncreaseValidationTests(unittest.TestCase):
    def test_build_validation_rows_distinguishes_exact_match_and_overwritten_event(self) -> None:
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
                    "station-3",
                ],
                "date": pd.to_datetime(
                    [
                        "2026-04-04T21:30:00Z",
                        "2026-04-05T08:15:00Z",
                        "2026-04-05T09:30:00Z",
                        "2026-04-04T21:40:00Z",
                        "2026-04-05T09:00:00Z",
                        "2026-04-05T10:00:00Z",
                        "2026-04-04T21:10:00Z",
                        "2026-04-05T11:15:00Z",
                    ],
                    utc=True,
                ),
                "diesel": [1.50, 1.53, 1.51, 1.60, 1.60, 1.63, 1.70, 1.72],
                "e5": [1.60, 1.60, 1.60, 1.70, 1.70, 1.70, 1.80, 1.80],
                "e10": [1.55, 1.55, 1.55, 1.65, 1.65, 1.65, 1.75, 1.75],
            }
        )
        events = build_first_daily_increase_events(prices, date(2026, 4, 5))
        raw_noon = build_raw_noon_snapshot(prices, date(2026, 4, 5))
        noon_snapshot = pd.DataFrame(
            {
                "station_uuid": ["station-1", "station-2", "station-3"],
                "diesel": [1.51, 1.63, 1.72],
                "e5": [1.60, 1.70, 1.80],
                "e10": [1.55, 1.65, 1.75],
                "last_update": [
                    "2026-04-05T11:30:00+02:00",
                    "2026-04-05T12:00:00+02:00",
                    "2026-04-04T23:10:00+02:00",
                ],
            }
        )
        noon_snapshot["last_update_ts"] = pd.to_datetime(noon_snapshot["last_update"], utc=True)

        validated = build_validation_rows(
            events,
            raw_noon_snapshot=raw_noon,
            noon_snapshot=noon_snapshot,
            noon_available=True,
        )

        station_1 = validated.loc[
            (validated["station_uuid"] == "station-1") & (validated["fuel"] == "diesel")
        ].iloc[0]
        self.assertEqual(station_1["noon_csv_snapshot_status"], "match")
        self.assertEqual(station_1["event_visibility_status"], "overwritten_before_noon")
        self.assertFalse(station_1["noon_csv_matches_exact_event"])

        station_2 = validated.loc[
            (validated["station_uuid"] == "station-2") & (validated["fuel"] == "diesel")
        ].iloc[0]
        self.assertEqual(station_2["noon_csv_snapshot_status"], "match")
        self.assertEqual(station_2["event_visibility_status"], "exact_event_match")
        self.assertTrue(station_2["noon_csv_matches_exact_event"])

        station_3 = validated.loc[
            (validated["station_uuid"] == "station-3") & (validated["fuel"] == "diesel")
        ].iloc[0]
        self.assertEqual(station_3["event_visibility_status"], "after_noon")


if __name__ == "__main__":
    unittest.main()
