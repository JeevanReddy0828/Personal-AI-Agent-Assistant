from __future__ import annotations

import asyncio
import random
import tempfile
import unittest
from pathlib import Path

from laptop_agent.access import acting_as
from laptop_agent.accounts import Principal
from laptop_agent.tools.diagnostics import (
    AnomaliesRequest, DriversRequest, _order, diagnostics_command, diagnostics_request, run_anomalies, run_drivers)
from test_everyday_requests import Everyday


def sales_csv(path: Path, rows: int = 60, seed: int = 11) -> Path:
    """Revenue moved by Ad spend (strongly) and Discount (weakly, downward); Weather is noise and
    Region is text. Columns are in that file order, so a ranking cannot pass by keeping it."""
    rng = random.Random(seed)
    lines = ["Month,Weather,Discount,Ad spend,Region,Revenue"]
    for m in range(rows):
        ad, discount, weather = rng.uniform(10, 50), rng.uniform(0, 20), rng.uniform(-5, 5)
        revenue = 1000 + 30 * ad - 12 * discount + rng.gauss(0, 60)
        lines.append(f"{2020 + m // 12}-{m % 12 + 1:02d},{weather:.1f},{discount:.1f},{ad:.1f},"
                     f"{'NE' if m % 2 else 'SW'},{revenue:.0f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class DiagnosticsRequestTests(unittest.TestCase):
    def test_a_drivers_request_is_read_the_way_it_is_said(self) -> None:
        cases = {
            "what drives Revenue in sales.csv": ((), None),
            "drivers of Revenue in sales.csv.": ((), None),
            "what drives Revenue in sales.csv using Ad spend, Discount and Weather please":
                (("Ad spend", "Discount", "Weather"), None),
            "what drives Revenue in sales.csv by Month using Ad spend": (("Ad spend",), "Month"),
            "what drives Revenue in sales.csv using Ad spend by Month?": (("Ad spend",), "Month"),
        }
        for text, (features, order_by) in cases.items():
            with self.subTest(text):
                request = diagnostics_request(text)
                self.assertIsInstance(request, DriversRequest)
                self.assertEqual((request.target, request.path, request.features, request.order_by),
                                 ("Revenue", "sales.csv", features, order_by))

    def test_an_anomalies_request_is_read_the_way_it_is_said(self) -> None:
        for text, label_by in (("anomalies in Rate in errors.csv", None), ("outliers in Rate in errors.csv.", None),
                               ("unusual values of Rate in errors.csv by Day", "Day")):
            with self.subTest(text):
                request = diagnostics_request(text)
                self.assertIsInstance(request, AnomaliesRequest)
                self.assertEqual((request.column, request.path, request.label_by), ("Rate", "errors.csv", label_by))

    def test_a_table_that_cannot_be_followed_gets_the_usage(self) -> None:
        for text, usage in (("what drives Revenue in sales.csv with gusto", "what drives <column>"),
                            ("anomalies in Rate in errors.csv using Day", "anomalies in <column>")):
            with self.subTest(text):
                result = diagnostics_command(text)
                self.assertFalse(result.ok)
                self.assertIn(f"Use: {usage}", result.message)

    def test_a_sentence_naming_no_table_is_left_alone(self) -> None:
        for text in ("what drives inflation", "anomalies in my sleep", "what drives revenue in sales.xlsx",
                     "forecast Revenue in sales.csv"):
            with self.subTest(text):
                self.assertIsNone(diagnostics_command(text))


class RunDriversTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.sales = sales_csv(self.dir / "sales.csv")

    def run_on(self, tail: str = "", path: Path | None = None):
        return run_drivers(diagnostics_request(f"what drives Revenue in {path or self.sales}{tail}"))

    def table(self, message: str) -> list[str]:
        return [line.split("|")[1].strip() for line in message.splitlines()
                if line.startswith("| ") and not line.startswith("| Feature")]

    def test_the_strongest_association_leads_and_the_text_column_is_named(self) -> None:
        result = self.run_on(" by Month")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.table(result.message), ["Ad spend", "Discount", "Weather"])
        coefficients = dict(zip(result.data["drivers"]["feature_names"], result.data["drivers"]["standardized_coefficients"]))
        self.assertGreater(coefficients["Ad spend"], 0.8)
        self.assertLess(coefficients["Discount"], 0)
        self.assertIn("Left out, as not all numbers: Region.", result.message)
        self.assertIn("which played no part in the fit", result.message)
        self.assertIn("not causal effects", result.message)
        self.assertNotIn("file order", result.message)

    def test_rows_without_dates_are_taken_in_file_order_and_said_so(self) -> None:
        self.assertIn("in file order", self.run_on().message)

    def test_named_features_are_the_only_ones_used(self) -> None:
        result = self.run_on(" using Ad spend, Discount")
        self.assertEqual(sorted(self.table(result.message)), ["Ad spend", "Discount"])
        self.assertNotIn("Left out", result.message)

    def test_too_few_rows_show_no_number_at_all(self) -> None:
        result = self.run_on(path=sales_csv(self.dir / "short.csv", rows=5))
        self.assertFalse(result.ok)
        self.assertIn("I can't say what moves with Revenue", result.message)
        self.assertNotIn("|", result.message)

    def test_what_cannot_be_settled_is_refused_with_a_reason(self) -> None:
        self.assertIn("cannot explain itself", self.run_on(" using Revenue, Discount").message)
        blank = self.dir / "blank.csv"
        blank.write_text("Month,Ad spend,Revenue\n2020-01,5,10\n2020-02,,12\n", encoding="utf-8")
        self.assertIn("Row 3 has no Ad spend", run_drivers(diagnostics_request(
            f"what drives Revenue in {blank} using Ad spend")).message)
        self.assertIn("is not a date I can read", run_drivers(diagnostics_request(
            f"what drives Revenue in {self.sales} by Region")).message)

    def test_collinear_features_carry_the_core_warning(self) -> None:
        rng = random.Random(3)
        twins = self.dir / "twins.csv"
        rows = ["a,b,y"]
        for _ in range(60):
            a = rng.uniform(0, 10)
            rows.append(f"{a:.3f},{a + rng.gauss(0, 0.05):.3f},{2 * a + rng.gauss(0, 1):.3f}")
        twins.write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.assertIn("VIF >= 10", run_drivers(diagnostics_request(f"what drives y in {twins}")).message)

    def test_dates_order_the_rows_before_the_tail_is_held_out(self) -> None:
        header = ["Month", "x"]
        body = [["2020-03", "3"], ["2020-01", "1"], ["2020-02", "2"]]
        self.assertEqual(_order(header, body, 0), [1, 2, 0])


class RunAnomaliesTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def csv(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_spikes_are_flagged_with_their_labels_and_signed_scores(self) -> None:
        rng = random.Random(5)
        rates = [0.003 + rng.gauss(0, 0.0002) for _ in range(40)]
        rates[17], rates[31] = 0.0095, -0.002
        path = self.csv("errors.csv", "Day,Rate\n" + "".join(f"d{i},{v:.5f}\n" for i, v in enumerate(rates)))
        result = run_anomalies(diagnostics_request(f"anomalies in Rate in {path} by Day"))
        self.assertTrue(result.ok, result.message)
        flagged = [line.split("|")[1].strip() for line in result.message.splitlines()
                   if line.startswith("| d")]
        self.assertEqual(flagged, ["d17", "d31"])
        self.assertIn("| d31 | -0.002 | -", result.message)
        self.assertIn("a trend or a season", result.message)

    def test_nothing_unusual_says_so(self) -> None:
        path = self.csv("calm.csv", "v\n" + "".join(f"{10 + (i % 5)}\n" for i in range(30)))
        result = run_anomalies(diagnostics_request(f"anomalies in v in {path}"))
        self.assertIn("No value is unusual by this measure.", result.message)

    def test_a_zero_spread_lists_what_differs_as_unscored_not_as_anomalies(self) -> None:
        # Codex's contract: with MAD zero a deviation has no calibrated score - not infinite,
        # and not nothing. It is listed as unscored, never in a table of scores.
        path = self.csv("flat.csv", "Name,Score\n" + "".join(f"p{i},{100 if i == 7 else 5}\n" for i in range(12)))
        result = run_anomalies(diagnostics_request(f"outliers in Score in {path} by Name"))
        self.assertIn("no value can be scored", result.message)
        self.assertIn("| p7 | 100 |", result.message)
        self.assertNotIn("| Score |", result.message)

    def test_a_short_column_carries_the_core_caution(self) -> None:
        path = self.csv("few.csv", "v\n1\n2\n3\n50\n")
        self.assertIn("Fewer than 10 observations", run_anomalies(diagnostics_request(f"anomalies in v in {path}")).message)


class ThroughTheAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.everyday = Everyday(Path(tmp.name), llm="none")
        self.sales = sales_csv(Path(tmp.name) / "sales.csv")

    def test_a_developer_gets_both_and_a_personal_account_neither(self) -> None:
        for command, heading in ((f"what drives Revenue in {self.sales} by Month", "**What moves with Revenue**"),
                                 (f"anomalies in Revenue in {self.sales} by Month", "**Unusual Revenue values**")):
            with self.subTest(command):
                result, ran = self.everyday.say(command)
                self.assertIn(heading, result.message)
                self.assertEqual(ran, command)
                # It reads a file, so default-deny refuses it to a personal account, and the planner
                # must not hand the sentence to anything else either.
                with acting_as(Principal("p1", "family", "personal")):
                    refused, routed = self.everyday.say(command)
                    direct = asyncio.run(self.everyday.orchestrator.handle(command, _allow_planner=False))
                self.assertNotIn(heading, refused.message)
                self.assertIsNone(routed, routed)
                self.assertIn("isn't available to a personal account", direct.message)


if __name__ == "__main__":
    unittest.main()
