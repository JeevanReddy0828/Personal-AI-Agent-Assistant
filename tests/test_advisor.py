from __future__ import annotations

import unittest

from laptop_agent.advisor import ProblemSolver

CANNED = "## Problem\nYou must choose a database.\n## Recommendation\nUse Postgres."


class _Brain:
    def __init__(self, reply: str = CANNED) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


class ProblemSolverTests(unittest.TestCase):
    def test_solves_without_research(self) -> None:
        brain = _Brain()
        result = ProblemSolver(decide=brain).solve("Postgres or MySQL?")
        self.assertTrue(result.ok)
        self.assertEqual(result.analysis, CANNED)
        self.assertFalse(result.used_research)
        self.assertEqual(result.sources, [])
        self.assertEqual(len(brain.prompts), 1)

    def test_grounds_with_research_in_prompt(self) -> None:
        brain = _Brain()
        sources = [{"title": "Bench", "url": "https://example.com/b"}]
        research = lambda q: (f"Benchmarks comparing options for: {q}", sources)
        result = ProblemSolver(decide=brain, research=research).solve("Postgres or MySQL?")
        self.assertTrue(result.ok)
        self.assertTrue(result.used_research)
        self.assertEqual(result.sources, sources)
        self.assertIn("Benchmarks comparing options", brain.prompts[0])  # context fed to the model

    def test_research_failure_is_nonfatal(self) -> None:
        def boom(_q):
            raise RuntimeError("search down")

        result = ProblemSolver(decide=_Brain(), research=boom).solve("Postgres or MySQL?")
        self.assertTrue(result.ok)  # advice still produced
        self.assertFalse(result.used_research)

    def test_empty_problem(self) -> None:
        result = ProblemSolver(decide=_Brain()).solve("   ")
        self.assertFalse(result.ok)

    def test_no_model_available(self) -> None:
        result = ProblemSolver(decide=lambda _p: "").solve("Postgres or MySQL?")
        self.assertFalse(result.ok)
        self.assertIn("language model", result.analysis)

    def test_brain_error_is_caught(self) -> None:
        def boom(_p):
            raise RuntimeError("model exploded")

        result = ProblemSolver(decide=boom).solve("Postgres or MySQL?")
        self.assertFalse(result.ok)
        self.assertIn("errored", result.analysis)

    def test_max_context_truncation(self) -> None:
        brain = _Brain()
        research = lambda q: ("Z" * 5000, [])  # Z marker doesn't appear in the system prompt
        ProblemSolver(decide=brain, research=research, max_context_chars=100).solve("big context")
        # The prompt embeds at most max_context_chars of grounding context.
        self.assertLessEqual(brain.prompts[0].count("Z"), 100)


if __name__ == "__main__":
    unittest.main()
