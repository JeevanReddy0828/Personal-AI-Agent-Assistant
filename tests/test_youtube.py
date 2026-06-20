from __future__ import annotations

import unittest

from laptop_agent.tools.youtube import MissingTranscriptError, YouTubeTool


class YouTubeIdTests(unittest.TestCase):
    def test_extracts_id_from_various_urls(self) -> None:
        ids = {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s": "dQw4w9WgXcQ",
            "dQw4w9WgXcQ": "dQw4w9WgXcQ",
        }
        for url, expected in ids.items():
            self.assertEqual(YouTubeTool.extract_id(url), expected, url)

    def test_rejects_non_youtube(self) -> None:
        self.assertIsNone(YouTubeTool.extract_id("https://example.com/video"))


class TranscriptTests(unittest.TestCase):
    def test_fetches_transcript_with_backend(self) -> None:
        tool = YouTubeTool(transcript_backend=lambda vid: f"hello from {vid}  spaced   out")
        result = tool.transcript("https://youtu.be/dQw4w9WgXcQ")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(result.data["transcript"], "hello from dQw4w9WgXcQ spaced out")
        self.assertEqual(result.data["url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_bad_link(self) -> None:
        self.assertFalse(YouTubeTool(transcript_backend=lambda v: "x").transcript("not a link").ok)

    def test_empty_transcript(self) -> None:
        self.assertFalse(YouTubeTool(transcript_backend=lambda v: "   ").transcript("dQw4w9WgXcQ").ok)

    def test_missing_dependency_hint(self) -> None:
        def raise_missing(_vid):
            raise MissingTranscriptError("install: pip install youtube-transcript-api")
        result = YouTubeTool(transcript_backend=raise_missing).transcript("dQw4w9WgXcQ")
        self.assertFalse(result.ok)
        self.assertIn("youtube-transcript-api", result.message)


if __name__ == "__main__":
    unittest.main()
