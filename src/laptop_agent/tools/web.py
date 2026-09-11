from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

# localhost, an IPv4 address, or a name ending in a real TLD. Without the TLD rule a
# sentence's last word ("me.") parses as a host.
_HOSTLIKE = re.compile(
    r"localhost|\d{1,3}(?:\.\d{1,3}){3}|[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.[A-Za-z]{2,}",
    re.IGNORECASE,
)


class WebTool:
    def __init__(self, approval_gate: ApprovalGate, downloads_dir: Path) -> None:
        self.approval_gate = approval_gate
        self.downloads_dir = downloads_dir

    def open_url(self, url: str) -> ToolResult:
        found = self._extract_url(url)
        if found is None:
            return ToolResult.failure(
                f"That is not an address I can open: {url!r}. Give me a link, "
                "for example: open url https://example.com"
            )
        normalized = self._normalize_url(found)
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Open URL: {normalized}",
                risk=RiskLevel.MEDIUM,
                reason="Opening an external URL can reveal local browser/session state to a website.",
            )
        )
        if not self._launch_browser(normalized):
            return ToolResult.failure(
                f"Couldn't open a browser for: {normalized}. You can paste it manually.", url=normalized
            )
        return ToolResult.success(f"Opened URL: {normalized}", url=normalized)

    @staticmethod
    def _launch_browser(url: str) -> bool:
        """Open a URL in the system browser, reliably across environments.

        webbrowser.open is flaky when launched from a packaged/embedded app, so on
        Windows go through the shell handler (os.startfile) first, and on macOS/Linux
        use the platform opener, falling back to webbrowser."""
        try:
            if sys.platform.startswith("win"):
                os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 - shell default handler
                return True
            if sys.platform == "darwin":
                subprocess.Popen(["open", url])
                return True
            if webbrowser.open(url):
                return True
            subprocess.Popen(["xdg-open", url])
            return True
        except Exception:
            try:
                return webbrowser.open(url)
            except Exception:
                return False

    def download(self, url: str, filename: str | None = None) -> ToolResult:
        found = self._extract_url(url)
        if found is None:
            return ToolResult.failure(
                f"That is not an address I can download: {url!r}. Give me a link, "
                "for example: download https://example.com/data.csv"
            )
        normalized = self._normalize_url(found)
        parsed = urlparse(normalized)
        guessed_name = filename or Path(parsed.path).name or "download.bin"
        target = (self.downloads_dir / guessed_name).resolve()
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Download file: {normalized}",
                risk=RiskLevel.HIGH,
                reason="Downloaded files can contain sensitive or unsafe content.",
                preview=f"Destination: {target}",
            )
        )
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(normalized) as response, target.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        return ToolResult.success(f"Downloaded to: {target}", path=str(target), url=normalized)

    @staticmethod
    def _normalize_url(url: str) -> str:
        if "://" not in url:
            return "https://" + url
        return url

    @staticmethod
    def _looks_like_url(raw: str) -> bool:
        """Whether this single token is an address.

        "download it for me" reached the approval gate as `https://it for me`: the words
        were pasted straight into a URL. A host has no spaces, and it is localhost, an
        IPv4 address, or a name ending in a real top-level domain — "me." is a sentence
        ending, not a host.
        """
        candidate = (raw or "").strip()
        if not candidate or len(candidate.split()) > 1:
            return False
        host = urlparse(WebTool._normalize_url(candidate)).netloc.split("@")[-1]
        host = host.rsplit(":", 1)[0] if ":" in host else host
        return bool(_HOSTLIKE.fullmatch(host))

    @staticmethod
    def _extract_url(raw: str) -> str | None:
        """The first address inside a phrase, or None when there isn't one.

        Refusing anything containing a space broke the commoner phrasing: "download it
        for me - https://gdoc.io/..." was rejected with the link sitting right there, and
        so was a link pasted on its own line. Only a request with no address at all should
        be refused.
        """
        for token in (raw or "").split():
            candidate = token.strip("<>()[]{}\"'`,;").rstrip(".!?")
            if WebTool._looks_like_url(candidate):
                return candidate
        return None
