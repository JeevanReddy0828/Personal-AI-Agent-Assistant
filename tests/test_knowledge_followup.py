from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.agents.orchestrator import AgentOrchestrator
from laptop_agent.knowledge import KnowledgeBase

standalone = AgentOrchestrator._standalone_question

# The shape of the real failure, in miniature: the file that answers the question says
# "model" and says it once; a long scrape about something else repeats "models".
README = (
    "J.A.R.V.I.S is a local-first personal laptop agent. "
    "The tiered brain is configured with OPENAI_MODEL for fast replies, OPENAI_SMART_MODEL "
    "for complex ones and OPENAI_ULTRA_MODEL for reasoning. "
    "Every risky action goes through the approval gate first. "
    "Run it with python -m laptop_agent.webui on port 8770."
)
SCRAPE = (
    "Retrieval augmented generation lets enterprises adapt generative models cheaply. "
    "Models are expensive to retrain, so models are grounded in a knowledge base instead. "
    "Teams use models for search, use models for support, and use models for analytics. "
    "The benefits of using models this way include cost and freshness. "
    "Organizations use models to avoid retraining models on domain data."
)

HISTORY = [
    {"role": "user", "text": "what is J.A.R.V.I.S?"},
    {"role": "assistant", "text": "J.A.R.V.I.S is a local-first personal laptop agent with an approval gate."},
]


def store(folder: str) -> KnowledgeBase:
    kb = KnowledgeBase(Path(folder) / "knowledge.json")
    kb.add("README.md", README)
    kb.add("research: retrieval augmented generation", SCRAPE)
    return kb


class StandaloneQuestionTests(unittest.TestCase):
    """`ask knowledge which models does it use` queried the index with the pronoun. Every
    other path — the router, the chat tiers, the agent, the advisor — already looks a
    follow-up up in its standalone form."""

    def test_a_pronoun_is_resolved_from_the_conversation(self) -> None:
        rewritten = standalone("which models does it use", HISTORY)
        self.assertNotEqual(rewritten, "which models does it use")
        self.assertIn("J.A.R.V.I.S", rewritten)

    def test_a_self_contained_question_is_left_alone(self) -> None:
        question = "what models does JARVIS use"
        self.assertEqual(standalone(question, HISTORY), question)

    def test_no_history_changes_nothing(self) -> None:
        question = "which models does it use"
        self.assertEqual(standalone(question, None), question)
        self.assertEqual(standalone(question, []), question)


class RetrievalQueryTests(unittest.TestCase):
    def test_the_referent_picks_the_document(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            kb = store(folder)
            bare = kb.answer("which models does it use")
            self.assertEqual(bare["sources"], ["research: retrieval augmented generation"])

            resolved = kb.answer(
                "which models does it use",
                retrieval_query=standalone("which models does it use", HISTORY),
            )
            self.assertEqual(resolved["sources"], ["README.md"])

    def test_the_question_still_picks_the_passage(self) -> None:
        """Scoring passages with the referent too answered the *referent*: it matched the
        README's opening line almost word for word and replied "J.A.R.V.I.S is a
        local-first personal laptop agent" to someone asking which models it uses.

        This asserts the answer is not that echo. It does not assert the ideal passage:
        `OPENAI_MODEL` tokenizes to "model" and the question says "models", so no passage
        in the file matches the question's own noun. Plural folding would close that, and
        measured over the real 30-document store it was a wash — one query fixed, one
        broken — so it is deliberately not here.
        """
        with tempfile.TemporaryDirectory() as folder:
            kb = store(folder)
            answer = str(
                kb.answer(
                    "which models does it use",
                    retrieval_query=standalone("which models does it use", HISTORY),
                )["answer"]
            )
            self.assertNotIn("local-first personal laptop agent", answer)


class AnswerFollowsTheRankingTests(unittest.TestCase):
    """The answer was quoted from whichever document held the best-scoring passage, which
    re-decided the document the ranking had already chosen. A scrape saying "models" nine
    times always outscores the file that says it once."""

    def test_the_answer_comes_from_the_top_ranked_document(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            kb = store(folder)
            query = "which JARVIS models are configured"

            ranked = kb.search(query, limit=2)
            self.assertEqual(ranked[0]["source"], "README.md")

            answer = kb.answer(query)
            self.assertEqual(
                answer["sources"], ["README.md"],
                "the answer was quoted from a document the ranking did not choose",
            )

    def test_the_pool_is_built_in_ranked_order(self) -> None:
        """`doc_index` decides which document the answer is quoted from, and it was
        carrying document-id order rather than relevance."""
        with tempfile.TemporaryDirectory() as folder:
            kb = KnowledgeBase(Path(folder) / "knowledge.json")
            kb.add("research: retrieval augmented generation", SCRAPE)  # added first, lower id
            kb.add("README.md", README)
            query = "which JARVIS models are configured"
            self.assertEqual(kb.search(query, limit=2)[0]["source"], "README.md")
            self.assertEqual(kb.answer(query)["sources"], ["README.md"])


if __name__ == "__main__":
    unittest.main()
