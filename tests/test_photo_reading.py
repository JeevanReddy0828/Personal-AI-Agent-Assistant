"""A picture the vision model cannot look at says why, and shows the text in it.

Reported from the phone: one photo answered "Read 239 character(s) of text from the image."
and showed none of it; the next said "no vision model is configured and OCR is unavailable"
while a vision model was configured - NVIDIA had retired it, and it answered HTTP 410."""

from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from pathlib import Path

from laptop_agent.agents.orchestrator import AgentOrchestrator
from laptop_agent.model_status import BROKEN
from laptop_agent.planner import Planner
from laptop_agent.planner.openai_compatible import OpenAICompatiblePlannerProvider
from laptop_agent.tools.transcribe import MissingDependencyError, TranscribeTool

import test_orchestrator

RECEIPT = "TOTAL 12.40\nTHANK YOU"


def retired(payload: dict) -> str:
    raise urllib.error.HTTPError("u", 410, "Gone", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]


def no_tesseract(path: Path) -> str:
    raise MissingDependencyError("Tesseract OCR binary not found.")


class PhotoReadingTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.photo = self.root / "image.jpg"
        self.photo.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)

    def orchestrator(self, ocr=lambda path: RECEIPT, vision=retired):
        o = self.build(self.root / "data")
        o.context = replace(o.context, transcribe=TranscribeTool(ocr_backend=ocr))
        o.vision_planner = (
            Planner(OpenAICompatiblePlannerProvider("k", "old/vision-model", transport=vision)) if vision else None
        )
        return o

    def look(self, o, command: str | None = None):
        return asyncio.run(o.handle(command or f"describe image {self.photo}"))

    def test_the_text_is_shown_with_the_reason_the_picture_was_not_seen(self) -> None:
        result = self.look(self.orchestrator())
        self.assertTrue(result.ok, result.message)
        self.assertIn("TOTAL 12.40", result.message)
        self.assertIn("retired", result.message)
        self.assertIn("old/vision-model", result.message)
        self.assertNotIn("character(s) of text from", result.message)
        self.assertEqual(result.data["ocr_text"], RECEIPT)

    def test_a_routed_reply_keeps_the_reason(self) -> None:
        result = self.look(self.orchestrator())
        self.assertEqual(AgentOrchestrator._humanize(result), result.message)

    def test_a_photo_without_text_names_the_retired_model_not_a_missing_one(self) -> None:
        result = self.look(self.orchestrator(ocr=lambda path: ""))
        self.assertFalse(result.ok)
        self.assertIn("retired", result.message)
        self.assertIn("no readable text", result.message)
        self.assertNotIn("no vision model is configured", result.message)

    def test_both_failing_gives_both_reasons(self) -> None:
        result = self.look(self.orchestrator(ocr=no_tesseract))
        self.assertFalse(result.ok)
        self.assertIn("retired", result.message)
        self.assertIn("Tesseract OCR binary not found", result.message)

    def test_without_a_vision_model_it_says_so(self) -> None:
        result = self.look(self.orchestrator(vision=None))
        self.assertTrue(result.ok, result.message)
        self.assertIn("no vision model is configured", result.message)
        self.assertIn("TOTAL 12.40", result.message)

    def test_a_busy_vision_model_is_not_called_broken(self) -> None:
        def busy(payload: dict) -> str:
            raise urllib.error.HTTPError("u", 503, "Busy", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]

        result = self.look(self.orchestrator(vision=busy))
        self.assertIn("unavailable right now", result.message)
        self.assertNotIn("OPENAI_VISION_MODEL", result.message)

    def test_a_provider_without_failure_reports_still_works(self) -> None:
        class OldVision:
            def describe_image(self, path, prompt, model=None):
                return None

        o = self.orchestrator()
        o.vision_planner = Planner(OldVision())
        result = self.look(o)
        self.assertTrue(result.ok, result.message)
        self.assertIn("gave no answer", result.message)

    def test_the_screen_shows_its_text_too(self) -> None:
        result = self.look(self.orchestrator(), "read screen")
        self.assertTrue(result.ok, result.message)
        self.assertIn("TOTAL 12.40", result.message)
        self.assertIn("retired", result.message)

    def test_the_webcam_shows_its_text_too(self) -> None:
        result = self.look(self.orchestrator(), "look at webcam")
        self.assertTrue(result.ok, result.message)
        self.assertIn("TOTAL 12.40", result.message)
        self.assertIn("retired", result.message)


class DescribeImageFailureTests(unittest.TestCase):
    def test_a_retired_model_is_reported_as_broken(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            photo = Path(raw) / "p.png"
            photo.write_bytes(b"\x89PNG")
            heard: list[tuple[str, str]] = []
            provider = OpenAICompatiblePlannerProvider("k", "old/vision-model", transport=retired)
            reply = provider.describe_image(str(photo), "what is this", on_failure=lambda *why: heard.append(why))
        self.assertIsNone(reply)
        self.assertEqual(heard[0][0], BROKEN)
        self.assertIn("retired", heard[0][1])


if __name__ == "__main__":
    unittest.main()
