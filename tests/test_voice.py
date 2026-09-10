from __future__ import annotations

import unittest

from laptop_agent.voice import SpeechChunker, clean_for_speech


class CleanForSpeechTests(unittest.TestCase):
    def test_drops_heading_underlines_and_rules(self) -> None:
        self.assertEqual(clean_for_speech("================"), "")
        self.assertEqual(clean_for_speech("--------------------"), "")

    def test_strips_markdown_markers(self) -> None:
        self.assertEqual(clean_for_speech("**Possible Sources**"), "Possible Sources")
        self.assertEqual(clean_for_speech("# Heading"), "Heading")
        self.assertEqual(clean_for_speech("- Government databases"), "Government databases")

    def test_keeps_plain_text(self) -> None:
        self.assertEqual(clean_for_speech("Agent Greg"), "Agent Greg")


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


class SpeechEchoLoopTests(unittest.TestCase):
    """Reading a URL aloud produced garbled speech the echo guard could not match, so
    the microphone heard it, treated it as a new request, and drew the picture again."""

    def test_an_embedded_picture_is_not_read_aloud(self) -> None:
        spoken = clean_for_speech("![a futuristic city](/api/image?name=a-futuristic-city-1789.jpg)\n\nHere is *a futuristic city*.")
        self.assertEqual(spoken, "Here is a futuristic city.")
        self.assertNotIn("api", spoken)
        self.assertNotIn("jpg", spoken)

    def test_a_picture_on_its_own_is_silent(self) -> None:
        self.assertEqual(clean_for_speech("![a fox](/api/image?name=fox-1.png)"), "")

    def test_a_link_reads_as_its_label(self) -> None:
        self.assertEqual(clean_for_speech("See [the docs](https://example.com/guide) for more."), "See the docs for more.")

    def test_a_bare_url_is_dropped(self) -> None:
        self.assertEqual(clean_for_speech("Visit https://build.nvidia.com to get a key."), "Visit to get a key.")

    def test_a_code_block_is_not_speech(self) -> None:
        self.assertEqual(clean_for_speech("Run:\n```bash\nnpm install\n```\nThen restart."), "Run: Then restart.")

    def test_ordinary_prose_is_untouched(self) -> None:
        self.assertEqual(clean_for_speech("Plain sentence with no markdown."), "Plain sentence with no markdown.")


if __name__ == "__main__":
    unittest.main()
