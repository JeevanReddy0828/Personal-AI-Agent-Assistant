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

    def test_deliverable_written_before_final_is_kept(self) -> None:
        # A diagram/code block followed by a one-line FINAL that says "above" must not
        # lose the diagram; a bare THOUGHT/ACTION preamble is still dropped.
        d = parse_agent_decision("THOUGHT: easy\n```mermaid\nflowchart TD\n  A-->B\n```\nFINAL: The chart is above.")
        self.assertTrue(d.is_final)
        self.assertIn("flowchart TD", d.final_answer)
        self.assertTrue(d.final_answer.endswith("The chart is above."))
        self.assertNotIn("THOUGHT", d.final_answer)
        d = parse_agent_decision("THOUGHT: done\nACTION: scan files .\nFINAL: all set")
        self.assertEqual(d.final_answer, "all set")
        # Leftover reasoning prose (even a wrapped multi-line THOUGHT) is not a deliverable.
        d = parse_agent_decision(
            "THOUGHT: I need to think about this carefully because the schema needs several\n"
            "considerations around normalization and refunds that I should mention first.\n"
            "FINAL: Here's the ERD."
        )
        self.assertEqual(d.final_answer, "Here's the ERD.")
        # A table is a deliverable.
        d = parse_agent_decision("| table | rows |\n|---|---|\n| users | 3 |\nFINAL: Counts above.")
        self.assertTrue(d.final_answer.startswith("| table |"))

    def test_uppercase_final_header_wins_over_a_prose_answer_line(self) -> None:
        raw = "Q: What is 2+2?\nAnswer: 4\nFINAL: The ERD is:\nerDiagram\n  USERS ||--o{ ORDERS : places"
        d = parse_agent_decision(raw)
        self.assertTrue(d.final_answer.startswith("The ERD is:"))
        self.assertIn("erDiagram", d.final_answer)
        self.assertNotIn("FINAL", d.final_answer)
        # Lower-case headers still parse when nothing better exists.
        self.assertEqual(parse_agent_decision("final: ok").final_answer, "ok")
        # An upper-case DONE: key inside a fenced deliverable is not the header.
        raw = "THOUGHT: config below\n```yaml\nTODO: pending\nDONE: complete\n```\nFINAL: The config is above."
        d = parse_agent_decision(raw)
        self.assertTrue(d.final_answer.startswith("```yaml\nTODO: pending\nDONE: complete\n```"))
        self.assertTrue(d.final_answer.endswith("The config is above."))
        self.assertNotIn("FINAL", d.final_answer)

    def test_thought_only_reply_drops_the_label(self) -> None:
        # A reply that is only a THOUGHT (no ACTION/FINAL) should surface the thought
        # text as the answer, without the literal 'THOUGHT:' prefix leaking to the user.
        d = parse_agent_decision("THOUGHT: I should summarize the README")
        self.assertTrue(d.is_final)
        self.assertEqual(d.final_answer, "I should summarize the README")
        self.assertNotIn("THOUGHT", d.final_answer)


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

    def test_conversation_context_reaches_the_model(self) -> None:
        brain = _ScriptedBrain(["FINAL: erDiagram\n  USERS ||--o{ ORDERS : places"])
        agent = AutonomousAgent(brain, _executor(lambda c: ToolResult.success("ok")), command_reference="- scan files <path>")
        context = "Recent conversation:\nUser: design a schema\nJ.A.R.V.I.S: users, orders, order_items tables"
        result = asyncio.run(agent.run("build an ERD for this", context=context))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.steps, [])                       # answered from context, no file hunting
        self.assertIn("order_items tables", brain.prompts[0])
        self.assertIn("CONVERSATION CONTEXT", brain.prompts[0])
        self.assertIn("reply FINAL: immediately", brain.prompts[0])

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


class ObservationTests(unittest.TestCase):
    """Asked to count the Python files in src, the agent answered 27 for a list of 131.
    The tool was right; the observation it was given named the data's keys and clipped the
    message without saying so, and the agent counted the few names it could still see."""

    def observe(self, result):
        from laptop_agent.reasoning import _observe

        return _observe(result)

    def test_a_list_reports_its_length(self) -> None:
        from laptop_agent.tools.base import ToolResult

        out = self.observe(ToolResult.success("Scanned files.", files=[{"p": i} for i in range(131)], root="src"))
        self.assertIn("files: 131 items", out)
        self.assertIn("root=src", out)

    def test_clipping_is_stated_not_implied(self) -> None:
        from laptop_agent.tools.base import ToolResult

        out = self.observe(ToolResult.success("x" * 900))
        self.assertIn("message clipped", out)
        self.assertIn("of 900 characters", out)

    def test_a_short_message_is_not_marked_clipped(self) -> None:
        from laptop_agent.tools.base import ToolResult

        self.assertNotIn("clipped", self.observe(ToolResult.success("all done")))

    def test_long_text_reports_its_size_rather_than_its_content(self) -> None:
        from laptop_agent.tools.base import ToolResult

        out = self.observe(ToolResult.success("Read it.", text="y" * 5000))
        self.assertIn("text: 5000 chars", out)
        self.assertNotIn("yyyy", out)

    def test_a_failure_still_reads_as_failed(self) -> None:
        from laptop_agent.tools.base import ToolResult

        self.assertTrue(self.observe(ToolResult.failure("nope")).startswith("[failed]"))


class SmallMappingObservationTests(unittest.TestCase):
    """`by_extension: 4 fields` told the agent nothing; `by_extension={.py=65}` is the
    answer to the question it was asked."""

    def observe(self, **data):
        from laptop_agent.reasoning import _observe
        from laptop_agent.tools.base import ToolResult

        return _observe(ToolResult.success("Scanned.", **data))

    def test_a_small_mapping_of_scalars_is_shown_whole(self) -> None:
        out = self.observe(by_extension={".py": 65, ".pyc": 66})
        self.assertIn(".py=65", out)

    def test_a_large_mapping_reports_its_size_instead(self) -> None:
        out = self.observe(counts={f"k{i}": i for i in range(40)})
        self.assertIn("counts: 40 fields", out)

    def test_a_mapping_of_objects_reports_its_size(self) -> None:
        out = self.observe(nested={"a": {"deep": 1}, "b": {"deep": 2}})
        self.assertIn("nested: 2 fields", out)
