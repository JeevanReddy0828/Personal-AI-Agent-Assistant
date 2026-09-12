from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.context import terms as context_terms
from laptop_agent.knowledge import _content_terms, _tokenize
from laptop_agent.safety import ApprovalGate
from laptop_agent.terms import collapse_acronyms, content_terms, words
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.obsidian import _query_terms, ObsidianVault

NOTE = """---
title: J.A.R.V.I.S
---

# J.A.R.V.I.S

The local-first personal laptop agent. It routes text to one tool or a chat reply and
keeps every risky action behind an approval gate.
"""
DECOY = "# Grocery list\n\nMilk, eggs, bread. Nothing to do with software at all.\n"


class SplitterTests(unittest.TestCase):
    def test_a_dotted_acronym_is_one_word(self) -> None:
        self.assertEqual(words("J.A.R.V.I.S"), ["jarvis"])
        self.assertEqual(collapse_acronyms("the U.S.A. exports"), "the USA exports")

    def test_a_version_number_is_not_an_acronym(self) -> None:
        self.assertEqual(words("version 10.2"), ["version", "10", "2"])
        self.assertEqual(collapse_acronyms("node.js and flux.1-schnell"), "node.js and flux.1-schnell")

    def test_stopwords_and_length_belong_to_the_caller(self) -> None:
        self.assertEqual(content_terms("the big cat", {"the"}, 2), ["big", "cat"])
        self.assertEqual(content_terms("the big cat", {"the"}, 4), [])


class EveryRetrievalPathKeepsTheAcronymTests(unittest.TestCase):
    """One tokenizer fix used to reach one module. `knowledge` learned to keep
    "J.A.R.V.I.S" whole while a vault search for the same name matched nothing, session
    retrieval had no term to rank on, and a file summary dropped the app's own name.

    These assert the behaviour at each caller rather than the shared helper, so a module
    that re-forks its own splitter fails here even though `terms.py` still passes.
    """

    def test_session_context_ranks_on_the_name(self) -> None:
        self.assertEqual(context_terms("what is J.A.R.V.I.S"), ["jarvis"])

    def test_knowledge_ranks_on_the_name(self) -> None:
        self.assertEqual(_tokenize("J.A.R.V.I.S"), ["jarvis"])
        self.assertIn("jarvis", _content_terms("what is J.A.R.V.I.S"))

    def test_vault_query_ranks_on_the_name(self) -> None:
        self.assertEqual(_query_terms("what is J.A.R.V.I.S"), ["jarvis"])

    def test_vault_search_finds_the_note_and_not_a_decoy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "jarvis.md").write_text(NOTE, encoding="utf-8")
            (root / "grocery.md").write_text(DECOY, encoding="utf-8")
            vault = ObsidianVault(str(root))

            found = vault.search("J.A.R.V.I.S")
            self.assertTrue(found.ok, "a search for the note's own title found nothing")
            self.assertEqual([hit["name"] for hit in found.data["results"]], ["jarvis"])

            # The old fallback ("any 2+ char token when the query is all stopwords")
            # ranked on "what"/"is" and returned the shopping list as a match.
            phrased = vault.search("what is J.A.R.V.I.S")
            self.assertEqual([hit["name"] for hit in phrased.data["results"]], ["jarvis"])

    def test_file_summary_keeps_the_name_as_a_keyword(self) -> None:
        tool = FileTool(ApprovalGate(ask=lambda request: True))
        summary = tool.summarize_text(NOTE * 3, source="jarvis.md", sentences=2)
        self.assertTrue(summary.ok)
        self.assertIn("jarvis", summary.data["keywords"])

    def test_file_question_answers_instead_of_returning_nothing(self) -> None:
        tool = FileTool(ApprovalGate(ask=lambda request: True))
        answer = tool.answer_text(NOTE * 3, "what is J.A.R.V.I.S", source="jarvis.md")
        self.assertTrue(answer.ok)
        # The answer quotes the source, so the name keeps its dots there; what changed is
        # that there is an answer at all. The old tokenizer left the question with no
        # terms, every sentence scored zero overlap, and this came back empty.
        found = collapse_acronyms(answer.data.get("answer") or "").lower()
        self.assertIn("jarvis", found)


class FileSummarizerKeepsItsOwnPatternTests(unittest.TestCase):
    """`tools.files` borrows only `collapse_acronyms`. Measured over this repo's prose,
    moving it onto `words()` changed 53 of 134 paragraphs — it gained 130 kinds of
    version number and lost 52 contractions, because "can't" splits at the apostrophe.
    """

    def test_a_contraction_stays_one_word(self) -> None:
        self.assertIn("can't", FileTool._content_words("It can't be done."))

    def test_a_version_number_is_not_a_keyword(self) -> None:
        self.assertNotIn("2026", FileTool._content_words("Released in 2026 and 120b wide."))


if __name__ == "__main__":
    unittest.main()
