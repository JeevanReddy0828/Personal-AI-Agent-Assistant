from __future__ import annotations

import json
import math
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
    a JSON file. Search ranks documents with a small TF-IDF style score and
    returns a snippet around the best match. No vectors, no network, no external
    services - everything stays on disk next to the other agent data.
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
        documents = store["documents"]
        indexed = []
        for doc in documents:
            text = str(doc.get("text", ""))
            indexed.append((doc, text, self._term_counts(text)))
        doc_frequencies = self._document_frequencies_from_counts([counts for _, _, counts in indexed], terms)
        document_count = len(documents)
        scored: list[tuple[int, float, int, dict[str, object]]] = []
        for doc, text, counts in indexed:
            score = self._tfidf_score(counts, terms, doc_frequencies, document_count)
            if score <= 0:
                continue
            matched = sum(1 for term in terms if counts.get(term, 0) > 0)
            scored.append(
                (
                    matched,
                    score,
                    sum(counts.get(term, 0) for term in terms),
                    {
                        "id": doc.get("id"),
                        "source": doc.get("source"),
                        "score": round(score, 4),
                        "matched_terms": matched,
                        "snippet": self._snippet(text, terms),
                        "char_count": doc.get("char_count"),
                    },
                )
            )
        scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]["id"] or 0))
        return [item[3] for item in scored[: max(1, min(limit, 25))]]

    def list_documents(self) -> list[dict[str, object]]:
        store = self._load()
        return [
            {"id": doc.get("id"), "source": doc.get("source"), "char_count": doc.get("char_count"), "preview": doc.get("preview")}
            for doc in store["documents"]
        ]

    def stats(self) -> dict[str, object]:
        documents = self.list_documents()
        total_chars = sum(int(doc.get("char_count") or 0) for doc in documents)
        sources_by_kind: dict[str, int] = {}
        for doc in documents:
            source = str(doc.get("source") or "")
            kind = "research" if source.startswith("research") else Path(source).suffix.lower() or "note"
            sources_by_kind[kind] = sources_by_kind.get(kind, 0) + 1
        return {
            "document_count": len(documents),
            "total_char_count": total_chars,
            "average_char_count": round(total_chars / len(documents)) if documents else 0,
            "sources_by_kind": dict(sorted(sources_by_kind.items())),
        }

    def export_markdown(self, title: str = "Knowledge Base Export") -> str:
        documents = self.list_documents()
        stats = self.stats()
        lines = [
            f"# {title}",
            "",
            "## Summary",
            f"- Documents: {stats['document_count']}",
            f"- Total characters indexed: {stats['total_char_count']}",
            f"- Average document size: {stats['average_char_count']} characters",
            "",
            "## Documents",
        ]
        if not documents:
            lines.append("- No documents are indexed yet.")
        for doc in documents:
            lines.extend(
                [
                    f"### #{doc.get('id')} - {doc.get('source')}",
                    "",
                    f"- Characters: {doc.get('char_count')}",
                    f"- Preview: {doc.get('preview') or ''}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

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
    def _document_frequencies_from_counts(counts_by_document: list[dict[str, int]], terms: set[str]) -> dict[str, int]:
        frequencies = {term: 0 for term in terms}
        for counts in counts_by_document:
            for term in terms:
                if counts.get(term, 0) > 0:
                    frequencies[term] += 1
        return frequencies

    @staticmethod
    def _tfidf_score(
        counts: dict[str, int],
        terms: set[str],
        document_frequencies: dict[str, int],
        document_count: int,
    ) -> float:
        score = 0.0
        for term in terms:
            count = counts.get(term, 0)
            if count <= 0:
                continue
            tf = 1.0 + math.log(count)
            idf = math.log((1 + document_count) / (1 + document_frequencies.get(term, 0))) + 1.0
            score += tf * idf
        return score

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
