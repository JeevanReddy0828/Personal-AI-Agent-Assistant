from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from laptop_agent.reasoning import (
    AgentRunResult,
    AgentRunTracker,
    AgentStep,
    AutonomousAgent,
    parse_agent_decision,
)
from laptop_agent.tools.base import ToolResult


class _ScriptedBrain:
    """A reasoning model stand-in: returns canned replies in order, recording prompts."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._replies.pop(0) if self._replies else "FINAL: out of replies"


def _executor(handlers):
    async def execute(command: str) -> ToolResult:
        return handlers(command)

    return execute


class ParseTests(unittest.TestCase):
    def test_parses_action(self) -> None:
        d = parse_agent_decision("THOUGHT: look first\nACTION: scan files .")
        self.assertFalse(d.is_final)
        self.assertEqual(d.command, "scan files .")
        self.assertEqual(d.thought, "look first")

    def test_final_wins_over_action(self) -> None:
        d = parse_agent_decision("THOUGHT: done\nACTION: scan files .\nFINAL: all set")
        self.assertTrue(d.is_final)
        self.assertEqual(d.final_answer, "all set")

    def test_strips_code_fences_and_quotes(self) -> None:
        d = parse_agent_decision('ACTION: `"read file README.md"`')
        self.assertEqual(d.command, "read file README.md")

    def test_bare_text_is_final(self) -> None:
        d = parse_agent_decision("I think the answer is 42.")
        self.assertTrue(d.is_final)
        self.assertIn("42", d.final_answer)

    def test_none_action_falls_through_to_final(self) -> None:
        d = parse_agent_decision("ACTION: none")
        self.assertTrue(d.is_final)


class AutonomousAgentTests(unittest.TestCase):
    def test_runs_steps_then_finishes(self) -> None:
        brain = _ScriptedBrain(
            [
                "THOUGHT: inspect\nACTION: scan files .",
                "THOUGHT: read it\nACTION: read file README.md",
                "THOUGHT: enough\nFINAL: Scanned the folder and read the README.",
            ]
        )
        seen: list[str] = []

        def handlers(command: str) -> ToolResult:
            seen.append(command)
            return ToolResult.success(f"ran {command}", count=1)

        agent = AutonomousAgent(brain, _executor(handlers), command_reference="- scan files <path>")
        result = asyncio.run(agent.run("look around"))

        self.assertEqual(result.status, "ok")
        self.assertEqual(seen, ["scan files .", "read file README.md"])
        self.assertEqual(len(result.steps), 2)
        self.assertIn("README", result.final_answer)
        # The scratchpad from earlier steps must reach the model on later turns.
        self.assertIn("scan files .", brain.prompts[-1])

    def test_observes_failures_and_keeps_going(self) -> None:
        brain = _ScriptedBrain(
            [
                "ACTION: read file missing.txt",
                "FINAL: could not read the file, stopping",
            ]
        )

        def handlers(command: str) -> ToolResult:
            return ToolResult.failure("no such file")

        agent = AutonomousAgent(brain, _executor(handlers))
        result = asyncio.run(agent.run("read a file"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.steps[0].status, "failed")
        self.assertIn("failed", result.steps[0].message)

    def test_step_cap_forces_summary(self) -> None:
        brain = _ScriptedBrain(["ACTION: tasks"] * 10)  # never emits FINAL

        def handlers(command: str) -> ToolResult:
            return ToolResult.success("ok")

        agent = AutonomousAgent(brain, _executor(handlers), max_steps=3)
        result = asyncio.run(agent.run("loop forever"))
        self.assertEqual(result.status, "stopped")
        self.assertEqual(len(result.steps), 3)
        self.assertTrue(result.final_answer)

    def test_on_step_callback_streams_each_step(self) -> None:
        brain = _ScriptedBrain(["ACTION: tasks", "ACTION: memory", "FINAL: done"])
        seen = []
        agent = AutonomousAgent(brain, _executor(lambda c: ToolResult.success("ok")))
        asyncio.run(agent.run("two steps", on_step=lambda step: seen.append(step.command)))
        self.assertEqual(seen, ["tasks", "memory"])

    def test_on_step_failure_is_swallowed(self) -> None:
        brain = _ScriptedBrain(["ACTION: tasks", "FINAL: done"])

        def boom(_step):
            raise RuntimeError("ui blew up")

        agent = AutonomousAgent(brain, _executor(lambda c: ToolResult.success("ok")))
        result = asyncio.run(agent.run("one step", on_step=boom))  # must not raise
        self.assertEqual(result.status, "ok")

    def test_no_brain_fails_cleanly(self) -> None:
        agent = AutonomousAgent(lambda _p: "", _executor(lambda c: ToolResult.success("x")))
        result = asyncio.run(agent.run("do a thing"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.steps, [])

    def test_empty_goal(self) -> None:
        agent = AutonomousAgent(lambda _p: "FINAL: x", _executor(lambda c: ToolResult.success("x")))
        result = asyncio.run(agent.run("   "))
        self.assertEqual(result.status, "failed")


class AgentRunTrackerTests(unittest.TestCase):
    def test_persists_runs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "agent_runs.json"
            tracker = AgentRunTracker(path)
            tracker.record_run(
                AgentRunResult(
                    goal="g",
                    final_answer="done",
                    status="ok",
                    steps=[AgentStep(index=0, thought="t", command="tasks", status="ok", message="[ok] ran")],
                )
            )
            reopened = AgentRunTracker(path)
            latest = reopened.latest()
            self.assertEqual(latest["status"], "ok")
            self.assertEqual(latest["ok_count"], 1)
            self.assertEqual(latest["run"], 1)
            self.assertEqual(reopened.all_runs()[0]["goal"], "g")


if __name__ == "__main__":
    unittest.main()
