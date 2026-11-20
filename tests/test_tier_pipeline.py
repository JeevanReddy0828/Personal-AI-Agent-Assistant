from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_orchestrator
from laptop_agent import config as config_module
from laptop_agent.agents.orchestrator import AgentOrchestrator
from laptop_agent.app import _build_tier_decider, build_orchestrator
from laptop_agent.config import load_config
from laptop_agent.decisions import TierDecider, TierDecision
from laptop_agent.planner import PlanDecision, Planner
from laptop_agent.tracing import current_trace

_LAYA_KEYS = ("LAYA_URL", "LAYA_API_KEY", "LAYA_DEADLINE_MS", "LAYA_TIER_CONTEXT")
# Neither a greeting nor a plain question, so it reaches the LLM router; the heuristic calls it fast.
_FAST_MESSAGE = "write me a two-line joke"


class _ChatRouter:
    def plan(self, text, available_commands, memory_profile, history=None):
        return PlanDecision(action="chat", confidence=0.8, explanation="chat", response="fast-reply")


class _Tier:
    def __init__(self, label: str) -> None:
        self.label = label

    def answer(self, text, memory_profile, model=None, history=None):
        return f"{self.label}-reply"


class _RecordingDecider:
    def __init__(self, level: int, source: str) -> None:
        self.level = level
        self.source = source
        self.calls: list[tuple[str, object]] = []

    def decide(self, text, history=None):
        self.calls.append((text, history))
        return TierDecision(level=self.level, source=self.source, steps=[("context", 0.5), ("laya", 12.0)])


def _laya_backend(score: float):
    return lambda body, timeout: {"answers": {"difficulty": {"score": score}}}


