from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.web import WebTool


class UrlValidationTests(unittest.TestCase):
    """Reported: "download it for me" reached the approval gate as `https://it for me` —
    the words were pasted straight into a URL."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tool(self, ask=lambda request: True) -> WebTool:
        return WebTool(ApprovalGate(ask=ask), Path(self._tmp.name))

    def test_a_phrase_is_not_a_url(self) -> None:
        for phrase in ("it for me", "a simple dataset", "something", "the file you mentioned"):
            self.assertFalse(WebTool._looks_like_url(phrase), phrase)

    def test_a_real_address_is(self) -> None:
        for url in ("https://example.com/data.csv", "example.com", "localhost:8770",
                    "https://youtu.be/abc123", "http://127.0.0.1:8770"):
            self.assertTrue(WebTool._looks_like_url(url), url)

    def test_download_refuses_a_phrase_without_asking_for_approval(self) -> None:
        asked: list[str] = []
        result = self.tool(ask=lambda request: asked.append(request.action) or True).download("it for me")
        self.assertFalse(result.ok)
        self.assertIn("not an address", result.message)
        self.assertEqual(asked, [])      # never reaches the gate, so never reaches the network

    def test_open_url_refuses_a_phrase(self) -> None:
        result = self.tool().open_url("it for me")
        self.assertFalse(result.ok)
        self.assertIn("not an address", result.message)


if __name__ == "__main__":
    unittest.main()
