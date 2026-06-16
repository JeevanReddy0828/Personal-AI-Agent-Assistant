from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Reminder:
    id: int
    message: str
    due_at: str
    done: bool = False
    created_at: str = ""


class ReminderStore:
    """Persistent local reminder list.

    This does not schedule OS notifications by itself. It stores reminders so
    the assistant can list upcoming items, show due items, and mark work done
    from any UI without adding platform-specific background services.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, due_at: str, message: str) -> dict[str, object]:
        due = self._parse_due_at(due_at)
        cleaned = message.strip()
        if not cleaned:
            return {"ok": False, "reason": "message is required"}
        store = self._load()
        reminder = Reminder(
            id=int(store["next_id"]),
            message=cleaned,
            due_at=due.isoformat(),
            created_at=datetime.now(UTC).isoformat(),
        )
        store["next_id"] = int(store["next_id"]) + 1
        store["reminders"].append(reminder.__dict__)
        self._save(store)
        return {"ok": True, "reminder": reminder.__dict__}

    def list(self, include_done: bool = False) -> list[dict[str, object]]:
        reminders = self._load()["reminders"]
        filtered = [item for item in reminders if include_done or not item.get("done")]
        return sorted(filtered, key=lambda item: str(item.get("due_at", "")))

    def due(self, now: datetime | None = None) -> list[dict[str, object]]:
        current = now or datetime.now(UTC)
        due_items = []
        for item in self.list(include_done=False):
            try:
                due_at = datetime.fromisoformat(str(item.get("due_at", "")))
            except ValueError:
                continue
            if due_at <= current:
                due_items.append(item)
        return due_items

    def complete(self, reminder_id: int) -> bool:
        store = self._load()
        changed = False
        for item in store["reminders"]:
            if int(item.get("id", 0)) == reminder_id and not item.get("done"):
                item["done"] = True
                item["completed_at"] = datetime.now(UTC).isoformat()
                changed = True
                break
        if changed:
            self._save(store)
        return changed

    @staticmethod
    def _parse_due_at(value: str) -> datetime:
        cleaned = value.strip().replace("Z", "+00:00")
        if "T" not in cleaned and " " in cleaned:
            cleaned = cleaned.replace(" ", "T", 1)
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            return {"next_id": 1, "reminders": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"next_id": 1, "reminders": []}
        if not isinstance(data, dict):
            return {"next_id": 1, "reminders": []}
        reminders = data.get("reminders")
        data["reminders"] = reminders if isinstance(reminders, list) else []
        try:
            data["next_id"] = max(int(data.get("next_id", 1)), self._infer_next_id(data["reminders"]))
        except (TypeError, ValueError):
            data["next_id"] = self._infer_next_id(data["reminders"])
        return data

    def _save(self, store: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _infer_next_id(reminders: list[object]) -> int:
        ids = []
        for item in reminders:
            if not isinstance(item, dict):
                continue
            try:
                ids.append(int(item.get("id", 0)))
            except (TypeError, ValueError):
                continue
        return (max(ids) + 1) if ids else 1
