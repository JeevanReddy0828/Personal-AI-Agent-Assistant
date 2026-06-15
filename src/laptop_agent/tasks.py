from __future__ import annotations

from dataclasses import dataclass, field


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

    _runs: list[dict[str, object]] = field(default_factory=list)
    max_runs: int = 20
    _next_run: int = 1

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
