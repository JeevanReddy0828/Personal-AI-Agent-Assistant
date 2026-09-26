from __future__ import annotations

import unittest
from fractions import Fraction

from laptop_agent.tools.calculator import (
    CalculatorError,
    CalculatorTool,
    evaluate,
    looks_like_arithmetic,
    normalize,
)


class ExactArithmeticTests(unittest.TestCase):
    """Reported: `solve - 67458363*37834872` produced a decision framework and never
    reached a number. A language model is the wrong tool for this."""

    def test_the_reported_case(self) -> None:
        self.assertEqual(evaluate("67458363*37834872"), 2552278529434536)

    def test_big_integers_stay_exact(self) -> None:
        # Float would silently round this; Python ints do not.
        self.assertEqual(evaluate("99999999999 * 99999999999"), 9999999999800000000001)

    def test_division_is_exact_not_floating(self) -> None:
        self.assertEqual(evaluate("1/3*3"), 1)
        self.assertEqual(evaluate("754/86982") * 86982, 754)

    def test_precedence_and_parentheses(self) -> None:
        self.assertEqual(evaluate("2+3*4"), 14)
        self.assertEqual(evaluate("(2+3)*4"), 20)
        self.assertEqual(evaluate("2**3**2"), 512)      # right associative
        # Unary minus binds looser than exponentiation: -(2**2), not (-2)**2.
        self.assertEqual(evaluate("-2**2"), -4)
        self.assertEqual(evaluate("(-2)**2"), 4)
        self.assertEqual(evaluate("2**-2"), Fraction(1, 4))
        self.assertEqual(evaluate("10 % 3"), 1)
        self.assertEqual(evaluate("7 // 2"), 3)

    def test_functions_and_constants(self) -> None:
        self.assertEqual(evaluate("sqrt(16)"), 4.0)
        self.assertEqual(evaluate("max(3, 9, 4)"), 9)
        self.assertAlmostEqual(evaluate("round(pi, 2)"), 3.14)

    def test_dictated_words_become_operators(self) -> None:
        # Voice input arrives as words: "seven fifty four divided by 86982".
        self.assertIn("/", normalize("754 divided by 2"))
        self.assertEqual(evaluate("754 divided by 2"), 377)
        self.assertEqual(evaluate("12 times 12"), 144)
        self.assertEqual(evaluate("what is 2 plus 2?"), 4)

    def test_thousands_separators_are_one_number(self) -> None:
        self.assertEqual(evaluate("1,234,567 + 1"), 1234568)

    def test_division_by_zero_is_a_clean_message(self) -> None:
        for expression in ("1/0", "1//0", "1%0"):
            with self.assertRaises(CalculatorError):
                evaluate(expression)

    def test_it_is_not_an_eval(self) -> None:
        # The whole point of a parser rather than eval(): no names, no attributes, no calls.
        for hostile in (
            "__import__('os').system('echo hi')",
            "(1).__class__",
            "open('x')",
            "1; import os",
        ):
            with self.assertRaises(CalculatorError):
                evaluate(hostile)

    def test_absurd_exponents_are_refused_rather_than_hanging(self) -> None:
        with self.assertRaises(CalculatorError):
            evaluate("9**999999")


class RoutingGuardTests(unittest.TestCase):
    """It must catch sums without hijacking questions that merely contain numbers."""

    def test_sums_are_recognised(self) -> None:
        for text in (
            "67458363*37834872",
            "what is 2+2",
            "754 divided by 86982",
            "(12 + 8) * 3",
            "15 * 0.2",
            "calculate 2**10",
        ):
            self.assertTrue(looks_like_arithmetic(text), text)

    def test_questions_with_numbers_are_left_alone(self) -> None:
        for text in (
            "should I use 2 or 3 replicas",
            "what happened in 2026",
            "summarize file report2024.pdf",
            "compare SQL and NoSQL",
            "play music despacito",
            "is 5 a lot of money",
            "open url https://example.com/a1",
        ):
            self.assertFalse(looks_like_arithmetic(text), text)

    def test_a_bare_number_is_not_a_sum(self) -> None:
        self.assertFalse(looks_like_arithmetic("42"))
        self.assertFalse(looks_like_arithmetic(""))


class CalculatorToolTests(unittest.TestCase):
    def test_the_answer_is_in_the_message(self) -> None:
        result = CalculatorTool().compute("67458363*37834872")
        self.assertTrue(result.ok)
        self.assertIn("2,552,278,529,434,536", result.message)
        self.assertEqual(result.data["result"], "2,552,278,529,434,536")

    def test_an_exact_fraction_shows_both_forms(self) -> None:
        result = CalculatorTool().compute("754/86982")
        self.assertTrue(result.ok)
        self.assertIn("0.008668", result.message)
        self.assertIn("exactly", result.message)

    def test_nonsense_fails_cleanly(self) -> None:
        result = CalculatorTool().compute("banana + 1")
        self.assertFalse(result.ok)
        self.assertIn("banana", result.message)


