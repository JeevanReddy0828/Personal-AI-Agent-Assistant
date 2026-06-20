from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import laptop_agent.tools.transcribe as transcribe_module
from laptop_agent.tools.transcribe import MissingDependencyError, TranscribeTool, warm_whisper


def raise_missing(_path: Path):
    raise MissingDependencyError("engine not installed: pip install something")


class WhisperCacheTests(unittest.TestCase):
    def test_model_is_cached_by_name(self) -> None:
        sentinel = object()
        transcribe_module._WHISPER_MODELS["unit-test-model"] = sentinel
        try:
            # Returns the cached model without importing whisper.
            self.assertIs(transcribe_module._load_whisper_model("unit-test-model"), sentinel)
        finally:
            transcribe_module._WHISPER_MODELS.pop("unit-test-model", None)

    def test_warm_whisper_is_safe_without_engine(self) -> None:
        # With Whisper unavailable, warming is a graceful no-op (False), never an
        # exception. Force the import to fail so the test is deterministic and never
        # downloads/loads a model.
        import sys

        saved = sys.modules.get("whisper", "absent")
        sys.modules["whisper"] = None  # makes `import whisper` raise ImportError
        try:
            self.assertFalse(warm_whisper())
        finally:
            if saved == "absent":
                sys.modules.pop("whisper", None)
            else:
                sys.modules["whisper"] = saved


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
