from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.knowledge import KnowledgeBase


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


if __name__ == "__main__":
    unittest.main()
