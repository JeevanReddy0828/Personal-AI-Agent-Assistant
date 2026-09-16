from __future__ import annotations

from laptop_agent.embeddings import Embedder, cosine, reciprocal_rank_fusion
from laptop_agent.storage import atomic_write_text, read_json, synchronized, positive_int
from laptop_agent.terms import content_terms, words

import json
import math
import re
from pathlib import Path

# How many consecutive sentences make a passage. One sentence is too small a unit to
# answer a question with: the line that defines a thing rarely repeats the words the
# question used, while a short line that mentions them often says nothing.
PASSAGE_SENTENCES = 3

# Small stopword set so common words do not dominate ranking.
_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "has", "had",
    "with", "this", "that", "from", "have", "was", "were", "will", "your", "into", "out",
    "about", "which", "their", "there", "them", "then", "than", "what", "when", "where",
    "who", "how", "why", "some", "such", "only", "more", "most", "over", "also", "been",
}


def _tokenize(text: str) -> list[str]:
    return [token for token in words(text) if len(token) > 1]


def _content_terms(text: str) -> list[str]:
    return content_terms(text, _STOPWORDS, 2)



# The agent indexes its own output: `solve` files its analysis as "advice: …", research
# files the scraped page, a transcript as "youtube:…". Useful for recall, but measured on
# the real store it had taken over — 30 of 31 documents were generated, against ONE real
# file, and the scrapes averaged 20k characters to the advice dumps' 5k. A long scrape then
# outranks the README on any word they share.
_GENERATED_PREFIXES = {"advice": "advice", "research": "research", "research report": "research",
                       "youtube": "youtube"}


def document_kind(source: str) -> str:
    """"file" for something the user indexed, else which kind of generated text it is."""
    text = (source or "").strip()
    if ":" not in text:
        return "file"
    head = text.split(":", 1)[0].strip().lower()
    return _GENERATED_PREFIXES.get(head, "file")


# A document the user indexed is the baseline; the agent's own output is discounted. These
# are deliberately gentle — swept over 15 queries with a known answer, file x1.5 with the
# generated kinds at 0.8-0.9 took top-1 from 12/15 to 13/15 and top-3 to 15/15, while a
# heavy hand (file x3) dropped top-1 back to 12/15. The weight only moves the secondary
# sort key, since distinct-terms-matched decides first, so it breaks ties rather than
# overruling relevance.
KIND_WEIGHTS = {"file": 1.5, "advice": 0.9, "research": 0.8, "youtube": 0.8}

# How many of each generated kind to keep, newest first. A user's own documents are never
# pruned. Without this the corpus grows with every `solve` and every research run forever.
GENERATED_CAPS = {"advice": 12, "research": 8, "youtube": 12}


# Markers of a passage that is instructions or markup rather than an explanation.
_CODE_FENCE = re.compile(r"```")
_COMMAND_LINE = re.compile(r"(?m)^\s*(?:\$|>|python -m |pip install |npm |git |curl )")


def _prune_generated(documents: list[dict[str, object]]) -> list[str]:
    """Trim each generated kind to its cap, oldest first. Returns the sources dropped.

    Ids are handed out in order, so the lowest id of a kind is its oldest document. A
    document the user indexed is never a candidate.
    """
    by_kind: dict[str, list[dict[str, object]]] = {}
    for doc in documents:
        by_kind.setdefault(document_kind(str(doc.get("source") or "")), []).append(doc)
    drop_ids: set[int] = set()
    dropped: list[str] = []
    for kind, cap in GENERATED_CAPS.items():
        group = sorted(by_kind.get(kind) or [], key=lambda d: d.get("id") or 0)
        for doc in group[: max(0, len(group) - cap)]:
            drop_ids.add(int(doc.get("id") or 0))
            dropped.append(str(doc.get("source") or ""))
    if drop_ids:
        documents[:] = [d for d in documents if int(d.get("id") or 0) not in drop_ids]
    return dropped


