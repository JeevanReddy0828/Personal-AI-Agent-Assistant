from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from html.parser import HTMLParser

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.websearch import _USER_AGENT, SearchBackend, _duckduckgo_backend, _normalize

# A fetch backend turns a URL into readable page text.
FetchBackend = Callable[[str], str]


class ResearchTool:
    """Gather web material on a topic: search, then fetch and clean top pages.

    This is the first step of the research workflow. The orchestrator summarizes
    and indexes what it returns. The whole gather is approval-gated once (network
    egress: it searches and fetches several pages), and both the search and fetch
    calls sit behind injectable backends so the success path is testable offline.
    """

    def __init__(
        self,
        approval_gate: ApprovalGate,
        search_backend: SearchBackend | None = None,
        fetch_backend: FetchBackend | None = None,
    ) -> None:
        self.approval_gate = approval_gate
        self._search = search_backend or _duckduckgo_backend
        self._fetch = fetch_backend or fetch_page_text

    def gather(self, topic: str, max_sources: int = 3) -> ToolResult:
        cleaned = topic.strip()
        if not cleaned:
            return ToolResult.failure("Use: research <topic>")
        sources_wanted = max(1, min(max_sources, 5))
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Research the web: {cleaned}",
                risk=RiskLevel.MEDIUM,
                reason="Research searches the web and downloads several pages to read over the network.",
                preview=f"Topic: {cleaned}\nPages to fetch: up to {sources_wanted}",
            )
        )
        try:
            results = self._search(cleaned, sources_wanted)
        except Exception as exc:
            return ToolResult.failure(f"Research search failed: {exc}", topic=cleaned)
        if not results:
            return ToolResult.failure(
                "No web results to research (the search endpoint may be rate-limited).",
                topic=cleaned,
            )

        sources: list[dict[str, object]] = []
        chunks: list[str] = []
        for item in results[:sources_wanted]:
            url = str(item.get("url", ""))
            title = str(item.get("title", ""))
            snippet = str(item.get("snippet", ""))
            page_text = ""
            if url.startswith("http"):
                try:
                    page_text = self._fetch(url)
                except Exception:
                    page_text = ""
            sources.append({"title": title, "url": url, "snippet": snippet, "fetched_chars": len(page_text)})
            chunks.append(f"{title}. {snippet} {page_text}".strip())

        combined = "\n\n".join(chunk for chunk in chunks if chunk).strip()
        return ToolResult.success(
            f"Gathered {len(sources)} source(s) for '{cleaned}'.",
            topic=cleaned,
            sources=sources,
            text=combined,
        )


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "head", "template", "svg", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return " ".join(self.parts)


def fetch_page_text(url: str, max_chars: int = 8000, max_bytes: int = 2_000_000) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = response.headers.get_content_type()
            raw = response.read(max_bytes)
    except (urllib.error.URLError, TimeoutError):
        return ""
    body = raw.decode("utf-8", errors="replace")
    if "html" in content_type or "<html" in body[:2000].lower():
        parser = _TextExtractor()
        parser.feed(body)
        body = parser.text()
    return _normalize(body)[:max_chars]
