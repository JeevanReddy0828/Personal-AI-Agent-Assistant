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
    turns = [
        {"role": "user", "text": "Design a database schema for an online marketplace with users, listings, orders."},
        {"role": "assistant", "text": SCHEMA},
    ]
    for index in range(extra):
        turns.insert(0, {"role": "assistant", "text": f"Earlier answer number {index} about the weather in Tokyo."})
        turns.insert(0, {"role": "user", "text": f"Earlier question number {index} about weather."})
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
        for query in ["build an ERD for this", "make it shorter", "turn the schema into a diagram", "shorter"]:
            self.assertTrue(refers_back(query), query)
        for query in ["what is the weather in Tokyo tomorrow", "research local-first ai agents"]:
            self.assertFalse(refers_back(query), query)


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
        self.assertIn("most likely refers to J.A.R.V.I.S's reply in turn 2", result.text)
        self.assertIn("do not search files", result.text)

    def test_budget_is_respected_and_relevant_sections_survive_truncation(self) -> None:
        budget = len(SCHEMA) // 2
        result = build_context(_session(), "add indexes to the action plan tables", budget=budget)
        self.assertLessEqual(len(result.text), budget + 200)  # the referent note may overhang slightly
        self.assertIn("omitted here", result.text)
        self.assertIn("remaining sections", result.text)
        self.assertIn("Action plan", result.text)  # named as a remaining section or surfaced as a chunk

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
