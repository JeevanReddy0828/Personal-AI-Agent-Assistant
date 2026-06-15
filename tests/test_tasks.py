from __future__ import annotations

import unittest

from laptop_agent.tasks import TaskRecord, TaskTracker


class TaskTrackerTests(unittest.TestCase):
    def test_records_run_counts(self) -> None:
        tracker = TaskTracker()
        run = tracker.record_run(
            [
                TaskRecord(index=0, command="help", status="ok", message="ok"),
                TaskRecord(index=1, command="boom", status="failed", message="nope"),
            ]
        )
        self.assertEqual(run["task_count"], 2)
        self.assertEqual(run["ok_count"], 1)
        self.assertEqual(run["failed_count"], 1)
        self.assertTrue(run["retry_available"])
        self.assertEqual(run["failed_commands"], ["boom"])
        self.assertEqual(tracker.latest(), run)

    def test_latest_is_none_initially(self) -> None:
        self.assertIsNone(TaskTracker().latest())

    def test_keeps_only_max_runs(self) -> None:
        tracker = TaskTracker(max_runs=2)
        for _ in range(3):
            tracker.record_run([TaskRecord(index=0, command="x", status="ok", message="ok")])
        self.assertEqual(len(tracker.all_runs()), 2)
        self.assertEqual(tracker.latest()["run"], 3)

    def test_retry_plan_returns_failed_commands(self) -> None:
        tracker = TaskTracker()
        run = tracker.record_run(
            [
                TaskRecord(index=0, command="scan files .", status="ok", message="ok"),
                TaskRecord(index=1, command="bogus command", status="failed", message="no route"),
                TaskRecord(index=2, command="read file missing.txt", status="failed", message="missing"),
            ]
        )
        plan = tracker.retry_plan()
        self.assertEqual(plan["run"], run["run"])
        self.assertEqual(plan["count"], 2)
        self.assertEqual(plan["commands"], ["bogus command", "read file missing.txt"])

    def test_retry_plan_can_target_kept_run(self) -> None:
        tracker = TaskTracker(max_runs=3)
        first = tracker.record_run([TaskRecord(index=0, command="first", status="failed", message="no")])
        tracker.record_run([TaskRecord(index=0, command="second", status="ok", message="ok")])
        self.assertEqual(tracker.failed_commands(int(first["run"])), ["first"])
        self.assertEqual(tracker.retry_plan(999)["commands"], [])


if __name__ == "__main__":
    unittest.main()
