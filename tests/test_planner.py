from __future__ import annotations

import unittest

from laptop_agent.planner import HeuristicPlannerProvider


class HeuristicPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = HeuristicPlannerProvider()

    def plan(self, text: str):
        return self.provider.plan(text, "help text", {})

    def test_routes_open_url(self) -> None:
        decision = self.plan("open website example.com")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "open url example.com")

    def test_routes_job_application_to_plan_only(self) -> None:
        decision = self.plan("apply to the job at https://example.com/jobs/1")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "plan apply job https://example.com/jobs/1")

    def test_unknown_request_returns_chat(self) -> None:
        decision = self.plan("invent something vague")
        self.assertTrue(decision.is_chat)
        self.assertLess(decision.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
