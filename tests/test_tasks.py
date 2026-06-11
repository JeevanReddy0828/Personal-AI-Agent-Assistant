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
        self.assertEqual(tracker.latest(), run)

    def test_latest_is_none_initially(self) -> None:
        self.assertIsNone(TaskTracker().latest())

    def test_keeps_only_max_runs(self) -> None:
        tracker = TaskTracker(max_runs=2)
        for _ in range(3):
            tracker.record_run([TaskRecord(index=0, command="x", status="ok", message="ok")])
        self.assertEqual(len(tracker.all_runs()), 2)
        self.assertEqual(tracker.latest()["run"], 3)


if __name__ == "__main__":
    unittest.main()
