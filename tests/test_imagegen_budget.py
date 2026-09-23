from __future__ import annotations

import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

from laptop_agent.failures import FailureLog
from laptop_agent.tools import imagegen
from laptop_agent.tools.imagegen import ImageTool


class ImageBudgetTests(unittest.TestCase):
    """The worst thing this app does to the user, measured rather than guessed.

    The real `.agent_data/traces.json` holds 300 turns. Eight failed, and four of those
    are `image`, at 60.4s, 61.0s, 62.6s and 62.9s — a primary erroring in about a second
    and the whole fallback budget then spent on a model CLAUDE.md already records as
    timing out. Per-attempt budgets added up: `timeout` for the primary and half of it
    for the fallback is 1.5x, which on the old 120s default is 180 seconds.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_the_budget_is_the_total_not_the_allowance_per_attempt(self) -> None:
        """Each attempt is handed what is LEFT, so two attempts cannot outlast one."""
        handed: list[float] = []

        def clock_watching_backend(model: str, body: dict, budget: float = 0.0) -> dict:
            handed.append(budget)
            raise TimeoutError("timed out")

        tool = ImageTool(api_key="k", data_dir=self.data_dir, model="primary",
                         fallback_model="secondary", timeout=30)
        tool._http_backend = clock_watching_backend        # the real path, with its budget
        result = tool.generate("a fox")

        self.assertFalse(result.ok)
        self.assertEqual(len(handed), 2, "the fallback was not tried")
        self.assertLessEqual(handed[0], 30.0)
        self.assertLessEqual(handed[1], handed[0],
                             "the fallback was handed a fresh budget instead of the remainder")
        self.assertLessEqual(sum(1 for h in handed if h > 30.0), 0,
                             "an attempt was allowed more than the whole budget")

    def test_a_slow_primary_leaves_no_time_and_the_fallback_is_skipped_out_loud(self) -> None:
        """The alternative is honest-but-silent: the user waits past the budget for a
        last resort that cannot finish inside it. Say it was skipped instead."""
        def slow_backend(model: str, body: dict, budget: float = 0.0) -> dict:
            time.sleep(0.25)
            raise TimeoutError("timed out")

        tool = ImageTool(api_key="k", data_dir=self.data_dir, model="primary",
                         fallback_model="secondary", timeout=0)   # nothing left after the first
        tool._http_backend = slow_backend
        started = time.monotonic()
        result = tool.generate("a fox")
        elapsed = time.monotonic() - started

        self.assertFalse(result.ok)
        self.assertIn("was not tried", result.message)
        self.assertIn("secondary", result.message)
        self.assertLess(elapsed, 2.0, "the skipped attempt still cost real time")

    def test_every_failed_attempt_records_its_reason(self) -> None:
        """This file had zero `record_failure` calls, so the reason for each of those
        four real failures was discarded at the moment it was understood — which is the
        rule CLAUDE.md states for every `except` that only returns a fallback."""
        log = FailureLog()
        original, imagegen.record_failure = imagegen.record_failure, log.record
        self.addCleanup(lambda: setattr(imagegen, "record_failure", original))

        def refusing_backend(model: str, body: dict, budget: float = 0.0) -> dict:
            raise urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)

        tool = ImageTool(api_key="k", data_dir=self.data_dir, model="primary",
                         fallback_model="secondary", timeout=30)
        tool._http_backend = refusing_backend
        result = tool.generate("a fox")

        self.assertFalse(result.ok)
        recorded = log.recent(10)
        self.assertEqual(len(recorded), 2, "not every attempt recorded a reason")
        self.assertTrue(all(r["where"] == "imagegen/attempt" for r in recorded), recorded)
        models = {r["context"].get("model") for r in recorded}
        self.assertEqual(models, {"primary", "secondary"})
        self.assertIn("404", str(recorded[0]["message"]) + str(recorded[1]["message"]))

    def test_the_default_budget_cannot_hold_the_user_for_minutes(self) -> None:
        """It was 120 *per attempt*, so a primary plus a fallback was 180 seconds."""
        tool = ImageTool(api_key="k", data_dir=self.data_dir)
        self.assertLessEqual(tool.timeout, 60,
                             "the default image budget is long enough to read as an outage")

    def test_a_working_model_is_still_used_and_the_fallback_left_alone(self) -> None:
        import base64 as b64
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        tried: list[str] = []

        def fine(model: str, body: dict) -> dict:
            tried.append(model)
            return {"artifacts": [{"base64": b64.b64encode(png).decode()}]}

        tool = ImageTool(api_key="", data_dir=self.data_dir, model="primary",
                         fallback_model="secondary", backend=fine)
        result = tool.generate("a fox")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(tried, ["primary"], "the fallback ran even though the primary worked")
        self.assertFalse(result.data["fell_back"])


if __name__ == "__main__":
    unittest.main()