class TierPipelineTests(unittest.TestCase):
    def tiered(self, root: Path) -> AgentOrchestrator:
        orchestrator = test_orchestrator.OrchestratorTests("test_reminder_flow").build(root)
        orchestrator.planner = Planner(_ChatRouter())
        orchestrator.smart_planner = Planner(_Tier("smart"))
        orchestrator.ultra_planner = Planner(_Tier("ultra"))
        return orchestrator

    def test_the_decider_chooses_ultra_for_an_otherwise_fast_message(self) -> None:
        self.assertEqual(AgentOrchestrator._complexity(_FAST_MESSAGE), 0)
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.tiered(Path(raw))
            orchestrator.tier_decider = TierDecider(AgentOrchestrator._complexity, backend=_laya_backend(2.6))
            result = asyncio.run(orchestrator.handle(_FAST_MESSAGE))
            self.assertEqual(result.message, "ultra-reply")
            self.assertEqual(result.data["planner"]["requested_model"], "ultra")
            self.assertEqual(result.data["planner"]["tier_source"], "laya")
            trace = orchestrator.traces.recent(1)[0]
            self.assertEqual(trace["tier_source"], "laya")
            self.assertIsInstance(trace["tier_ms"], float)

    def test_without_a_decider_the_heuristic_decides_as_before(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.tiered(Path(raw))
            self.assertIsNone(orchestrator.tier_decider)
            simple = asyncio.run(orchestrator.handle(_FAST_MESSAGE))
            hard = asyncio.run(orchestrator.handle("give me a comprehensive in-depth analysis from scratch"))
            self.assertEqual(simple.message, "fast-reply")
            self.assertEqual(simple.data["planner"]["requested_model"], "fast")
            self.assertEqual(simple.data["planner"]["tier_source"], "heuristic")
            self.assertEqual(hard.message, "ultra-reply")
            first = orchestrator.traces.recent(2)[1]
            self.assertEqual(first["tier_source"], "heuristic")
            self.assertIsNone(first["tier_ms"])

    def test_the_decider_gets_the_conversation_and_its_steps_are_timed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.tiered(Path(raw))
            decider = _RecordingDecider(level=1, source="cue")
            orchestrator.tier_decider = decider
            history = [{"role": "user", "text": "earlier question"}, {"role": "assistant", "text": "earlier answer"}]
            result = asyncio.run(orchestrator.handle(_FAST_MESSAGE, history=history))
            self.assertEqual(result.message, "smart-reply")
            self.assertEqual(result.data["planner"]["tier_source"], "cue")
            self.assertEqual(decider.calls, [(_FAST_MESSAGE, history)])
            self.assertEqual(orchestrator.traces.recent(1)[0]["tier_ms"], 12.5)

    def test_the_result_names_the_source_without_a_trace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.tiered(Path(raw))
            self.assertIsNone(current_trace())
            untraced = asyncio.run(orchestrator._handle(_FAST_MESSAGE))
            self.assertEqual(untraced.data["planner"]["tier_source"], "heuristic")
            orchestrator.tier_decider = TierDecider(AgentOrchestrator._complexity, backend=_laya_backend(2.6))
            untraced = asyncio.run(orchestrator._handle(_FAST_MESSAGE))
            self.assertEqual(untraced.message, "ultra-reply")
            self.assertEqual(untraced.data["planner"]["tier_source"], "laya")
            self.assertEqual(orchestrator.traces.recent(), [])

    def test_the_constructor_takes_a_tier_decider(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            context = test_orchestrator.OrchestratorTests("test_reminder_flow").build(Path(raw)).context
            decider = _RecordingDecider(level=0, source="heuristic")
            orchestrator = AgentOrchestrator(context, data_dir=Path(raw), tier_decider=decider)
            self.assertIs(orchestrator.tier_decider, decider)


class TierDeciderWiringTests(unittest.TestCase):
    def config(self, root: Path):
        return test_orchestrator.OrchestratorTests("test_reminder_flow").config(root)

    def test_no_laya_url_means_no_decider(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            self.assertIsNone(_build_tier_decider(config))
            self.assertIsNone(build_orchestrator(lambda request: True, config=config).tier_decider)

    def test_a_laya_url_builds_a_decider_from_the_settings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = replace(self.config(Path(raw)), laya_url="http://127.0.0.1:8000", laya_api_key="secret",
                             laya_deadline_ms=400, laya_tier_context=True)
            decider = _build_tier_decider(config)
            self.assertIsInstance(decider, TierDecider)
            self.assertEqual(decider._api_key, "secret")
            self.assertEqual(decider.status(), {"laya": True, "cooling_down": False, "deadline_ms": 400,
                                                "use_context": True})
            self.assertIsInstance(build_orchestrator(lambda request: True, config=config).tier_decider, TierDecider)

    def test_only_a_loopback_http_url_builds_a_decider(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = self.config(Path(raw))
            for url in ("http://127.0.0.1:8000", "http://[::1]:8000", "http://localhost:8000"):
                with self.subTest(url=url):
                    self.assertIsInstance(_build_tier_decider(replace(base, laya_url=url)), TierDecider)
            for url in ("http://192.168.1.5:8000", "https://example.com", "file:///x"):
                with self.subTest(url=url):
                    with patch("laptop_agent.app.record_failure") as record:
                        self.assertIsNone(_build_tier_decider(replace(base, laya_url=url)))
                    record.assert_called_once()
                    self.assertNotIn(url, repr(record.call_args))


class LayaConfigTests(unittest.TestCase):
    def load(self, values: dict[str, str]):
        with patch.object(config_module, "_load_dotenv", lambda *a, **k: None), patch.dict(os.environ, {}):
            for key in _LAYA_KEYS:
                os.environ.pop(key, None)
            os.environ.update(values)
            return load_config()

    def test_defaults_when_unset(self) -> None:
        loaded = self.load({})
        self.assertIsNone(loaded.laya_url)
        self.assertIsNone(loaded.laya_api_key)
        self.assertEqual(loaded.laya_deadline_ms, 250)
        self.assertFalse(loaded.laya_tier_context)

    def test_reads_the_laya_settings(self) -> None:
        loaded = self.load({"LAYA_URL": " http://127.0.0.1:8000 ", "LAYA_API_KEY": "k",
                            "LAYA_DEADLINE_MS": "400", "LAYA_TIER_CONTEXT": "Yes"})
        self.assertEqual(loaded.laya_url, "http://127.0.0.1:8000")
        self.assertEqual(loaded.laya_api_key, "k")
        self.assertEqual(loaded.laya_deadline_ms, 400)
        self.assertTrue(loaded.laya_tier_context)

    def test_blank_url_and_key_are_unset(self) -> None:
        loaded = self.load({"LAYA_URL": "  ", "LAYA_API_KEY": "", "LAYA_DEADLINE_MS": "soon"})
        self.assertIsNone(loaded.laya_url)
        self.assertIsNone(loaded.laya_api_key)
        self.assertEqual(loaded.laya_deadline_ms, 250)

    def test_context_flag_spellings(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "on", "On"):
            self.assertTrue(self.load({"LAYA_TIER_CONTEXT": value}).laya_tier_context, value)
        for value in ("", "0", "false", "off", "no", "maybe"):
            self.assertFalse(self.load({"LAYA_TIER_CONTEXT": value}).laya_tier_context, value)


if __name__ == "__main__":
    unittest.main()
