from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class WorkflowStep:
    index: int
    command: str
    status: str
    message: str


class WorkflowTracker:
    """Persistent history for sequential multi-step workflows."""

    def __init__(self, path: Path, max_runs: int = 30) -> None:
        self.path = path
        self.max_runs = max_runs
        self._runs: list[dict[str, object]] = []
        self._next_run = 1
        self._load()

    def record_run(self, steps: list[WorkflowStep], stopped_at: int | None = None) -> dict[str, object]:
        ok_count = sum(1 for step in steps if step.status == "ok")
        failed_count = sum(1 for step in steps if step.status != "ok")
        run = {
            "run": self._next_run,
            "created_at": datetime.now(UTC).isoformat(),
            "step_count": len(steps),
            "ok_count": ok_count,
            "failed_count": failed_count,
            "stopped_at": stopped_at,
            "retry_available": stopped_at is not None,
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

    def retry_commands(self, run_number: int | None = None) -> list[str]:
        run = self._find_run(run_number) if run_number is not None else self.latest()
        if not run:
            return []
        stopped_at = run.get("stopped_at")
        if stopped_at is None:
            return []
        try:
            start = int(stopped_at)
        except (TypeError, ValueError):
            return []
        commands = []
        for step in run.get("steps", []):
            if not isinstance(step, dict):
                continue
            try:
                index = int(step.get("index", -1))
            except (TypeError, ValueError):
                continue
            if index >= start and step.get("command"):
                commands.append(str(step["command"]))
        return commands

    def _find_run(self, run_number: int) -> dict[str, object] | None:
        for run in self._runs:
            if run.get("run") == run_number:
                return run
        return None

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
