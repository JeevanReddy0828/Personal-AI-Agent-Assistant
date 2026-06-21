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
        """Rank notes for a query. A match in the title, an alias, or the frontmatter
        ``summary`` is weighted far above a body match — a "tiered retrieval" signal
        (title/summary first) that mirrors how a strong second-brain is searched."""
        if not self.available():
            return self.status()
        terms = _query_terms(query)
        if not terms:
            return ToolResult.failure("Give something to search for in the vault.")
        hits: list[dict[str, object]] = []
        for note in self._notes():
            try:
                text = note.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta = _frontmatter(text)
            aliases = " ".join(meta["aliases"]).lower()
            summary = meta["summary"].lower()
            name_l = note.stem.lower()
            body = text.lower()
            score = 0
            for term in terms:
                score += body.count(term)  # body weight 1
                score += 6 if term in name_l else 0  # title is the strongest signal
                score += 4 if term in aliases else 0  # aliases catch synonyms the title misses
                score += 3 if term in summary else 0  # the AI-search summary signal
            if score <= 0:
                continue
            snippet = meta["summary"] or self._snippet(text, terms)
            hits.append({
                "name": note.stem, "rel": str(note.relative_to(self.vault_path)),
                "score": score, "snippet": snippet, "summary": meta["summary"],
            })
        hits.sort(key=lambda item: -int(item["score"]))
        return ToolResult.success(f"Found {len(hits)} matching note(s).", query=query, results=hits[:limit])

    def context_for(self, query: str, max_chars: int = 4000, seeds: int = 2, max_notes: int = 6) -> ToolResult:
        """Link-aware retrieval: take the top matching notes, then pull in their 1-hop
        neighbours (out- and back-links) so the agent answers from connected context,
        not a single note. Links are the retrieval substrate of a second brain.

        Seeds from the top ``seeds`` matches (not just #1) so a near-tie in ranking
        still reaches the right neighbourhood."""
        if not self.available():
            return self.status()
        found = self.search(query, limit=max(seeds, 1))
        if not found.ok:
            return found
        results = found.data.get("results", [])
        if not results:
            return ToolResult.failure(f"No vault note matches '{query}'.", query=query)
        seed_names = [str(r["name"]) for r in results[:seeds]]
        order = list(seed_names)
        for name in seed_names:  # append each seed's neighbours after the seeds
            detail = self.note_detail(name)
            for neighbour in list(detail.data.get("outlinks", [])) + list(detail.data.get("backlinks", [])):
                if neighbour not in order:
                    order.append(neighbour)
        used: list[str] = []
        parts: list[str] = []
        for name in order:
            if len(used) >= max_notes:
                break
            note = self.read_note(name)
            if note.ok:
                used.append(name)
                parts.append(f"## {name}\n{note.data.get('text', '')}")
        context = "\n\n".join(parts)[:max_chars]
        return ToolResult.success(
            f"Assembled context from {len(used)} linked note(s).",
            query=query, primary=seed_names[0], notes=used, context=context,
        )

    def audit(self) -> ToolResult:
        """Vault health for a memory that an agent relies on: orphans (no links in or
        out), broken wiki-links (point at a missing note), and notes missing a
        frontmatter ``summary`` (the strong AI-search signal)."""
        if not self.available():
            return self.status()
        notes = self._notes()
        resolvable = {n.stem.lower() for n in notes}
        for note in notes:
            for alias in _frontmatter(note.read_text(encoding="utf-8", errors="replace"))["aliases"]:
                resolvable.add(alias.lower())
        out_links: dict[str, list[str]] = {}
        referenced: set[str] = set()
        broken: list[dict[str, str]] = []
        missing_summary: list[str] = []
        for note in notes:
            text = note.read_text(encoding="utf-8", errors="replace")
            links = _wikilinks(text)
            out_links[note.stem] = links
            for link in links:
                referenced.add(link.lower())
                if link.lower() not in resolvable:
                    broken.append({"note": note.stem, "link": link})
            if not _frontmatter(text)["summary"]:
                missing_summary.append(note.stem)
        orphans = [n.stem for n in notes if not out_links[n.stem] and n.stem.lower() not in referenced]
        return ToolResult.success(
            f"Audited {len(notes)} note(s): {len(orphans)} orphan(s), {len(broken)} broken link(s), "
            f"{len(missing_summary)} without a summary.",
            note_count=len(notes), orphans=sorted(orphans), broken_links=broken,
            missing_summary=sorted(missing_summary),
        )

    @staticmethod
    def _snippet(text: str, terms: list[str]) -> str:
        lowered = text.lower()
        index = min((lowered.find(term) for term in terms if term in lowered), default=0)
        return " ".join(text[max(0, index - 60): index + 180].split())

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
        notes = self._notes()
        for note in notes:
            if note.stem.lower() == stem:
                return note
        # Fall back to frontmatter aliases so links/recall by a synonym still resolve.
        for note in notes:
            try:
                aliases = _frontmatter(note.read_text(encoding="utf-8", errors="replace"))["aliases"]
            except OSError:
                continue
            if any(alias.strip().lower() == stem for alias in aliases):
                return note
        return None


def _frontmatter(text: str) -> dict:
    """Parse the leading ``--- ... ---`` YAML block (no yaml dep). Always returns a
    dict with 'tags', 'aliases' (lists) and 'summary' (str), empty if absent."""
    meta: dict = {"tags": [], "aliases": [], "summary": ""}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end == -1:
        return meta
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip().lower(), value.strip()
        if key in {"tags", "aliases"}:
            meta[key] = _parse_list(value)
        elif key == "summary":
            meta["summary"] = value.strip().strip("\"'")
    return meta


def _parse_list(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [item.strip().strip("\"'") for item in value.split(",") if item.strip()]


# Common words carry no retrieval signal and (as substrings) pollute ranking —
# e.g. "is" inside "disconnected". Dropped from query terms.
_STOPWORDS = frozenset(
    "the a an and or of to in on at by for with from about into over is it its are was were be been "
    "this that these those what when where why who how do does did can could will would should i me my "
    "we our you your they them their he she his her as so if then than out up down off no not".split()
)


def _query_terms(query: str) -> list[str]:
    """Meaningful search terms: drop stopwords and very short tokens. Falls back to
    any 2+ char token if a query is all stopwords (e.g. a bare 'who')."""
    tokens = re.findall(r"[a-z0-9]+", query.lower())
    terms = [t for t in tokens if len(t) >= 3 and t not in _STOPWORDS]
    return terms or [t for t in tokens if len(t) >= 2]


def _wikilinks(text: str) -> list[str]:
    """Note names referenced by ``[[Name]]`` / ``[[Name|alias]]`` / ``[[Name#heading]]``."""
    links = []
    for raw in re.findall(r"\[\[([^\]]+)\]\]", text):
        name = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if name:
            links.append(name)
    return links
