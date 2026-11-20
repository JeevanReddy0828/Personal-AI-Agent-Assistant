"""A model tier that has not started its answer within `FIRST_WORD_SECONDS` is left for the next
tier, and counted as busy so the next turns skip it for a while. Live, on 2026-11-11 the fast and
smart tiers took 23-30s to start answering, and nothing moved on: a slow tier was never a failing one."""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_orchestrator as base

from laptop_agent.agents import chat_tiers
from laptop_agent.agents.orchestrator import AgentOrchestrator
from laptop_agent.model_status import DEGRADED
from laptop_agent.planner import Planner


class Streams:
    """A provider that waits `delay` seconds before its first word, then streams `words`."""

    def __init__(self, words: list[str], delay: float = 0.0, between: float = 0.0) -> None:
        self.words, self.delay, self.between, self.calls = words, delay, between, 0

    def stream_answer(self, *args, **kwargs):
        self.calls += 1
        time.sleep(self.delay)
        for word in self.words:
            yield word
            time.sleep(self.between)

    def answer(self, *args, **kwargs) -> str:
        return "".join(self.words)


class FirstWordTests(unittest.TestCase):
    def test_a_tier_that_does_not_start_in_time_is_left_and_counted_busy(self) -> None:
        heard: list[str] = []
        why: list[tuple[str, str]] = []
        with patch.object(chat_tiers, "FIRST_WORD_SECONDS", 0.2):
            started = time.monotonic()
            reply = AgentOrchestrator._tier_reply(
                Streams(["late"], delay=1.0), "hi", {}, [], heard.append, failures=why
            )
            waited = time.monotonic() - started
        self.assertEqual(reply, "")
        self.assertEqual(heard, [])
        self.assertLess(waited, 0.8)
        self.assertEqual(why[-1][0], DEGRADED)
        self.assertIn("did not start", why[-1][1])

    def test_a_tier_that_starts_in_time_is_not_cut_off_later(self) -> None:
        heard: list[str] = []
        with patch.object(chat_tiers, "FIRST_WORD_SECONDS", 0.2):
            reply = AgentOrchestrator._tier_reply(
                Streams(["a", "b", "c"], between=0.15), "hi", {}, [], heard.append, failures=[]
            )
        self.assertEqual(reply, "abc")
        self.assertEqual(heard, ["a", "b", "c"])

    def test_a_stream_that_breaks_still_falls_back_and_clears_what_it_showed(self) -> None:
        class Breaks:
            def stream_answer(self, *args):
                yield "partial"
                raise TimeoutError("interrupted")

        heard: list[str] = []

        def token(text: str) -> None:
            heard.append(text)

        token.reset = heard.clear  # type: ignore[attr-defined]
        self.assertEqual(AgentOrchestrator._tier_reply(Breaks(), "hi", {}, [], token), "")
        self.assertEqual(heard, [])

    def test_chat_moves_to_the_next_tier_and_skips_the_slow_one_next_time(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = base.OrchestratorTests("config").build(Path(raw))
            slow, quick = Streams(["slow"], delay=1.0), Streams(["quick answer"])
            orchestrator.planner = Planner(slow)
            orchestrator.smart_planner = Planner(quick)
            orchestrator.ultra_planner = None
            orchestrator.fallback_planner = None
            heard: list[str] = []
            with patch.object(chat_tiers, "FIRST_WORD_SECONDS", 0.2):
                for _ in range(2):
                    heard.clear()
                    result = asyncio.run(
                        orchestrator.handle("tell me something about otters please", on_token=heard.append)
                    )
                    self.assertIn("quick answer", result.message)
            self.assertFalse(orchestrator.model_status.should_attempt("fast"))
            self.assertEqual(slow.calls, 1)  # the second turn did not wait on it again


if __name__ == "__main__":
    unittest.main()