class EverydayArithmeticTests(unittest.TestCase):
    """The sums people actually say. Each of these reached a chat model (which is the wrong
    tool for arithmetic) or failed outright, found by driving a conversational corpus
    through the real orchestrator."""

    def value(self, text: str):
        result = CalculatorTool().compute(text)
        self.assertTrue(result.ok, f"{text!r}: {result.message}")
        return result.data["result"]

    def test_to_the_power_of_is_an_operator_not_a_square(self) -> None:
        # It was rewritten to "**2", so "2 to the power of 10" became "2 **2 10".
        self.assertEqual(self.value("what's 2 to the power of 10"), "1,024")
        self.assertEqual(self.value("2 to the 10th"), "1,024")
        self.assertEqual(self.value("3 squared"), "9")
        self.assertEqual(self.value("2 cubed"), "8")

    def test_percentages_tips_and_discounts(self) -> None:
        self.assertEqual(self.value("what's 15% of 80"), "12")
        self.assertEqual(self.value("15 percent of 80"), "12")
        self.assertEqual(self.value("what's a 20% tip on $45"), "9")
        self.assertEqual(self.value("25% off 80"), "60")
        # A bare % is still modulo.
        self.assertEqual(evaluate("10 % 3"), 1)
        self.assertEqual(evaluate("10 mod 3"), 1)

    def test_roots_splits_and_times(self) -> None:
        self.assertEqual(self.value("what's the square root of 144"), "12")
        self.assertEqual(self.value("sqrt 2"), "1.414213562")
        self.assertEqual(self.value("split $120 between 4 people"), "30")
        self.assertEqual(self.value("12 x 13"), "156")

    def test_fractions_of_and_multiples(self) -> None:
        self.assertEqual(self.value("what's 1/4 of 200"), "50")
        self.assertEqual(self.value("half of 30"), "15")
        self.assertEqual(self.value("a third of 90"), "30")
        self.assertEqual(self.value("two thirds of 90"), "60")
        self.assertEqual(self.value("3 quarters of 100"), "75")
        self.assertEqual(self.value("double 25"), "50")
        self.assertEqual(self.value("triple 12"), "36")
        for text in ("double check my work", "half of my team is remote", "a third of the class"):
            self.assertFalse(looks_like_arithmetic(text), text)

    def test_the_sum_is_shown_the_way_it_reads(self) -> None:
        # "3 **2 = **9**" rendered "2 = " in bold and left a stray "9**" behind.
        self.assertEqual(CalculatorTool().compute("whats 3 squared").message, "3^2 = **9**")
        self.assertEqual(CalculatorTool().compute("7 times 8").message, "7 × 8 = **56**")
        self.assertEqual(CalculatorTool().compute("7 times 8").data["expression"], "7 * 8")

    def test_dictated_numbers(self) -> None:
        self.assertEqual(self.value("What's five plus five?"), "10")
        self.assertEqual(self.value("twelve times twelve"), "144")
        self.assertEqual(self.value("one hundred and twenty plus five"), "125")
        self.assertEqual(self.value("a hundred divided by four"), "25")

    def test_everyday_sums_are_recognised_and_prose_is_not(self) -> None:
        for text in ("what's 15% of 80", "what is five plus five", "sqrt 2",
                     "split $120 between 4 people", "what's the square root of 144"):
            self.assertTrue(looks_like_arithmetic(text), text)
        for text in ("split the bill", "what is one direction", "i have 2 kids and 3 dogs",
                     "two and three", "top 10 movies of 2024", "split screen"):
            self.assertFalse(looks_like_arithmetic(text), text)


class HugeResultTests(unittest.TestCase):
    """Fuzzing found two ways out of `compute` with an exception, and one way to hang it.
    An exception here escaped the whole turn: the CLI session ended and the web page
    showed a raw "Error: ..."."""

    def test_a_result_too_long_to_print_is_shown_in_scientific_form(self) -> None:
        # Python refuses to turn an int of more than 4,300 digits into text.
        result = CalculatorTool().compute("10**4000*10**1000")
        self.assertTrue(result.ok, result.message)
        self.assertIn("5,001-digit number", result.message)

    def test_a_result_too_large_for_a_float_still_answers(self) -> None:
        # `value=float(result)` raised OverflowError after the sum had succeeded.
        result = CalculatorTool().compute("9**4096")
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(result.data["value"])

    def test_a_giant_power_is_refused_before_it_is_computed(self) -> None:
        # Ran for over 100 seconds on a worker thread before this.
        import time

        started = time.perf_counter()
        result = CalculatorTool().compute("(123456789**4096)**4096")
        self.assertFalse(result.ok)
        self.assertIn("too large", result.message)
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()


class CapabilitiesAnswerTests(unittest.TestCase):
    """"what can you do" is the first thing anyone asks, and it used to return a hundred
    lines of raw command syntax."""

    def answer(self) -> str:
        from laptop_agent.agents.orchestrator import AgentOrchestrator

        return AgentOrchestrator._capabilities().message

    def test_it_is_grouped_prose_not_the_command_list(self) -> None:
        text = self.answer()
        self.assertIn("Files and documents", text)
        self.assertIn("Make things", text)
        self.assertIn("Do things", text)
        # The raw list starts this way; the tour must not just reproduce it.
        self.assertNotIn("remember <key> = <value>", text)

    def test_it_says_risky_things_ask_first(self) -> None:
        self.assertIn("asks you first", self.answer())

    def test_it_points_at_help_for_the_full_list(self) -> None:
        self.assertIn("help", self.answer())


class CapabilityRoutingTests(unittest.TestCase):
    def test_capability_questions_route_to_the_tour(self) -> None:
        from laptop_agent.planner.heuristic import HeuristicPlannerProvider

        planner = HeuristicPlannerProvider()
        for text in ("what can you do", "what can you do?", "what are you capable of",
                     "how can you help", "what can I ask you"):
            decision = planner.plan(text, "", {})
            self.assertEqual(decision.command, "capabilities", text)

    def test_asking_for_the_command_list_still_gets_it(self) -> None:
        from laptop_agent.planner.heuristic import HeuristicPlannerProvider

        self.assertEqual(HeuristicPlannerProvider().plan("commands", "", {}).command, "help")
