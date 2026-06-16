from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.autopilot import AutopilotPlanner, AutopilotStep, AutopilotTracker, parse_autopilot_steps


class AutopilotTests(unittest.TestCase):
    def test_planner_builds_status_plan(self) -> None:
        plan = AutopilotPlanner().plan("give me a morning briefing")
        self.assertIn("briefing", plan)
        self.assertIn("reminders due", plan)

    def test_planner_builds_project_health_plan(self) -> None:
        plan = AutopilotPlanner().plan("check project health")
        self.assertIn("scan files .", plan)
        self.assertIn("knowledge stats", plan)

    def test_safe_command_allowlist_blocks_side_effects(self) -> None:
        planner = AutopilotPlanner()
        self.assertTrue(planner.is_safe_command("read file README.md"))
        self.assertTrue(planner.is_safe_command("knowledge stats"))
        self.assertFalse(planner.is_safe_command("run command del important.txt"))
        self.assertFalse(planner.is_safe_command("email api send gmail to a@example.com subject x body y"))

    def test_tracker_persists_runs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "autopilot.json"
            tracker = AutopilotTracker(path)
            tracker.record_run(
                "status",
                [
                    AutopilotStep(index=0, command="briefing", status="ok", message="ok"),
                    AutopilotStep(index=1, command="run command dir", status="blocked", message="blocked"),
                ],
            )

            reopened = AutopilotTracker(path)
            latest = reopened.latest()

            self.assertEqual(latest["status"], "blocked")
            self.assertEqual(latest["ok_count"], 1)
            self.assertEqual(latest["blocked_count"], 1)

    def test_parse_autopilot_steps(self) -> None:
        self.assertEqual(parse_autopilot_steps("briefing ;; tasks ;; knowledge stats"), ["briefing", "tasks", "knowledge stats"])


if __name__ == "__main__":
    unittest.main()
