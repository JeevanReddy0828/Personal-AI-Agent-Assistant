"""A picture request is drawn from a detailed prompt, and a follow-up from the last picture.

Reported from the phone: "Chatgpt in mojito image" reached FLUX as the three words
"Chatgpt in mojito" and came back as a plain mojito, and the "more lime" turn before it had
drawn an unrelated glass, because every follow-up started from nothing."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path

from laptop_agent.planner import Planner
from laptop_agent.tools.imagegen import ImageTool

import test_orchestrator

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
REWRITE = "A tall glass mojito with crushed ice, mint and lime, a small white knot-shaped logo on the rim"
PREVIOUS = {
    "image": "C:/data/images/mojito-1.jpg",
    "name": "mojito-1.jpg",
    "url": "/api/image?name=mojito-1.jpg",
    "prompt": "A tall glass of mojito on a tropical patio at golden hour",
    "request": "mojito",
    "seed": 4242,
}


def picture_turn(data: dict | None = PREVIOUS) -> dict[str, str]:
    """An assistant turn as the web client sends it back: the reply, then its tool data."""
    text = f"![mojito]({PREVIOUS['url']})\n\nHere is *mojito*."
    if data is not None:
        text += "\n[tool result data, context only - not a format to imitate] " + json.dumps(data, separators=(",", ":"))
    return {"role": "assistant", "text": text}


class Writer:
    """The fast tier, answering every rewrite with one fixed prompt and keeping what it was asked."""

    def __init__(self, reply: str = REWRITE, delay: float = 0.0, error: Exception | None = None) -> None:
        self.reply, self.delay, self.error = reply, delay, error
        self.asked: list[str] = []

    def answer(self, text, profile, model=None, history=None, max_tokens=900):
        self.asked.append(text)
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.reply


class ImagePromptTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sent: list[dict] = []

    def orchestrator(self, writer: Writer | None):
        o = self.build(self.root)
        if writer is not None:
            o.planner = Planner(writer)

        def backend(model: str, body: dict) -> dict:
            self.sent.append(body)
            return {"artifacts": [{"base64": base64.b64encode(JPEG).decode()}]}

        o._image_tool_cache = ImageTool(api_key="", data_dir=self.root, backend=backend)
        return o

    def draw(self, o, request: str, history=None):
        return asyncio.run(o.handle(f"image {request}", history=history or []))

    def test_the_model_draws_the_detailed_prompt_and_the_caption_keeps_the_users_words(self) -> None:
        writer = Writer()
        result = self.draw(self.orchestrator(writer), "Chatgpt in mojito")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.sent[0]["prompt"], REWRITE)
        self.assertIn("Here is *Chatgpt in mojito*.", result.message)
        self.assertIn("Request: Chatgpt in mojito", writer.asked[0])
        self.assertNotIn("previous picture", writer.asked[0])
        self.assertEqual((result.data["prompt"], result.data["request"]), (REWRITE, "Chatgpt in mojito"))

    def test_a_follow_up_is_drawn_from_the_last_pictures_prompt_and_seed(self) -> None:
        writer = Writer()
        result = self.draw(self.orchestrator(writer), "Chatgpt in mojito",
                           [{"role": "user", "text": "mojito"}, picture_turn()])
        self.assertTrue(result.ok, result.message)
        self.assertIn(PREVIOUS["prompt"], writer.asked[0])
        self.assertEqual(self.sent[0]["seed"], 4242)
        self.assertEqual(result.data["seed"], 4242)

    def test_the_same_request_again_is_a_new_picture(self) -> None:
        self.draw(self.orchestrator(Writer()), "mojito", [picture_turn()])
        self.assertNotEqual(self.sent[0]["seed"], 4242)

    def test_asking_for_another_one_is_a_new_picture(self) -> None:
        self.draw(self.orchestrator(Writer()), "another mojito", [picture_turn()])
        self.assertNotEqual(self.sent[0]["seed"], 4242)

    def test_only_the_latest_reply_counts_as_the_last_picture(self) -> None:
        writer = Writer()
        history = [picture_turn(), {"role": "user", "text": "thanks"},
                   {"role": "assistant", "text": "You're welcome."}]
        self.draw(self.orchestrator(writer), "a red fox", history)
        self.assertNotIn(PREVIOUS["prompt"], writer.asked[0])
        self.assertNotEqual(self.sent[0]["seed"], 4242)

    def test_a_reply_without_picture_data_is_not_a_picture(self) -> None:
        writer = Writer()
        self.draw(self.orchestrator(writer), "a red fox", [picture_turn(None)])
        self.assertNotIn("previous picture", writer.asked[0])
        other = dict(PREVIOUS, url="/api/document?name=x.pdf")
        self.draw(self.orchestrator(writer), "a red fox", [picture_turn(other)])
        self.assertNotIn("previous picture", writer.asked[1])

    def test_without_a_model_the_request_is_drawn_as_written(self) -> None:
        self.draw(self.orchestrator(None), "Chatgpt in mojito")
        self.assertEqual(self.sent[0]["prompt"], "Chatgpt in mojito")

    def test_a_failing_model_costs_the_rewrite_not_the_picture(self) -> None:
        result = self.draw(self.orchestrator(Writer(error=RuntimeError("model offline"))), "Chatgpt in mojito")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.sent[0]["prompt"], "Chatgpt in mojito")

    def test_a_slow_model_is_not_waited_for(self) -> None:
        o = self.orchestrator(Writer(delay=2.0))
        o._IMAGE_PROMPT_SECONDS = 0.2
        started = time.monotonic()
        result = self.draw(o, "Chatgpt in mojito")
        self.assertTrue(result.ok, result.message)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.sent[0]["prompt"], "Chatgpt in mojito")

    def test_a_reply_about_itself_is_not_a_prompt(self) -> None:
        self.draw(self.orchestrator(Writer("I can't draw brand logos, but here is a mojito.")), "Chatgpt in mojito")
        self.assertEqual(self.sent[0]["prompt"], "Chatgpt in mojito")

    def test_a_labelled_or_quoted_reply_is_cleaned(self) -> None:
        self.draw(self.orchestrator(Writer(f'Prompt: "{REWRITE}"\nThis keeps the scene.')), "Chatgpt in mojito")
        self.assertEqual(self.sent[0]["prompt"], REWRITE)


class ImageToolSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sent: list[dict] = []

    def tool(self) -> ImageTool:
        def backend(model: str, body: dict) -> dict:
            self.sent.append(body)
            return {"artifacts": [{"base64": base64.b64encode(JPEG).decode()}]}

        return ImageTool(api_key="", data_dir=Path(self._tmp.name), backend=backend)

    def test_every_picture_carries_the_seed_it_was_drawn_from(self) -> None:
        result = self.tool().generate("a fox")
        self.assertIsInstance(self.sent[0]["seed"], int)
        self.assertGreater(self.sent[0]["seed"], 0)
        self.assertEqual(result.data["seed"], self.sent[0]["seed"])

    def test_a_given_seed_is_used(self) -> None:
        self.tool().generate("a fox", seed=4242, detail="A red fox asleep in deep snow")
        self.assertEqual((self.sent[0]["seed"], self.sent[0]["prompt"]), (4242, "A red fox asleep in deep snow"))


if __name__ == "__main__":
    unittest.main()
