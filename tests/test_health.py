from __future__ import annotations

import unittest
from types import SimpleNamespace

from laptop_agent.health import system_health


class _Provider:
    def __init__(self, name):
        self.__class__.__name__ = name


def _orchestrator(provider_name, vault_available, smart=False, vision=False):
    obsidian = SimpleNamespace(available=lambda: vault_available)
    context = SimpleNamespace(obsidian=obsidian)
    planner = SimpleNamespace(provider=_Provider(provider_name))
    return SimpleNamespace(
        planner=planner,
        context=context,
        smart_planner=object() if smart else None,
        vision_planner=object() if vision else None,
    )


def _config(**kw):
    base = dict(
        llm_model="m", llm_smart_model=None, llm_ultra_model=None, llm_vision_model=None,
        obsidian_vault="C:/vault", imap_host=None, imap_username=None, imap_password=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class HealthTests(unittest.TestCase):
    def test_ok_when_llm_reachable(self) -> None:
        h = system_health(_orchestrator("OpenAICompatiblePlannerProvider", True), True, _config())
        self.assertEqual(h["overall"], "ok")
        self.assertTrue(h["llm"]["configured"])
        self.assertTrue(h["vault"]["connected"])

    def test_degraded_when_unreachable(self) -> None:
        h = system_health(_orchestrator("OpenAICompatiblePlannerProvider", True), False, _config())
        self.assertEqual(h["overall"], "degraded")

    def test_setup_when_heuristic(self) -> None:
        h = system_health(_orchestrator("HeuristicPlannerProvider", False), None, _config())
        self.assertEqual(h["overall"], "setup")
        self.assertFalse(h["llm"]["configured"])

    def test_email_configured_flag(self) -> None:
        cfg = _config(imap_host="imap.gmail.com", imap_username="a@b.com", imap_password="x")
        h = system_health(_orchestrator("OpenAICompatiblePlannerProvider", True), True, cfg)
        self.assertTrue(h["email"]["configured"])

    def test_degraded_tier_surfaced_while_overall_ok(self) -> None:
        from laptop_agent.model_status import ModelStatus

        orch = _orchestrator("OpenAICompatiblePlannerProvider", True)
        status = ModelStatus()
        status.record("fast", True)
        status.record("smart", False)  # advanced tier busy, fast still healthy
        orch.model_status = status
        h = system_health(orch, True, _config())
        self.assertEqual(h["overall"], "ok")  # fast tier reachable -> overall ok
        self.assertTrue(h["llm"]["degraded_tier"])
        self.assertEqual(h["llm"]["tiers"]["smart"], "degraded")

    def test_missing_model_status_defaults_safely(self) -> None:
        h = system_health(_orchestrator("OpenAICompatiblePlannerProvider", True), True, _config())
        self.assertEqual(h["llm"]["tiers"], {})
        self.assertFalse(h["llm"]["degraded_tier"])


if __name__ == "__main__":
    unittest.main()
