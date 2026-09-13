from __future__ import annotations

from laptop_agent.storage import atomic_write_text, read_json, synchronized

import json
import re
from pathlib import Path
from typing import Any

_KEY_SHAPE = re.compile(r"[^a-z0-9]+")


def _normalized(key: str) -> str:
    """How a key reads, ignoring spelling of the separator.

    `remember` stores whatever shape the phrasing produced — "favourite editor" from one
    sentence, "favorite_color" from another — so forgetting has to match on the words.
    """
    return _KEY_SHAPE.sub(" ", (key or "").lower()).strip()


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
    def forget_profile_value(self, key: str) -> str | None:
        """Remove one remembered fact; returns the key removed, or None if there was none.

        Anything it is told, it could never be told to drop again — there was no way to
        remove a fact once saved, so a wrong answer or a stray write stayed in "what do you
        remember about me?" forever.
        """
        profile = self._data.setdefault("profile", {})
        wanted = _normalized(key)
        if not wanted:
            return None
        for existing in list(profile):
            if _normalized(existing) == wanted:
                del profile[existing]
                self.save()
                return existing
        return None

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
