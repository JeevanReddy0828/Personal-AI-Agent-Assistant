from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

# A backend fetches a feed URL and returns its bytes. Injectable so the success path is
# unit-tested offline, per the weather/websearch pattern.
FeedBackend = Callable[[str], bytes]

# Google News RSS: free, no key, no account, and it searches any topic. Its own links are
# consent/redirect pages that yield no article text, so it supplies breadth and the
# publisher feeds below supply readable text.
GOOGLE_TOP = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
GOOGLE_SEARCH = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
# Publisher feeds whose article pages actually fetch. Kept small on purpose: each one is a
# network round-trip, and the point is a readable answer rather than an exhaustive one.
PUBLISHER_FEEDS = (
    ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
)
_TAGS = re.compile(r"<[^>]+>")
_ENTITIES = {"&nbsp;": " ", "&amp;": "&", "&quot;": '"', "&#39;": "'", "&lt;": "<", "&gt;": ">"}


def _http_feed(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "laptop-agent/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def _clean(text: str) -> str:
    out = _TAGS.sub(" ", text or "")
    for entity, char in _ENTITIES.items():
        out = out.replace(entity, char)
    return re.sub(r"\s+", " ", out).strip()


def _age(published: str) -> str:
    """'2h ago' reads better than a timestamp when the whole point is recency."""
    if not published:
        return ""
    try:
        when = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    minutes = int((datetime.now(UTC) - when).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 48 * 60:
        return f"{minutes // 60}h ago"
    return f"{minutes // 1440}d ago"


def _parse(raw: bytes, fallback_source: str = "") -> list[dict[str, str]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    items: list[dict[str, str]] = []
    for node in root.findall(".//item"):
        title = _clean(node.findtext("title") or "")
        link = (node.findtext("link") or "").strip()
        if not title or not link:
            continue
        summary = _clean(node.findtext("description") or "")
        # Google News repeats the headline as the description; that is not a summary.
        if summary.startswith(title):
            summary = summary[len(title):].strip(" -–—|")
        elif summary[:40] == title[:40]:
            summary = ""
        # What survives stripping the repeated headline is usually just the outlet name.
        if len(summary) < 30:
            summary = ""
        published = (node.findtext("pubDate") or "").strip()
        items.append({
            "title": title,
            "url": link,
            "source": _clean(node.findtext("source") or "") or fallback_source,
            "published": published,
            "age": _age(published),
            "summary": summary,
        })
    return items


class NewsTool:
    """Real headlines, not a list of news homepages.

    A generic web search for "latest news" returns cnn.com and foxnews.com with their
    boilerplate taglines — the complaint that prompted this. RSS returns the actual stories
    with source and timestamp, free and without a key, and the top few are enriched with
    article text so the answer has substance rather than links.
    """

    def __init__(
        self,
        backend: FeedBackend | None = None,
        page_reader: Callable[[str], str] | None = None,
        approval_gate: ApprovalGate | None = None,
    ) -> None:
        self._fetch = backend or _http_feed
        self._read_page = page_reader
        self._gate = approval_gate

    def _feed(self, url: str, source: str = "") -> list[dict[str, str]]:
        try:
            return _parse(self._fetch(url), source)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return []       # one dead feed must not lose the whole briefing

    def headlines(self, topic: str = "", limit: int = 8, with_text: int = 3) -> ToolResult:
        subject = (topic or "").strip()
        if self._gate is not None:  # network read -> MEDIUM, like web search and weather
            self._gate.require(
                ApprovalRequest(
                    action=f"Read news headlines{' about ' + subject[:80] if subject else ''}",
                    risk=RiskLevel.MEDIUM,
                    reason="Reading the news fetches public feeds over the network.",
                )
            )
        if subject:
            items = self._feed(GOOGLE_SEARCH.format(query=urllib.parse.quote(subject)))
        else:
            # Publisher feeds first: they carry real summaries and their article pages
            # fetch, where a Google News link is a consent page that yields nothing.
            items = []
            for source, url in PUBLISHER_FEEDS:
                items.extend(self._feed(url, source))
            items.extend(self._feed(GOOGLE_TOP))

        seen: set[str] = set()
        unique: list[dict[str, str]] = []
        for item in items:
            key = item["title"].lower()[:70]
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        unique = unique[: max(1, limit)]
        if not unique:
            return ToolResult.failure(
                f"I could not reach the news feeds{' for ' + subject if subject else ''} just now. "
                "Try again in a moment."
            )

        # Depth for the first few: a headline says what happened, the article says how.
        if self._read_page is not None:
            readable = [i for i in unique if "news.google.com" not in i["url"]][: max(0, with_text)]
            for item in readable:
                try:
                    text = self._read_page(item["url"])
                except Exception:
                    text = ""
                if text and len(text) > 200:
                    item["text"] = text[:1500]

        heading = f"Top stories about {subject}" if subject else "Top stories right now"
        lines = [f"**{heading}**", ""]
        for index, item in enumerate(unique, start=1):
            meta = " · ".join(part for part in (item.get("source", ""), item.get("age", "")) if part)
            lines.append(f"{index}. [{item['title']}]({item['url']})" + (f"  \n   _{meta}_" if meta else ""))
            body = item.get("summary") or ""
            if body:
                lines.append(f"   {body[:220]}")
        return ToolResult.success(
            "\n".join(lines),
            headlines=unique,
            topic=subject,
            count=len(unique),
        )
