from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from html.parser import HTMLParser

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult


class WebSearchError(RuntimeError):
    """Raised by a backend when a web search cannot be completed."""


# A search backend turns (query, limit) into a list of {title, url, snippet} dicts.
SearchBackend = Callable[[str, int], list[dict[str, str]]]

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class WebSearchTool:
    """Approval-gated web search built only on the standard library.

    Searching sends the query to an external engine, so it goes through the
    approval gate (network egress + the query leaves the machine). The HTTP call
    sits behind an injectable backend so the success path is testable offline;
    the default backend queries DuckDuckGo's HTML endpoint and parses results
    with html.parser.
    """

    def __init__(self, approval_gate: ApprovalGate, search_backend: SearchBackend | None = None) -> None:
        self.approval_gate = approval_gate
        self._backend = search_backend or _duckduckgo_backend

    def search(self, query: str, limit: int = 5) -> ToolResult:
        cleaned = query.strip()
        if not cleaned:
            return ToolResult.failure("Use: web search <query>")
        safe_limit = max(1, min(limit, 15))
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Web search: {cleaned}",
                risk=RiskLevel.MEDIUM,
                reason="Web search sends your query to an external search engine over the network.",
                preview=f"Query: {cleaned}\nResults: up to {safe_limit}",
            )
        )
        try:
            results = self._backend(cleaned, safe_limit)
        except WebSearchError as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # pragma: no cover - defensive for live network calls.
            return ToolResult.failure(f"Web search failed: {exc}")

        if not results:
            return ToolResult.failure(
                "No web results found (the search endpoint may be rate-limited or unreachable).",
                query=cleaned,
            )
        return ToolResult.success(f"Found {len(results)} web result(s).", query=cleaned, results=results[:safe_limit])


class _ResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: (value or "") for key, value in attrs}
        css = attributes.get("class", "")
        if tag == "a" and ("result__a" in css or "result-link" in css):
            self._current = {"title": "", "url": _clean_url(attributes.get("href", "")), "snippet": ""}
            self._in_title = True
        elif "result__snippet" in css or "result-snippet" in css:
            self._in_snippet = True

    def handle_data(self, data: str) -> None:
        if self._in_title and self._current is not None:
            self._current["title"] += data
        elif self._in_snippet and self.results:
            self.results[-1]["snippet"] += data

    def handle_endtag(self, tag: str) -> None:
        if self._in_title and tag == "a":
            self._in_title = False
            if self._current and self._current.get("url"):
                self.results.append(self._current)
            self._current = None
        elif self._in_snippet and tag in {"td", "div"}:
            self._in_snippet = False


def _clean_url(href: str) -> str:
    if not href:
        return ""
    if "uddg=" in href:
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg")
        if target:
            return target[0]
    if href.startswith("//"):
        return "https:" + href
    return href


def _normalize(text: str) -> str:
    return " ".join(text.split())


# --- Real search-API backends (optional, key-gated) -------------------------------
# These use proper JSON APIs that don't rate-limit scripted clients like the free DDG
# scraper does. Each takes an injectable HTTP transport so the parsing is tested offline.

# An HTTP transport turns (url, headers, data|None) into a decoded response body string.
HttpTransport = Callable[[str, dict[str, str], bytes | None], str]


def _http_request(url: str, headers: dict[str, str], data: bytes | None = None) -> str:
    method = "POST" if data is not None else "GET"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WebSearchError(f"Search API request failed: {exc}") from exc


def brave_search_backend(api_key: str, transport: HttpTransport | None = None) -> SearchBackend:
    """Brave Search API backend. Docs: https://api.search.brave.com/ ."""
    send = transport or _http_request

    def backend(query: str, limit: int) -> list[dict[str, str]]:
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
            {"q": query, "count": limit}
        )
        headers = {"Accept": "application/json", "X-Subscription-Token": api_key, "User-Agent": _USER_AGENT}
        body = send(url, headers, None)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise WebSearchError(f"Brave returned invalid JSON: {exc}") from exc
        items = (payload.get("web") or {}).get("results") or []
        results = []
        for item in items:
            title = _normalize(str(item.get("title", "")))
            url_value = str(item.get("url", "")).strip()
            snippet = _normalize(str(item.get("description", "")))
            if title and url_value:
                results.append({"title": title, "url": url_value, "snippet": snippet})
            if len(results) >= limit:
                break
        return results

    return backend


def serper_search_backend(api_key: str, transport: HttpTransport | None = None) -> SearchBackend:
    """Serper.dev (Google results) backend. Docs: https://serper.dev/ ."""
    send = transport or _http_request

    def backend(query: str, limit: int) -> list[dict[str, str]]:
        data = json.dumps({"q": query, "num": limit}).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-API-KEY": api_key, "User-Agent": _USER_AGENT}
        body = send("https://google.serper.dev/search", headers, data)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise WebSearchError(f"Serper returned invalid JSON: {exc}") from exc
        results = []
        for item in payload.get("organic") or []:
            title = _normalize(str(item.get("title", "")))
            url_value = str(item.get("link", "")).strip()
            snippet = _normalize(str(item.get("snippet", "")))
            if title and url_value:
                results.append({"title": title, "url": url_value, "snippet": snippet})
            if len(results) >= limit:
                break
        return results

    return backend


def build_search_backend(
    provider: str | None,
    api_key: str | None,
    *,
    primary: SearchBackend | None = None,
    fallback: SearchBackend | None = None,
) -> SearchBackend:
    """Pick a search backend from config. With a configured provider + key, use that real
    API and fall back to DuckDuckGo when it errors or returns nothing; otherwise use DDG
    directly. ``primary``/``fallback`` are injectable for testing."""
    fallback = fallback or _duckduckgo_backend
    provider = (provider or "").strip().lower()
    if primary is None and api_key and provider in {"brave", "serper"}:
        primary = brave_search_backend(api_key) if provider == "brave" else serper_search_backend(api_key)
    if primary is None:
        return fallback

    def resilient(query: str, limit: int) -> list[dict[str, str]]:
        try:
            results = primary(query, limit)
        except WebSearchError:
            results = []
        if results:
            return results
        return fallback(query, limit)  # API down/empty → free scraper as a safety net

    return resilient


def _duckduckgo_backend(query: str, limit: int) -> list[dict[str, str]]:
    # The lite endpoint accepts a POST form and is far more tolerant of
    # automated clients than the full HTML endpoint, which serves an anti-bot
    # challenge page to scripted requests.
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    request = urllib.request.Request(
        "https://lite.duckduckgo.com/lite/",
        data=data,
        headers={"User-Agent": _USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WebSearchError(f"Web search request failed: {exc}") from exc

    parser = _ResultParser()
    parser.feed(html)
    results: list[dict[str, str]] = []
    for item in parser.results:
        title = _normalize(item.get("title", ""))
        snippet = _normalize(item.get("snippet", ""))
        url_value = item.get("url", "")
        if title and url_value:
            results.append({"title": title, "url": url_value, "snippet": snippet})
        if len(results) >= limit:
            break
    return results
