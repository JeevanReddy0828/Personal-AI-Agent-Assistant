from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.workflows import WorkflowStep, WorkflowTracker


class WorkflowTrackerTests(unittest.TestCase):
    def test_records_and_persists_runs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "workflows.json"
            tracker = WorkflowTracker(path)
            run = tracker.record_run(
                [
                    WorkflowStep(index=0, command="help", status="ok", message="ok"),
                    WorkflowStep(index=1, command="missing", status="failed", message="no"),
                ],
                stopped_at=1,
            )

            reopened = WorkflowTracker(path)

            self.assertEqual(reopened.latest()["run"], run["run"])
            self.assertEqual(reopened.retry_commands(), ["missing"])

    def test_retry_commands_from_failed_step_include_later_steps(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tracker = WorkflowTracker(Path(raw) / "workflows.json")
            tracker.record_run(
                [
                    WorkflowStep(index=0, command="first", status="ok", message="ok"),
                    WorkflowStep(index=1, command="second", status="failed", message="no"),
                    WorkflowStep(index=2, command="third", status="pending", message="not run"),
                ],
                stopped_at=1,
            )
            self.assertEqual(tracker.retry_commands(), ["second", "third"])

    def test_corrupt_storage_falls_back_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "workflows.json"
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(WorkflowTracker(path).latest())


if __name__ == "__main__":
    unittest.main()
