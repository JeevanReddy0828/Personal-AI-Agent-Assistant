from __future__ import annotations

import ctypes
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote_plus

from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.web import WebTool

# A resolver turns a search query into ranked videos, best first: [{"id", "title"}].
# Injectable so the success path is unit-tested without the network.
VideoResolver = Callable[[str], list[dict[str, str]]]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# The search page embeds its results as JSON. Two plain anchors rather than one clever
# pattern: find each result, then read the first title that follows it.
_RESULT = re.compile(r'"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})"')
_TITLE = re.compile(r'"text":"((?:[^"\\]|\\.)*)"')


def _default_resolver(query: str) -> list[dict[str, str]]:
    """Top YouTube results for a query, with no API key.

    Playing a song meant opening a page of search results and leaving the user to click.
    YouTube serves the ranked list inside the HTML, so the first hit can be opened
    directly. Any failure returns an empty list and the caller falls back to the search
    page — losing this should cost one click, not the feature.
    """
    url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = response.read(3_000_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return []
    videos: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _RESULT.finditer(body):
        video_id = match.group(1)
        if video_id in seen:
            continue
        seen.add(video_id)
        title_match = _TITLE.search(body, match.end(), match.end() + 4000)
        title = ""
        if title_match:
            try:
                title = json.loads('"' + title_match.group(1) + '"')
            except ValueError:
                title = ""
        videos.append({"id": video_id, "title": title})
        if len(videos) >= 5:
            break
    return videos


class MusicTool:
    def __init__(
        self,
        approval_gate: ApprovalGate,
        desktop: DesktopTool,
        web: WebTool,
        resolver: VideoResolver | None = None,
    ) -> None:
        self.approval_gate = approval_gate
        self.desktop = desktop
        self.web = web
        self._resolve = resolver or _default_resolver

    def play(self, target: str) -> ToolResult:
        target = (target or "").strip()
        if not target:
            return ToolResult.failure("Nothing to play — tell me a song, artist, or URL.")
        if target.startswith(("http://", "https://")):
            return self.web.open_url(target)
        path = Path(target).expanduser()
        if path.exists():
            return self.desktop.open_app_or_file(str(path.resolve()))
        # Otherwise treat it as a search and open it on YouTube (no API key needed).
        query = self._youtube_query(target)
        if not query:
            return ToolResult.failure(
                "What should I play? Name a song, artist or playlist - for example: "
                "play music new telugu hits.",
                asked=target,
            )
        # Play the top hit rather than handing over a page of search results.
        videos = self._resolve(query)
        if videos:
            best = videos[0]
            opened = self.web.open_url(f"https://www.youtube.com/watch?v={best['id']}")
            if not opened.ok:
                return opened
            title = best.get("title") or query
            return ToolResult.success(
                f'Playing "{title}" on YouTube.',
                query=query,
                title=title,
                video_id=best["id"],
                url=opened.data.get("url"),
                source="youtube",
                alternatives=videos[1:4],
            )
        opened = self.web.open_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
        if not opened.ok:
            return opened
        return ToolResult.success(
            f'Couldn\'t reach YouTube to pick a video, so here are the results for "{query}".',
            query=query,
            url=opened.data.get("url"),
            source="youtube",
            resolved=False,
        )

    @staticmethod
    def _youtube_query(target: str) -> str:
        # Strip filler so "play some youtube music" becomes a sensible search.
        query = re.sub(r"\b(?:on\s+)?(?:youtube|yt|youtube music)\b", " ", target, flags=re.IGNORECASE)
        query = re.sub(r"\b(?:some|a|an|the|please|for me|songs?|tracks?)\b", " ", query, flags=re.IGNORECASE)
        # Trailing sentence punctuation is not part of the song title: dictated speech
        # ends in a full stop, and "new telugu hits." was searched with the dot.
        query = re.sub(r"\s+", " ", query).strip(" ,.!?;:'\"")
        # What survives the cleanup has to still name something. "play songs in the
        # youtube you just opened" was reduced to "in you just opened" and searched
        # verbatim; a back-reference is a question to ask, not a query to run.
        FILLER = {
            "in", "on", "at", "of", "to", "and", "or", "it", "that", "this", "these",
            "those", "you", "your", "just", "opened", "open", "here", "there", "now",
            "me", "my", "please", "again", "more", "one", "up", "from", "with", "same",
        }
        words = [w for w in re.findall(r"[a-z0-9']+", query.lower()) if w not in FILLER]
        return query if words else ""

    def media_key(self, key: str) -> ToolResult:
        if not sys.platform.startswith("win"):
            return ToolResult.failure("Media key support is currently implemented for Windows only.")
        codes = {"playpause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
        code = codes.get(key.lower())
        if code is None:
            return ToolResult.failure("Unknown media key.", supported=sorted(codes))
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)
        return ToolResult.success(f"Sent media key: {key}")
