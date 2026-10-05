from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.planner import PlanDecision
from test_everyday_requests import Everyday


class ShellNegationFollowupTests(unittest.TestCase):
    def _route(self, text: str, command: str):
        with tempfile.TemporaryDirectory() as tmp:
            everyday = Everyday(Path(tmp))
            everyday.orchestrator.planner.provider.plan = lambda *_args, **_kwargs: PlanDecision(
                action="command", command=command, confidence=0.5, explanation="llm")
            result, ran = everyday.say(text)
            return result, ran, everyday.approvals

    def test_polite_explanation_does_not_raise_a_shell_card(self):
        for text, command, reply in (
            ("please explain how to run npm install", "run command npm install", "answered]"),
            ("can you explain how to run npm install", "run command npm install", "answered]"),
            ("can you explain what npm install does", "run command npm install", "answered]"),
            ("please, what does this command do: ipconfig /flushdns", "run command ipconfig /flushdns", "answered]"),
            ("I saw git status in a tutorial", "run command git status", "nothing was run"),
            ("I read about how to run npm install", "run command npm install", "answered]"),
        ):
            with self.subTest(text=text):
                result, ran, approvals = self._route(text, command)
                self.assertFalse(any(risk in ("high", "critical") for risk, _ in approvals),
                                 (text, ran, approvals))
                self.assertIsNone(ran, (text, ran, approvals))
                self.assertIn(reply, result.message)

    def test_negated_open_stays_non_actionable_even_if_llm_routes_it(self):
        for text, command in (
            ("do not open youtube", "open url https://www.youtube.com"),
            ("Jarvis, please don't open youtube", "open url https://www.youtube.com"),
            ("do not remind me to call mom", "remind me to call mom at 5pm"),
            ("do not run this command: del notes.txt", "run command del notes.txt"),
        ):
            with self.subTest(text=text):
                result, ran, approvals = self._route(text, command)
                self.assertIsNone(ran, (text, ran, approvals))
                self.assertFalse(approvals, (text, ran, approvals))
                self.assertIn("answered]", result.message)

    def test_explicit_run_still_reaches_approval_with_explanation_suffix(self):
        text = "please run npm install and explain how to run it"
        _result, _ran, approvals = self._route(text, "run command npm install")
        self.assertTrue(any(risk == "critical" for risk, _ in approvals), approvals)


if __name__ == "__main__":
    unittest.main()
