from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.tools.transcribe import MissingDependencyError, TranscribeTool


def raise_missing(_path: Path):
    raise MissingDependencyError("engine not installed: pip install something")


class OcrTests(unittest.TestCase):
    def test_extracts_text_with_backend(self) -> None:
        tool = TranscribeTool(ocr_backend=lambda path: "  Hello world  ")
        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "a.png"
            image.write_bytes(b"x")
            result = tool.ocr_image(str(image))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["text"], "Hello world")
            self.assertEqual(result.data["char_count"], 11)

    def test_rejects_non_image(self) -> None:
        tool = TranscribeTool(ocr_backend=lambda path: "x")
        with tempfile.TemporaryDirectory() as raw:
            doc = Path(raw) / "a.txt"
            doc.write_text("x", encoding="utf-8")
            result = tool.ocr_image(str(doc))
            self.assertFalse(result.ok)

    def test_missing_file(self) -> None:
        tool = TranscribeTool(ocr_backend=lambda path: "x")
        result = tool.ocr_image("nope.png")
        self.assertFalse(result.ok)

    def test_missing_dependency_is_clean_failure(self) -> None:
        tool = TranscribeTool(ocr_backend=raise_missing)
        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "a.jpg"
            image.write_bytes(b"x")
            result = tool.ocr_image(str(image))
            self.assertFalse(result.ok)
            self.assertIn("pip install", result.message)


class TranscribeMediaTests(unittest.TestCase):
    def test_transcribes_with_backend(self) -> None:
        tool = TranscribeTool(
            asr_backend=lambda path: {
                "text": " spoken words ",
                "language": "en",
                "engine": "fake",
                "segments": [{"start": 0, "end": 1, "text": "spoken words"}],
            }
        )
        with tempfile.TemporaryDirectory() as raw:
            clip = Path(raw) / "clip.mp4"
            clip.write_bytes(b"x")
            result = tool.transcribe_media(str(clip))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["text"], "spoken words")
            self.assertEqual(result.data["kind"], "video")
            self.assertEqual(result.data["language"], "en")
            self.assertEqual(result.data["segment_count"], 1)

    def test_rejects_non_media(self) -> None:
        tool = TranscribeTool(asr_backend=lambda path: {"text": "x"})
        with tempfile.TemporaryDirectory() as raw:
            doc = Path(raw) / "a.png"
            doc.write_bytes(b"x")
            result = tool.transcribe_media(str(doc))
            self.assertFalse(result.ok)

    def test_missing_dependency_is_clean_failure(self) -> None:
        tool = TranscribeTool(asr_backend=raise_missing)
        with tempfile.TemporaryDirectory() as raw:
            clip = Path(raw) / "a.mp3"
            clip.write_bytes(b"x")
            result = tool.transcribe_media(str(clip))
            self.assertFalse(result.ok)
            self.assertIn("pip install", result.message)


if __name__ == "__main__":
    unittest.main()
