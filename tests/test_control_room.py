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


if __name__ == "__main__":
    unittest.main()
