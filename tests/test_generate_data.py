import unittest
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from scripts.generate_data import (
    DateRange,
    _brand_distribution_summary,
    _daily_noon_reset_metrics,
    _hourly_variation,
    _load_prices,
    _noon_to_noon_markdown_profile,
    generate,
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

    def test_daily_metrics_use_completed_noon_cycle_and_first_day_midnight_reference(self) -> None:
        series = self.build_series()

        daily, summary = _daily_noon_reset_metrics(
            series,
            [date(2026, 4, 1), date(2026, 4, 2)],
        )

        self.assertEqual(len(daily), 1)

        self.assertEqual(daily[0]["date"], "2026-04-01")
        self.assertEqual(daily[0]["window_kind"], "full")
        self.assertEqual(daily[0]["noon_price"], 1.75)
        self.assertEqual(daily[0]["prior_reference_label"], "00:00")
        self.assertEqual(daily[0]["prior_reference_price"], 1.7)
        self.assertEqual(daily[0]["window_end_timestamp"], "2026-04-02T11:59+02:00")
        self.assertEqual(daily[0]["max_price_delta_vs_prior"], 0.05)
        self.assertEqual(daily[0]["post_noon_decreases"], 4)
        self.assertEqual(daily[0]["min_time_text"], "10:00")
        self.assertEqual(daily[0]["min_duration_minutes"], 120)
        self.assertEqual(daily[0]["min_duration_text"], "2h")

        self.assertEqual(summary["days"], 1)
        self.assertEqual(summary["analysis_start"], "2026-04-01")
        self.assertEqual(summary["analysis_end"], "2026-04-01")
        self.assertEqual(summary["full_cycles"], 1)
        self.assertEqual(summary["partial_cycles"], 0)
        self.assertEqual(summary["noon_price_avg"], 1.75)
        self.assertEqual(summary["prior_reference_price_avg"], 1.7)
        self.assertEqual(summary["max_price_delta_vs_prior_avg"], 0.05)
        self.assertEqual(summary["post_noon_decreases_avg"], 4.0)
        self.assertEqual(summary["min_time_text"], "10:00")
        self.assertEqual(summary["min_duration_text"], "2h")

    def test_daily_metrics_fall_back_to_partial_cycle_before_first_completed_noon_window(self) -> None:
        daily, summary = _daily_noon_reset_metrics(
            self.build_series(),
            [date(2026, 4, 1)],
        )

        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["window_kind"], "partial")
        self.assertEqual(daily[0]["prior_reference_label"], "00:00")
        self.assertEqual(daily[0]["post_noon_decreases"], 3)
        self.assertEqual(daily[0]["min_price"], 1.69)
        self.assertEqual(daily[0]["min_time_text"], "22:15")
        self.assertEqual(daily[0]["min_duration_minutes"], 105)
        self.assertEqual(summary["partial_cycles"], 1)
        self.assertEqual(summary["full_cycles"], 0)

    def test_daily_metrics_exclude_next_noon_from_previous_cycle(self) -> None:
        series = pd.Series(
            [1.70, 1.75, 1.67, 1.60, 1.62],
            index=pd.DatetimeIndex(
                [
                    "2026-03-31 23:40",
                    "2026-04-01 12:00",
                    "2026-04-02 10:00",
                    "2026-04-02 12:00",
                    "2026-04-02 13:00",
                ],
                tz="Europe/Berlin",
            ),
        )

        daily, _ = _daily_noon_reset_metrics(
            series,
            [date(2026, 4, 1), date(2026, 4, 2)],
        )

        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["window_end_timestamp"], "2026-04-02T11:59+02:00")
        self.assertEqual(daily[0]["min_price"], 1.67)
        self.assertEqual(daily[0]["min_time_text"], "10:00")

    def test_daily_metrics_track_actual_post_noon_maximum_and_increases(self) -> None:
        series = pd.Series(
            [1.70, 1.75, 1.78, 1.74, 1.69],
            index=pd.DatetimeIndex(
                [
                    "2026-03-31 12:00",
                    "2026-04-01 12:00",
                    "2026-04-01 13:15",
                    "2026-04-01 18:00",
                    "2026-04-02 09:30",
                ],
                tz="Europe/Berlin",
            ),
        )

        daily, summary = _daily_noon_reset_metrics(
            series,
            [date(2026, 4, 1), date(2026, 4, 2)],
        )

        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["noon_price"], 1.75)
        self.assertEqual(daily[0]["max_price"], 1.78)
        self.assertEqual(daily[0]["max_price_delta_vs_prior"], 0.08)
        self.assertEqual(daily[0]["post_noon_decreases"], 2)
        self.assertEqual(daily[0]["post_noon_increases"], 1)
        self.assertEqual(daily[0]["daily_range"], 0.09)
        self.assertEqual(summary["max_price_avg"], 1.78)
        self.assertEqual(summary["post_noon_increases_avg"], 1.0)

    def test_cycle_profile_tracks_markdown_from_previous_noon_across_24_hours(self) -> None:
        cycle_hourly, cycle_summary = _noon_to_noon_markdown_profile(
            self.build_series(),
            [date(2026, 4, 1), date(2026, 4, 2)],
        )

        self.assertEqual(cycle_summary["days"], 1)
        self.assertEqual(cycle_summary["cycle_start"], "2026-04-01")
        self.assertEqual(cycle_summary["cycle_end"], "2026-04-02")
        self.assertFalse(cycle_summary["partial"])
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

    def test_cycle_profile_exposes_partial_interim_window_until_midnight(self) -> None:
        cycle_hourly, cycle_summary = _noon_to_noon_markdown_profile(
            self.build_series(),
            [date(2026, 4, 1)],
        )

        self.assertEqual(cycle_summary["days"], 1)
        self.assertTrue(cycle_summary["partial"])
        self.assertEqual(cycle_summary["cycle_start"], "2026-04-01")
        self.assertEqual(cycle_summary["cycle_end"], "2026-04-02")
        self.assertEqual(cycle_summary["last_label"], "00")
        self.assertEqual(len(cycle_hourly), 13)
        self.assertEqual(cycle_hourly[0]["label"], "12")
        self.assertEqual(cycle_hourly[-1]["label"], "00")
        self.assertEqual(cycle_hourly[-1]["markdown_median"], 0.06)

    def test_hourly_variation_uses_noon_reference_before_and_after_noon(self) -> None:
        series = pd.Series(
            [2.00, 1.95, 2.10, 2.05],
            index=pd.DatetimeIndex(
                [
                    "2026-04-01 12:00",
                    "2026-04-02 00:00",
                    "2026-04-02 12:00",
                    "2026-04-02 13:00",
                ],
                tz="Europe/Berlin",
            ),
        )

        hourly, best_hourly, _, _, used_days, _ = _hourly_variation(
            series,
            window_start=pd.Timestamp("2026-04-02 00:00").to_pydatetime(),
            window_end=pd.Timestamp("2026-04-02 23:59").to_pydatetime(),
            analysis_days=[date(2026, 4, 2)],
            noon_reference_prices={
                date(2026, 4, 1): 2.00,
                date(2026, 4, 2): 2.10,
            },
        )

        self.assertEqual(used_days, 1)
        self.assertEqual(hourly.loc[hourly["hour"] == 0, "price"].item(), -0.05)
        self.assertEqual(hourly.loc[hourly["hour"] == 12, "price"].item(), 0.0)
        self.assertEqual(hourly.loc[hourly["hour"] == 13, "price"].item(), -0.05)
        self.assertEqual(best_hourly.loc[best_hourly["hour"] == 0, "price"].item(), 1.95)

    def test_hourly_variation_uses_midnight_reference_before_noon_on_law_effective_day(self) -> None:
        series = pd.Series(
            [2.20, 2.00, 1.95, 2.10],
            index=pd.DatetimeIndex(
                [
                    "2026-03-31 12:00",
                    "2026-04-01 00:00",
                    "2026-04-01 08:00",
                    "2026-04-01 12:00",
                ],
                tz="Europe/Berlin",
            ),
        )

        hourly, _, _, _, used_days, _ = _hourly_variation(
            series,
            window_start=pd.Timestamp("2026-03-31 00:00").to_pydatetime(),
            window_end=pd.Timestamp("2026-04-01 23:59").to_pydatetime(),
            analysis_days=[date(2026, 3, 31), date(2026, 4, 1)],
            noon_reference_prices={
                date(2026, 3, 31): 2.20,
                date(2026, 4, 1): 2.10,
            },
        )

        self.assertEqual(used_days, 1)
        self.assertEqual(hourly.loc[hourly["hour"] == 8, "price"].item(), -0.05)
        self.assertEqual(hourly.loc[hourly["hour"] == 12, "price"].item(), 0.0)


