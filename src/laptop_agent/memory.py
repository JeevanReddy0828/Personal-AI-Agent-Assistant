from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {"profile": {}, "preferences": {}, "notes": []}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            self._data.update(loaded)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2, sort_keys=True)

    def set_profile_value(self, key: str, value: str) -> None:
        self._data.setdefault("profile", {})[key] = value
        self.save()

    def get_profile(self) -> dict[str, Any]:
        return dict(self._data.get("profile", {}))

    def add_note(self, note: str) -> None:
        self._data.setdefault("notes", []).append(note)
        self.save()

    def dump(self) -> dict[str, Any]:
        return dict(self._data)
