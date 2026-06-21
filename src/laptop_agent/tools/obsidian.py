from __future__ import annotations

import re
from pathlib import Path

from laptop_agent.tools.base import ToolResult


class ObsidianVault:
    """Read, search, and write notes in a local Obsidian vault (a folder of .md files).

    The vault doubles as the agent's durable, human-readable memory: notes written
    here persist across restarts and are visible/editable inside Obsidian. All
    operations are read/write on local Markdown only, so they are not approval
    gated, like the JSON profile memory.
    """

    def __init__(self, vault_path: str | None) -> None:
        self.vault_path = Path(vault_path).expanduser() if vault_path else None

    def available(self) -> bool:
        return self.vault_path is not None and self.vault_path.is_dir()

    def status(self) -> ToolResult:
        if not self.vault_path:
            return ToolResult.failure("No Obsidian vault configured. Set OBSIDIAN_VAULT to your vault folder.")
        if not self.vault_path.is_dir():
            return ToolResult.failure(f"Obsidian vault folder not found: {self.vault_path}")
        notes = self._notes()
        return ToolResult.success(
            f"Obsidian vault connected with {len(notes)} note(s).",
            vault=str(self.vault_path),
            note_count=len(notes),
            memory_folder="Agent Memory",
        )

    def list_notes(self, limit: int = 50) -> ToolResult:
        if not self.available():
            return self.status()
        notes = self._notes()[:limit]
        return ToolResult.success(
            f"{len(notes)} note(s) in the vault.",
            notes=[{"name": note.stem, "rel": str(note.relative_to(self.vault_path))} for note in notes],
        )

    def search(self, query: str, limit: int = 8) -> ToolResult:
        if not self.available():
            return self.status()
        terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1]
        if not terms:
            return ToolResult.failure("Give something to search for in the vault.")
        hits: list[dict[str, object]] = []
        for note in self._notes():
            try:
                text = note.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lowered = text.lower()
            score = sum(lowered.count(term) for term in terms)
            if score <= 0:
                continue
            index = min((lowered.find(term) for term in terms if term in lowered), default=0)
            snippet = " ".join(text[max(0, index - 60): index + 180].split())
            hits.append({"name": note.stem, "rel": str(note.relative_to(self.vault_path)), "score": score, "snippet": snippet})
        hits.sort(key=lambda item: -int(item["score"]))
        return ToolResult.success(f"Found {len(hits)} matching note(s).", query=query, results=hits[:limit])

    def read_note(self, name: str, max_chars: int = 8000) -> ToolResult:
        if not self.available():
            return self.status()
        note = self._resolve(name)
        if note is None:
            return ToolResult.failure(f"No note named '{name}' in the vault.")
        text = note.read_text(encoding="utf-8", errors="replace")
        return ToolResult.success(f"Read note '{note.stem}'.", name=note.stem, text=text[:max_chars])

    def note_detail(self, name: str, max_chars: int = 20000) -> ToolResult:
        """Read a note plus its wiki-links: ``outlinks`` (notes it references) and
        ``backlinks`` (notes that reference it). Powers the vault browser."""
        base = self.read_note(name, max_chars=max_chars)
        if not base.ok:
            return base
        outlinks = sorted({link for link in _wikilinks(str(base.data.get("text", ""))) if link})
        back = self.backlinks(name)
        base.data["outlinks"] = outlinks
        base.data["backlinks"] = back.data.get("backlinks", []) if back.ok else []
        return base

    def backlinks(self, name: str) -> ToolResult:
        """Notes whose text contains a ``[[name]]`` wiki-link to this note."""
        if not self.available():
            return self.status()
        note = self._resolve(name)
        target = (note.stem if note else name).strip().lower()
        hits: list[str] = []
        for candidate in self._notes():
            if note is not None and candidate == note:
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(link.lower() == target for link in _wikilinks(text)):
                hits.append(candidate.stem)
        return ToolResult.success(
            f"{len(hits)} note(s) link here.", name=(note.stem if note else name), backlinks=sorted(set(hits))
        )

    def save_note(self, title: str, content: str, folder: str = "Agent Memory") -> ToolResult:
        if not self.vault_path:
            return ToolResult.failure("No Obsidian vault configured. Set OBSIDIAN_VAULT to your vault folder.")
        if not self.vault_path.is_dir():
            return ToolResult.failure(f"Obsidian vault folder not found: {self.vault_path}")
        safe_title = re.sub(r"[^\w \-]+", "", title).strip() or "Note"
        target = self.vault_path / folder / f"{safe_title}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        return ToolResult.success(
            f"Saved note '{safe_title}' to the Obsidian vault.",
            name=safe_title,
            rel=str(target.relative_to(self.vault_path)),
        )

    def append_memory(self, text: str, heading: str = "Memory log") -> ToolResult:
        """Append a timestamp-free bullet to a rolling memory note in the vault."""
        if not self.vault_path:
            return ToolResult.failure("No Obsidian vault configured. Set OBSIDIAN_VAULT to your vault folder.")
        if not self.vault_path.is_dir():
            return ToolResult.failure(f"Obsidian vault folder not found: {self.vault_path}")
        target = self.vault_path / "Agent Memory" / f"{heading}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else f"# {heading}\n\n"
        updated = existing.rstrip() + f"\n- {text.strip()}\n"
        target.write_text(updated, encoding="utf-8")
        return ToolResult.success("Saved to vault memory.", rel=str(target.relative_to(self.vault_path)))

    def _notes(self) -> list[Path]:
        if not self.vault_path:
            return []
        return sorted(p for p in self.vault_path.rglob("*.md") if ".obsidian" not in p.parts)

    def _resolve(self, name: str) -> Path | None:
        stem = name.strip().removesuffix(".md").lower()
        for note in self._notes():
            if note.stem.lower() == stem:
                return note
        return None


def _wikilinks(text: str) -> list[str]:
    """Note names referenced by ``[[Name]]`` / ``[[Name|alias]]`` / ``[[Name#heading]]``."""
    links = []
    for raw in re.findall(r"\[\[([^\]]+)\]\]", text):
        name = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if name:
            links.append(name)
    return links
