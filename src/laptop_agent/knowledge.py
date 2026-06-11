from __future__ import annotations

import json
import re
from pathlib import Path

# Small stopword set so common words do not dominate ranking.
_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "has", "had",
    "with", "this", "that", "from", "have", "was", "were", "will", "your", "into", "out",
    "about", "which", "their", "there", "them", "then", "than", "what", "when", "where",
    "who", "how", "why", "some", "such", "only", "more", "most", "over", "also", "been",
}


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 1]


def _content_terms(text: str) -> list[str]:
    return [token for token in _tokenize(text) if token not in _STOPWORDS]


class KnowledgeBase:
    """Local, dependency-free searchable index over extracted document text.

    Documents (the text pulled from files, OCR, or transcription) are stored in
    a JSON file. Search ranks documents by how often the query's content words
    appear, and returns a snippet around the best match. No vectors, no network,
    no external services - everything stays on disk next to the other agent data.
    """

    def __init__(self, path: Path, max_text_chars: int = 200_000) -> None:
        self.path = path
        self.max_text_chars = max_text_chars

    def add(self, source: str, text: str) -> dict[str, object]:
        cleaned = text.strip()
        if not cleaned:
            return {"ok": False, "reason": "no extractable text"}
        store = self._load()
        documents = [doc for doc in store["documents"] if doc.get("source") != source]
        entry = {
            "id": store["next_id"],
            "source": source,
            "char_count": len(cleaned),
            "preview": " ".join(cleaned.split())[:160],
            "text": cleaned[: self.max_text_chars],
        }
        documents.append(entry)
        store["documents"] = documents
        store["next_id"] = store["next_id"] + 1
        self._save(store)
        return {"ok": True, "id": entry["id"], "source": source, "char_count": entry["char_count"]}

    def search(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        terms = set(_content_terms(query))
        if not terms:
            return []
        store = self._load()
        scored: list[tuple[int, int, dict[str, object]]] = []
        for doc in store["documents"]:
            counts = self._term_counts(str(doc.get("text", "")))
            score = sum(counts.get(term, 0) for term in terms)
            if score <= 0:
                continue
            matched = sum(1 for term in terms if counts.get(term, 0) > 0)
            scored.append(
                (
                    score,
                    matched,
                    {
                        "id": doc.get("id"),
                        "source": doc.get("source"),
                        "score": score,
                        "matched_terms": matched,
                        "snippet": self._snippet(str(doc.get("text", "")), terms),
                        "char_count": doc.get("char_count"),
                    },
                )
            )
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]["id"] or 0))
        return [item[2] for item in scored[: max(1, min(limit, 25))]]

    def list_documents(self) -> list[dict[str, object]]:
        store = self._load()
        return [
            {"id": doc.get("id"), "source": doc.get("source"), "char_count": doc.get("char_count"), "preview": doc.get("preview")}
            for doc in store["documents"]
        ]

    def forget(self, doc_id: int) -> bool:
        store = self._load()
        remaining = [doc for doc in store["documents"] if doc.get("id") != doc_id]
        existed = len(remaining) != len(store["documents"])
        if existed:
            store["documents"] = remaining
            self._save(store)
        return existed

    def clear(self) -> int:
        store = self._load()
        count = len(store["documents"])
        store["documents"] = []
        self._save(store)
        return count

    @staticmethod
    def _term_counts(text: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for token in _tokenize(text):
            counts[token] = counts.get(token, 0) + 1
        return counts

    @staticmethod
    def _snippet(text: str, terms: set[str], window: int = 160) -> str:
        lowered = text.lower()
        best = len(text)
        for term in terms:
            index = lowered.find(term)
            if index != -1:
                best = min(best, index)
        if best == len(text):
            return " ".join(text.split())[:window]
        start = max(0, best - 60)
        end = min(len(text), best + window)
        snippet = " ".join(text[start:end].split())
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(text) else ""
        return f"{prefix}{snippet}{suffix}"

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            return {"next_id": 1, "documents": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"next_id": 1, "documents": []}
        if not isinstance(data, dict):
            return {"next_id": 1, "documents": []}
        data.setdefault("next_id", 1)
        documents = data.get("documents")
        data["documents"] = documents if isinstance(documents, list) else []
        return data

    def _save(self, store: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(store, indent=2), encoding="utf-8")
