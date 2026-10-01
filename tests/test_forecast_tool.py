from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from laptop_agent.access import acting_as
from laptop_agent.analytics.forecast import forecast
from laptop_agent.accounts import Principal
from laptop_agent.tools.forecast import SeriesError, forecast_request, load_series, run_forecast
from test_everyday_requests import Everyday


class LoadSeriesTests(unittest.TestCase):
    """A file becomes finite, ordered, evenly spaced numbers, or a reason it cannot."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def csv(self, text: str, name: str = "data.csv", encoding: str = "utf-8") -> Path:
        path = self.dir / name
        path.write_text(text, encoding=encoding)
        return path

    def refused(self, path: Path, column: str, date_column: str | None = None) -> str:
        with self.assertRaises(SeriesError) as raised:
            load_series(path, column, date_column)
        return str(raised.exception)

    def test_a_month_column_is_ordered_and_labelled_by_month(self) -> None:
        path = self.csv('Month,Revenue\n2025-03,"$1,300.50"\n2025-01,"1,200"\n2025-02,1250\n')
        series = load_series(path, "revenue", "month")
        self.assertEqual(series.period, "month")
        self.assertEqual(series.labels, ["2025-01", "2025-02", "2025-03"])
        self.assertEqual(series.values, [1200.0, 1250.0, 1300.5])
        self.assertEqual(series.next_labels(2), ["2025-04", "2025-05"])

    def test_each_calendar_period_is_recognised_and_continued(self) -> None:
        cases = {
            "day": ("2025-12-30\n2025-12-31\n2026-01-01", ["2026-01-02"]),
            "week": ("2025-01-06\n2025-01-13\n2025-01-20", ["2025-01-27"]),
            "month": ("2025-01-31\n2025-02-28\n2025-03-31", ["2025-04"]),
            "quarter": ("2025-01-01\n2025-04-01\n2025-07-01", ["2025-Q4"]),
            "year": ("2023\n2024\n2025", ["2026"]),
        }
        for period, (dates, after) in cases.items():
            with self.subTest(period):
                rows = "".join(f"{day},{index}\n" for index, day in enumerate(dates.split("\n")))
                series = load_series(self.csv("When,Count\n" + rows), "Count", "When")
                self.assertEqual(series.period, period)
                self.assertEqual(series.next_labels(1), after)

    def test_quarters_and_years_are_counted_on_the_calendar_the_labels_use(self) -> None:
        # Codex's review of #159: counting from the first date's month accepted these as
        # consecutive periods while labelling them Q1, Q2, Q4, Q4 and 2024, 2025, 2027.
        ends = load_series(self.csv("Date,Sales\n2025-03-31,1\n2025-06-30,2\n2025-09-30,3\n2025-12-31,4\n"),
                           "Sales", "Date")
        self.assertEqual((ends.labels, ends.next_labels(1)), (["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"], ["2026-Q1"]))
        skipped = self.csv("Date,Sales\n2025-03-31,1\n2025-06-30,2\n2025-10-01,3\n2025-12-31,4\n", name="q.csv")
        self.assertIn("Two rows fall in the quarter of 2025-Q4", self.refused(skipped, "Sales", "Date"))
        years = self.csv("Date,Sales\n2024-12-31,1\n2025-12-31,2\n2027-01-01,3\n", name="y.csv")
        self.assertIn("1 year is missing: 2026", self.refused(years, "Sales", "Date"))

    def test_without_a_date_column_the_rows_are_taken_in_file_order(self) -> None:
        series = load_series(self.csv("Steps\n10\n12\n11\n"), "steps")
        self.assertEqual((series.period, series.labels, series.values), ("row", ["1", "2", "3"], [10.0, 12.0, 11.0]))
        self.assertEqual(series.next_labels(2), ["4", "5"])

    def test_a_missing_period_is_named_not_filled(self) -> None:
        path = self.csv("Month,Sales\n2025-01,5\n2025-02,6\n2025-06,7\n")
        message = self.refused(path, "Sales", "Month")
        self.assertIn("3 months are missing: 2025-03, 2025-04, 2025-05", message)
        many = self.csv("Month,Sales\n2024-01,5\n2024-02,5\n2025-01,7\n", name="many.csv")
        self.assertIn("10 months are missing: 2024-03, 2024-04, 2024-05 and 7 more", self.refused(many, "Sales", "Month"))

    def test_two_rows_in_one_period_are_not_added_up(self) -> None:
        path = self.csv("Day,Sales\n2025-01-01,5\n2025-01-02,6\n2025-01-02,7\n2025-01-03,8\n")
        self.assertIn("Two rows fall in the day of 2025-01-02", self.refused(path, "Sales", "Day"))

    def test_uneven_dates_are_refused(self) -> None:
        fortnightly = self.csv("Day,Sales\n2025-01-01,5\n2025-01-15,6\n2025-01-29,7\n")
        self.assertIn("closest dates in Day are 14 days apart", self.refused(fortnightly, "Sales", "Day"))
        same = self.csv("Day,Sales\n2025-01-01,5\n2025-01-01,6\n", name="same.csv")
        self.assertIn("Every row has the same Day", self.refused(same, "Sales", "Day"))
        drifting = self.csv("Day,Sales\n2025-01-06,5\n2025-01-13,6\n2025-01-21,7\n2025-01-28,8\n", name="d.csv")
        self.assertIn("2025-01-21 falls between two weeks", self.refused(drifting, "Sales", "Day"))

    def test_a_cell_that_is_not_plainly_a_number_is_refused_with_its_row(self) -> None:
        self.assertIn("Row 3 has no Sales", self.refused(self.csv("Sales\n5\n\n7\n"), "Sales"))
        self.assertIn("Row 2: 'n/a' in Sales is not a number", self.refused(self.csv("Sales\nn/a\n"), "Sales"))
        # A decimal comma is a question, not fifteen.
        self.assertIn("'1,5' in Sales is not a number", self.refused(self.csv('Sales\n"1,5"\n2\n'), "Sales"))
        self.assertIn("Row 2: 'yesterday' in Day", self.refused(self.csv("Day,Sales\nyesterday,5\n"), "Sales", "Day"))

    def test_an_unknown_column_lists_the_ones_that_exist(self) -> None:
        path = self.csv("Month,Region,Revenue\n2025-01,East,5\n2025-02,West,6\n")
        self.assertIn("There is no column called 'sales'. Its numeric columns are: Revenue.", self.refused(path, "sales"))
        self.assertIn("Its columns are: Month, Region, Revenue.", self.refused(path, "Revenue", "date"))

    def test_a_file_saved_by_excel_still_matches_its_first_column(self) -> None:
        path = self.csv("Month,Revenue\n2025-01,5\n2025-02,6\n", encoding="utf-8-sig")
        self.assertEqual(load_series(path, "Revenue", "Month").labels, ["2025-01", "2025-02"])

    def test_what_cannot_be_a_series_says_so(self) -> None:
        self.assertIn("not .xlsx", self.refused(self.dir / "book.xlsx", "Sales"))
        self.assertIn("There is no file at", self.refused(self.dir / "absent.csv", "Sales"))
        self.assertIn("has no rows under its header", self.refused(self.csv("Sales\n"), "Sales"))
        self.assertIn("One dated row", self.refused(self.csv("Day,Sales\n2025-01-01,5\n"), "Sales", "Day"))


class ForecastRequestTests(unittest.TestCase):
    def test_the_request_names_a_column_a_file_and_optionally_dates_and_a_horizon(self) -> None:
        request = forecast_request(r'forecast Revenue in "C:\my data\sales.csv" by Month for the next 6 months')
        self.assertEqual((request.column, request.path, request.date_column, request.horizon),
                         ("Revenue", r"C:\my data\sales.csv", "Month", 6))
        bare = forecast_request(r"forecast steps from E:\logs\walks.tsv")
        self.assertEqual((bare.column, bare.date_column, bare.horizon), ("steps", None, 3))

    def test_the_weather_is_left_alone(self) -> None:
        # Without a .csv or .tsv this is not a data forecast, and the weather routes stay whole.
        for text in ("forecast", "forecast for tomorrow", "boston forecast", "forecast in boston",
                     "what's the forecast", "forecast revenue in sales.xlsx"):
            with self.subTest(text):
                self.assertIsNone(forecast_request(text))


class RunForecastTests(unittest.TestCase):
    """What the core established is what the answer says, in its three states."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def run_on(self, values: list[float], tail: str = "") -> object:
        path = self.dir / "sales.csv"
        path.write_text("Revenue\n" + "".join(f"{value}\n" for value in values), encoding="utf-8")
        return run_forecast(forecast_request(f"forecast Revenue in {path}{tail}"))

    def test_a_supported_forecast_with_a_measured_range(self) -> None:
        values = [1000 + 12 * month + (month * 37) % 11 - 5 for month in range(60)]
        result = self.run_on(values)
        self.assertTrue(result.ok)
        self.assertIn("| Row | Forecast | 80% range |", result.message)
        self.assertEqual([line.split(" | ")[0] for line in result.message.splitlines() if line.startswith("| 6")],
                         ["| 61", "| 62", "| 63"])
        self.assertIn("Range: the middle 80% of its own errors", result.message)
        # Accuracy is stated only from stretches that played no part in choosing the method.
        self.assertIn("later stretches that played no part in choosing it, its average miss was", result.message)
        self.assertEqual(result.data["labels"], ["61", "62", "63"])
        self.assertTrue(result.data["forecast"]["enough_data"])

    def test_a_band_needs_both_bounds_at_every_step(self) -> None:
        # Codex's review of #159: only the lower bounds were checked, so one null upper
        # bound raised TypeError instead of giving the point table.
        values = [1000 + 12 * month + (month * 37) % 11 - 5 for month in range(60)]
        real = forecast(values, 3)
        with patch("laptop_agent.tools.forecast.forecast", return_value=replace(real, upper=(None, *real.upper[1:]))):
            result = self.run_on(values)
        self.assertTrue(result.ok)
        self.assertNotIn("range |", result.message)
        self.assertIn("No range yet", result.message)
        self.assertEqual(len([line for line in result.message.splitlines() if line.startswith("| 6")]), 3)

    def test_a_kept_baseline_is_not_said_to_be_unbeaten(self) -> None:
        # With few tests a smoother must win by a margin, so a small raw win keeps the baseline.
        values = [1000 + 12 * month + (month * 37) % 11 - 5 for month in range(60)]
        real = forecast(values, 3)
        kept = replace(real, method="naive", baseline_method="naive", holdout_baseline_mae=real.holdout_mae)
        with patch("laptop_agent.tools.forecast.forecast", return_value=kept):
            message = self.run_on(values).message
        self.assertIn("No smoother improved on it by enough to replace it", message)
        self.assertNotIn("Nothing smoother beat it", message)

    def test_a_supported_forecast_without_a_range_draws_none(self) -> None:
        result = self.run_on([100 + 3 * index for index in range(24)])
        self.assertTrue(result.ok)
        self.assertIn("| Row | Forecast |\n", result.message)
        self.assertNotIn("range |", result.message)
        self.assertIn("No range yet (", result.message)

    def test_too_little_history_shows_no_number_at_all(self) -> None:
        # The core's points are then the last value repeated, explicitly not a forecast.
        result = self.run_on([41, 42, 43])
        self.assertFalse(result.ok)
        self.assertIn("I can't forecast Revenue from sales.csv yet", result.message)
        self.assertNotIn("43", result.message)
        self.assertFalse(result.data["forecast"]["enough_data"])

    def test_a_horizon_past_the_limit_is_refused_not_shortened(self) -> None:
        result = self.run_on(list(range(60)), " for 49 months")
        self.assertFalse(result.ok)
        self.assertIn("1 to 48 periods ahead, not 49", result.message)

    def test_the_cores_own_limits_are_said_not_raised(self) -> None:
        result = self.run_on([1.0] * 5000)
        self.assertFalse(result.ok)
        self.assertIn("4096", result.message)


class ThroughTheAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.everyday = Everyday(Path(tmp.name), llm="none")
        self.path = Path(tmp.name) / "sales.csv"
        self.path.write_text("Month,Revenue\n" + "".join(
            f"{2020 + month // 12}-{month % 12 + 1:02d},{1000 + 12 * month + (month * 37) % 11 - 5}\n"
            for month in range(60)), encoding="utf-8")

    def test_a_developer_gets_the_forecast_and_a_personal_account_does_not(self) -> None:
        command = f"forecast Revenue in {self.path} by Month"
        result, ran = self.everyday.say(command)
        self.assertTrue(result.ok, result.message)
        self.assertIn("**Revenue: the next 3 months**", result.message)
        self.assertEqual(ran, command)
        # It reads a file, so default-deny refuses it to a personal account, wherever it is reached.
        with acting_as(Principal("p1", "family", "personal")):
            refused, _ran = self.everyday.say(command)
            self.assertNotIn("the next 3 months", refused.message)
            direct = asyncio.run(self.everyday.orchestrator.handle(command, _allow_planner=False))
        self.assertIn("isn't available to a personal account", direct.message)


if __name__ == "__main__":
    unittest.main()
