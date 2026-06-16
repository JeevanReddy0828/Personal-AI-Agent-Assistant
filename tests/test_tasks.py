from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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

    def test_persists_runs_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "tasks.json"
            tracker = TaskTracker(path)
            first = tracker.record_run([TaskRecord(index=0, command="first", status="ok", message="done")])
            second = tracker.record_run([TaskRecord(index=0, command="second", status="failed", message="no")])

            reopened = TaskTracker(path)

            self.assertEqual(reopened.latest()["run"], second["run"])
            self.assertEqual(reopened.all_runs()[0]["run"], first["run"])
            self.assertEqual(reopened.failed_commands(), ["second"])
            third = reopened.record_run([TaskRecord(index=0, command="third", status="ok", message="ok")])
            self.assertEqual(third["run"], 3)

    def test_corrupt_storage_falls_back_to_empty_tracker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "tasks.json"
            path.write_text("not json", encoding="utf-8")

            tracker = TaskTracker(path)

            self.assertIsNone(tracker.latest())
            run = tracker.record_run([TaskRecord(index=0, command="help", status="ok", message="ok")])
            self.assertEqual(run["run"], 1)


if __name__ == "__main__":
    unittest.main()
