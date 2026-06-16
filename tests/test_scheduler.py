from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from laptop_agent.scheduler import (
    Schedule,
    ScheduleError,
    SchedulerStore,
    parse_schedule,
)


def _t(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 16, hour, minute, tzinfo=UTC)


class ParseScheduleTests(unittest.TestCase):
    def test_interval_minutes(self) -> None:
        s = parse_schedule("every 30 minutes")
        self.assertEqual(s.kind, "interval")
        self.assertEqual(s.seconds, 1800)

    def test_interval_hours_and_aliases(self) -> None:
        self.assertEqual(parse_schedule("every 2 hours").seconds, 7200)
        self.assertEqual(parse_schedule("hourly").seconds, 3600)
        self.assertEqual(parse_schedule("every minute").seconds, 60)

    def test_daily_at(self) -> None:
        s = parse_schedule("daily at 08:30")
        self.assertEqual(s.kind, "daily")
        self.assertEqual((s.hour, s.minute), (8, 30))

    def test_describe_roundtrips(self) -> None:
        self.assertEqual(parse_schedule("every 2 hours").describe(), "every 2 hours")
        self.assertEqual(parse_schedule("daily at 07:05").describe(), "daily at 07:05")

    def test_invalid(self) -> None:
        with self.assertRaises(ScheduleError):
            parse_schedule("whenever I feel like it")
        with self.assertRaises(ScheduleError):
            parse_schedule("daily at 25:00")
        with self.assertRaises(ScheduleError):
            parse_schedule("every 5 lightyears")


class DueLogicTests(unittest.TestCase):
    def test_interval_due_when_never_run(self) -> None:
        s = Schedule(kind="interval", seconds=3600)
        self.assertTrue(s.is_due(_t(10), None))

    def test_interval_not_due_until_elapsed(self) -> None:
        s = Schedule(kind="interval", seconds=3600)
        last = _t(10)
        self.assertFalse(s.is_due(last + timedelta(minutes=30), last))
        self.assertTrue(s.is_due(last + timedelta(minutes=60), last))

    def test_daily_due_after_target_once(self) -> None:
        s = Schedule(kind="daily", hour=8, minute=0)
        self.assertFalse(s.is_due(_t(7, 59), None))
        self.assertTrue(s.is_due(_t(8, 1), None))
        # Already ran today at/after 08:00 -> not due again today.
        self.assertFalse(s.is_due(_t(9, 0), _t(8, 1)))
        # Next day it is due again.
        self.assertTrue(s.is_due(_t(8, 1) + timedelta(days=1), _t(8, 1)))


class SchedulerStoreTests(unittest.TestCase):
    def test_add_list_remove_persist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "scheduler.json"
            store = SchedulerStore(path)
            job = store.add("command", "briefing", "daily at 08:00", _t(12))
            self.assertEqual(job.id, 1)
            self.assertEqual(job.kind, "command")

            reopened = SchedulerStore(path)
            self.assertEqual(len(reopened.list_jobs()), 1)
            self.assertEqual(reopened.list_jobs()[0].spec, "briefing")
            self.assertTrue(reopened.remove(1))
            self.assertFalse(reopened.remove(1))
            self.assertEqual(SchedulerStore(path).list_jobs(), [])

    def test_due_jobs_and_mark_ran(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SchedulerStore(Path(raw) / "s.json")
            store.add("command", "tasks", "every 30 minutes", _t(10))
            self.assertEqual(len(store.due_jobs(_t(10))), 1)  # never run -> due
            store.mark_ran(1, _t(10), "ok")
            self.assertEqual(store.due_jobs(_t(10, 15)), [])  # ran 15 min ago
            self.assertEqual(len(store.due_jobs(_t(10, 45))), 1)  # 45 min later -> due
            self.assertEqual(store.list_jobs()[0].run_count, 1)

    def test_disabled_jobs_not_due(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SchedulerStore(Path(raw) / "s.json")
            store.add("agent", "triage email", "hourly", _t(10))
            store.set_enabled(1, False)
            self.assertEqual(store.due_jobs(_t(20)), [])

    def test_rejects_bad_kind_and_empty_spec(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SchedulerStore(Path(raw) / "s.json")
            with self.assertRaises(ScheduleError):
                store.add("nonsense", "x", "hourly", _t(10))
            with self.assertRaises(ScheduleError):
                store.add("command", "   ", "hourly", _t(10))


if __name__ == "__main__":
    unittest.main()
