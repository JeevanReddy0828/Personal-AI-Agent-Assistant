from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.safety import ApprovalDenied, ApprovalGate
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

    def test_a_sentence_ending_is_not_a_host(self) -> None:
        # "me." parses as a netloc with a dot in it; a host needs a real TLD.
        for phrase in ("me.", "now.", "this.", "one.two."):
            self.assertFalse(WebTool._looks_like_url(phrase), phrase)


class UrlInsideAPhraseTests(unittest.TestCase):
    """Reported after the guard above shipped: "download it for me -
    https://gdoc.io/..." was refused with the link sitting right there, and so was a
    link pasted on its own line. Refusing every phrase was too blunt — only a request
    with no address at all should be refused."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_a_trailing_link_is_used(self) -> None:
        self.assertEqual(
            WebTool._extract_url("it for me - https://gdoc.io/resume-templates/classic/"),
            "https://gdoc.io/resume-templates/classic/",
        )

    def test_a_link_on_its_own_line_is_used(self) -> None:
        self.assertEqual(
            WebTool._extract_url("it for me\nhttps://www.kaggle.com/datasets/kaggle/pokemon"),
            "https://www.kaggle.com/datasets/kaggle/pokemon",
        )

    def test_surrounding_punctuation_is_not_part_of_the_link(self) -> None:
        self.assertEqual(
            WebTool._extract_url("grab (https://example.com/a.pdf) now"),
            "https://example.com/a.pdf",
        )
        self.assertEqual(
            WebTool._extract_url("see https://example.com/report.html."),
            "https://example.com/report.html",
        )

    def test_a_phrase_with_no_link_is_still_refused(self) -> None:
        for phrase in ("it for me", "download it for me.", "please get me this one", "that file"):
            self.assertIsNone(WebTool._extract_url(phrase), phrase)

    def test_open_url_opens_the_link_inside_the_phrase(self) -> None:
        opened: list[str] = []
        tool = WebTool(ApprovalGate(ask=lambda request: True), Path(self._tmp.name))
        tool._launch_browser = lambda url: opened.append(url) or True  # type: ignore[method-assign]
        result = tool.open_url("open this one for me https://example.com/page")
        self.assertTrue(result.ok)
        self.assertEqual(opened, ["https://example.com/page"])

    def test_the_gate_is_asked_about_the_extracted_link_only(self) -> None:
        asked: list[str] = []
        tool = WebTool(
            ApprovalGate(ask=lambda request: asked.append(request.action) or False),
            Path(self._tmp.name),
        )
        with self.assertRaises(ApprovalDenied):
            tool.download("it for me - https://example.com/data.csv")
        self.assertEqual(asked, ["Download file: https://example.com/data.csv"])


if __name__ == "__main__":
    unittest.main()
