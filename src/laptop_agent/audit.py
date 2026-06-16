from __future__ import annotations

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

    def record(self, event_type: str, **payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = AuditEvent(
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), sort_keys=True, default=str) + "\n")

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
