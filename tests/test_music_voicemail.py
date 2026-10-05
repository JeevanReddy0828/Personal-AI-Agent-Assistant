"""Personal voicemail and messages must never become a YouTube music search."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_everyday_requests import Everyday


class MusicVoicemailTests(unittest.TestCase):
    def test_personal_messages_do_not_route_to_music(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            for said in ("play my voicemail", "play my messages", "um play my voicemail"):
                with self.subTest(said=said):
                    result, ran = everyday.say(said, stream=False)
                    self.assertIsNone(ran, (said, ran, result.message))
                    self.assertIn("answered]", result.message)
            result, ran = everyday.say("play Voicemail by Tyler the Creator", stream=False)
            self.assertTrue(result.ok, result.message)
            self.assertTrue(ran.startswith("play music "), ran)

    def test_music_tool_refuses_a_model_routed_personal_message(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            music = everyday.orchestrator.context.music
            music._resolve = lambda query: self.fail(f"unexpected YouTube search: {query}")
            for target in ("my voicemail", "my messages", "my voice messages"):
                with self.subTest(target=target):
                    result = music.play(target)
                    self.assertFalse(result.ok)
                    self.assertIn("messages", result.message.lower())


if __name__ == "__main__":
    unittest.main()
