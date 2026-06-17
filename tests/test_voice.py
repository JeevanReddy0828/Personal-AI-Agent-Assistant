from __future__ import annotations

import unittest

from laptop_agent.voice import SpeechChunker


class SpeechChunkerTests(unittest.TestCase):
    def test_emits_complete_sentence(self) -> None:
        chunker = SpeechChunker()
        self.assertEqual(chunker.feed("Hello there. "), ["Hello there."])

    def test_holds_incomplete_sentence(self) -> None:
        chunker = SpeechChunker()
        self.assertEqual(chunker.feed("How are "), [])
        self.assertEqual(chunker.flush(), "How are")

    def test_streams_across_deltas(self) -> None:
        chunker = SpeechChunker()
        out: list[str] = []
        for delta in ["Hello there", ". How are ", "you today? Great"]:
            out.extend(chunker.feed(delta))
        self.assertEqual(out, ["Hello there.", "How are you today?"])
        self.assertEqual(chunker.flush(), "Great")

    def test_merges_short_fragments_forward(self) -> None:
        chunker = SpeechChunker(min_chars=8)
        # "Hi." is too short to speak alone, so it merges with the next sentence.
        self.assertEqual(chunker.feed("Hi. "), [])
        self.assertEqual(chunker.feed("Go right now. "), ["Hi. Go right now."])

    def test_splits_on_newline(self) -> None:
        chunker = SpeechChunker()
        self.assertEqual(chunker.feed("First line\nSecond line\n"), ["First line", "Second line"])

    def test_flush_is_idempotent(self) -> None:
        chunker = SpeechChunker()
        chunker.feed("Tail text")
        self.assertEqual(chunker.flush(), "Tail text")
        self.assertIsNone(chunker.flush())


if __name__ == "__main__":
    unittest.main()
