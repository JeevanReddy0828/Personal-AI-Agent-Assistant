from __future__ import annotations

import unittest

from laptop_agent.agents.control_room import AgentControlRoom


class AgentControlRoomTests(unittest.TestCase):
    def test_snapshot_counts_available_idle_and_unavailable_agents(self) -> None:
        room = AgentControlRoom.standard(obsidian_available=False)
        snapshot = room.snapshot()
        self.assertEqual(snapshot["summary"]["total"], 10)
        self.assertEqual(snapshot["summary"]["working"], 0)
        self.assertEqual(snapshot["summary"]["unavailable"], 1)
        self.assertEqual(room.detail("notes")["status"], "unavailable")

    def test_marks_agent_working_then_idle(self) -> None:
        room = AgentControlRoom.standard(obsidian_available=True)
        agent_id = room.start("research local-first agents")
        self.assertEqual(agent_id, "research")
        self.assertEqual(room.detail("research")["status"], "working")
        self.assertEqual(room.detail("research")["current_task"], "research local-first agents")
        room.finish(agent_id, "done", ok=True)
        detail = room.detail("research")
        self.assertEqual(detail["status"], "idle")
        self.assertIsNone(detail["current_task"])
        self.assertEqual(detail["last_message"], "done")

    def test_unknown_command_routes_to_planner(self) -> None:
        room = AgentControlRoom.standard(obsidian_available=True)
        self.assertEqual(room.agent_for("tell me a joke"), "planner")

    def test_history_and_counters_accumulate(self) -> None:
        room = AgentControlRoom.standard(obsidian_available=True)
        a = room.start("scan files .")
        room.finish(a, "Scanned 3 files.", ok=True)
        b = room.start("summarize file missing.txt")
        room.finish(b, "File does not exist.", ok=False)
        detail = room.detail("files")
        self.assertEqual(detail["completed"], 1)
        self.assertEqual(detail["failed"], 1)
        self.assertEqual(len(detail["history"]), 2)
        self.assertEqual(detail["history"][0]["task"], "summarize file missing.txt")
        self.assertFalse(detail["history"][0]["ok"])

    def test_history_is_capped(self) -> None:
        room = AgentControlRoom.standard(obsidian_available=True)
        for i in range(20):
            a = room.start(f"recall item {i}")
            room.finish(a, "ok", ok=True)
        self.assertLessEqual(len(room.detail("knowledge")["history"]), 12)


if __name__ == "__main__":
    unittest.main()
