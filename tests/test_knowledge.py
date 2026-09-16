from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.knowledge import GENERATED_CAPS, KnowledgeBase, document_kind


class DocumentKindTests(unittest.TestCase):
    """The agent indexes its own output. Measured on the real store, 30 of 31 documents
    were generated against ONE real file, and the scrapes averaged 20k characters to the
    advice dumps' 5k — so a scrape outranked the README on any word they shared."""

    def test_generated_prefixes_are_recognised(self) -> None:
        self.assertEqual(document_kind("advice: should I use Postgres"), "advice")
        self.assertEqual(document_kind("research: local-first agents"), "research")
        self.assertEqual(document_kind("research report: llms"), "research")
        self.assertEqual(document_kind("youtube:qXfPsEHFQpQ"), "youtube")

    def test_a_users_document_is_a_file_even_with_a_colon(self) -> None:
        self.assertEqual(document_kind("README.md"), "file")
        self.assertEqual(document_kind("C:/notes/plan.md"), "file")
        self.assertEqual(document_kind(""), "file")


class KindWeightTests(unittest.TestCase):
    def test_a_real_file_outranks_the_agents_own_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = KnowledgeBase(Path(raw) / "knowledge.json")
            body = "The approval gate stops risky actions before they run."
            kb.add("advice: how should approvals work", body)
            kb.add("notes.md", body)                      # identical text, so only kind differs
            self.assertEqual(kb.search("approval gate")[0]["source"], "notes.md")

    def test_the_weight_does_not_overrule_relevance(self) -> None:
        """It only moves the secondary sort key — distinct terms matched still decides."""
        with tempfile.TemporaryDirectory() as raw:
            kb = KnowledgeBase(Path(raw) / "knowledge.json")
            kb.add("notes.md", "The approval gate is mentioned here.")
            kb.add("advice: downloads", "The approval gate blocks a download until you allow it.")
            self.assertEqual(kb.search("approval gate download")[0]["source"], "advice: downloads")


class PruneTests(unittest.TestCase):
    def test_generated_documents_are_capped_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = KnowledgeBase(Path(raw) / "knowledge.json")
            cap = GENERATED_CAPS["advice"]
            for index in range(cap + 4):
                kb.add(f"advice: question {index}", f"An analysis of question {index}.")
            sources = {d["source"] for d in kb.list_documents()}
            self.assertEqual(len(sources), cap, sorted(sources))
            self.assertNotIn("advice: question 0", sources, "the oldest should go first")
            self.assertIn(f"advice: question {cap + 3}", sources, "the newest must stay")

    def test_a_users_own_documents_are_never_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = KnowledgeBase(Path(raw) / "knowledge.json")
            for index in range(40):
                kb.add(f"file-{index}.md", f"Notes number {index}.")
            self.assertEqual(len(kb.list_documents()), 40)

    def test_prune_reports_what_it_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = KnowledgeBase(Path(raw) / "knowledge.json")
            cap = GENERATED_CAPS["research"]
            # Write past the cap without add()'s pruning, as an older store would look.
            import json as _json
            documents = [{"id": i + 1, "source": f"research: topic {i}", "text": f"About topic {i}.",
                          "char_count": 14, "preview": "x"} for i in range(cap + 3)]
            (Path(raw) / "knowledge.json").write_text(
                _json.dumps({"next_id": cap + 4, "documents": documents}), encoding="utf-8")

            outcome = kb.prune()
            self.assertEqual(outcome["removed"], 3)
            self.assertEqual(outcome["remaining"], cap)
            self.assertEqual(outcome["sources"], ["research: topic 0", "research: topic 1", "research: topic 2"])

    def test_pruning_an_already_tidy_store_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = KnowledgeBase(Path(raw) / "knowledge.json")
            kb.add("notes.md", "Nothing to prune here.")
            outcome = kb.prune()
            self.assertEqual(outcome["removed"], 0)
            self.assertEqual(len(kb.list_documents()), 1)


