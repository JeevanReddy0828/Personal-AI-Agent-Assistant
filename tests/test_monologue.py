from __future__ import annotations

import io
import json
import unittest
import urllib.error

from laptop_agent.planner.openai_compatible import (
    OpenAICompatiblePlannerProvider,
    looks_like_thinking,
)

# Real leaks, from the reported transcript and from probing the model directly.
LEAKS = [
    ("The user is noting a discrepancy between two numbers. I will respond accordingly.", False),
    ("The user is noting a discrepancy. I'll respond accordingly.", False),
    ("Okay, the user is pointing out a contradiction in my previous statements", True),
    ("We need to respond exactly as asked.", True),
    ("The user says: 'Reply with exactly: ready'. So we must respond exactly", True),
    ("Let me think about what they are really asking here.", True),
    ("Hmm, the user wants a summary of the file", True),
]

# Ordinary replies that begin the same way and must survive. A guard that eats real
# answers is worse than the leak it prevents.
REAL = [
    ("The user interface is built with plain HTML and a nonce-based CSP.", False),
    ("The users table has a primary key called user_id.", False),
    ("You're absolutely right to call out that inconsistency — 9 is correct.", False),
    ("A CSV file is a plain-text format for tabular data.", False),
    ("The user asked me to summarise it, so here is the summary: ...", False),
    ("First, install the package. Then run the server.", True),
    ("The user is right, and the answer is 9.", False),
    ("", True),
]


class DetectionTests(unittest.TestCase):
    """Reported: the assistant printed "The user is noting a discrepancy... I'll respond
    accordingly" instead of responding.

    Measured cause: a reasoning model cut off mid-thought returns its reasoning as
    `content` rather than `reasoning_content`. The same prompt at 16, 40, 120 and 400
    tokens all came back "Okay, the user is pointing out a contradiction..." with
    finish_reason "length"; at 900 it finished and answered properly."""

    def test_a_monologue_is_recognised(self) -> None:
        for text, truncated in LEAKS:
            self.assertTrue(looks_like_thinking(text, truncated), text[:60])

    def test_an_ordinary_reply_is_not(self) -> None:
        for text, truncated in REAL:
            self.assertFalse(looks_like_thinking(text, truncated), text[:60])

    def test_a_complete_reply_that_merely_starts_that_way_survives(self) -> None:
        # Opening with "the user is" is only damning when the reply was cut off, or when
        # it promises an answer it never gives.
        self.assertFalse(
            looks_like_thinking("The user is asking about CSV files, and the answer is: "
                                "a CSV is comma-separated values.", False))


class StrippingTests(unittest.TestCase):
    def test_an_unclosed_think_tag_is_stripped(self) -> None:
        # A truncated reply leaves the tag open, and the pair regex needs the closing tag
        # to match at all — so the whole chain of thought used to be shown.
        self.assertEqual(
            OpenAICompatiblePlannerProvider._strip_reasoning("<think>notes that never close"), "")

    def test_a_closed_think_tag_still_works(self) -> None:
        self.assertEqual(
            OpenAICompatiblePlannerProvider._strip_reasoning("<think>notes</think>The answer."),
            "The answer.")

    def test_ordinary_text_is_untouched(self) -> None:
        self.assertEqual(
            OpenAICompatiblePlannerProvider._strip_reasoning("A plain answer."), "A plain answer.")


class TransportTests(unittest.TestCase):
    """The transport never looked at finish_reason, so a reply cut off mid-thought was
    returned as though it were complete."""

    def respond(self, content: str, finish: str = "stop"):
        payload = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}

        class Response:
            def read(self_inner):
                return json.dumps(payload).encode("utf-8")

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return Response()

    def transport(self, content: str, finish: str = "stop") -> str:
        from laptop_agent.planner import openai_compatible as module

        provider = OpenAICompatiblePlannerProvider(
            "k", "m", base_url="https://example.invalid/v1")
        original = module.urllib.request.urlopen
        module.urllib.request.urlopen = lambda *a, **k: self.respond(content, finish)
        try:
            return provider._http_transport({"model": "m", "messages": []})
        finally:
            module.urllib.request.urlopen = original

    def test_a_truncated_monologue_returns_nothing(self) -> None:
        # Empty means "this tier gave nothing", which the orchestrator already handles by
        # degrading to the next one — far better than showing the user the model's notes.
        self.assertEqual(
            self.transport("Okay, the user is pointing out a contradiction", "length"), "")

    def test_a_truncated_real_answer_is_still_returned(self) -> None:
        out = self.transport("A CSV file is a plain-text format for tabular", "length")
        self.assertIn("CSV", out)

    def test_a_complete_answer_is_returned(self) -> None:
        self.assertEqual(self.transport("The answer is 9.", "stop"), "The answer is 9.")

    def test_the_leak_is_recorded_so_it_is_visible(self) -> None:
        from laptop_agent.failures import FAILURES

        FAILURES.clear()
        self.transport("Okay, the user is pointing out a contradiction", "length")
        recorded = FAILURES.recent()
        self.assertTrue(recorded)
        self.assertEqual(recorded[0]["where"], "llm/monologue")


if __name__ == "__main__":
    unittest.main()
