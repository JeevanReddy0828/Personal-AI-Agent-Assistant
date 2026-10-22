from __future__ import annotations

import json
from pathlib import Path
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import laptop_agent.webui as webui
from laptop_agent.voice import clean_for_speech
from laptop_agent.spoken import MORE_ON_SCREEN, SPOKEN_WORDS
from laptop_agent.tools.base import ToolResult


class VoiceStreamTests(unittest.TestCase):
    """Confirm /api/stream emits incremental `tts` sentence events when voice=true,
    so the browser can start speaking before generation finishes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._original_handle = webui._orchestrator.handle
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        webui._orchestrator.handle = cls._original_handle

    def setUp(self) -> None:
        # Drive on_token with known deltas so the sentence boundaries are deterministic.
        async def fake_handle(command, history=None, on_token=None, **kwargs):
            if on_token:
                for delta in ["Hello there", ". How are ", "you today? See ya. "]:
                    on_token(delta)
            return ToolResult.success("Hello there. How are you today? See ya.")

        webui._orchestrator.handle = fake_handle

    def _stream(self, payload: dict) -> list[dict]:
        req = urllib.request.Request(
            self.base + "/api/stream",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Jarvis-Token": webui._API_TOKEN},
        )
        body = urllib.request.urlopen(req, timeout=15).read().decode()
        events = []
        for block in body.split("\n\n"):
            block = block.strip()
            if block.startswith("data:"):
                events.append(json.loads(block[5:].strip()))
        return events

    def test_voice_stream_emits_tts_sentences(self) -> None:
        events = self._stream({"command": "hi", "history": [], "voice": True})
        tts = [e["text"] for e in events if e.get("type") == "tts"]
        # Each complete sentence streams as it forms, before generation finishes.
        self.assertEqual(tts, ["Hello there.", "How are you today?", "See ya."])
        self.assertTrue(any(e.get("type") == "done" for e in events))

    def test_no_tts_events_without_voice_flag(self) -> None:
        events = self._stream({"command": "hi", "history": []})
        self.assertFalse([e for e in events if e.get("type") == "tts"])
        self.assertTrue(any(e.get("type") == "token" for e in events))

    def test_short_nonstreamed_result_stays_one_utterance(self) -> None:
        async def instant(*args, **kwargs):
            return ToolResult.success("Done.")

        webui._orchestrator.handle = instant
        events = self._stream({"command": "status", "voice": True})
        self.assertEqual([e["text"] for e in events if e.get("type") == "tts"], ["Done."])

    def test_long_nonstreamed_results_are_bounded_and_complete(self) -> None:
        cases = json.loads((Path(__file__).parent / "data" / "nonstreamed_speech_cases.json").read_text(encoding="utf-8"))["cases"]
        for case in cases:
            reply = case["reply"] * case.get("repeat", 1)

            async def instant(*args, **kwargs):
                return ToolResult.success(reply)

            webui._orchestrator.handle = instant
            with self.subTest(case=case["name"]):
                events = self._stream({"command": "tool result", "voice": True})
                chunks = [e["text"] for e in events if e.get("type") == "tts"]
                expected = clean_for_speech(reply)
                # Jeevan, 2026-10-15: a long result is not read out whole. What is said is the start
                # of it, in bounded pieces, then one pointer to the screen (spoken.py).
                if len(expected.split()) <= SPOKEN_WORDS:
                    self.assertEqual(" ".join(chunks).split(), expected.split())
                    continue
                self.assertEqual(chunks[-1], MORE_ON_SCREEN)
                said = " ".join(chunks[:-1]).split()
                self.assertEqual(said, expected.split()[:len(said)])
                self.assertLess(len(said), len(expected.split()))
                self.assertTrue(all(len(chunk) <= 360 for chunk in chunks))

    def test_final_result_is_cleaned_before_splitting_and_reset_discards_old_text(self) -> None:
        reply = ("The user-facing report is ready. " * 14) + "\n```python\nsecret_code()\n```\nThe final check passed."

        async def fallback(*args, on_token=None, **kwargs):
            on_token("Old draft that should be reset. ")
            on_token.reset()
            return ToolResult.success(reply)

        webui._orchestrator.handle = fallback
        events = self._stream({"command": "report", "voice": True})
        after_reset = events[next(i for i, event in enumerate(events) if event.get("type") == "reset") + 1:]
        chunks = [e["text"] for e in after_reset if e.get("type") == "tts"]
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[-1], MORE_ON_SCREEN)
        said = " ".join(chunks[:-1]).split()
        self.assertEqual(said, clean_for_speech(reply).split()[:len(said)])
        self.assertNotIn("Old draft", " ".join(chunks))
        self.assertNotIn("secret_code", " ".join(chunks))

    def test_a_long_failure_still_says_why_it_failed(self) -> None:
        reason = "The operation failed because the destination is protected. Ask for approval before retrying."
        reply = " ".join(f"Step {n} finished." for n in range(1, 19)) + " " + reason

        async def failing(*args, **kwargs):
            return ToolResult.failure(reply)

        webui._orchestrator.handle = failing
        events = self._stream({"command": "move it", "voice": True})
        said = " ".join(e["text"] for e in events if e.get("type") == "tts")
        self.assertIn(reason, said)
        self.assertLessEqual(len(said.split()), SPOKEN_WORDS + len(MORE_ON_SCREEN.split()) + 5)

    def test_a_reset_gives_the_replacement_answer_a_fresh_budget(self) -> None:
        async def replaced(*args, on_token=None, **kwargs):
            for n in range(1, 19):
                on_token(f"Step {n} finished. ")
            on_token.reset()
            return ToolResult.failure("The operation failed. Ask for approval before retrying.")

        webui._orchestrator.handle = replaced
        events = self._stream({"command": "move it", "voice": True})
        after_reset = events[next(i for i, event in enumerate(events) if event.get("type") == "reset") + 1:]
        self.assertEqual([e["text"] for e in after_reset if e.get("type") == "tts"],
                         ["The operation failed. Ask for approval before retrying."])


if __name__ == "__main__":
    unittest.main()
