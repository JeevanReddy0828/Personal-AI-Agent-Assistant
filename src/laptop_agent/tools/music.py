from __future__ import annotations

import ctypes
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.web import WebTool


class MusicTool:
    def __init__(self, approval_gate: ApprovalGate, desktop: DesktopTool, web: WebTool) -> None:
        self.approval_gate = approval_gate
        self.desktop = desktop
        self.web = web

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
        opened = self.web.open_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
        if not opened.ok:
            return opened
        return ToolResult.success(
            f"Searching YouTube for “{query}”.", query=query, url=opened.data.get("url"), source="youtube"
        )

    @staticmethod
    def _youtube_query(target: str) -> str:
        # Strip filler so "play some youtube music" becomes a sensible search.
        query = re.sub(r"\b(?:on\s+)?(?:youtube|yt|youtube music)\b", " ", target, flags=re.IGNORECASE)
        query = re.sub(r"\b(?:some|a|an|the|please|for me|songs?|tracks?)\b", " ", query, flags=re.IGNORECASE)
        query = re.sub(r"\s+", " ", query).strip(" ,'\"")
        return query or "music"

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
