from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TaskRecord:
    index: int
    command: str
    status: str  # "ok" | "failed"
    message: str


@dataclass
class TaskTracker:
    """In-memory dashboard of recent parallel task runs.

    Each call to ``multi`` records one run: a batch of subtasks with their final
    status and message. ``latest`` returns the most recent run for the
    ``tasks`` command so the user can see what the agent did in parallel.
    """

    _runs: list[dict[str, object]] = field(default_factory=list)
    max_runs: int = 20

    def record_run(self, records: list[TaskRecord]) -> dict[str, object]:
        ok = sum(1 for record in records if record.status == "ok")
        failed = len(records) - ok
        run = {
            "run": len(self._runs) + 1,
            "task_count": len(records),
            "ok_count": ok,
            "failed_count": failed,
            "tasks": [record.__dict__ for record in records],
        }
        self._runs.append(run)
        if len(self._runs) > self.max_runs:
            self._runs = self._runs[-self.max_runs :]
        return run

    def latest(self) -> dict[str, object] | None:
        return self._runs[-1] if self._runs else None

    def all_runs(self) -> list[dict[str, object]]:
        return list(self._runs)
