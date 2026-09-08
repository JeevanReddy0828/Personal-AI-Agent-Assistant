from __future__ import annotations

from laptop_agent.storage import atomic_write_text, read_json, synchronized

import json
from pathlib import Path
from typing import Any


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {"profile": {}, "preferences": {}, "notes": []}
        self.load()

    @synchronized
    def load(self) -> None:
        if not self.path.exists():
            return
        loaded = read_json(self.path, {})
        if isinstance(loaded, dict):
            self._data = {key: loaded.get(key) if isinstance(loaded.get(key), type(default)) else default
                          for key, default in {"profile": {}, "preferences": {}, "notes": []}.items()}

    @synchronized
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(self._data, indent=2, sort_keys=True))

    @synchronized
    def set_profile_value(self, key: str, value: str) -> None:
        self._data.setdefault("profile", {})[key] = value
        self.save()

    @synchronized
    def get_profile(self) -> dict[str, Any]:
        return dict(self._data.get("profile", {}))

    @synchronized
    def add_note(self, note: str) -> None:
        self._data.setdefault("notes", []).append(note)
        self.save()

    @synchronized
    def dump(self) -> dict[str, Any]:
        return dict(self._data)
