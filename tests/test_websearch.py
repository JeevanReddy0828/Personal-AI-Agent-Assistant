from __future__ import annotations

import unittest

import json

from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.websearch import (
    WebSearchError,
    WebSearchTool,
    _ResultParser,
    _clean_url,
    brave_search_backend,
    build_search_backend,
    serper_search_backend,
)


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


class SearchApiBackendTests(unittest.TestCase):
    def test_brave_backend_parses_and_respects_limit(self) -> None:
        captured = {}

        def transport(url, headers, data):
            captured["url"] = url
            captured["token"] = headers.get("X-Subscription-Token")
            return json.dumps(
                {"web": {"results": [
                    {"title": "A", "url": "https://a.com", "description": "first"},
                    {"title": "B", "url": "https://b.com", "description": "second"},
                    {"title": "C", "url": "https://c.com", "description": "third"},
                ]}}
            )

        backend = brave_search_backend("k-123", transport=transport)
        results = backend("iran war", 2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], {"title": "A", "url": "https://a.com", "snippet": "first"})
        self.assertEqual(captured["token"], "k-123")
        self.assertIn("q=iran+war", captured["url"])

    def test_serper_backend_parses_organic_and_posts(self) -> None:
        seen = {}

        def transport(url, headers, data):
            seen["url"] = url
            seen["key"] = headers.get("X-API-KEY")
            seen["body"] = json.loads(data.decode("utf-8"))
            return json.dumps({"organic": [{"title": "T", "link": "https://t.com", "snippet": "snip"}]})

        backend = serper_search_backend("s-key", transport=transport)
        results = backend("latest news", 5)
        self.assertEqual(results, [{"title": "T", "url": "https://t.com", "snippet": "snip"}])
        self.assertEqual(seen["key"], "s-key")
        self.assertEqual(seen["body"]["q"], "latest news")

    def test_invalid_json_raises_websearcherror(self) -> None:
        backend = brave_search_backend("k", transport=lambda u, h, d: "<html>not json</html>")
        with self.assertRaises(WebSearchError):
            backend("x", 3)

    def test_build_uses_primary_when_it_returns_results(self) -> None:
        primary = lambda q, n: [{"title": "P", "url": "https://p", "snippet": ""}]
        fallback = lambda q, n: [{"title": "F", "url": "https://f", "snippet": ""}]
        backend = build_search_backend("brave", "key", primary=primary, fallback=fallback)
        self.assertEqual(backend("q", 5)[0]["title"], "P")

    def test_build_falls_back_when_primary_empty_or_errors(self) -> None:
        fallback = lambda q, n: [{"title": "F", "url": "https://f", "snippet": ""}]

        empty = build_search_backend("brave", "key", primary=lambda q, n: [], fallback=fallback)
        self.assertEqual(empty("q", 5)[0]["title"], "F")

        def boom(q, n):
            raise WebSearchError("api 429")

        erroring = build_search_backend("brave", "key", primary=boom, fallback=fallback)
        self.assertEqual(erroring("q", 5)[0]["title"], "F")

    def test_build_without_key_returns_fallback(self) -> None:
        fallback = lambda q, n: [{"title": "DDG", "url": "https://d", "snippet": ""}]
        backend = build_search_backend("", None, fallback=fallback)
        self.assertEqual(backend("q", 5)[0]["title"], "DDG")


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
