"""A polite slide-deck request must produce the requested file type."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.document import DocumentTool, split_format
from test_document import DECK
from test_everyday_requests import Everyday


class DeckCourtesyTests(unittest.TestCase):
    def test_could_you_make_a_slide_deck_keeps_powerpoint_format(self) -> None:
        def fake_export(body: str, path: Path) -> ToolResult:
            path.write_bytes(b"offline deck")
            return ToolResult.success("Wrote deck.", slides=3)

        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            everyday.orchestrator._document_tool_cache = DocumentTool(
                data_dir=Path(raw), writer=lambda prompt: DECK,
            )
            with mock.patch("laptop_agent.tools.document._write_pptx", side_effect=fake_export):
                for modal in ("can", "could", "would", "will"):
                    request = f"{modal} you make a slide deck about Mars"
                    with self.subTest(request=request):
                        self.assertEqual(split_format(request), ("Mars", "pptx"))
                        result, ran = everyday.say(request, stream=False)
                        self.assertEqual(ran, f"document {request}")
                        self.assertTrue(result.ok, result.message)
                        self.assertEqual(result.data["format"], "pptx")
                        self.assertIn(".pptx", result.message)

    def test_courtesy_does_not_turn_document_request_into_a_deck(self) -> None:
        self.assertEqual(
            split_format("could you write a report on presentation skills as a pdf"),
            ("could you write a report on presentation skills", "pdf"),
        )


if __name__ == "__main__":
    unittest.main()
