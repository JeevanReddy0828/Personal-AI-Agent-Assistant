from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.embeddings import Embedder, cosine, reciprocal_rank_fusion
from laptop_agent.knowledge import KnowledgeBase

# A stand-in for meaning: documents and questions about the same thing get the same axis,
# and they deliberately share no vocabulary, which is the case keyword scoring misses.
AXES = {
    "safety": ("approval", "risky", "delete", "permission"),
    "weather": ("forecast", "open-meteo", "temperature", "sign up"),
    "network": ("congestion", "round trip", "packet", "slow"),
}


def fake_backend(texts, input_type):
    vectors = []
    for text in texts:
        low = text.lower()
        vector = [1.0 if any(word in low for word in words) else 0.0 for words in AXES.values()]
        vectors.append(vector if any(vector) else [0.0, 0.0, 0.0])
    return vectors


class EmbedderTests(unittest.TestCase):
    def test_documents_and_queries_use_different_input_types(self) -> None:
        seen: list[str] = []

        def backend(texts, input_type):
            seen.append(input_type)
            return [[1.0, 0.0]] * len(texts)

        embedder = Embedder(backend=backend)
        embedder.document("a passage")
        embedder.query("a question")
        # The model is asymmetric; using one type for both quietly costs accuracy.
        self.assertEqual(seen, ["passage", "query"])

    def test_an_unreachable_service_returns_none_rather_than_raising(self) -> None:
        def dead(texts, input_type):
            raise TimeoutError("no route")

        self.assertIsNone(Embedder(backend=dead).query("anything"))

    def test_a_short_batch_is_refused_rather_than_misaligned(self) -> None:
        # Fewer vectors than texts would silently pair the wrong vector with a document.
        self.assertIsNone(Embedder(backend=lambda t, k: [[1.0]]).documents(["a", "b"]))

    def test_no_key_means_unavailable(self) -> None:
        self.assertFalse(Embedder(api_key="").available())
        self.assertIsNone(Embedder(api_key="").query("anything"))

    def test_blank_text_is_not_embedded(self) -> None:
        self.assertIsNone(Embedder(backend=fake_backend).query("   "))

    def test_cosine_handles_degenerate_input(self) -> None:
        self.assertEqual(cosine([], [1.0]), 0.0)
        self.assertEqual(cosine([0.0, 0.0], [0.0, 0.0]), 0.0)
        self.assertEqual(cosine([1.0, 2.0], [1.0]), 0.0)
        self.assertAlmostEqual(cosine([1.0, 0.0], [1.0, 0.0]), 1.0)

    def test_fusion_ranks_by_position_not_score(self) -> None:
        fused = reciprocal_rank_fusion([[1, 2, 3], [3, 1, 2]])
        # 1 is first then second; 3 is last then first — 1 should still lead.
        self.assertGreater(fused[1], fused[3])
        self.assertGreater(fused[3], fused[2])


