from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class ScheduleError(ValueError):
    """Raised when a schedule expression cannot be parsed."""


@dataclass(frozen=True)
class Schedule:
    """A recurrence rule, stored as plain data so it round-trips through JSON.

    Two kinds, covering the common cases without a cron dependency:
    - interval: fire every ``seconds`` seconds.
    - daily: fire once per day at ``hour``:``minute`` (local-naive, compared in UTC).
    """

    kind: str  # "interval" | "daily"
    seconds: int = 0
    hour: int = 0
    minute: int = 0

    def describe(self) -> str:
        if self.kind == "interval":
            return f"every {_humanize_seconds(self.seconds)}"
        return f"daily at {self.hour:02d}:{self.minute:02d}"

    def to_dict(self) -> dict[str, object]:
        if self.kind == "interval":
            return {"kind": "interval", "seconds": self.seconds}
        return {"kind": "daily", "hour": self.hour, "minute": self.minute}

    @staticmethod
    def from_dict(data: dict) -> "Schedule":
        kind = str(data.get("kind", "interval"))
        if kind == "daily":
            return Schedule(kind="daily", hour=int(data.get("hour", 0)), minute=int(data.get("minute", 0)))
        return Schedule(kind="interval", seconds=int(data.get("seconds", 3600)))

    def is_due(self, now: datetime, last_run: datetime | None) -> bool:
        """Whether the job should fire at ``now``. The daily target is built in the same
        timezone as ``now`` (callers pass local-aware time), so 'daily at 08:00' fires at
        the user's 08:00, not 08:00 UTC."""
        if self.kind == "interval":
            if last_run is None:
                return True
            return (now - last_run).total_seconds() >= self.seconds
        target = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if now < target:
            return False
        # Past today's target: due unless we already ran at/after it today.
        return last_run is None or last_run < target


_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
}


def parse_schedule(text: str) -> Schedule:
    """Parse a human schedule like 'every 30 minutes', 'hourly', or 'daily at 08:30'."""
    lowered = text.strip().lower()
    if not lowered:
        raise ScheduleError("Empty schedule.")
    if lowered in {"hourly", "every hour"}:
        return Schedule(kind="interval", seconds=3600)
    if lowered in {"daily", "every day"}:
        return Schedule(kind="daily", hour=9, minute=0)
    if lowered in {"every minute", "minutely"}:
        return Schedule(kind="interval", seconds=60)

    daily = re.match(r"(?:daily\s+at|every\s+day\s+at|at)\s+(\d{1,2}):(\d{2})$", lowered)
    if daily:
        hour, minute = int(daily.group(1)), int(daily.group(2))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ScheduleError(f"Invalid time of day: {daily.group(1)}:{daily.group(2)}")
        return Schedule(kind="daily", hour=hour, minute=minute)

    interval = re.match(r"every\s+(\d+)\s+([a-z]+)$", lowered)
    if interval:
        amount, unit = int(interval.group(1)), interval.group(2)
        if unit not in _UNIT_SECONDS:
            raise ScheduleError(f"Unknown time unit '{unit}'. Use minutes, hours, or days.")
        if amount <= 0:
            raise ScheduleError("Interval must be a positive number.")
        return Schedule(kind="interval", seconds=amount * _UNIT_SECONDS[unit])

    raise ScheduleError(
        "Could not parse schedule. Try 'every 30 minutes', 'every 2 hours', 'hourly', or 'daily at 08:30'."
    )


def _humanize_seconds(seconds: int) -> str:
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds % size == 0 and seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


@dataclass
class ScheduledJob:
    id: int
    kind: str  # "command" | "agent"
    spec: str  # the command text or the agent goal
    schedule: Schedule
    enabled: bool = True
    created_at: str = ""
    last_run_at: str | None = None
    last_status: str | None = None
    run_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "spec": self.spec,
            "schedule": self.schedule.to_dict(),
            "schedule_text": self.schedule.describe(),
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "run_count": self.run_count,
        }

    @staticmethod
    def from_dict(data: dict) -> "ScheduledJob":
        return ScheduledJob(
            id=int(data["id"]),
            kind=str(data.get("kind", "command")),
            spec=str(data.get("spec", "")),
            schedule=Schedule.from_dict(data.get("schedule", {})),
            enabled=bool(data.get("enabled", True)),
            created_at=str(data.get("created_at", "")),
            last_run_at=data.get("last_run_at"),
            last_status=data.get("last_status"),
            run_count=int(data.get("run_count", 0)),
        )


class SchedulerStore:
    """Persistent store of scheduled jobs. Pure of execution: it tracks what should run
    and when (clock is supplied by the caller), but never runs anything itself."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._jobs: list[ScheduledJob] = []
        self._next_id = 1
        # The web UI runs a background ticker thread that calls due_jobs/mark_ran while
        # request threads may add/remove, so every read and mutation is serialized.
        self._lock = threading.Lock()
        self._load()

    def add(self, kind: str, spec: str, schedule_text: str, now: datetime) -> ScheduledJob:
        if kind not in {"command", "agent"}:
            raise ScheduleError("Job kind must be 'command' or 'agent'.")
        if not spec.strip():
            raise ScheduleError("Nothing to run — provide a command or goal.")
        schedule = parse_schedule(schedule_text)  # parse (may raise) before taking the lock
        with self._lock:
            job = ScheduledJob(
                id=self._next_id,
                kind=kind,
                spec=spec.strip(),
                schedule=schedule,
                created_at=now.isoformat(),
            )
            self._next_id += 1
            self._jobs.append(job)
            self._save()
            return job

    def remove(self, job_id: int) -> bool:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [job for job in self._jobs if job.id != job_id]
            if len(self._jobs) != before:
                self._save()
                return True
            return False

    def set_enabled(self, job_id: int, enabled: bool) -> bool:
        with self._lock:
            for job in self._jobs:
                if job.id == job_id:
                    job.enabled = enabled
                    self._save()
                    return True
            return False

    def list_jobs(self) -> list[ScheduledJob]:
        with self._lock:
            return list(self._jobs)

    def due_jobs(self, now: datetime) -> list[ScheduledJob]:
        with self._lock:
            due = []
            for job in self._jobs:
                if not job.enabled:
                    continue
                last = _parse_iso(job.last_run_at)
                if job.schedule.is_due(now, last):
                    due.append(job)
            return due

    def mark_ran(self, job_id: int, now: datetime, status: str) -> None:
        with self._lock:
            for job in self._jobs:
                if job.id == job_id:
                    job.last_run_at = now.isoformat()
                    job.last_status = status
                    job.run_count += 1
                    self._save()
                    return

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        jobs = data.get("jobs")
        if isinstance(jobs, list):
            for raw in jobs:
                if isinstance(raw, dict):
                    try:
                        self._jobs.append(ScheduledJob.from_dict(raw))
                    except (KeyError, ValueError, TypeError):
                        continue
        ids = [job.id for job in self._jobs]
        self._next_id = max([int(data.get("next_id", 1))] + [i + 1 for i in ids])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"next_id": self._next_id, "jobs": [job.to_dict() for job in self._jobs]}, indent=2),
            encoding="utf-8",
        )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
