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

    def test_routes_form_inspection(self) -> None:
        decision = self.plan("inspect forms at example.com/apply")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "inspect forms example.com/apply")

    def test_routes_fill_preview(self) -> None:
        decision = self.plan("preview form fill for example.com/apply")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "preview form fill example.com/apply")

    def test_routes_fill_form(self) -> None:
        decision = self.plan("fill the form at example.com/apply")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "fill form example.com/apply")

    def test_routes_unread_email(self) -> None:
        decision = self.plan("show unread emails")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email unread")

    def test_routes_email_search(self) -> None:
        decision = self.plan("find emails about invoice")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email search invoice")

    def test_routes_email_token_status(self) -> None:
        decision = self.plan("show email token status")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email tokens status")

    def test_routes_gmail_api_search(self) -> None:
        decision = self.plan("find emails about invoice in gmail")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email api search gmail invoice")

    def test_routes_outlook_unread(self) -> None:
        decision = self.plan("show unread emails in outlook")
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "email api unread outlook")

    def test_unknown_request_returns_chat(self) -> None:
        decision = self.plan("invent something vague")
        self.assertTrue(decision.is_chat)
        self.assertLess(decision.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
