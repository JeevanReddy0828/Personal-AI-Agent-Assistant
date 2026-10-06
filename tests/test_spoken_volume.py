"""Voice-transcribed volume numbers reach the media level control."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_everyday_requests import Everyday


class SpokenVolumeTests(unittest.TestCase):
    def test_spoken_level_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            for said, level in (("set the volume to fifty", 50),
                                ("um set the volume to fifty percent", 50),
                                ("turn the volume to twenty five", 25),
                                ("volume one hundred", 100)):
                with self.subTest(said=said):
                    result, ran = everyday.say(said, stream=False)
                    self.assertEqual(ran, f"media volume {level}")
                    self.assertTrue(result.ok, result.message)
                    self.assertIn(f"{level}%", result.message)

    def test_volume_question_does_not_change_level(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            result, ran = everyday.say("how do I set the volume to fifty", stream=False)
            self.assertIsNone(ran)
            self.assertIn("answered", result.message)


if __name__ == "__main__":
    unittest.main()
