from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


SAFE_PREFIXES = (
    "help",
    "memory",
    "audit",
    "agents",
    "agent ",
    "briefing",
    "reminders",
    "tasks",
    "workflow status",
    "knowledge",
    "knowledge list",
    "knowledge stats",
    "recall ",
    "ask knowledge ",
    "scan files ",
    "read file ",
    "summarize file ",
    "extract text ",
    "file info ",
    "extract tables ",
)


@dataclass(frozen=True)
class AutopilotStep:
    index: int
    command: str
    status: str
    message: str


class AutopilotPlanner:
    """Small deterministic goal planner for unattended safe work."""

    def plan(self, goal: str) -> list[str]:
        lowered = goal.lower()
        if any(term in lowered for term in ("day", "morning", "brief", "status", "catch me up")):
            return ["briefing", "reminders due", "tasks", "agents", "knowledge stats"]
        if any(term in lowered for term in ("project", "code", "repo", "health", "workspace")):
            return ["scan files .", "tasks", "workflow status", "knowledge stats", "audit"]
        if any(term in lowered for term in ("knowledge", "memory", "what do you know")):
            return ["knowledge stats", "knowledge list", "memory"]
        return ["briefing", "tasks", "knowledge stats"]

    @staticmethod
    def is_safe_command(command: str) -> bool:
        lowered = command.strip().lower()
        return any(lowered == prefix.strip() or lowered.startswith(prefix) for prefix in SAFE_PREFIXES)


class AutopilotTracker:
    def __init__(self, path: Path, max_runs: int = 30) -> None:
        self.path = path
        self.max_runs = max_runs
        self._runs: list[dict[str, object]] = []
        self._next_run = 1
        self._load()

    def record_run(self, goal: str, steps: list[AutopilotStep]) -> dict[str, object]:
        blocked = sum(1 for step in steps if step.status == "blocked")
        failed = sum(1 for step in steps if step.status == "failed")
        ok = sum(1 for step in steps if step.status == "ok")
        run = {
            "run": self._next_run,
            "goal": goal,
            "created_at": datetime.now(UTC).isoformat(),
            "step_count": len(steps),
            "ok_count": ok,
            "failed_count": failed,
            "blocked_count": blocked,
            "status": "blocked" if blocked else "failed" if failed else "ok",
            "steps": [step.__dict__ for step in steps],
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


def parse_autopilot_steps(expression: str) -> list[str]:
    return [item.strip() for item in re.split(r"\s*;;\s*", expression) if item.strip()]
