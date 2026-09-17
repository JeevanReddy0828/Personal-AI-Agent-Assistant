"""The fallback ladder decided everything from `bool(reply)`.

A retired model (HTTP 410), a rejected API key (401), a model this account cannot call
(404), a parameter the endpoint refuses (400) and a genuinely overloaded endpoint (503)
all produced the same empty reply, and all were recorded identically as "degraded". So a
permanent misconfiguration was retried every 60 seconds forever and reported to the user
as *busy* — advice to wait, for something that would never recover.

ERRORS.md records that costing real time twice: the ultra tier returning HTTP 400 on every
request while health called it congested, and chat pointed at model ids NVIDIA had retired,
where "a new key can't revive a retired model". This is the boundary those two share.
"""

from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path

from laptop_agent.model_status import (
    BROKEN, BROKEN_COOLDOWN, DEGRADED, DEGRADED_COOLDOWN, ModelStatus,
)
from laptop_agent.planner.core import PlanDecision, Planner
from laptop_agent.planner.openai_compatible import OpenAICompatiblePlannerProvider, classify_failure


def http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"body"))


class ClassifyTests(unittest.TestCase):
    PERMANENT = (400, 401, 403, 404, 410, 422)
    TRANSIENT = (408, 429, 500, 502, 503, 504)

    def test_a_misconfigured_tier_is_broken(self) -> None:
        for code in self.PERMANENT:
            with self.subTest(code):
                kind, detail = classify_failure(http(code), "nvidia/some-model")
                self.assertEqual(kind, BROKEN, f"HTTP {code} was treated as transient")
                self.assertIn(str(code), detail)
                self.assertIn("nvidia/some-model", detail,
                              "the advice does not name the model to change")

    def test_a_loaded_tier_is_only_degraded(self) -> None:
        for code in self.TRANSIENT:
            with self.subTest(code):
                kind, _ = classify_failure(http(code), "m")
                self.assertEqual(kind, DEGRADED, f"HTTP {code} was treated as permanent")

    def test_network_trouble_is_degraded_not_broken(self) -> None:
        """An unexplained failure is the transient assumption. Guessing "broken" would
        stop trying a tier that was only having a bad minute."""
        for exc in (TimeoutError("slow"), urllib.error.URLError("refused")):
            with self.subTest(exc):
                self.assertEqual(classify_failure(exc, "m")[0], DEGRADED)

    def test_the_reason_reads_as_advice(self) -> None:
        self.assertIn("retired", classify_failure(http(410), "m")[1])
        self.assertIn("API key", classify_failure(http(401), "m")[1])
        self.assertIn("cannot call", classify_failure(http(404), "m")[1])


class CooldownTests(unittest.TestCase):
    def test_a_broken_tier_waits_far_longer_than_a_busy_one(self) -> None:
        self.assertGreater(BROKEN_COOLDOWN, DEGRADED_COOLDOWN * 5)

    def test_a_broken_tier_is_not_retried_on_the_busy_schedule(self) -> None:
        """The cost of getting this wrong: a failing network round-trip added to every
        message, forever, for an endpoint that is never coming back."""
        status = ModelStatus()
        status.record("ultra", False, reason=BROKEN, detail="retired")
        self.assertFalse(status.should_attempt("ultra"))
        # Still refused at a point where a merely-busy tier would already be retried.
        self.assertFalse(status.should_attempt("ultra", cooldown_seconds=DEGRADED_COOLDOWN + 1))

    def test_a_broken_tier_is_eventually_retried(self) -> None:
        """Long, not forever: a key can be fixed while the app runs, and a tier that is
        never retried can never be seen to recover."""
        status = ModelStatus()
        status.record("ultra", False, reason=BROKEN, detail="retired")
        self.assertTrue(status.should_attempt("ultra", cooldown_seconds=0))

    def test_recovering_clears_the_reason(self) -> None:
        status = ModelStatus()
        status.record("ultra", False, reason=BROKEN, detail="retired")
        self.assertEqual(status.broken_tiers(), ["ultra"])
        status.record("ultra", True)
        self.assertEqual(status.broken_tiers(), [])
        self.assertEqual(status.reason("ultra"), "")

    def test_an_unexplained_failure_stays_degraded(self) -> None:
        status = ModelStatus()
        status.record("smart", False)
        self.assertEqual(status.status("smart"), DEGRADED)
        self.assertEqual(status.broken_tiers(), [])


