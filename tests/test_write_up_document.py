"""Phrasal 'write up' must not leak 'up' into the generated document topic."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.document import DocumentTool
from test_document import SAMPLE
from test_everyday_requests import Everyday


class WriteUpDocumentTests(unittest.TestCase):
    def test_one_pager_word_doc_has_the_requested_topic(self) -> None:
        def fake_export(body: str, path: Path) -> ToolResult:
            path.write_bytes(b"offline word file")
            return ToolResult.success("Wrote Word document.")

        with tempfile.TemporaryDirectory() as raw:
            everyday = Everyday(Path(raw))
            everyday.orchestrator._document_tool_cache = DocumentTool(
                data_dir=Path(raw), writer=lambda prompt: SAMPLE,
            )
            with mock.patch("laptop_agent.tools.document._write_docx", side_effect=fake_export):
                request = "write up a one pager on remote work as a word doc"
                result, ran = everyday.say(request, stream=False)
            self.assertEqual(ran, "document one pager on remote work as a word doc")
            self.assertTrue(result.ok, result.message)
            self.assertEqual(result.data["request"], "one pager on remote work")
            self.assertEqual(result.data["format"], "docx")
            self.assertIn(".docx", result.message)


if __name__ == "__main__":
    unittest.main()