def _prose_weight(passage: str) -> float:
    """How much of this reads like an explanation rather than a command or a table.

    Never zero: a code block can still be the best available answer, it should just stop
    outranking the sentence that actually defines the thing.
    """
    weight = 1.0
    if _CODE_FENCE.search(passage):
        weight *= 0.45
    if _COMMAND_LINE.search(passage):
        weight *= 0.6
    # map() over the C methods, not a genexpr calling two of them per character: this was
    # 46% of an answer's time (784,480 isalpha calls for five answers). Measured exactly
    # equivalent on 2000 mixed passages, and 1.45x faster; a regex was both slower and wrong.
    letters = sum(map(str.isalpha, passage)) + sum(map(str.isspace, passage))
    if passage and letters / len(passage) < 0.7:
        weight *= 0.75      # mostly punctuation, pipes or symbols: a table or a diagram
    return weight


class KnowledgeBase:
    """Local, dependency-free searchable index over extracted document text.

    Documents (the text pulled from files, OCR, or transcription) are stored in
    a JSON file. Search ranks documents with a small TF-IDF style score and
    returns a snippet around the best match. No vectors, no network, no external
    services - everything stays on disk next to the other agent data.
    """

    def __init__(
        self,
        path: Path,
        max_text_chars: int = 200_000,
        embedder: "Embedder | None" = None,
    ) -> None:
        self.path = path
        self.max_text_chars = max_text_chars
        # Optional: keyword scoring only finds a document that reuses the asker's words.
        self.embedder = embedder
        self._cache_key: tuple[int, int] | None = None
        self._cache_store: dict[str, object] | None = None
        self._counts_cache: dict[int, dict[str, int]] = {}

    @synchronized
    def add(self, source: str, text: str) -> dict[str, object]:
        self._invalidate()
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
        # Embed here rather than at search time: a document is written once and searched
        # many times, so the round trip belongs on this side.
        if self.embedder is not None and self.embedder.available():
            vector = self.embedder.document(entry["text"])
            if vector:
                entry["vector"] = vector
        documents.append(entry)
        pruned = _prune_generated(documents)
        store["documents"] = documents
        store["next_id"] = store["next_id"] + 1
        self._save(store)
        return {"ok": True, "id": entry["id"], "source": source,
                "char_count": entry["char_count"], "pruned": len(pruned)}

    @synchronized
    def prune(self) -> dict[str, object]:
        """Apply the generated-document caps now, rather than waiting for the next add."""
        self._invalidate()
        store = self._load()
        documents = list(store["documents"])
        dropped = _prune_generated(documents)
        if dropped:
            store["documents"] = documents
            self._save(store)
        return {"ok": True, "removed": len(dropped), "sources": dropped, "remaining": len(documents)}

    @synchronized
    def backfill_vectors(self, batch_size: int = 8) -> dict[str, object]:
        """Embed documents stored before semantic search existed.

        Batched because one request per document would be a round trip each; a batch that
        fails is simply left for the next run rather than aborting the rest.
        """
        if self.embedder is None or not self.embedder.available():
            return {"ok": False, "reason": "no embedding model is configured"}
        self._invalidate()
        store = self._load()
        documents = store["documents"]
        pending = [d for d in documents if not d.get("vector") and str(d.get("text", "")).strip()]
        if not pending:
            return {"ok": True, "embedded": 0, "pending": 0, "total": len(documents)}
        embedded = 0
        for start in range(0, len(pending), max(1, batch_size)):
            batch = pending[start : start + max(1, batch_size)]
            vectors = self.embedder.documents([str(d.get("text", "")) for d in batch])
            if not vectors:
                continue
            for doc, vector in zip(batch, vectors):
                doc["vector"] = vector
                embedded += 1
        if embedded:
            self._save(store)
        return {
            "ok": True,
            "embedded": embedded,
            "pending": len(pending) - embedded,
            "total": len(documents),
        }

    @synchronized
    def search(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        return self._search_unlocked(query, limit)

    def _search_unlocked(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        terms = set(_content_terms(query))
        query_vector = None
        if self.embedder is not None and self.embedder.available():
            query_vector = self.embedder.query(query)
        if not terms and not query_vector:
            return []
        store = self._load()
        documents = store["documents"]
        indexed = []
        for doc in documents:
            text = str(doc.get("text", ""))
            indexed.append((doc, text, self._counts_for(doc, text)))
        doc_frequencies = self._document_frequencies_from_counts([counts for _, _, counts in indexed], terms)
        document_count = len(documents)
        scored: list[tuple[int, float, int, dict[str, object]]] = []
        for doc, text, counts in indexed:
            score = self._tfidf_score(counts, terms, doc_frequencies, document_count)
            if score <= 0:
                continue
            score *= KIND_WEIGHTS.get(document_kind(str(doc.get("source") or "")), 1.0)
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
        results = [item[3] for item in scored]
        if query_vector:
            results = self._fuse_with_vectors(results, indexed, query_vector)
        return results[: max(1, min(limit, 25))]

    def _fuse_with_vectors(
        self,
        lexical: list[dict[str, object]],
        indexed: list[tuple[dict[str, object], str, dict[str, int]]],
        query_vector: list[float],
    ) -> list[dict[str, object]]:
        """Merge the keyword ranking with a vector ranking by rank, not by score.

        A TF-IDF score and a cosine similarity are not comparable numbers, so fusing them
        arithmetically makes the blend depend on whichever spread happens to be wider.
        """
        similar: list[tuple[float, dict[str, object]]] = []
        for doc, text, counts in indexed:
            vector = doc.get("vector")
            if not vector:
                continue
            similar.append((cosine(query_vector, vector), doc))
        if not similar:
            return lexical
        similar.sort(key=lambda pair: -pair[0])

        by_id = {row["id"]: row for row in lexical}
        for score, doc in similar:
            doc_id = doc.get("id")
            if doc_id in by_id:
                by_id[doc_id]["similarity"] = round(score, 4)
                continue
            # A document the keyword pass scored at zero: this is the case vectors exist for.
            text = str(doc.get("text", ""))
            by_id[doc_id] = {
                "id": doc_id,
                "source": doc.get("source"),
                "score": 0.0,
                "similarity": round(score, 4),
                "matched_terms": 0,
                "snippet": " ".join(text.split())[:240],
                "char_count": doc.get("char_count"),
            }
        fused = reciprocal_rank_fusion(
            [[row["id"] for row in lexical], [doc.get("id") for _, doc in similar]]
        )
        merged = list(by_id.values())
        merged.sort(key=lambda row: (-fused.get(row["id"], 0.0), row["id"] or 0))
        return merged

    @synchronized
    def answer(self, question: str, limit: int = 6, retrieval_query: str | None = None) -> dict[str, object]:
        """``retrieval_query`` picks the document; ``question`` picks the passage inside it.

        A follow-up is looked up in its standalone form ("… (referring to: J.A.R.V.I.S is
        a local-first …)"), which is what finds the right document. Scoring passages with
        that same text answers the *referent* instead of the question: it matched the
        README's opening blurb almost verbatim and returned "J.A.R.V.I.S is a local-first
        personal agent" to someone who asked which models it uses.
        """
        terms = set(_content_terms(question))
        store = self._load()
        # Pick the documents with the ranking that knows about meaning, then pull sentences
        # from those. Scoring every sentence in the corpus by word overlap alone answered
        # "when should I pick a document store over tables" out of a README table, because
        # "store", "tables" and "pick" appear there.
        ranked = self._search_unlocked(retrieval_query or question, limit=4)
        # In ranked order, not store order. `doc_index` below is used as the tiebreaker
        # that decides which document the answer is quoted from, and it was carrying
        # document-id order instead of relevance — so the ranking's decision was thrown
        # away at the moment it was supposed to be honoured.
        by_id = {d.get("id"): d for d in store["documents"]}
        pool = [by_id[row["id"]] for row in ranked if row["id"] in by_id] or store["documents"]
        def lead_with_best() -> dict[str, object]:
            """Ranking found the document; sentence scoring cannot pick a line out of it
            when the question shares no word with it. Quote the opening instead."""
            excerpts = [
                {"id": d.get("id"), "source": d.get("source"),
                 "sentence": " ".join(str(d.get("text", "")).split())[:400], "score": 0.0}
                for d in pool[:2]
            ]
            return {
                "ok": True,
                "question": question,
                "answer": " ".join(str(e["sentence"]) for e in excerpts),
                "excerpts": excerpts,
                "sources": [str(e["source"]) for e in excerpts if e.get("source")],
            }

        if not terms:
            if not ranked:
                return {"ok": False, "reason": "question has no searchable terms", "question": question}
            return lead_with_best()
        # Weight each query term by how rare it is in the pool. Every term counted the
        # same before, so "what is JARVIS" ranked a sentence that says "is" three times
        # above the one that actually says JARVIS, and the answer came out of an unrelated
        # research scrape even though the ranking had put the README first by 14.5 to 5.9.
        pool_counts = [self._counts_for(doc, str(doc.get("text", ""))) for doc in pool]
        total_docs = len(pool) or 1
        weights = {
            term: math.log((total_docs + 1) / (sum(1 for c in pool_counts if term in c) + 0.5))
            for term in terms
        }

        # Score a PASSAGE, not a lone sentence. Two failures came out of scoring
        # sentences: a short line that merely mentions the word beat the line that
        # answers the question ("Optional browser checks need Playwright... JARVIS_BROWSE"
        # scored 0.651 against 0.467 for "J.A.R.V.I.S - Local-First Personal Agent"),
        # because dividing by length rewarded brevity that hard; and the best six
        # sentences came from different documents, so the answer read as a collage of a
        # README and an unrelated TCP scrape. A window of consecutive sentences carries
        # enough context to be an answer, and stays in one document.
        candidates: list[tuple[float, int, int, dict[str, object]]] = []
        for doc_index, doc in enumerate(pool):
            source = str(doc.get("source") or "")
            doc_id = int(doc.get("id") or 0)
            sentences = self._split_sentences(str(doc.get("text", "")))
            for start in range(len(sentences)):
                window = sentences[start:start + PASSAGE_SENTENCES]
                passage = " ".join(window)
                # Every window was tokenized and then thrown away if no query term was in
                # it, which is most of them. A term can only match as a token if it is
                # present as a substring, so this skips the same windows the overlap test
                # would have — without paying to tokenize them first.
                lowered = passage.lower()
                if not any(term in lowered for term in terms):
                    continue
                counts = self._term_counts(passage)
                overlap = sum(counts.get(term, 0) * weights[term] for term in terms)
                if overlap <= 0:
                    continue
                # A gentler length penalty: enough to stop a whole document winning,
                # not enough for a passing mention to outrank the explanation.
                score = overlap / (max(sum(counts.values()), 1) ** 0.18)
                # A shell snippet is rarely the answer to "what is X". The passage that
                # beat the README's own definition of itself was an install command that
                # happened to contain JARVIS_BROWSER_TESTS.
                score *= _prose_weight(passage)
                candidates.append(
                    (
                        score,
                        doc_index,
                        start,
                        {
                            "id": doc_id,
                            "source": source,
                            "sentence": passage,
                            "score": round(score, 4),
                        },
                    )
                )
        if not candidates:
            if ranked:
                return lead_with_best()
            return {"ok": False, "reason": "no relevant indexed text", "question": question}
        # Answer out of ONE document. Taking the best sentences globally stitched a README
        # and an unrelated TCP scrape into a single paragraph: every piece defensible, the
        # whole thing incoherent. The ranking already decided which document answers this;
        # quoting across that decision only undoes it.
        ordered = sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))
        # Quote the highest-RANKED document that has a usable passage, not the one holding
        # the highest-scoring passage. Picking by passage score re-decided the document
        # and undid the ranking: asked which models the app uses, the README ranked first
        # and the answer still came out of an NVIDIA RAG scrape, because a 24,000-character
        # scrape that says "models" 36 times always outscores the file that says it once.
        best_doc = min(candidate[1] for candidate in candidates)
        wanted = max(1, min(limit, 4))
        selected: list[tuple[float, int, int, dict[str, object]]] = []
        used_positions: set[int] = set()
        for candidate in ordered:
            if candidate[1] != best_doc:
                continue
            start = candidate[2]
            # Consecutive windows overlap heavily; a second passage should be new text.
            if any(abs(start - taken) < PASSAGE_SENTENCES for taken in used_positions):
                continue
            used_positions.add(start)
            selected.append(candidate)
            if len(selected) >= wanted:
                break
        # Read in document order, not score order, so the passages flow.
        selected.sort(key=lambda item: item[2])
        excerpts = [item[3] for item in selected]
        answer = " … ".join(str(item["sentence"]) for item in excerpts)
        sources = []
        seen = set()
        for item in excerpts:
            source = str(item.get("source") or "")
            if source and source not in seen:
                seen.add(source)
                sources.append(source)
        return {
            "ok": True,
            "question": question,
            "answer": answer,
            "excerpts": excerpts,
            "sources": sources,
        }

    @synchronized
    def list_documents(self) -> list[dict[str, object]]:
        store = self._load()
        return [
            {"id": doc.get("id"), "source": doc.get("source"), "char_count": doc.get("char_count"), "preview": doc.get("preview")}
            for doc in store["documents"]
        ]

    @synchronized
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

    @synchronized
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

    @synchronized
    def forget(self, doc_id: int) -> bool:
        self._invalidate()
        store = self._load()
        remaining = [doc for doc in store["documents"] if doc.get("id") != doc_id]
        existed = len(remaining) != len(store["documents"])
        if existed:
            store["documents"] = remaining
            self._save(store)
        return existed

    @synchronized
    def clear(self) -> int:
        self._invalidate()
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
    def _split_sentences(text: str) -> list[str]:
        compact = re.sub(r"\s+", " ", text).strip()
        if not compact:
            return []
        parts = re.split(r"(?<=[.!?])\s+", compact)
        return [part.strip() for part in parts if len(part.split()) >= 4]

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

    @synchronized
    def _invalidate(self) -> None:
        """Drop the caches. Called before every mutation, so a mutator that fails part way
        through can never leave a dirty store behind for a reader to see."""
        self._cache_key = None
        self._cache_store = None
        self._counts_cache = {}

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            return {"next_id": 1, "documents": []}
        # Parsing 1.6MB of JSON on every search cost 12ms of the 41ms a search took, and
        # the file has not changed between two searches. Keyed on the file's own identity
        # so an edit by another process (or the other agent) is still picked up.
        try:
            stat = self.path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        if key is not None and key == self._cache_key and self._cache_store is not None:
            return self._cache_store
        try:
            data = read_json(self.path, {})
        except (OSError, ValueError):
            return {"next_id": 1, "documents": []}
        if not isinstance(data, dict):
            return {"next_id": 1, "documents": []}
        data.setdefault("next_id", 1)
        documents = data.get("documents")
        data["documents"] = [d for d in documents if isinstance(d, dict) and isinstance(d.get("id"), int)] if isinstance(documents, list) else []
        data["next_id"] = max([positive_int(data.get("next_id"))] + [d["id"] + 1 for d in data["documents"]])
        if key is not None:
            self._cache_key, self._cache_store, self._counts_cache = key, data, {}
        return data

    def _counts_for(self, doc: dict[str, object], text: str) -> dict[str, int]:
        """Term counts for one document, computed once per load.

        Tokenizing every document from scratch on every search was 72% of a search's time
        (0.217s of 0.300s over five searches). The corpus does not change between two
        searches, so this is pure waste. Keyed by document id, and the whole cache is
        dropped whenever the store is reloaded or mutated.
        """
        doc_id = doc.get("id")
        if not isinstance(doc_id, int):
            return self._term_counts(text)
        cached = self._counts_cache.get(doc_id)
        if cached is None:
            cached = self._term_counts(text)
            self._counts_cache[doc_id] = cached
        return cached

    @synchronized
    def _save(self, store: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(store, indent=2))
        self._invalidate()
