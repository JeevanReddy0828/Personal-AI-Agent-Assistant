from __future__ import annotations

import re
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

    def report(self, topic: str, max_sources: int = 4) -> ToolResult:
        gathered = self.gather(topic, max_sources=max_sources)
        if not gathered.ok:
            return gathered

        cleaned = str(gathered.data["topic"])
        sources = list(gathered.data.get("sources", []))
        text = str(gathered.data.get("text", ""))
        report = self._markdown_report(cleaned, text, sources)
        return ToolResult.success(
            f"Created research report for '{cleaned}' from {len(sources)} source(s).",
            topic=cleaned,
            sources=sources,
            report=report,
            word_count=len(re.findall(r"\S+", report)),
        )

    @classmethod
    def _markdown_report(cls, topic: str, text: str, sources: list[dict[str, object]]) -> str:
        sentences = cls._split_sentences(text)
        key_sentences = cls._rank_sentences(sentences, topic, limit=6)
        overview = " ".join(key_sentences[:2]) if key_sentences else "No readable source text was available."
        findings = key_sentences[2:6] or key_sentences[:4]
        source_lines = []
        for index, source in enumerate(sources, start=1):
            title = str(source.get("title") or f"Source {index}").strip()
            url = str(source.get("url") or "").strip()
            snippet = str(source.get("snippet") or "").strip()
            fetched = int(source.get("fetched_chars") or 0)
            label = f"[{title}]({url})" if url.startswith("http") else title
            detail = f" - {snippet}" if snippet else ""
            source_lines.append(f"{index}. {label}{detail} (fetched {fetched} chars)")
        if not source_lines:
            source_lines.append("No sources were available.")

        lines = [
            f"# Research Report: {topic}",
            "",
            "## Overview",
            overview,
            "",
            "## Key Findings",
        ]
        lines.extend(f"- {sentence}" for sentence in findings)
        if not findings:
            lines.append("- No key findings could be extracted from the gathered text.")
        lines.extend(
            [
                "",
                "## Caveats",
                "- This report is generated from the fetched search results only; verify important details against primary sources.",
                "",
                "## Sources",
                *source_lines,
            ]
        )
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        compact = re.sub(r"\s+", " ", text).strip()
        if not compact:
            return []
        parts = re.split(r"(?<=[.!?])\s+", compact)
        return [part.strip() for part in parts if len(part.split()) >= 5]

    @staticmethod
    def _rank_sentences(sentences: list[str], topic: str, limit: int) -> list[str]:
        if not sentences:
            return []
        terms = {term for term in re.findall(r"[a-z0-9]+", topic.lower()) if len(term) > 2}
        scored = []
        for index, sentence in enumerate(sentences):
            lowered = sentence.lower()
            topic_hits = sum(lowered.count(term) for term in terms)
            content_words = [word for word in re.findall(r"[a-z0-9]+", lowered) if len(word) > 3]
            score = topic_hits * 4 + min(len(set(content_words)), 18)
            scored.append((score, -index, sentence))
        scored.sort(reverse=True)
        selected = sorted(scored[: max(1, limit)], key=lambda item: -item[1])
        return [sentence for _score, _index, sentence in selected]


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
