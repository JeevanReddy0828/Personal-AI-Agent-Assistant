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


# Everything the store keeps, with the empty value each starts from. A file written before
# a section existed simply loads it empty.
_SECTIONS: dict[str, Any] = {"profile": {}, "preferences": {}, "notes": [], "lists": {}}

# One list, however it is called: "grocery list" and "shopping list" are the same list.
_LIST_ALIASES = {"grocery": "shopping", "groceries": "shopping", "shop": "shopping",
                 "to do": "todo", "to-do": "todo", "todos": "todo", "things to do": "todo", "task": "todo",
                 "tasks": "todo"}


def list_name(name: str) -> str:
    cleaned = _normalized(re.sub(r"\blists?\b", " ", name or ""))
    return _LIST_ALIASES.get(cleaned, cleaned)


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {key: type(default)() for key, default in _SECTIONS.items()}
        self.load()

    @synchronized
    def load(self) -> None:
        if not self.path.exists():
            return
        loaded = read_json(self.path, {})
        if isinstance(loaded, dict):
            self._data = {key: loaded.get(key) if isinstance(loaded.get(key), type(default)) else type(default)()
                          for key, default in _SECTIONS.items()}

    @synchronized
    def lists(self) -> dict[str, list[str]]:
        return {name: list(items) for name, items in self._data.setdefault("lists", {}).items() if items}

    @synchronized
    def list_items(self, name: str) -> list[str]:
        return list(self._data.setdefault("lists", {}).get(list_name(name), []))

    @synchronized
    def add_to_list(self, name: str, items: list[str]) -> list[str]:
        """Add items, skipping any already there; returns the ones actually added."""
        current = self._data.setdefault("lists", {}).setdefault(list_name(name), [])
        present = {_normalized(item) for item in current}
        added = []
        for item in items:
            cleaned = item.strip()
            if cleaned and _normalized(cleaned) not in present:
                current.append(cleaned)
                present.add(_normalized(cleaned))
                added.append(cleaned)
        if added:
            self.save()
        return added

    @synchronized
    def remove_from_list(self, name: str, item: str) -> str | None:
        """Remove the item that reads like `item`; returns it, or None if nothing does."""
        current = self._data.setdefault("lists", {}).get(list_name(name), [])
        wanted = _normalized(item)
        found = next((entry for entry in current if _normalized(entry) == wanted), None)
        if found is None:
            found = next((entry for entry in current if wanted and wanted in _normalized(entry)), None)
        if found is not None:
            current.remove(found)
            self.save()
        return found

    @synchronized
    def clear_list(self, name: str) -> int:
        removed = len(self._data.setdefault("lists", {}).pop(list_name(name), []))
        if removed:
            self.save()
        return removed

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
    def forget_notes(self, words: str) -> list[str]:
        """Drop every note containing all of these words; returns what was dropped.

        Notes could be added and never removed, so something said in passing - a parking
        spot, a door code - stayed in every answer about the user for good.
        """
        # Only words that say something: "forget the" must not match every note with "the".
        filler = {"the", "a", "an", "my", "i", "that", "it", "to", "of", "on", "in", "at", "is",
                  "was", "me", "about", "and", "or", "for", "this", "what", "told", "you", "said"}
        wanted = [word for word in _normalized(words).split() if word not in filler]
        if not wanted:
            return []
        notes = self._data.setdefault("notes", [])
        dropped = [note for note in notes if all(word in _normalized(str(note)).split() for word in wanted)]
        if dropped:
            self._data["notes"] = [note for note in notes if note not in dropped]
            self.save()
        return [str(note) for note in dropped]

    @synchronized
    def dump(self) -> dict[str, Any]:
        return dict(self._data)
