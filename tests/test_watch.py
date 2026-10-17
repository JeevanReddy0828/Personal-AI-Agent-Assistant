"""Watches: told of a new headline on a topic, or when the laptop needs attention.

Nothing could say "tell me when there's news about Nvidia" or "warn me if my battery runs low".
A watch is a scheduled job, and what it finds is a reminder due now.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.scheduler import SchedulerStore
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.watch import Watches

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")
START = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
CALM = {"battery_percent": 80, "plugged": False, "cpu_percent": 10.0, "ram_percent": 40.0, "disk_free_gb": 200.0}


class WatchTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.scheduler = SchedulerStore(root / "schedules.json")
        self.told: list[str] = []
        self.titles: list[str] = ["Old story one", "Old story two"]
        self.reading = dict(CALM)
        self.now = START
        self.watches = Watches(root / "watches.json", self.scheduler, self.told.append,
                               lambda topic: list(self.titles), lambda: dict(self.reading), lambda: self.now)

    def test_only_a_new_headline_is_told_and_only_once(self) -> None:
        self.assertTrue(self.watches.watch_news("Nvidia").ok)
        self.watches.check_news("nvidia")
        self.assertEqual(self.told, [], "headlines seen when the watch started were told")
        self.titles.insert(0, "Nvidia ships a new chip")
        self.watches.check_news("nvidia")
        self.watches.check_news("nvidia")
        self.assertEqual(self.told, ["News about nvidia: Nvidia ships a new chip"])

    def test_a_watch_is_one_scheduled_job(self) -> None:
        self.watches.watch_news("nvidia")
        self.assertIn("already", self.watches.watch_news("NVIDIA").message)
        jobs = self.scheduler.list_jobs()
        self.assertEqual([(job.kind, job.spec) for job in jobs], [("command", "watch check news nvidia")])
        self.assertEqual(jobs[0].schedule.seconds, 7200)

    def test_feeds_down_at_the_start_records_on_the_first_check_without_telling(self) -> None:
        self.titles = []
        self.watches.watch_news("nvidia")
        self.titles = ["Already old"]
        self.watches.check_news("nvidia")
        self.assertEqual(self.told, [])

    def test_no_headlines_is_a_failure_that_tells_nothing(self) -> None:
        self.watches.watch_news("nvidia")
        self.titles = []
        self.assertFalse(self.watches.check_news("nvidia").ok)
        self.assertEqual(self.told, [])

    def test_a_low_battery_is_told_only_off_the_charger(self) -> None:
        self.reading.update(battery_percent=12, plugged=True)
        self.watches.check_health()
        self.assertEqual(self.told, [])
        self.reading["plugged"] = False
        self.watches.check_health()
        self.assertEqual(self.told, ["Laptop health: battery at 12% and not charging."])

    def test_one_busy_cpu_sample_is_not_a_problem_two_are(self) -> None:
        self.reading["cpu_percent"] = 97.0
        self.watches.check_health()
        self.assertEqual(self.told, [])
        self.watches.check_health()
        self.assertEqual(len(self.told), 1)
        self.assertIn("CPU at 97%", self.told[0])

    def test_the_same_warning_waits_two_hours(self) -> None:
        self.reading["disk_free_gb"] = 2.0
        self.watches.check_health()
        self.now = START + timedelta(hours=1)
        self.watches.check_health()
        self.assertEqual(len(self.told), 1)
        self.now = START + timedelta(hours=2, minutes=1)
        self.watches.check_health()
        self.assertEqual(len(self.told), 2)

    def test_memory_pressure_is_told(self) -> None:
        self.reading["ram_percent"] = 95.0
        self.watches.check_health()
        self.assertEqual(self.told, ["Laptop health: memory 95% used."])

    def test_unwatch_removes_the_job_and_listing_shows_what_is_left(self) -> None:
        self.watches.watch_news("nvidia")
        self.watches.watch_health()
        self.assertIn("News about nvidia", self.watches.listing().message)
        self.assertTrue(self.watches.unwatch("the news about nvidia").ok)
        self.assertTrue(self.watches.unwatch("health").ok)
        self.assertEqual(self.scheduler.list_jobs(), [])
        self.assertFalse(self.watches.unwatch("nvidia").ok)
        self.assertIn("not watching anything", self.watches.listing().message)

    def test_unwatch_leaves_other_scheduled_jobs_alone(self) -> None:
        self.scheduler.add("command", "reminder add now stretch", "every 2 hours", START)
        self.assertIn("not watching anything", self.watches.listing().message)
        self.watches.watch_news("nvidia")
        self.assertNotIn("stretch", self.watches.listing().message)
        self.watches.unwatch("nvidia")
        self.assertEqual([job.spec for job in self.scheduler.list_jobs()], ["reminder add now stretch"])


class WatchCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.orch = self.build(Path(temp.name))
        self.titles = ["Old story"]
        self.orch._news_tool_cache = SimpleNamespace(
            headlines=lambda topic, with_text=0: ToolResult.success("", headlines=[{"title": t} for t in self.titles]))

    def say(self, text: str):
        return asyncio.run(self.orch.handle(text, history=[]))

    def test_a_new_headline_found_by_the_ticker_becomes_a_reminder(self) -> None:
        result = self.say("tell me when there's news about nvidia")
        self.assertTrue(result.ok, result.message)
        self.titles.insert(0, "Nvidia ships a new chip")
        later = datetime.now().astimezone() + timedelta(hours=3)
        asyncio.run(self.orch.run_due_schedules(later))
        messages = [str(item.get("message")) for item in self.orch.context.reminders.list(include_done=True)]
        self.assertIn("News about nvidia: Nvidia ships a new chip", messages)

    def test_every_watch_form_is_developer_only(self) -> None:
        with acting_as(PERSONAL):
            for command in ("watches", "watch health", "watch check health", "watch news x", "watch check news x",
                            "unwatch x"):
                with self.subTest(command):
                    self.assertIsNotNone(refused_command(command))
            self.assertFalse(self.say("tell me when there's news about nvidia").ok)
        self.assertEqual(self.orch.context.scheduler.list_jobs(), [])


class WatchRoutingTests(unittest.TestCase):
    ROUTED = {
        "watch the news about nvidia": "watch news nvidia",
        "tell me when there's news about the fed": "watch news the fed",
        "let me know if there are any new headlines about SpaceX": "watch news SpaceX",
        "keep an eye on the news about apple": "watch news apple",
        "turn on health alerts": "watch health",
        "warn me when my battery is low": "watch health",
        "monitor my laptop": "watch health",
        "what am I watching": "watches",
        "stop watching the news about the fed": "unwatch the fed",
        "turn off health alerts": "unwatch health",
        "unwatch apple": "unwatch apple",
    }
    STAY = ("watch a movie", "what should I watch tonight", "watch out for ice", "I'm watching the news",
            "keep an eye on the oven", "stop watching me", "stop watching tv", "my battery is low",
            "how do I monitor my laptop's temperature")

    def test_watch_requests_are_routed(self) -> None:
        planner = HeuristicPlannerProvider()
        for text, command in self.ROUTED.items():
            with self.subTest(text):
                self.assertEqual(planner.plan(text, "", {}).command, command)

    def test_near_misses_stay_with_the_chat(self) -> None:
        planner = HeuristicPlannerProvider()
        for text in self.STAY:
            with self.subTest(text):
                self.assertFalse((planner.plan(text, "", {}).command or "").startswith(("watch", "unwatch")), text)


if __name__ == "__main__":
    unittest.main()