class ProviderReportsWhyTests(unittest.TestCase):
    def test_answer_reports_the_reason(self) -> None:
        for code, expected in ((410, BROKEN), (503, DEGRADED)):
            with self.subTest(code):
                seen: list[tuple[str, str]] = []
                provider = OpenAICompatiblePlannerProvider(
                    "k", "m", transport=lambda payload, c=code: (_ for _ in ()).throw(http(c)))
                self.assertIsNone(
                    provider.answer("hi", {}, on_failure=lambda k, d: seen.append((k, d))))
                self.assertEqual(seen[0][0], expected)

    def test_a_caller_that_passes_no_sink_is_unaffected(self) -> None:
        """Every existing caller — the advisor, the document tool, the copilot — calls
        answer() with no sink and must behave exactly as before."""
        provider = OpenAICompatiblePlannerProvider(
            "k", "m", transport=lambda payload: (_ for _ in ()).throw(http(410)))
        self.assertIsNone(provider.answer("hi", {}))


class LadderTests(unittest.TestCase):
    """End to end: a tier that says why is recorded accordingly, and the turn still gets
    answered by falling through."""

    def build(self, root: Path):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_orchestrator import OrchestratorTests
        return OrchestratorTests("test_reminder_flow").build(root)

    def orchestrator_with_smart(self, root: Path, smart):
        class LowConfRouter:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.0, explanation="", response=None)

        class FastRouting:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.5, explanation="",
                                    response="fast routed answer")

        orchestrator = self.build(root)
        orchestrator.router = Planner(LowConfRouter())
        orchestrator.planner = Planner(FastRouting())
        orchestrator.smart_planner = Planner(smart)
        orchestrator.ultra_planner = None
        return orchestrator

    ASK = "walk me through the tradeoffs of this design in depth"

    def test_a_retired_tier_is_recorded_broken_and_the_turn_still_answers(self) -> None:
        class RetiredSmart:
            def answer(self, text, profile, model=None, history=None, on_failure=None):
                if on_failure is not None:
                    on_failure(*classify_failure(http(410), "nvidia/retired"))
                return None

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.orchestrator_with_smart(Path(raw), RetiredSmart())
            result = asyncio.run(orchestrator.handle(self.ASK))
            self.assertTrue(result.ok)
            self.assertIn("fast routed answer", result.message)
            self.assertEqual(orchestrator.model_status.status("smart"), BROKEN)
            self.assertIn("retired", orchestrator.model_status.reason("smart"))
            self.assertEqual(orchestrator.model_status.broken_tiers(), ["smart"])

    def test_a_busy_tier_is_not_marked_broken(self) -> None:
        class BusySmart:
            def answer(self, text, profile, model=None, history=None, on_failure=None):
                if on_failure is not None:
                    on_failure(*classify_failure(http(503), "nvidia/smart"))
                return None

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.orchestrator_with_smart(Path(raw), BusySmart())
            asyncio.run(orchestrator.handle(self.ASK))
            self.assertEqual(orchestrator.model_status.status("smart"), DEGRADED)
            self.assertEqual(orchestrator.model_status.broken_tiers(), [])

    def test_a_silent_tier_is_still_only_degraded(self) -> None:
        """A provider that reports nothing (an older one, or a test double) must not be
        promoted to "broken" on a guess."""
        class SilentSmart:
            def answer(self, text, profile, model=None, history=None):
                return None

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.orchestrator_with_smart(Path(raw), SilentSmart())
            asyncio.run(orchestrator.handle(self.ASK))
            self.assertEqual(orchestrator.model_status.status("smart"), DEGRADED)


class HealthTests(unittest.TestCase):
    def test_health_names_the_broken_tier_and_what_to_change(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_health import _config, _orchestrator
        from laptop_agent.health import system_health

        orchestrator = _orchestrator("OpenAICompatiblePlannerProvider", True)
        orchestrator.model_status = ModelStatus()
        orchestrator.model_status.record(
            "ultra", False, reason=BROKEN,
            detail="that model id has been retired by the provider (nvidia/ultra) - HTTP 410")
        report = system_health(orchestrator, True, _config())
        self.assertEqual(report["llm"]["broken_tiers"], ["ultra"])
        self.assertIn("retired", report["llm"]["tier_reasons"]["ultra"])
        self.assertTrue(report["llm"]["degraded_tier"], "a broken tier is also degraded")


if __name__ == "__main__":
    unittest.main()
