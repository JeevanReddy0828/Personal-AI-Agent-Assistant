from __future__ import annotations

import asyncio
import unittest

from laptop_agent import selfcheck
from laptop_agent.selfcheck import (
    MUST_STAY_CHAT,
    ROUTING_CONTRACT,
    check_not_grabbed,
    check_plain_questions,
    check_routing,
    run_selfcheck,
)


class RoutingContractTests(unittest.TestCase):
    """The contract is the regression guard for the bug class that keeps costing time:
    the user says something and it does not reach the tool that handles it."""

    def test_every_phrasing_reaches_the_tool_it_names(self) -> None:
        for check in check_routing():
            with self.subTest(check=check.name):
                self.assertTrue(check.ok, f"{check.name}: {check.detail}")

    def test_ordinary_prose_is_still_left_alone(self) -> None:
        for check in check_not_grabbed():
            with self.subTest(check=check.name):
                self.assertTrue(check.ok, f"{check.name}: {check.detail}")

    def test_a_question_about_your_own_data_never_takes_the_shortcut(self) -> None:
        for check in check_plain_questions():
            with self.subTest(check=check.name):
                self.assertTrue(check.ok, f"{check.name}: {check.detail}")

    def test_the_contract_covers_the_bugs_that_put_it_here(self) -> None:
        """Written as a membership test on purpose. These exact phrasings were each
        shipped broken; dropping one from the table to make a change pass would quietly
        remove the only thing that would catch it happening again."""
        phrases = {phrase for phrase, _expected, _why in ROUTING_CONTRACT}
        for required in ("do i have any reminders",
                         "what reminders do i have",
                         "can you show me my reminders",
                         "remind me to pay the bill due friday",
                         "whatsapp on the left and chrome on the right",
                         "notepad on the top left and spotify on the bottom right"):
            self.assertIn(required, phrases, f"{required!r} was dropped from the contract")
        prose = {phrase for phrase, _why in MUST_STAY_CHAT}
        self.assertIn("the value is in the middle and the key is on the left", prose)
        self.assertIn("should i put the legend on the right", prose)


class SelfcheckDetectsBreakageTests(unittest.TestCase):
    """A check that cannot fail is not a check.

    The meter's test asserted `#vmeter.hidden` and passed for three months while the
    element rendered nothing. So this asserts the self-check reports a break, rather
    than asserting it currently says yes — which it would do either way.
    """

    def test_a_broken_route_is_reported_as_a_failure(self) -> None:
        class DeafRouter:
            def plan(self, text, help_text, profile, history=None):
                return type("D", (), {"command": None})()

        checks = check_routing(DeafRouter())
        self.assertTrue(checks, "the contract is empty")
        self.assertTrue(all(not check.ok for check in checks),
                        "a router that answers nothing was reported as healthy")
        self.assertIn("expected", checks[0].detail)

    def test_a_greedy_router_is_reported_as_a_failure(self) -> None:
        class GreedyRouter:
            def plan(self, text, help_text, profile, history=None):
                return type("D", (), {"command": "window everything"})()

        checks = check_not_grabbed(GreedyRouter())
        self.assertTrue(all(not check.ok for check in checks),
                        "a router that grabs ordinary prose was reported as healthy")
        self.assertIn("grabbed by", checks[0].detail)

    def test_the_verdict_counts_the_failures_rather_than_hiding_them(self) -> None:
        class DeafRouter:
            def plan(self, text, help_text, profile, history=None):
                return type("D", (), {"command": None})()

        _checks, verdict = run_selfcheck(DeafRouter())
        self.assertIn("failed", verdict)
        self.assertNotIn("All", verdict)


class SelfcheckCommandTests(unittest.TestCase):
    def build(self):
        from laptop_agent.app import build_orchestrator

        # The app's own wiring, which CLAUDE.md records as safe in a test: no network,
        # and the runner has already pointed the data directory at a scratch path.
        return build_orchestrator(lambda request: True)

    def test_the_command_answers_and_carries_the_detail(self) -> None:
        orchestrator = self.build()
        result = asyncio.run(orchestrator.handle("selfcheck"))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.data["failed"], 0)
        self.assertEqual(result.data["passed"], len(result.data["checks"]))
        self.assertIn("checks pass", result.message)

    def test_a_failing_check_makes_the_command_itself_fail(self) -> None:
        """`ok` has to follow the checks. A diagnostic that reports success while
        listing failures is the shape this whole file exists to stop."""
        original = selfcheck.check_routing
        selfcheck.check_routing = lambda router=None: [
            selfcheck.Check("routes: something", False, "expected 'x', got nothing")
        ]
        self.addCleanup(lambda: setattr(selfcheck, "check_routing", original))

        orchestrator = self.build()
        result = asyncio.run(orchestrator.handle("selfcheck"))
        self.assertFalse(result.ok, "the command reported success while a check failed")
        self.assertIn("FAIL", result.message)
        self.assertGreaterEqual(result.data["failed"], 1)


if __name__ == "__main__":
    unittest.main()
