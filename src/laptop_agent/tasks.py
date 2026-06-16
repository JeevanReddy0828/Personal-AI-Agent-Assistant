from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TaskRecord:
    index: int
    command: str
    status: str  # "ok" | "failed"
    message: str
    attempts: int = 1


@dataclass
class TaskTracker:
    """In-memory dashboard of recent parallel task runs.

    Each call to ``multi`` records one run: a batch of subtasks with their final
    status and message. ``latest`` returns the most recent run for the
    ``tasks`` command so the user can see what the agent did in parallel.
    """

    storage_path: Path | None = None
    max_runs: int = 20
    _runs: list[dict[str, object]] = field(default_factory=list, init=False)
    _next_run: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        self._load()

    def record_run(self, records: list[TaskRecord], retry_of: int | None = None) -> dict[str, object]:
        ok = sum(1 for record in records if record.status == "ok")
        failed = len(records) - ok
        failed_commands = [record.command for record in records if record.status != "ok"]
        run = {
            "run": self._next_run,
            "retry_of": retry_of,
            "task_count": len(records),
            "ok_count": ok,
            "failed_count": failed,
            "retry_available": bool(failed_commands),
            "failed_commands": failed_commands,
            "tasks": [record.__dict__ for record in records],
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

    def failed_commands(self, run_number: int | None = None) -> list[str]:
        run = self._find_run(run_number) if run_number is not None else self.latest()
        if run is None:
            return []
        return [
            str(task.get("command", ""))
            for task in run.get("tasks", [])
            if isinstance(task, dict) and task.get("status") != "ok" and task.get("command")
        ]

    def retry_plan(self, run_number: int | None = None) -> dict[str, object]:
        run = self._find_run(run_number) if run_number is not None else self.latest()
        commands = self.failed_commands(run_number)
        return {
            "run": run.get("run") if run else None,
            "commands": commands,
            "count": len(commands),
        }

    def _find_run(self, run_number: int) -> dict[str, object] | None:
        for run in self._runs:
            if run.get("run") == run_number:
                return run
        return None

    def _load(self) -> None:
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
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
        if self.storage_path is None:
            return
        payload = {"next_run": self._next_run, "runs": self._runs[-self.max_runs :]}
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _infer_next_run(self) -> int:
        numbers = []
        for run in self._runs:
            try:
                numbers.append(int(run.get("run", 0)))
            except (TypeError, ValueError):
                continue
        return (max(numbers) + 1) if numbers else 1