class HybridSearchTests(unittest.TestCase):
    DOCS = (
        ("safety", "The approval gate stops risky actions; nothing is deleted without permission."),
        ("weather", "Open-Meteo supplies the forecast, free and with no sign up."),
        ("network", "Slow start doubles the congestion window each round trip."),
    )

    def store(self, embedder: Embedder | None) -> KnowledgeBase:
        base = KnowledgeBase(Path(self._tmp.name) / "kb.json", embedder=embedder)
        for source, text in self.DOCS:
            base.add(source, text)
        return base

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_a_paraphrase_finds_nothing_without_vectors(self) -> None:
        # The behaviour this replaces: no shared word, so no result at all.
        self.assertEqual(self.store(None).search("will it erase my documents unasked"), [])

    def test_a_paraphrase_is_found_with_vectors(self) -> None:
        hits = self.store(Embedder(backend=fake_backend)).search("will it delete my documents unasked")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source"], "safety")
        self.assertEqual(hits[0]["matched_terms"], 0)   # purely semantic
        self.assertGreater(hits[0]["similarity"], 0.5)

    def test_a_vector_is_stored_once_with_the_document(self) -> None:
        calls: list[str] = []

        def counting(texts, input_type):
            calls.append(input_type)
            return fake_backend(texts, input_type)

        base = self.store(Embedder(backend=counting))
        self.assertEqual(calls, ["passage"] * len(self.DOCS))
        base.search("anything about permission")
        # searching adds one query embedding, never re-embeds the documents
        self.assertEqual(calls.count("passage"), len(self.DOCS))
        self.assertEqual(calls.count("query"), 1)

    def test_backfill_embeds_documents_stored_without_vectors(self) -> None:
        plain = self.store(None)                       # saved before semantic search existed
        raw = plain._load()
        self.assertTrue(all(not d.get("vector") for d in raw["documents"]))

        with_vectors = KnowledgeBase(Path(self._tmp.name) / "kb.json", embedder=Embedder(backend=fake_backend))
        outcome = with_vectors.backfill_vectors()
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["embedded"], len(self.DOCS))
        self.assertEqual(outcome["pending"], 0)
        self.assertTrue(all(d.get("vector") for d in with_vectors._load()["documents"]))

    def test_backfill_is_idempotent(self) -> None:
        base = self.store(Embedder(backend=fake_backend))   # already embedded on add
        outcome = base.backfill_vectors()
        self.assertEqual(outcome["embedded"], 0)
        self.assertEqual(outcome["pending"], 0)

    def test_backfill_without_a_model_says_so(self) -> None:
        outcome = self.store(None).backfill_vectors()
        self.assertFalse(outcome["ok"])
        self.assertIn("no embedding model", outcome["reason"])

    def test_a_failed_batch_leaves_those_documents_for_next_time(self) -> None:
        self.store(None)                                    # documents exist, no vectors

        def dead(texts, input_type):
            raise TimeoutError("offline")

        base = KnowledgeBase(Path(self._tmp.name) / "kb.json", embedder=Embedder(backend=dead))
        outcome = base.backfill_vectors()
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["embedded"], 0)
        self.assertEqual(outcome["pending"], len(self.DOCS))

    def test_an_answer_is_drawn_from_the_ranked_documents_only(self) -> None:
        # Previously every sentence in the corpus competed on word overlap, which is how a
        # question about databases was answered out of a README table. The answer now comes
        # from the documents the ranking chose, so its sources stay bounded.
        base = self.store(Embedder(backend=fake_backend))
        for index in range(8):
            base.add(f"filler{index}", f"Document number {index} mentions permission in passing.")
        out = base.answer("what stops risky actions without permission")
        self.assertTrue(out["ok"])
        self.assertLessEqual(len(out["sources"]), 4)
        self.assertTrue(str(out["answer"]).strip())

    def test_a_paraphrase_with_no_shared_words_still_answers(self) -> None:
        out = self.store(Embedder(backend=fake_backend)).answer("will it erase my documents unprompted")
        self.assertTrue(out["ok"])
        self.assertTrue(str(out["answer"]).strip())

    def test_keyword_matches_still_win_on_exact_terms(self) -> None:
        hits = self.store(Embedder(backend=fake_backend)).search("congestion window")
        self.assertEqual(hits[0]["source"], "network")

    def test_a_failing_embedder_falls_back_to_keywords(self) -> None:
        def dead(texts, input_type):
            raise TimeoutError("offline")

        base = self.store(Embedder(backend=dead))
        hits = base.search("congestion window")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source"], "network")


if __name__ == "__main__":
    unittest.main()


class DottedAcronymTests(unittest.TestCase):
    """The app could not find itself. The README is titled "J.A.R.V.I.S", which tokenized
    to six single letters and then to nothing (tokens under two characters are dropped),
    so "what is JARVIS" had zero overlap with the document that answers it."""

    def test_a_dotted_acronym_is_one_word(self) -> None:
        from laptop_agent.knowledge import _tokenize

        self.assertEqual(_tokenize("J.A.R.V.I.S"), ["jarvis"])
        self.assertIn("usa", _tokenize("U.S.A. exports"))

    def test_the_query_now_overlaps_the_title(self) -> None:
        from laptop_agent.knowledge import _content_terms, _tokenize

        title = _tokenize("J.A.R.V.I.S — Local-First Personal Agent")
        self.assertIn("jarvis", set(_content_terms("what is JARVIS")) & set(title))

    def test_ordinary_text_and_numbers_are_untouched(self) -> None:
        from laptop_agent.knowledge import _tokenize

        self.assertEqual(
            _tokenize("The approval gate stops risky actions."),
            ["the", "approval", "gate", "stops", "risky", "actions"],
        )
        self.assertIn("10", _tokenize("version 10.2"))
        self.assertEqual(_tokenize("read file src/main.py"), ["read", "file", "src", "main", "py"])

    def test_a_document_is_found_by_its_dotted_name(self) -> None:
        import tempfile
        from pathlib import Path

        from laptop_agent.knowledge import KnowledgeBase

        with tempfile.TemporaryDirectory() as raw:
            base = KnowledgeBase(Path(raw) / "kb.json")
            base.add("README.md", "# J.A.R.V.I.S — Local-First Personal Agent\n\nIt runs on your laptop.")
            base.add("other.md", "Unrelated notes about statistical significance and sigma.")
            hits = base.search("what is JARVIS")
            self.assertTrue(hits)
            self.assertEqual(hits[0]["source"], "README.md")


class SentenceWeightingTests(unittest.TestCase):
    """Every query term counted the same, so a sentence saying a common word three times
    outranked the one that actually answered the question."""

    def base(self, tmp):
        from pathlib import Path

        from laptop_agent.knowledge import KnowledgeBase

        base = KnowledgeBase(Path(tmp) / "kb.json")
        base.add("README.md", "The project is called Zebracorn. It is a local agent. It is useful.")
        base.add("noise.md", "This is a thing. It is another thing. It is also a thing here.")
        return base

    def test_a_rare_term_outweighs_a_common_one(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = self.base(tmp).answer("what is Zebracorn")
            self.assertTrue(out["ok"])
            self.assertIn("Zebracorn", str(out["answer"]))
            self.assertEqual(out["sources"][0], "README.md")
