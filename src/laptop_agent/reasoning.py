from __future__ import annotations

from laptop_agent.failures import record_failure
from laptop_agent.cancellation import check_cancelled
from laptop_agent.context import FOLLOW_UP_RULE
from laptop_agent.storage import atomic_write_text, read_json, synchronized

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
_FINAL_HEAD_RE = re.compile(r"^[ \t]*(FINAL|ANSWER|DONE)[ \t]*:[ \t]*", re.IGNORECASE | re.MULTILINE)
_THOUGHT_RE = re.compile(r"^\s*(?:THOUGHT|THINK|REASON)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
# The loop's own label for a tool result. Upper case only, as the prompt spells it, so an
# answer may still say "Observation: ..." in its prose.
_OBSERVATION_RE = re.compile(r"^[ \t]*OBSERVATION[ \t]*:", re.MULTILINE)
_FORMAT_ACTION_RE = re.compile(r"\s*ACTION\s*:")
_FORMAT_REMINDER = (
    "\n\nYour last reply had no ACTION: line and no FINAL: line, so nothing could run. "
    "Reply again in exactly the format above: THOUGHT then ACTION, or THOUGHT then FINAL."
)
# What a deliverable written before FINAL looks like (code, headings, tables, lists,
# Mermaid), as opposed to leftover reasoning prose.
_DELIVERABLE_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:```|~~~|#{1,6}[ \t]|\|.*\||[-*][ \t]|\d+\.[ \t]|(?:erDiagram|flowchart|graph|sequenceDiagram|classDiagram|stateDiagram)\b)"
)


@dataclass(frozen=True)
class AgentDecision:
    """One parsed turn of the reasoning model."""

    thought: str
    command: str
    final_answer: str
    is_final: bool
    # False when the reply had neither an ACTION nor a FINAL line and was taken whole.
    structured: bool = True


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


_FENCE_OPEN_RE = re.compile(r"^\s{0,3}(```|~~~)")


