from __future__ import annotations

import unittest

from laptop_agent.context import build_context, chunk_text, context_block, rank_chunks, refers_back, chunk_history

SCHEMA = (
    "## Problem\nDesign a robust PostgreSQL schema for an online marketplace.\n\n"
    "## Key considerations\n- Immutability: order totals reflect price at purchase time.\n"
    "- Payment lifecycle: pending, succeeded, failed, refunded.\n\n"
    "## Options\n**Option A: Fully normalized with snapshots.** Pros: integrity. Cons: joins.\n"
    "**Option B: Denormalized totals with triggers.** Pros: fast reads. Cons: drift.\n\n"
    "## Recommendation\nOption A.\n\n"
    "## Action plan\n1. Create `users` (id UUID PK, email citext unique).\n"
    "2. Create `listings` (id, seller_id FK users, price_cents, currency, status).\n"
    "3. Create `orders` (id, buyer_id FK users, total_cents, status).\n"
    "4. Create `order_items` (order_id FK orders, listing_id FK listings, snapshot_price_cents).\n"
    "5. Create `payments` (order_id FK, amount_cents, status).\n"
    "6. Create `refunds` (payment_id FK payments, amount_cents).\n"
    "7. Create `reviews` (order_id FK, reviewer_id FK users, rating 1-5).\n\n"
    "```sql\nCREATE TABLE users (id uuid PRIMARY KEY, email citext UNIQUE);\n```\n\n"
    "## Watch-outs\n- Snapshot drift.\n- Refund exceeding payment.\n"
)


def _session(extra: int = 0) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    for index in range(extra):  # oldest first: filler weather turns, then the schema exchange
        turns.append({"role": "user", "text": f"Earlier question number {index} about weather."})
        turns.append({"role": "assistant", "text": f"Earlier answer number {index} about the weather in Tokyo."})
    turns.append({"role": "user", "text": "Design a database schema for an online marketplace with users, listings, orders."})
    turns.append({"role": "assistant", "text": SCHEMA})
    return turns


class ChunkingTests(unittest.TestCase):
    def test_headings_label_chunks_and_fences_stay_whole(self) -> None:
        pieces = chunk_text(SCHEMA)
        headings = [heading for heading, _ in pieces]
        self.assertIn("Action plan", headings)
        self.assertIn("Watch-outs", headings)
        fenced = [text for _, text in pieces if "```sql" in text]
        self.assertEqual(len(fenced), 1)
        self.assertTrue(fenced[0].rstrip().endswith("```"))

    def test_oversized_paragraph_is_split_at_boundaries(self) -> None:
        text = ". ".join(f"Sentence number {i} says something" for i in range(200))
        pieces = chunk_text(text, target=400, hard=800)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(piece) <= 800 for _, piece in pieces))
        self.assertEqual("".join(piece.replace(" ", "") for _, piece in pieces), text.replace(" ", ""))

    def test_refers_back_detects_follow_ups(self) -> None:
        for query in ["build an ERD for this", "make it shorter", "turn the schema above into a diagram", "shorter", "in mermaid"]:
            self.assertTrue(refers_back(query), query)
        for query in [
            "what is the weather in Tokyo tomorrow", "research local-first ai agents",
            "explain the design principles of REST APIs", "check weather", "scan files .", "play music",
            "edit the items list",  # 'it' inside 'edit'/'items' does not count
            "Answer the user's question using the web search results below, which are current. Prefer this live "
            "information over any prior knowledge, lead with the most up-to-date facts, and cite sources inline. "
            "If the results don't clearly answer it, say what is and isn't known. QUESTION: did the war end? "
            "WEB SEARCH RESULTS: [1] a [2] b [3] c [4] d [5] e f g h i j k l m n o p q r s t u v w x y z a b c d",
        ]:
            self.assertFalse(refers_back(query), query)

    def test_nested_fences_stay_one_chunk(self) -> None:
        text = "Here is how to write a fenced block:\n\n~~~\n```python\nprint('hi')\n```\n~~~\n\nThat is all."
        pieces = chunk_text(text)
        fenced = [piece for _, piece in pieces if "```python" in piece]
        self.assertEqual(len(fenced), 1)
        self.assertIn("~~~\n```python\nprint('hi')\n```\n~~~", fenced[0])  # closed by the matching ~~~ only


