"""No running wild: the limits on what agents may do across a run, a day and the app, beyond the
supervisor's step-by-step rules (test_supervisor.py). Each is a plain rule, with its own test."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import test_orchestrator as base
from laptop_agent import supervisor
from laptop_agent.planner import Planner
from laptop_agent.supervisor import AgentLedger, RunTree, Supervisor, Verdict
from test_supervisor import Clock, Script, step


class RunLimitTests(unittest.TestCase):
    """One run's budget: web requests, one site's share, time, model calls, messages, approval cards."""

    def test_research_spends_the_web_budget(self) -> None:
        """A research step searches and fetches pages: 20 of them were over 100 requests in minutes."""
        run = Supervisor(RunTree(), tier="ultra")
        for n in range(supervisor.MAX_WEB_REQUESTS // supervisor.WEB_COST["research"]):
            self.assertIs(run.check(f"research topic {n}", "ultra").verdict, Verdict.ALLOW)
        stopped = run.check("research one more", "ultra")
        self.assertEqual((stopped.verdict, stopped.rule), (Verdict.STOP, "breaker:web"))

    def test_one_site_gets_a_few_requests_at_most(self) -> None:
        run = Supervisor(RunTree(), tier="ultra")
        for n in range(supervisor.MAX_PER_SITE):
            command = f"summarize youtube https://www.youtube.com/watch?v={n}"
            self.assertIs(run.check(command, "ultra").verdict, Verdict.ALLOW)
        self.assertIs(run.check("summarize youtube https://youtu.be/abc", "ultra").verdict, Verdict.ALLOW)
        stopped = run.check("summarize youtube https://WWW.YouTube.com/watch?v=9", "ultra")
        self.assertEqual((stopped.verdict, stopped.rule), (Verdict.STOP, "breaker:site"))
        self.assertIn("www.youtube.com", stopped.reason)

    def test_searches_share_one_site_whatever_they_are_called(self) -> None:
        run = Supervisor(RunTree(), tier="ultra")
        for command in ("web search a", "news b", "web search c", "news d", "web search e"):
            self.assertIs(run.check(command, "ultra").verdict, Verdict.ALLOW, command)
        self.assertEqual(run.check("news f", "ultra").rule, "breaker:site")

    def test_offline_steps_cost_nothing(self) -> None:
        run = Supervisor(RunTree(), tier="ultra")
        for n in range(15):
            self.assertIs(run.check(f"convert {n} km to miles", "ultra").verdict, Verdict.ALLOW)
        self.assertEqual(run.tree.web, 0)

    def test_a_run_has_ten_minutes(self) -> None:
        clock = Clock(0.0)
        tree = RunTree(clock=clock)
        run = Supervisor(tree, tier="ultra")
        self.assertIs(run.check("convert 1 km to miles", "ultra").verdict, Verdict.ALLOW)
        clock.now += supervisor.MAX_RUN_SECONDS + 1
        stopped = run.check("convert 2 km to miles", "ultra")
        self.assertEqual((stopped.verdict, stopped.rule), (Verdict.STOP, "breaker:time"))

    def test_model_calls_are_capped(self) -> None:
        tree = RunTree()
        for _ in range(supervisor.MAX_MODEL_CALLS):
            self.assertIsNone(tree.count_call())
        self.assertEqual(tree.count_call().rule, "breaker:calls")
        self.assertEqual(tree.stopped.rule, "breaker:calls")

    def test_another_stop_still_lets_the_run_say_what_it_found(self) -> None:
        tree = RunTree()
        tree.stop("repeat", "it went in circles")
        self.assertIsNone(tree.count_call())

    def test_a_run_sends_one_message(self) -> None:
        run = Supervisor(RunTree(), tier="ultra")
        self.assertIs(run.check("message whatsapp to mum :: home soon", "ultra").verdict, Verdict.ALLOW)
        refused = run.check("email api send gmail to a@example.com subject s body b", "ultra")
        self.assertEqual((refused.verdict, refused.rule), (Verdict.REFUSE, "sends"))
        self.assertEqual(run.tree.refusals, 1)

    def test_a_run_raises_three_approval_cards_at_most(self) -> None:
        run = Supervisor(RunTree(), tier="ultra")
        run.check("web search cats", "ultra")
        for command in ("download https://a.example/1", "open url https://b.example", "file move a to b"):
            self.assertIn(run.check(command, "ultra").verdict, {Verdict.ALLOW, Verdict.ASK}, command)
        stopped = run.check("file copy a to b", "ultra")
        self.assertEqual((stopped.verdict, stopped.rule), (Verdict.STOP, "breaker:cards"))

    def test_a_run_nobody_watches_only_reads_and_computes(self) -> None:
        run = Supervisor(RunTree(unattended=True), tier="ultra")
        for command in ("read file a.txt", "weather Oslo", "convert 3 km to miles"):
            self.assertIs(run.check(command, "ultra").verdict, Verdict.ALLOW, command)
        for command in ("open url https://example.com", "remember x = y", "download https://example.com/a",
                        "frobnicate"):
            refused = Supervisor(RunTree(unattended=True), tier="ultra").check(command, "ultra")
            self.assertEqual((refused.verdict, refused.rule), (Verdict.REFUSE, "unattended"), command)


class LedgerTests(unittest.TestCase):
    """Runs and steps a day across every run, kept in a file, and the switch that turns agents off."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "agent_ledger.json"
        self.day = [date(2026, 11, 20)]
        self.ledger = AgentLedger(self.path, today=lambda: self.day[0])

    def test_a_day_has_a_ceiling_of_runs_and_steps(self) -> None:
        for _ in range(supervisor.MAX_RUNS_PER_DAY):
            self.assertEqual(self.ledger.start_run(), "")
        self.assertIn("used up", self.ledger.start_run())
        for _ in range(supervisor.MAX_STEPS_PER_DAY):
            self.assertTrue(self.ledger.charge_step())
        self.assertFalse(self.ledger.charge_step())

    def test_the_counts_survive_a_restart_and_reset_the_next_day(self) -> None:
        self.ledger.start_run()
        again = AgentLedger(self.path, today=lambda: self.day[0])
        self.assertEqual(again.state()["runs"], 1)
        self.day[0] += timedelta(days=1)
        self.assertEqual(again.state()["runs"], 0)

    def test_off_holds_across_days_and_restarts(self) -> None:
        self.ledger.set_off(True)
        self.day[0] += timedelta(days=1)
        later = AgentLedger(self.path, today=lambda: self.day[0])
        self.assertIn("turned off", later.start_run())
        self.assertFalse(later.charge_step())
        later.set_off(False)
        self.assertEqual(later.start_run(), "")

    def test_an_unreadable_ledger_fails_closed(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIn("used up", self.ledger.start_run())
        self.assertFalse(self.ledger.charge_step())

    def test_a_run_stops_when_the_day_is_spent(self) -> None:
        self.path.write_text(json.dumps({"date": self.day[0].isoformat(), "runs": 1,
                                         "steps": supervisor.MAX_STEPS_PER_DAY}), encoding="utf-8")
        stopped = Supervisor(RunTree(ledger=self.ledger), tier="ultra").check("convert 1 km to miles", "ultra")
        self.assertEqual((stopped.verdict, stopped.rule), (Verdict.STOP, "breaker:daily"))


class AppLimitTests(unittest.TestCase):
    """The limits the orchestrator holds: one run at a time, the switch, scheduled agents, model calls."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.orchestrator = base.OrchestratorTests("config").build(self.tmp)
        self.orchestrator.context.web.approval_gate._ask = lambda request: False
        self.orchestrator.context.web._launch_browser = lambda url: True

    def brain(self, *replies: str) -> Script:
        script = Script(*replies)
        self.orchestrator.smart_planner = Planner(script)
        self.orchestrator.planner = Planner(script)
        return script

    def run_agent(self, goal: str = "look into cats", **kwargs):
        return asyncio.run(self.orchestrator._run_agent(goal, **kwargs))

    def test_one_agent_run_at_a_time(self) -> None:
        self.brain("FINAL: done")
        self.orchestrator._agent_running.acquire()
        try:
            result = self.run_agent()
        finally:
            self.orchestrator._agent_running.release()
        self.assertFalse(result.ok)
        self.assertIn("another agent run is going", result.message)
        self.assertTrue(self.run_agent().ok)

    def test_agents_off_refuses_every_run_until_on(self) -> None:
        self.brain("FINAL: done", "FINAL: done again")
        off = asyncio.run(self.orchestrator.handle("agents off"))
        self.assertTrue(off.ok)
        for result in (self.run_agent(), self.run_agent(team=True), asyncio.run(self.orchestrator.handle("agent run x"))):
            self.assertFalse(result.ok)
            self.assertIn("turned off", result.message)
        asyncio.run(self.orchestrator.handle("agents on"))
        self.assertTrue(self.run_agent().ok)

    def test_a_run_cannot_turn_agents_on_or_off(self) -> None:
        for command in ("agents on", "agents off"):
            refused = Supervisor(tier="ultra").check(command, "ultra")
            self.assertEqual((refused.verdict, refused.rule), (Verdict.REFUSE, "direct"), command)

    def test_a_run_past_its_model_calls_stops_and_says_what_it_found(self) -> None:
        self.brain(step("convert 1 km to miles"), step("convert 2 km to miles"), step("convert 3 km to miles"),
                   "FINAL: never")
        with patch.object(supervisor, "MAX_MODEL_CALLS", 2):
            result = self.run_agent()
        self.assertEqual(result.data["agent"]["status"], "stopped")
        self.assertIn("asked a model more than 2 times", result.message)
        self.assertIn("convert 1 km to miles", result.message)

    def test_a_helpers_model_calls_count_toward_the_run(self) -> None:
        self.orchestrator.ultra_planner = Planner(Script(step("delegate fast count it"), "FINAL: planner done"))
        self.orchestrator.smart_planner = None
        self.orchestrator.planner = Planner(Script(step("convert 1 km to miles"), step("convert 2 km to miles"),
                                                   "FINAL: helper done"))
        with patch.object(supervisor, "MAX_MODEL_CALLS", 3):
            result = self.run_agent(team=True)
        self.assertEqual(result.data["agent"]["status"], "stopped")
        self.assertIn("asked a model more than 3 times", result.message)

    def test_a_scheduled_agent_runs_at_most_hourly_and_five_at_most(self) -> None:
        refused = asyncio.run(self.orchestrator.handle("schedule agent every 30 minutes :: check the news"))
        self.assertFalse(refused.ok)
        self.assertIn("at most once an hour", refused.message)
        for n in range(supervisor.MAX_AGENT_SCHEDULES):
            added = asyncio.run(self.orchestrator.handle(f"schedule agent every 2 hours :: job {n}"))
            self.assertTrue(added.ok, added.message)
        sixth = asyncio.run(self.orchestrator.handle("schedule agent daily at 08:00 :: one more"))
        self.assertFalse(sixth.ok)
        self.assertIn("remove one first", sixth.message)

    def test_a_scheduled_agent_runs_unattended_and_an_old_too_often_one_does_not_run(self) -> None:
        scheduler = self.orchestrator.context.scheduler
        now = datetime.now().astimezone()
        scheduler.add("agent", "open the page", "every 2 hours", now)
        scheduler.add("agent", "too often", "every 10 minutes", now)
        self.brain(step("open url https://example.com"), "FINAL: could not")
        ran = asyncio.run(self.orchestrator.run_due_schedules(now + timedelta(seconds=1)))
        results = {job["spec"]: job for job in ran.data["ran"]}
        self.assertIn("once an hour", results["too often"]["message"])
        runs = self.orchestrator.context.agent_runs.all_runs()
        self.assertEqual(runs[-1]["steps"][0]["status"], "failed")
        self.assertIn("nobody is watching", runs[-1]["steps"][0]["message"])


if __name__ == "__main__":
    unittest.main()
