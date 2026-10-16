from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from laptop_agent.access import acting_as
from laptop_agent.accounts import Principal
from laptop_agent.tools.breakdown import breakdown_command, breakdown_request
from test_everyday_requests import Everyday

SALES = """Date,Region,Product,Customer,Units,Revenue
2025-01-05,North,Widget,Acme,10,"1,200"
2025-01-19,South,Gadget,Bolt,4,800
2025-02-02,North,Gadget,Acme,6,"1,150"
2025-02-14,West,Widget,Cora,2,240
2025-02-27,East,Gizmo,Dyna,8,960
2025-03-03,North,Gizmo,Bolt,12,"1,440"
2025-03-18,South,Widget,Cora,5,600
2025-03-29,East,Widget,Acme,3,
2025-04-04,North,Widget,Dyna,9,"1,080"
2025-04-21,South,Gizmo,Bolt,7,840
"""


class BreakdownRequestTests(unittest.TestCase):
    def test_a_question_is_read_the_way_it_is_said(self) -> None:
        cases = {
            "total revenue by region in sales.csv": ("sum", "revenue", "region", None, False, False),
            "what's the total Revenue per Region in sales.csv?": ("sum", "Revenue", "Region", None, False, False),
            "can you show me the average units for each product in sales.csv please": (
                "mean", "units", "product", None, False, False),
            "median revenue grouped by region from sales.csv": ("median", "revenue", "region", None, False, False),
            "how many orders per month in sales.csv": ("count", "orders", "month", None, False, False),
            "number of customers by region in sales.csv": ("count", "customers", "region", None, False, False),
            "top 5 products by revenue in sales.csv": (None, "revenue", "products", 5, False, False),
            "bottom 2 regions by average units in sales.csv": ("mean", "units", "regions", 2, True, False),
            "which region has the highest revenue in sales.csv": (None, "revenue", "region", None, False, True),
            "which customer had the fewest orders in sales.csv": (None, "orders", "customer", None, True, True),
            "revenue by quarter in sales.csv": (None, "revenue", "quarter", None, False, False),
        }
        for text, (stat, column, group, limit, ascending, single) in cases.items():
            with self.subTest(text):
                request = breakdown_request(text)
                self.assertIsNotNone(request)
                self.assertEqual((request.stat, request.column, request.group, request.path, request.limit,
                                  request.ascending, request.single),
                                 (stat, column, group, "sales.csv", limit, ascending, single))

    def test_only_a_bare_column_by_column_has_to_name_a_real_column(self) -> None:
        self.assertTrue(breakdown_request("revenue by region in sales.csv").must_match)
        self.assertFalse(breakdown_request("total revenue by region in sales.csv").must_match)

    def test_a_sentence_without_a_table_or_a_grouping_is_not_one(self) -> None:
        for text in ("total revenue by region", "analyze spreadsheet sales.csv", "what is in sales.csv",
                     "summarize sales.csv", "total revenue in sales.csv", "read file sales.csv"):
            with self.subTest(text):
                self.assertIsNone(breakdown_request(text))


class BreakdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.sales = self.dir / "sales.csv"
        self.sales.write_text(SALES, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def ask(self, question: str):
        result = breakdown_command(f"{question} in {self.sales}")
        self.assertIsNotNone(result, question)
        return result

    def test_a_total_by_group_leads_with_the_answer_and_its_share(self) -> None:
        result = self.ask("total revenue by region")
        self.assertTrue(result.ok, result.message)
        self.assertIn("North leads with 4,870, 59% of the total (8,310).", result.message)
        self.assertIn("The top 3 make up 97%.", result.message)
        self.assertIn("West is lowest at 240.", result.message)
        self.assertEqual([g["label"] for g in result.data["groups"]], ["North", "South", "East", "West"])
        self.assertEqual(result.data["chart"], {"kind": "bar", "title": "Total Revenue by Region",
                                                "labels": ["North", "South", "East", "West"],
                                                "values": [4870.0, 2240.0, 960.0, 240.0], "time": False})

    def test_a_blank_is_left_out_and_said_and_an_odd_cell_is_refused_with_its_row(self) -> None:
        self.assertIn("1 row with no Revenue was left out.", self.ask("total revenue by region").message)
        self.sales.write_text(SALES.replace("2025-04-21,South,Gizmo,Bolt,7,840", "2025-04-21,South,Gizmo,Bolt,7,N/A"),
                              encoding="utf-8")
        refused = self.ask("total revenue by region")
        self.assertFalse(refused.ok)
        self.assertIn("Row 11: 'N/A' in Revenue is not a number", refused.message)

    def test_averages_and_medians_are_set_against_the_whole_column(self) -> None:
        average = self.ask("average units per product").message
        self.assertIn("Gizmo has the highest average Units, 9, against 6.6 overall; Gadget the lowest, 5.", average)
        median = self.ask("median revenue by region").message
        self.assertIn("North has the highest median Revenue, 1,175, against 960 overall", median)

    def test_counting_rows_names_and_numbers_are_three_different_counts(self) -> None:
        rows = self.ask("how many orders by region")
        self.assertIn("**Number of orders by Region**", rows.message)
        self.assertEqual(rows.data["groups"][0], {"label": "North", "value": 4.0})
        names = self.ask("how many customers by region")
        self.assertIn("**Distinct Customer by Region**", names.message)
        self.assertEqual(names.data["groups"][0], {"label": "North", "value": 3.0})   # Acme twice, once each
        # Equal amounts are separate sales: a column of numbers counts its filled cells.
        numbers = self.ask("count of revenue by region")
        self.assertEqual({g["label"]: g["value"] for g in numbers.data["groups"]}["East"], 1.0)

    def test_equal_amounts_in_one_group_are_separate_sales(self) -> None:
        self.sales.write_text("Region,Revenue\nNorth,5\nNorth,5\nSouth,3\n", encoding="utf-8")
        counted = self.ask("count of revenue by region")
        self.assertEqual(counted.data["groups"][0], {"label": "North", "value": 2.0})

    def test_a_plural_names_its_column_even_beside_a_longer_one(self) -> None:
        # "products" is in both "Product" and "Product ID" as words; the plural of one is exact.
        self.sales.write_text("Product ID,Product,Revenue\n1,Widget,5\n2,Widget,7\n3,Gizmo,2\n", encoding="utf-8")
        top = self.ask("top 2 products by revenue")
        self.assertTrue(top.ok, top.message)
        self.assertEqual(top.data["chart"]["labels"], ["Widget", "Gizmo"])

    def test_a_year_column_is_a_number_not_a_second_date(self) -> None:
        self.sales.write_text("Year,Date,Revenue\n2025,2025-01-04,5\n2025,2025-02-09,7\n", encoding="utf-8")
        monthly = self.ask("total revenue by month")
        self.assertTrue(monthly.ok, monthly.message)
        self.assertIn("by month of Date", monthly.message)

    def test_a_time_breakdown_runs_in_order_and_says_how_the_latest_moved(self) -> None:
        monthly = self.ask("how many orders per month")
        self.assertIn("by month of Date", monthly.message)
        self.assertEqual([g["label"] for g in monthly.data["groups"]], ["2025-01", "2025-02", "2025-03", "2025-04"])
        self.assertIn("The latest, 2025-04, is 2, down 33% on 2025-03.", monthly.message)
        self.assertTrue(monthly.data["chart"]["time"])
        quarterly = self.ask("revenue by quarter")
        self.assertIn("2025-Q1 was the highest at 6,390. The latest, 2025-Q2, is 1,920, down 70% on 2025-Q1.",
                      quarterly.message)

    def test_two_date_columns_are_not_chosen_between_quietly(self) -> None:
        self.sales.write_text("Ordered,Shipped,Revenue\n2025-01-03,2025-01-09,10\n2025-02-03,2025-02-05,12\n",
                              encoding="utf-8")
        refused = self.ask("total revenue by month")
        self.assertFalse(refused.ok)
        self.assertIn("Ordered or Shipped", refused.message)
        chosen = self.ask("total revenue by month of shipped")
        self.assertTrue(chosen.ok, chosen.message)
        self.assertIn("by month of Shipped", chosen.message)

    def test_top_bottom_and_which_answer_with_the_ranking(self) -> None:
        top = self.ask("top 2 customers by revenue")
        self.assertEqual(top.data["chart"]["labels"], ["Bolt", "Acme"])
        self.assertIn("…and 2 more", top.message)
        which = self.ask("which region has the highest revenue")
        self.assertIn("North has the highest, 4,870.", which.message)
        self.assertEqual(which.data["chart"]["labels"], ["North", "South", "East", "West"])
        fewest = self.ask("which customer has the fewest orders")
        self.assertIn("Cora and Dyna share the lowest, 2.", fewest.message)
        self.assertIn("Acme and Bolt share the highest, 3.", self.ask("which customer has the most orders").message)
        units = self.ask("which region has the highest units")
        self.assertIn("North has the highest, 37.", units.message)
        self.assertFalse(self.ask("top 0 regions by revenue").ok)

    def test_what_cannot_be_answered_says_why(self) -> None:
        missing = self.ask("total revenue by planet")
        self.assertFalse(missing.ok)
        self.assertIn("There is no column called 'planet'. Its columns are: Date, Region", missing.message)
        text = self.ask("total customer by region")
        self.assertIn("Customer is not a column of numbers", text.message)
        self.assertIn("count of Customer by Region", text.message)
        self.assertIn("There is no file at", breakdown_command("total revenue by region in nope.csv").message)

    def test_a_bare_sentence_whose_words_name_no_column_is_left_alone(self) -> None:
        self.assertIsNone(breakdown_command(f"how do i sort by date in {self.sales}"))
        self.assertIsNotNone(breakdown_command(f"revenue by region in {self.sales}"))

    def test_the_file_is_the_last_in_that_exists_and_a_quoted_one_is_taken_whole(self) -> None:
        folder = self.dir / "sales in 2025"
        folder.mkdir()
        (folder / "q1.csv").write_text("Revenue in USD,Region\n5,North\n7,South\n2,North\n", encoding="utf-8")
        result = breakdown_command(f"total revenue in usd by region in {folder / 'q1.csv'}")
        self.assertTrue(result.ok, result.message)
        self.assertIn("**Total Revenue in USD by Region**", result.message)
        quoted = breakdown_command(f'total revenue in usd by region in "{folder / "q1.csv"}"')
        self.assertTrue(quoted.ok, quoted.message)

    def test_a_long_table_says_it_was_cut(self) -> None:
        self.sales.write_text("Region,Revenue\n" + "North,1\n" * 10_005, encoding="utf-8")
        self.assertIn("Only the first 10,000 rows were read.", self.ask("total revenue by region").message)


class BreakdownThroughTheAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "sales.csv").write_text(SALES, encoding="utf-8")
        self._cwd = os.getcwd()
        os.chdir(root)
        self.everyday = Everyday(root / "data", llm="none")

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_said_plainly_politely_or_after_the_wake_word_it_is_computed(self) -> None:
        for said in ("total revenue by region in sales.csv",
                     "can you tell me the total revenue by region in sales.csv?",
                     "Jarvis, what's the total revenue by region in sales.csv?"):
            with self.subTest(said):
                result, ran = self.everyday.say(said)
                self.assertIn("North leads with 4,870", result.message)
                self.assertIsNotNone(ran)

    def test_the_other_table_commands_keep_their_own_sentences(self) -> None:
        # Checked before the breakdown, so a sentence of theirs never becomes a total.
        for said, expected in (("forecast revenue in sales.csv by date", "Row 9 has no Revenue"),
                               ("what drives revenue in sales.csv", "Row 9 has no Revenue"),
                               ("analyze spreadsheet sales.csv", "Analyzed 10 row(s)")):
            with self.subTest(said):
                result, _ran = self.everyday.say(said)
                self.assertIn(expected, result.message)

    def test_a_personal_account_is_refused_because_it_reads_a_file(self) -> None:
        command = "total revenue by region in sales.csv"
        with acting_as(Principal("p1", "family", "personal")):
            routed, ran = self.everyday.say(command)
            direct = asyncio.run(self.everyday.orchestrator.handle(command, _allow_planner=False))
        self.assertNotIn("North leads", routed.message)
        self.assertIsNone(ran, ran)
        self.assertIn("isn't available to a personal account", direct.message)


if __name__ == "__main__":
    unittest.main()