def _fenced_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of fenced code blocks (an unclosed fence runs to the end)."""
    spans: list[tuple[int, int]] = []
    marker, start, position = "", 0, 0
    for line in text.splitlines(keepends=True):
        if marker:
            if line.strip() == marker:
                spans.append((start, position + len(line)))
                marker = ""
        else:
            opener = _FENCE_OPEN_RE.match(line)
            if opener:
                marker, start = opener.group(1), position
        position += len(line)
    if marker:
        spans.append((start, len(text)))
    return spans


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


def _drop_invented_observation(raw: str) -> str:
    """Cut a reply at an OBSERVATION it wrote itself, unless a FINAL answer came first.

    Only the loop writes observations, after running an ACTION. Asked to summarise a README,
    a model wrote "OBSERVATION: [ok] ... # Codex Project ..." for a file that says nothing of
    the kind, summarised that, and the run reported it as the answer. Whatever follows an
    invented result rests on it, so it goes too; an ACTION written before it still runs.
    """
    fenced = _fenced_spans(raw)

    def outside(match: re.Match[str]) -> bool:
        return not any(start <= match.start() < end for start, end in fenced)

    observation = next((match for match in _OBSERVATION_RE.finditer(raw) if outside(match)), None)
    if observation is None:
        return raw
    final = next((match for match in _FINAL_HEAD_RE.finditer(raw) if outside(match)), None)
    if final is not None and final.start() < observation.start():
        return raw
    return raw[: observation.start()].rstrip()


def parse_agent_decision(text: str) -> AgentDecision:
    """Parse a reasoning turn: the first of a real ACTION and FINAL wins; bare text is a final answer.

    A FINAL written after an ACTION was written before that action ran, so the action runs
    and the FINAL goes, as an invented OBSERVATION does. FINAL used to win outright, and
    live a run ended on "[The full content of README.md would be provided here after the
    action runs...]", another on an answer the model had just said it could not give yet.
    """
    raw = _drop_invented_observation((text or "").strip())
    thought_match = _THOUGHT_RE.search(raw)
    thought = thought_match.group(1).strip() if thought_match else ""

    # Prefer an upper-case header outside any fenced block: a deliverable may legitimately
    # contain a line such as "Done: migrated", "Answer: 4" or a "DONE:" YAML key.
    fenced = _fenced_spans(raw)
    heads = [head for head in _FINAL_HEAD_RE.finditer(raw) if not any(start <= head.start() < end for start, end in fenced)]
    final_match = next((head for head in heads if head.group(1).isupper()), heads[0] if heads else None)
    action_match = next(
        (action for action in _ACTION_RE.finditer(raw)
         if _runnable(action) and not any(start <= action.start() < end for start, end in fenced)),
        None,
    )
    # Ahead of a FINAL only in the format's own spelling: a deliverable written before FINAL
    # may hold a prose line such as "Next: run the installer", which is not a step.
    if action_match and (final_match is None or (_FORMAT_ACTION_RE.match(action_match.group(0))
                                                 and action_match.start() < final_match.start())):
        return AgentDecision(thought=thought, command=_runnable(action_match), final_answer="", is_final=False)
    if final_match:
        answer = raw[final_match.end():].strip().strip("`").strip()
        # The model sometimes writes the deliverable (a diagram, code, a table) and then a
        # one-line FINAL that refers to it "above" — keep that body, minus the headers.
        body = _ACTION_RE.sub("", _THOUGHT_RE.sub("", raw[: final_match.start()])).strip()
        if body and _DELIVERABLE_RE.search("\n" + body):
            answer = f"{body}\n\n{answer}".strip()
        return AgentDecision(thought=thought, command="", final_answer=answer, is_final=True)

    # No structured headers: the model just answered. Treat the whole thing as the
    # final answer so the loop ends gracefully instead of spinning. Strip a THOUGHT:
    # header so the user sees clean prose; if the reply was *only* a thought, surface
    # that thought's text (without the label) rather than the raw 'THOUGHT: …' line.
    if thought_match:
        stripped = _THOUGHT_RE.sub("", raw).strip()
        answer = stripped or thought
    else:
        answer = raw
    return AgentDecision(
        thought=thought, command="", final_answer=answer or raw, is_final=True, structured=bool(_ACTION_RE.search(raw))
    )


def _runnable(action: re.Match[str]) -> str:
    """The command an ACTION line names, or '' for 'none', 'stop' and the like."""
    command = _strip_command(action.group(1))
    return "" if command.lower() in {"none", "n/a", "stop", "done"} else command


def _describe_datum(key: str, value: object) -> str:
    """One fact about a piece of tool data, chosen so the agent can reason about it.

    Naming the keys was not enough. Asked to count the Python files in src, the agent
    saw "[data: files, root]" against a truncated message and answered 27 for a list of
    131 — it had no way to know the size of what it was looking at, and no hint that the
    message had been cut, so it counted the few names it could see and stated the result
    as fact. A length is the one fact that makes a list answerable.
    """
    if isinstance(value, (list, tuple)):
        return f"{key}: {len(value)} items"
    if isinstance(value, dict):
        # A small mapping of scalars *is* the fact - `by_extension={'.py': 65, ...}` is the
        # answer to "how many python files are in src", where "by_extension: 4 fields"
        # sent the agent back to counting names and it said 42 against a true 65.
        scalars = {
            str(k): v for k, v in list(value.items())[:10]
            if isinstance(v, (int, float, bool, str)) and len(str(v)) <= 40
        }
        if scalars and len(scalars) == len(value):
            rendered = ", ".join(f"{k}={v}" for k, v in scalars.items())
            if len(rendered) <= 220:
                return f"{key}={{{rendered}}}"
        return f"{key}: {len(value)} fields"
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return f"{key}={value}"
    text = str(value)
    if len(text) > 60:
        return f"{key}: {len(text)} chars"
    return f"{key}={text}"


def _observe(result: ToolResult) -> str:
    """Compress a tool result into a short observation line for the scratchpad.

    Truncation is stated rather than implied: an agent that cannot tell a complete
    observation from a clipped one will answer confidently from the visible fragment.
    """
    status = "ok" if result.ok else "failed"
    message = (result.message or "").strip().replace("\n", " ")
    cut = ""
    if len(message) > 320:
        cut = f" [message clipped: showing 317 of {len(message)} characters]"
        message = message[:317] + "…"
    data = result.data if isinstance(result.data, dict) else {}
    facts = ", ".join(_describe_datum(key, value) for key, value in sorted(data.items()))
    suffix = f" [data: {facts}]" if facts else ""
    return f"[{status}] {message}{cut}{suffix}"


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

    def _build_prompt(self, goal: str, steps: list[AgentStep], context: str = "") -> str:
        lines = [
            "You are the autonomous executor inside a local laptop assistant.",
            "Achieve the user's GOAL by choosing ONE command at a time from the AVAILABLE COMMANDS.",
            "After each command you will be shown its OBSERVATION, then choose the next command.",
            "Reply in EXACTLY this format:",
            "THOUGHT: <one short sentence of reasoning>",
            "ACTION: <a single command, copied verbatim from AVAILABLE COMMANDS with concrete arguments>",
            "When the goal is met (or cannot proceed), instead reply:",
            "THOUGHT: <why you are stopping>",
            "FINAL: <the complete answer for the user — everything they should see goes after FINAL:, "
            "including any diagram, code or table; it may span many lines>",
            "Rules: one command per turn, no prose outside the format, never invent commands, and never "
            "write OBSERVATION yourself: it is added after your ACTION runs.",
            "",
            f"GOAL: {goal}",
        ]
        if context:
            lines += [
                "",
                "CONVERSATION CONTEXT (this session — what the user and you already said):",
                context,
                "If the GOAL refers to something in the CONVERSATION CONTEXT ('this', 'it', 'the schema above'),",
                f"work from that text. {FOLLOW_UP_RULE}",
                "If the goal can be completed from the context alone (a diagram, summary, rewrite or answer about",
                "it), reply FINAL: immediately with the complete result.",
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

    async def run(
        self,
        goal: str,
        on_step: Callable[[AgentStep], None] | None = None,
        context: str = "",
    ) -> AgentRunResult:
        """Run the loop. ``on_step`` (if given) is called after each executed step so a UI
        can render the trace live; it must not raise (failures are swallowed). ``context``
        is the session transcript block (``laptop_agent.context``) so a goal like "build an
        ERD for this" resolves against what was already said."""
        goal = goal.strip()
        context = (context or "").strip()
        if not goal:
            return AgentRunResult(goal="", final_answer="No goal was provided.", status="failed")

        def _emit(step: AgentStep) -> None:
            if on_step is None:
                return
            try:
                on_step(step)
            except Exception as exc:
                # A broken progress callback must not abort the run, but losing every
                # step notification silently makes a live agent look frozen.
                record_failure("agent/on_step", exc, command=step.command[:80])

        steps: list[AgentStep] = []
        for index in range(self.max_steps):
            check_cancelled()
            prompt = self._build_prompt(goal, steps, context)
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

            check_cancelled()
            decision = parse_agent_decision(reply)
            if not decision.structured:
                decision = self._ask_again(prompt, decision)
            if decision.is_final and not decision.structured and not decision.final_answer:
                return AgentRunResult(
                    goal=goal,
                    final_answer="The reasoning model did not reply with a step or an answer.",
                    status="failed",
                    steps=steps,
                )
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
        check_cancelled()
        summary = self._summarize(goal, steps, context)
        return AgentRunResult(goal=goal, final_answer=summary, status="stopped", steps=steps)

    def _ask_again(self, prompt: str, decision: AgentDecision) -> AgentDecision:
        """Ask once more when a reply had neither an ACTION nor a FINAL line.

        Such a reply was taken as the answer, so a run ended "ok" on the model's own
        deliberation ("Actually, looking at the AVAILABLE COMMANDS... But wait..."). A second
        reply without them is still accepted, so a model that simply answered is not refused.
        """
        try:
            retried = parse_agent_decision(self._decide(prompt + _FORMAT_REMINDER) or "")
        except Exception as exc:
            record_failure("agent/ask-again", exc)
            return decision
        check_cancelled()
        return retried if retried.structured or not decision.final_answer else decision

    def _summarize(self, goal: str, steps: list[AgentStep], context: str = "") -> str:
        prompt = self._build_prompt(goal, steps, context) + (
            "\n\nYou have reached the step limit. Reply with FINAL: <summary of progress "
            "and what remains> only."
        )
        try:
            reply = self._decide(prompt) or ""
            check_cancelled()
            decision = parse_agent_decision(reply)
            if decision.final_answer:
                return decision.final_answer
        except Exception as exc:
            # The step limit message below is the fallback; without this the reason the
            # agent stopped reasoning was thrown away.
            record_failure("agent/final", exc, goal=goal[:80])
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

    @synchronized
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

    @synchronized
    def latest(self) -> dict[str, object] | None:
        return self._runs[-1] if self._runs else None

    @synchronized
    def all_runs(self) -> list[dict[str, object]]:
        return list(self._runs)

    @synchronized
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = read_json(self.path, {})
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

    @synchronized
    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps({'next_run': self._next_run, 'runs': self._runs[-self.max_runs:]}, indent=2))

    @synchronized
    def _infer_next_run(self) -> int:
        numbers = []
        for run in self._runs:
            try:
                numbers.append(int(run.get("run", 0)))
            except (TypeError, ValueError):
                continue
        return (max(numbers) + 1) if numbers else 1