class KnowledgeBaseTests(unittest.TestCase):
    def _kb(self, root: Path) -> KnowledgeBase:
        return KnowledgeBase(root / "knowledge.json")

    def test_add_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add("notes.md", "The quarterly invoice covers cloud hosting and storage costs.")
            kb.add("resume.txt", "Experienced engineer skilled in Python and distributed systems.")
            results = kb.search("invoice hosting")
            self.assertTrue(results)
            self.assertEqual(results[0]["source"], "notes.md")
            self.assertIn("invoice", results[0]["snippet"].lower())

    def test_a_cached_index_still_sees_new_documents(self) -> None:
        """Parsing 1.6MB of JSON and re-tokenizing 282k characters on every search cost
        39ms of a 41ms search. Both are cached now, so the risk moves to staleness."""
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add("first.md", "The approval gate stops risky actions.")
            self.assertEqual(kb.search("approval")[0]["source"], "first.md")   # warms the cache

            kb.add("second.md", "The approval gate also guards downloads and shell commands.")
            sources = {hit["source"] for hit in kb.search("approval")}
            self.assertEqual(sources, {"first.md", "second.md"}, "a cached index hid a new document")

    def test_a_cached_index_forgets_a_removed_document(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add("gone.md", "The approval gate stops risky actions.")
            doc_id = kb.search("approval")[0]["id"]
            self.assertTrue(kb.forget(int(doc_id)))
            self.assertEqual(kb.search("approval"), [], "a cached index kept a forgotten document")

    def test_an_edit_on_disk_is_picked_up(self) -> None:
        """Codex edits this file too, and the cache is keyed on the file's own identity so
        a change made by another process is not served stale."""
        import json
        import os
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "knowledge.json"
            kb = KnowledgeBase(path)
            kb.add("first.md", "The approval gate stops risky actions.")
            self.assertTrue(kb.search("approval"))

            store = json.loads(path.read_text(encoding="utf-8"))
            store["documents"].append({"id": 99, "source": "outside.md",
                                       "text": "An approval gate written by another process.",
                                       "char_count": 44, "preview": "outside"})
            path.write_text(json.dumps(store), encoding="utf-8")
            os.utime(path, (1, 1))          # a different mtime, as any real write would have

            sources = {hit["source"] for hit in kb.search("approval")}
            self.assertIn("outside.md", sources, "an edit by another process was served from cache")

    def test_answering_skips_passages_with_no_query_term(self) -> None:
        """Every 3-sentence window was tokenized and then discarded when no query term was
        in it, which is most of them. The substring pre-check must not change the answer."""
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add("mixed.md",
                   "Unrelated opening sentence about weather. "
                   "Another filler line with nothing useful. "
                   "The approval gate blocks a download until you say yes. "
                   "More filler that mentions nothing. ")
            answer = kb.answer("what does the approval gate do")
            self.assertTrue(answer["ok"], answer)
            self.assertIn("approval gate", str(answer["answer"]).lower())

    def test_ranking_prefers_query_coverage_over_repetition(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add("python-notes.md", "python " * 20)
            kb.add("invoice-runbook.md", "python invoice reconciliation gateway")
            results = kb.search("python invoice")
            self.assertEqual(results[0]["source"], "invoice-runbook.md")
            self.assertEqual(results[0]["matched_terms"], 2)
            self.assertGreater(results[0]["score"], 0)

    def test_search_no_match_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add("a.txt", "hello world")
            self.assertEqual(kb.search("nonexistentterm"), [])

    def test_answer_from_indexed_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add(
                "payments.md",
                "The Helios gateway accepts payment requests. "
                "Retries are safe because every request includes an idempotency key. "
                "Reports are exported every Friday.",
            )
            answer = kb.answer("How are retries safe?")
            self.assertTrue(answer["ok"])
            self.assertIn("idempotency key", answer["answer"])
            self.assertEqual(answer["sources"], ["payments.md"])

    def test_answer_no_match_reports_reason(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add("payments.md", "The gateway accepts payment requests.")
            answer = kb.answer("Kubernetes autoscaling")
            self.assertFalse(answer["ok"])
            self.assertEqual(answer["reason"], "no relevant indexed text")

    def test_reindex_same_source_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add("a.txt", "first version about apples")
            kb.add("a.txt", "second version about oranges")
            self.assertEqual(len(kb.list_documents()), 1)
            self.assertEqual(kb.search("apples"), [])
            self.assertTrue(kb.search("oranges"))

    def test_persists_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self._kb(Path(raw)).add("a.txt", "persistent indexed content")
            reopened = self._kb(Path(raw))
            self.assertEqual(len(reopened.list_documents()), 1)
            self.assertTrue(reopened.search("persistent"))

    def test_forget_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            first = kb.add("a.txt", "alpha content")
            kb.add("b.txt", "beta content")
            self.assertTrue(kb.forget(int(first["id"])))
            self.assertFalse(kb.forget(999))
            self.assertEqual(len(kb.list_documents()), 1)
            self.assertEqual(kb.clear(), 1)
            self.assertEqual(kb.list_documents(), [])

    def test_empty_text_not_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            outcome = kb.add("blank.txt", "   ")
            self.assertFalse(outcome["ok"])
            self.assertEqual(kb.list_documents(), [])

    def test_stats_and_export_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kb = self._kb(Path(raw))
            kb.add("notes.md", "alpha beta gamma")
            kb.add("research: asyncio", "asyncio event loops and tasks")
            stats = kb.stats()
            self.assertEqual(stats["document_count"], 2)
            self.assertGreater(stats["total_char_count"], 0)
            self.assertEqual(stats["sources_by_kind"][".md"], 1)
            self.assertEqual(stats["sources_by_kind"]["research"], 1)
            exported = kb.export_markdown()
            self.assertIn("# Knowledge Base Export", exported)
            self.assertIn("notes.md", exported)
            self.assertIn("research: asyncio", exported)


if __name__ == "__main__":
    unittest.main()
