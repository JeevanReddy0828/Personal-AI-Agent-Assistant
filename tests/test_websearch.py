from __future__ import annotations

import unittest

from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.websearch import WebSearchError, WebSearchTool, _ResultParser, _clean_url


def approve() -> ApprovalGate:
    return ApprovalGate(lambda request: True)


def deny() -> ApprovalGate:
    return ApprovalGate(lambda request: False)


SAMPLE = [{"title": "DuckDuckGo", "url": "https://duckduckgo.com", "snippet": "search"}]


class WebSearchToolTests(unittest.TestCase):
    def test_returns_backend_results(self) -> None:
        tool = WebSearchTool(approve(), search_backend=lambda q, n: SAMPLE)
        result = tool.search("ddg")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["results"][0]["title"], "DuckDuckGo")

    def test_empty_query_fails(self) -> None:
        tool = WebSearchTool(approve(), search_backend=lambda q, n: SAMPLE)
        self.assertFalse(tool.search("   ").ok)

    def test_empty_results_is_failure(self) -> None:
        tool = WebSearchTool(approve(), search_backend=lambda q, n: [])
        result = tool.search("obscure query")
        self.assertFalse(result.ok)
        self.assertIn("No web results", result.message)

    def test_backend_error_is_clean_failure(self) -> None:
        def boom(query: str, limit: int):
            raise WebSearchError("network down")

        result = WebSearchTool(approve(), search_backend=boom).search("x")
        self.assertFalse(result.ok)
        self.assertIn("network down", result.message)

    def test_denied_approval_blocks_search(self) -> None:
        called = []
        tool = WebSearchTool(deny(), search_backend=lambda q, n: called.append(q) or SAMPLE)
        with self.assertRaises(Exception):
            tool.search("secret query")
        self.assertEqual(called, [])

    def test_limit_is_respected(self) -> None:
        backend = lambda q, n: [{"title": str(i), "url": f"u{i}", "snippet": ""} for i in range(n)]
        result = WebSearchTool(approve(), search_backend=backend).search("x", limit=3)
        self.assertEqual(len(result.data["results"]), 3)


class ParserTests(unittest.TestCase):
    def test_parses_result_anchor_and_snippet(self) -> None:
        html = (
            '<a class="result__a" href="https://example.com/page">Example Title</a>'
            '<a class="result__snippet">A short description.</a>'
        )
        parser = _ResultParser()
        parser.feed(html)
        self.assertEqual(parser.results[0]["url"], "https://example.com/page")
        self.assertIn("Example Title", parser.results[0]["title"])

    def test_clean_url_decodes_redirect(self) -> None:
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fx&rut=abc"
        self.assertEqual(_clean_url(href), "https://example.com/x")


if __name__ == "__main__":
    unittest.main()