class BuildContextTests(unittest.TestCase):
    def test_empty_history_gives_no_block(self) -> None:
        self.assertEqual(context_block([], "anything"), "")
        self.assertEqual(context_block([{"role": "user", "text": "   "}], "x"), "")

    def test_short_session_is_verbatim_with_role_labels(self) -> None:
        text = context_block(
            [{"role": "user", "text": "summarize the README"}, {"role": "assistant", "text": "Done."}],
            "what did I ask?",
        )
        self.assertIn("Recent conversation", text)
        self.assertIn("User: summarize the README", text)
        self.assertIn("J.A.R.V.I.S: Done.", text)

    def test_follow_up_keeps_the_whole_previous_answer_and_names_the_referent(self) -> None:
        result = build_context(_session(), "build an ERD for this", budget=9000)
        self.assertTrue(result.refers_back)
        self.assertIn("order_items", result.text)          # the tables the follow-up needs
        self.assertIn("CREATE TABLE users", result.text)   # fenced code survives verbatim
        self.assertIn("most likely means J.A.R.V.I.S's reply in turn 2", result.text)
        self.assertIn("Never search files", result.text)

    def test_budget_is_respected_and_relevant_sections_survive_truncation(self) -> None:
        budget = len(SCHEMA) // 2
        result = build_context(_session(), "add indexes to the action plan tables", budget=budget)
        self.assertLessEqual(len(result.text), budget)
        self.assertIn("omitted here", result.text)
        self.assertIn("remaining sections", result.text)
        self.assertIn("Action plan", result.text)  # named as a remaining section or surfaced as a chunk
        # The referent note always fits inside the budget too.
        result = build_context(_session(), "build an ERD for this", budget=600)
        self.assertLessEqual(len(result.text), 600)
        self.assertIn("most likely means", result.text)
        result = build_context(_session(), "build an ERD for this", budget=300)  # too small for the note
        self.assertLessEqual(len(result.text), 300)
        self.assertNotIn("most likely means", result.text)

    def test_accepts_context_query_is_decided_by_signature(self) -> None:
        from laptop_agent.context import accepts_context_query

        def legacy(text, profile, model=None, history=None):
            raise TypeError("a bug inside the provider must not look like an unsupported keyword")

        def modern(text, profile, model=None, history=None, context_query=None):
            return "ok"

        self.assertFalse(accepts_context_query(legacy))
        self.assertTrue(accepts_context_query(modern))
        self.assertTrue(accepts_context_query(lambda *a, **kw: "ok"))

    def test_context_is_memoized_per_inputs(self) -> None:
        history = _session()
        first = build_context(history, "build an ERD for this")
        self.assertIs(build_context(list(history), "build an ERD for this"), first)
        self.assertIsNot(build_context(history, "add indexes"), first)

    def test_long_session_gets_an_outline_and_relevant_earlier_chunks(self) -> None:
        history = _session(extra=6) + [
            {"role": "user", "text": "thanks"},
            {"role": "assistant", "text": "Any time, Jeevan."},
            {"role": "user", "text": "and what about the weather?"},
            {"role": "assistant", "text": "Still sunny in Tokyo."},
        ]
        result = build_context(history, "which tables did you propose for refunds and payments", budget=6000)
        self.assertIn("Most recent turns:", result.text)
        self.assertRegex(result.text, r"\d+\. J\.A\.R\.V\.I\.S: Problem")
        self.assertIn("Relevant earlier context", result.text)
        self.assertIn("refunds", result.text)
        self.assertLessEqual(len(result.text), 6000 + 300)

    def test_ranking_prefers_matching_and_referent_chunks(self) -> None:
        turns = [("user", "hi"), ("assistant", "## Fruit\napples and pears\n\n## Cars\nengines and wheels")]
        chunks = chunk_history(turns)
        ranked = rank_chunks(chunks, "tell me about engines", 2)
        self.assertIn("engines", ranked[0][1].text)
        boosted = rank_chunks(chunks, "more", 2, referent_turn=1)
        self.assertTrue(all(score >= 1.0 for score, chunk in boosted if chunk.turn == 1))


if __name__ == "__main__":
    unittest.main()
