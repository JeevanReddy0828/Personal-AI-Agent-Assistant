"""Voice-like ways to ask for slides must produce a deck, not chat or markdown."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.document import DocumentTool, split_format
from test_document import DECK
from test_everyday_requests import Everyday


class SpokenDeckTests(unittest.TestCase):
    def test_throw_together_or_whip_up_slides(self) -> None:
        def fake_export(body: str, path: Path) -> ToolResult:
            path.write_bytes(b"offline deck")
            return ToolResult.success("Wrote deck.", slides=3)

        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            everyday.orchestrator._document_tool_cache = DocumentTool(
                data_dir=Path(raw), writer=lambda prompt: DECK,
            )
            with mock.patch("laptop_agent.tools.document._write_pptx", side_effect=fake_export):
                for said in ("throw together slides about the water cycle",
                             "um throw together a slide deck about the water cycle",
                             "whip up slides on the water cycle"):
                    with self.subTest(said=said):
                        result, ran = everyday.say(said, stream=False)
                        self.assertTrue(ran and ran.startswith("document "), (ran, result.message))
                        self.assertTrue(result.ok, result.message)
                        self.assertEqual(result.data["format"], "pptx")
                        self.assertIn(".pptx", result.message)

    def test_format_parser_reads_the_spoken_verbs(self) -> None:
        self.assertEqual(split_format("throw together slides about the water cycle"),
                         ("the water cycle", "pptx"))
        self.assertEqual(split_format("whip up a slide deck on tides"), ("tides", "pptx"))
        self.assertEqual(split_format("throw together a salad"), ("throw together a salad", "pdf"))


if __name__ == "__main__":
    unittest.main()
