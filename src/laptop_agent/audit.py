from __future__ import annotations

from laptop_agent.failures import record_failure
from laptop_agent.storage import atomic_write_text, read_json, synchronized

import json
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    payload: dict[str, Any]
    created_at: str


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path

    # The audit log is append-only and was never rotated, so it grew for the life of the
    # install. Every approval, every risky action, forever. Rotating keeps one previous
    # generation, which is what an audit trail is actually read for - what happened
    # recently - without turning a local app into a disk-space problem.
    MAX_BYTES = 8 * 1024 * 1024

    def _rotate_if_large(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size >= self.MAX_BYTES:
                previous = self.path.with_suffix(self.path.suffix + ".1")
                previous.unlink(missing_ok=True)
                self.path.replace(previous)
        except OSError as exc:  # never let housekeeping lose the event being recorded
            record_failure("audit/rotate", exc, path=str(self.path))

    @synchronized
    def record(self, event_type: str, **payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_large()
        event = AuditEvent(
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), sort_keys=True, default=str) + "\n")

    @synchronized
    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = deque(handle, maxlen=max(1, limit))
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events