class BrandDistributionSummaryTests(unittest.TestCase):
    def test_brand_distribution_groups_top_brands_and_misc(self) -> None:
        snapshot = pd.DataFrame(
            {
                "station_uuid": ["s1", "s2", "s3", "s4", "s5"],
                "diesel": [1.70, 1.72, 1.74, 1.73, 0.0],
            }
        )
        stations = pd.DataFrame(
            {
                "station_uuid": ["s1", "s2", "s3", "s4", "s5"],
                "brand": ["ARAL", "ARAL", "SHELL", "Q1", "JET"],
            }
        )

        rows = _brand_distribution_summary(snapshot, stations, "diesel", top_n_brands=1)

        self.assertEqual([row["brand"] for row in rows], ["Gesamtmarkt", "ARAL", "MISC"])
        self.assertEqual(rows[0]["count"], 4)
        self.assertEqual(rows[0]["median"], 1.725)
        self.assertEqual(rows[1]["count"], 2)
        self.assertEqual(rows[1]["median"], 1.71)
        self.assertEqual(rows[2]["count"], 2)
        self.assertEqual(rows[2]["median"], 1.735)

    @patch("scripts.generate_data._load_prices")
    @patch("scripts.generate_data.download_stations")
    def test_generate_uses_prior_day_noon_for_management_brand_snapshot(
        self,
        mock_download_stations,
        mock_load_prices,
    ) -> None:
        class FakeDate(date):
            @classmethod
            def today(cls) -> "FakeDate":
                return cls(2026, 4, 3)

        mock_download_stations.return_value = pd.DataFrame(
            {
                "uuid": ["s1", "s2"],
                "brand": ["ARAL", "SHELL"],
            }
        )
        mock_load_prices.return_value = pd.DataFrame(
            {
                "station_uuid": ["s1", "s1", "s1", "s1", "s2", "s2", "s2", "s2"],
                "date": pd.to_datetime(
                    [
                        "2026-04-01T00:00:00+02:00",
                        "2026-04-01T12:00:00+02:00",
                        "2026-04-02T11:00:00+02:00",
                        "2026-04-02T13:00:00+02:00",
                        "2026-04-01T00:00:00+02:00",
                        "2026-04-01T12:00:00+02:00",
                        "2026-04-02T11:00:00+02:00",
                        "2026-04-02T13:00:00+02:00",
                    ],
                    utc=True,
                ),
                "diesel": [1.83, 1.8, 1.7, 1.9, 1.88, 1.85, 1.75, 1.95],
            }
        )

        with TemporaryDirectory() as tmpdir:
            with patch("scripts.generate_data.date", FakeDate):
                generate(Path(tmpdir), analysis_days_count=8)

            summary_path = (
                Path(tmpdir)
                / "data2"
                / "2026"
                / "04"
                / "02"
                / "management_boxplots.json"
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["snapshot_date"], "2026-04-02")
        self.assertEqual(summary["view_modes"]["diesel"], "hourly")
        self.assertEqual(summary["bucket_counts"]["diesel"], 24)
        self.assertEqual(summary["brand_snapshot_label"], "Vortag 12:00")
        self.assertEqual(summary["brand_snapshot_date"], "2026-04-02")
        self.assertTrue(summary["brand_snapshot_timestamp"].startswith("2026-04-02T12:00"))

        brand_medians = {
            row["brand"]: row["median"] for row in summary["brand_distributions"]["diesel"]
        }
        self.assertEqual(brand_medians["Gesamtmarkt"], 1.725)
        self.assertEqual(brand_medians["ARAL"], 1.7)
        self.assertEqual(brand_medians["SHELL"], 1.75)


if __name__ == "__main__":
    unittest.main()
