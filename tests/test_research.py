from __future__ import annotations

import unittest

from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.research import ResearchTool, _TextExtractor, fetch_page_text


def approve() -> ApprovalGate:
    return ApprovalGate(lambda request: True)


def deny() -> ApprovalGate:
    return ApprovalGate(lambda request: False)


RESULTS = [
    {"title": "Alpha", "url": "https://example.com/a", "snippet": "first"},
    {"title": "Beta", "url": "https://example.com/b", "snippet": "second"},
]


class ResearchToolTests(unittest.TestCase):
    def test_gather_collects_sources_and_text(self) -> None:
        tool = ResearchTool(
            approve(),
            search_backend=lambda q, n: RESULTS,
            fetch_backend=lambda url: f"page body for {url}",
        )
        result = tool.gather("widgets")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["sources"]), 2)
        self.assertIn("page body", result.data["text"])
        self.assertEqual(result.data["sources"][0]["fetched_chars"], len("page body for https://example.com/a"))

    def test_gather_tolerates_fetch_errors(self) -> None:
        def boom(url: str) -> str:
            raise RuntimeError("timeout")

        tool = ResearchTool(approve(), search_backend=lambda q, n: RESULTS, fetch_backend=boom)
        result = tool.gather("widgets")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["sources"][0]["fetched_chars"], 0)

    def test_no_results_is_failure(self) -> None:
        tool = ResearchTool(approve(), search_backend=lambda q, n: [], fetch_backend=lambda url: "")
        self.assertFalse(tool.gather("widgets").ok)

    def test_empty_topic_fails(self) -> None:
        tool = ResearchTool(approve(), search_backend=lambda q, n: RESULTS, fetch_backend=lambda url: "x")
        self.assertFalse(tool.gather("   ").ok)

    def test_denied_approval_blocks(self) -> None:
        calls: list[str] = []
        tool = ResearchTool(deny(), search_backend=lambda q, n: calls.append(q) or RESULTS, fetch_backend=lambda url: "x")
        with self.assertRaises(Exception):
            tool.gather("secret")
        self.assertEqual(calls, [])

    def test_non_http_url_is_not_fetched(self) -> None:
        fetched: list[str] = []
        tool = ResearchTool(
            approve(),
            search_backend=lambda q, n: [{"title": "X", "url": "ftp://nope", "snippet": "s"}],
            fetch_backend=lambda url: fetched.append(url) or "data",
        )
        result = tool.gather("widgets")
        self.assertTrue(result.ok)
        self.assertEqual(fetched, [])


class TextExtractorTests(unittest.TestCase):
    def test_skips_script_and_style(self) -> None:
        parser = _TextExtractor()
        parser.feed("<html><head><style>x{}</style></head><body>Hello <script>bad()</script>World</body></html>")
        text = parser.text()
        self.assertIn("Hello", text)
        self.assertIn("World", text)
        self.assertNotIn("bad()", text)
        self.assertNotIn("x{}", text)


if __name__ == "__main__":
    unittest.main()
