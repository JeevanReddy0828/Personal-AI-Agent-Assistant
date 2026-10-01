from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.tools.forecast import SeriesError, load_series


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


if __name__ == "__main__":
    unittest.main()
