"""Talking, not reading out: the spoken style, the spoken budget, and the greeting."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer

import laptop_agent.webui as webui
from laptop_agent.planner.openai_compatible import OpenAICompatiblePlannerProvider
from laptop_agent.spoken import (MORE_ON_SCREEN, SPOKEN, SPOKEN_WORDS, VOICE_STYLE, SpokenBudget, greeting,
                                 reply_style, spoken_turn)
from laptop_agent.tools.base import ToolResult

from test_llm_planner import _FakeStream


def system_prompt(spoken: bool) -> str:
    seen = {}
    real = urllib.request.urlopen

    def fake(request, timeout=None, **kwargs):
        seen["response"] = _FakeStream(request, "stop")
        return seen["response"]

    urllib.request.urlopen = fake
    try:
        with spoken_turn(spoken):
            "".join(OpenAICompatiblePlannerProvider("k", "m").stream_answer("how's it going", {}))
    finally:
        urllib.request.urlopen = real
    return seen["response"].payload["messages"][0]["content"]


class SpokenStyleTests(unittest.TestCase):
    def test_a_spoken_turn_asks_for_talk_not_markdown(self) -> None:
        spoken, written = system_prompt(True), system_prompt(False)
        self.assertIn(VOICE_STYLE, spoken)
        self.assertNotIn("Markdown.", spoken.replace(VOICE_STYLE, ""))
        self.assertIn("Answer directly and helpfully in Markdown.", written)
        self.assertNotIn(VOICE_STYLE, written)

    def test_the_mark_ends_with_its_turn(self) -> None:
        with spoken_turn(True):
            self.assertEqual(reply_style(), VOICE_STYLE)
        self.assertFalse(SPOKEN.get())


class BudgetTests(unittest.TestCase):
    def test_sentences_are_said_until_the_budget_then_one_pointer(self) -> None:
        budget = SpokenBudget(words=6)
        self.assertEqual(budget.take("One two three four."), "One two three four.")
        self.assertEqual(budget.take("Five six seven."), "Five six seven.")
        self.assertIsNone(budget.take("Eight nine."))
        self.assertIsNone(budget.take("Ten."))
        self.assertEqual(budget.closing(), MORE_ON_SCREEN)

    def test_a_short_reply_has_no_pointer(self) -> None:
        budget = SpokenBudget()
        budget.take("Done.")
        self.assertIsNone(budget.closing())
        self.assertIsNone(budget.take("   "))


class GreetingTests(unittest.TestCase):
    first = staticmethod(lambda options: options[0])

    def test_by_time_of_day_and_name(self) -> None:
        self.assertEqual(greeting("Jeevan", datetime(2026, 10, 15, 8), self.first), "Good morning, Jeevan. What can I do for you?")
        self.assertEqual(greeting("Jeevan", datetime(2026, 10, 15, 14), self.first), "Good afternoon, Jeevan. What can I do for you?")
        self.assertEqual(greeting("Jeevan", datetime(2026, 10, 15, 19), self.first), "Good evening, Jeevan. What can I do for you?")
        self.assertEqual(greeting("Jeevan", datetime(2026, 10, 15, 2), self.first), "Still up, Jeevan? What can I do for you?")

    def test_without_a_name_it_does_not_invent_one(self) -> None:
        self.assertEqual(greeting(None, datetime(2026, 10, 15, 19), self.first), "Good evening. What can I do for you?")

    def test_it_varies(self) -> None:
        said = {greeting("Jeevan", datetime(2026, 10, 15, 10), lambda options: options[n]) for n in range(4)}
        self.assertEqual(len(said), 4)


class SpokenStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._original_handle = webui._orchestrator.handle
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        webui._orchestrator.handle = cls._original_handle

    def stream(self, payload: dict) -> list[dict]:
        request = urllib.request.Request(self.base + "/api/stream", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", "X-Jarvis-Token": webui._API_TOKEN})
        body = urllib.request.urlopen(request, timeout=15).read().decode()
        return [json.loads(block.strip()[5:]) for block in body.split("\n\n") if block.strip().startswith("data:")]

    def test_a_voice_turn_runs_marked_as_spoken_and_a_typed_one_does_not(self) -> None:
        seen: list[bool] = []

        async def handle(command, history=None, on_token=None, **kwargs):
            seen.append(SPOKEN.get())
            return ToolResult.success("Fine.")

        webui._orchestrator.handle = handle
        self.stream({"command": "hi", "voice": True})
        self.stream({"command": "hi"})
        self.assertEqual(seen, [True, False])

    def test_a_long_reply_is_said_in_part_and_points_at_the_screen(self) -> None:
        reply = " ".join(f"Sentence number {n} carries a few more words to say." for n in range(30))

        async def handle(command, history=None, on_token=None, **kwargs):
            return ToolResult.success(reply)

        webui._orchestrator.handle = handle
        said = [event["text"] for event in self.stream({"command": "long", "voice": True}) if event.get("type") == "tts"]
        self.assertEqual(said[-1], MORE_ON_SCREEN)
        words = sum(len(text.split()) for text in said[:-1])
        self.assertLessEqual(words, SPOKEN_WORDS + 60)
        self.assertLess(words, len(reply.split()))

    def test_the_greeting_uses_the_remembered_name(self) -> None:
        memory = webui._orchestrator.context.memory
        before = memory.get_profile().get("name")
        memory.set_profile_value("name", "jeevan")
        try:
            body = json.loads(urllib.request.urlopen(urllib.request.Request(
                self.base + "/api/greeting", headers={"X-Jarvis-Token": webui._API_TOKEN}), timeout=15).read())
        finally:
            if before is None:
                memory.forget_profile_value("name")
            else:
                memory.set_profile_value("name", before)
        self.assertTrue(body["ok"])
        self.assertIn("Jeevan", body["text"])
        self.assertIn("/api/greeting", webui._PERSONAL_ROUTES)


if __name__ == "__main__":
    unittest.main()
