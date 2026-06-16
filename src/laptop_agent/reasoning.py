from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Awaitable, Callable

from laptop_agent.tools.base import ToolResult


# How the reasoning model is asked to answer each turn. We accept a couple of
# header spellings so a smaller model that drifts slightly still parses.
_ACTION_RE = re.compile(r"^\s*(?:ACTION|COMMAND|NEXT)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_FINAL_RE = re.compile(r"^\s*(?:FINAL|ANSWER|DONE)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_THOUGHT_RE = re.compile(r"^\s*(?:THOUGHT|THINK|REASON)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class AgentDecision:
    """One parsed turn of the reasoning model."""

    thought: str
    command: str
    final_answer: str
    is_final: bool


@dataclass(frozen=True)
class AgentStep:
    index: int
    thought: str
    command: str
    status: str  # ok | failed | blocked
    message: str


@dataclass
class AgentRunResult:
    goal: str
    final_answer: str
    status: str  # ok | failed | stopped
    steps: list[AgentStep] = field(default_factory=list)


def _strip_command(raw: str) -> str:
    """Pull a runnable command out of a model line — drop fences, quotes, trailing prose."""
    command = raw.strip()
    # The model sometimes wraps the command in backticks or quotes.
    command = command.strip("`").strip()
    if (command.startswith('"') and command.endswith('"')) or (command.startswith("'") and command.endswith("'")):
        command = command[1:-1].strip()
    # Keep only the first line — the command is a single instruction.
    command = command.splitlines()[0].strip() if command else command
    return command


def parse_agent_decision(text: str) -> AgentDecision:
    """Parse a reasoning turn. FINAL wins over ACTION; bare text is treated as a final answer."""
    raw = (text or "").strip()
    thought_match = _THOUGHT_RE.search(raw)
    thought = thought_match.group(1).strip() if thought_match else ""

    final_match = _FINAL_RE.search(raw)
    if final_match:
        answer = final_match.group(1).strip().strip("`").strip()
        return AgentDecision(thought=thought, command="", final_answer=answer, is_final=True)

    action_match = _ACTION_RE.search(raw)
    if action_match:
        command = _strip_command(action_match.group(1))
        if command and command.lower() not in {"none", "n/a", "stop", "done"}:
            return AgentDecision(thought=thought, command=command, final_answer="", is_final=False)

    # No structured headers: the model just answered. Treat the whole thing as the
    # final answer so the loop ends gracefully instead of spinning. Drop a leading
    # THOUGHT: header if that is all it emitted, so the user sees clean prose.
    answer = _THOUGHT_RE.sub("", raw).strip() if thought_match else raw
    return AgentDecision(thought=thought, command="", final_answer=answer or raw, is_final=True)


def _observe(result: ToolResult) -> str:
    """Compress a tool result into a short observation line for the scratchpad."""
    status = "ok" if result.ok else "failed"
    message = (result.message or "").strip().replace("\n", " ")
    if len(message) > 320:
        message = message[:317] + "…"
    keys = ", ".join(sorted(result.data.keys())) if isinstance(result.data, dict) and result.data else ""
    suffix = f" [data: {keys}]" if keys else ""
    return f"[{status}] {message}{suffix}"


class AutonomousAgent:
    """A plan -> act -> observe -> replan loop over the agent's own command set.

    The reasoning model is injected as ``decide`` (prompt string -> reply string) so the
    happy path is unit-tested offline. ``execute`` runs one command and returns a
    ``ToolResult`` — in production this is the orchestrator's own ``handle`` path, so risky
    tools still pass through the approval gate. Unlike autopilot (safe allowlist only), this
    loop can genuinely act, then react to what it observes.
    """

    def __init__(
        self,
        decide: Callable[[str], str],
        execute: Callable[[str], Awaitable[ToolResult]],
        command_reference: str = "",
        max_steps: int = 6,
    ) -> None:
        self._decide = decide
        self._execute = execute
        self._command_reference = command_reference.strip()
        self.max_steps = max(1, max_steps)

    def _build_prompt(self, goal: str, steps: list[AgentStep]) -> str:
        lines = [
            "You are the autonomous executor inside a local laptop assistant.",
            "Achieve the user's GOAL by choosing ONE command at a time from the AVAILABLE COMMANDS.",
            "After each command you will be shown its OBSERVATION, then choose the next command.",
            "Reply in EXACTLY this format:",
            "THOUGHT: <one short sentence of reasoning>",
            "ACTION: <a single command, copied verbatim from AVAILABLE COMMANDS with concrete arguments>",
            "When the goal is met (or cannot proceed), instead reply:",
            "THOUGHT: <why you are stopping>",
            "FINAL: <a concise answer for the user, summarizing what you did and found>",
            "Rules: one command per turn, no prose outside the format, never invent commands.",
            "",
            f"GOAL: {goal}",
        ]
        if self._command_reference:
            lines += ["", "AVAILABLE COMMANDS:", self._command_reference]
        if steps:
            lines += ["", "PROGRESS SO FAR:"]
            for step in steps:
                lines.append(f"{step.index + 1}. ACTION: {step.command}")
                lines.append(f"   OBSERVATION: [{step.status}] {step.message}")
        else:
            lines += ["", "PROGRESS SO FAR: (nothing yet — choose the first command)"]
        lines += ["", "Your turn:"]
        return "\n".join(lines)

    async def run(self, goal: str, on_step: Callable[[AgentStep], None] | None = None) -> AgentRunResult:
        """Run the loop. ``on_step`` (if given) is called after each executed step so a UI
        can render the trace live; it must not raise (failures are swallowed)."""
        goal = goal.strip()
        if not goal:
            return AgentRunResult(goal="", final_answer="No goal was provided.", status="failed")

        def _emit(step: AgentStep) -> None:
            if on_step is None:
                return
            try:
                on_step(step)
            except Exception:
                pass

        steps: list[AgentStep] = []
        for index in range(self.max_steps):
            prompt = self._build_prompt(goal, steps)
            try:
                reply = self._decide(prompt) or ""
            except Exception as exc:  # the brain is injected; never crash the loop on it
                return AgentRunResult(
                    goal=goal,
                    final_answer=f"Reasoning model error: {exc}",
                    status="failed",
                    steps=steps,
                )
            if not reply.strip():
                return AgentRunResult(
                    goal=goal,
                    final_answer="No reasoning model is available to plan this goal.",
                    status="failed",
                    steps=steps,
                )

            decision = parse_agent_decision(reply)
            if decision.is_final:
                return AgentRunResult(
                    goal=goal,
                    final_answer=decision.final_answer or "Done.",
                    status="ok",
                    steps=steps,
                )

            try:
                result = await self._execute(decision.command)
            except Exception as exc:
                result = ToolResult.failure(str(exc))

            step = AgentStep(
                index=index,
                thought=decision.thought,
                command=decision.command,
                status="ok" if result.ok else "failed",
                message=_observe(result),
            )
            steps.append(step)
            _emit(step)

        # Ran out of steps without a FINAL — ask for a closing summary, falling back
        # to a local recap so we always hand the user something coherent.
        summary = self._summarize(goal, steps)
        return AgentRunResult(goal=goal, final_answer=summary, status="stopped", steps=steps)

    def _summarize(self, goal: str, steps: list[AgentStep]) -> str:
        prompt = self._build_prompt(goal, steps) + (
            "\n\nYou have reached the step limit. Reply with FINAL: <summary of progress "
            "and what remains> only."
        )
        try:
            reply = self._decide(prompt) or ""
            decision = parse_agent_decision(reply)
            if decision.final_answer:
                return decision.final_answer
        except Exception:
            pass
        ran = "; ".join(f"{step.command} -> {step.status}" for step in steps) or "no steps ran"
        return f"Reached the {self.max_steps}-step limit on: {goal}. Progress: {ran}."


class AgentRunTracker:
    """Persists autonomous agent runs so history survives restarts (mirrors AutopilotTracker)."""

    def __init__(self, path: Path, max_runs: int = 30) -> None:
        self.path = path
        self.max_runs = max_runs
        self._runs: list[dict[str, object]] = []
        self._next_run = 1
        self._load()

    def record_run(self, result: AgentRunResult) -> dict[str, object]:
        ok = sum(1 for step in result.steps if step.status == "ok")
        failed = sum(1 for step in result.steps if step.status == "failed")
        run = {
            "run": self._next_run,
            "goal": result.goal,
            "created_at": datetime.now(UTC).isoformat(),
            "status": result.status,
            "final_answer": result.final_answer,
            "step_count": len(result.steps),
            "ok_count": ok,
            "failed_count": failed,
            "steps": [step.__dict__ for step in result.steps],
        }
        self._next_run += 1
        self._runs.append(run)
        if len(self._runs) > self.max_runs:
            self._runs = self._runs[-self.max_runs :]
        self._save()
        return run

    def latest(self) -> dict[str, object] | None:
        return self._runs[-1] if self._runs else None

    def all_runs(self) -> list[dict[str, object]]:
        return list(self._runs)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        runs = data.get("runs")
        if isinstance(runs, list):
            self._runs = [run for run in runs if isinstance(run, dict)][-self.max_runs :]
        try:
            self._next_run = max(int(data.get("next_run", 1)), self._infer_next_run())
        except (TypeError, ValueError):
            self._next_run = self._infer_next_run()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"next_run": self._next_run, "runs": self._runs[-self.max_runs :]}, indent=2),
            encoding="utf-8",
        )

    def _infer_next_run(self) -> int:
        numbers = []
        for run in self._runs:
            try:
                numbers.append(int(run.get("run", 0)))
            except (TypeError, ValueError):
                continue
        return (max(numbers) + 1) if numbers else 1
