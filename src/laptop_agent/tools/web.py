from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

from laptop_agent.failures import record_failure

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult, reserve_new_path

# localhost, an IPv4 address, or a name ending in a real TLD. Without the TLD rule a
# sentence's last word ("me.") parses as a host.
_HOSTLIKE = re.compile(
    r"localhost|\d{1,3}(?:\.\d{1,3}){3}|[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.[A-Za-z]{2,}",
    re.IGNORECASE,
)
# The only schemes a link may have. `open url` runs without a card in the web app and hands the
# address to the Windows shell, which starts whatever is registered for it: file://server/x.exe
# ran a program from a share, and ms-msdt:, search-ms: and the like each start their own handler.
WEB_SCHEMES = frozenset({"http", "https"})
DOWNLOAD_TIMEOUT = 30
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
Opener = Callable[[str], BinaryIO]


def is_web_url(url: str) -> bool:
    return urlparse(url).scheme.lower() in WEB_SCHEMES


class _WebRedirects(urllib.request.HTTPRedirectHandler):
    """urllib follows an http page's redirect to ftp:; a download stays on the web."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not is_web_url(newurl):
            raise urllib.error.HTTPError(newurl, code, "Redirected away from http(s).", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_web(url: str) -> BinaryIO:
    return urllib.request.build_opener(_WebRedirects).open(url, timeout=DOWNLOAD_TIMEOUT)


class WebTool:
    def __init__(self, approval_gate: ApprovalGate, downloads_dir: Path, opener: Opener = _open_web) -> None:
        self.approval_gate = approval_gate
        self.downloads_dir = downloads_dir
        self._open = opener

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
        # A bare name only: a name with a folder in it would land outside the downloads folder.
        guessed_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(filename or parsed.path).name).strip(" .")[:180]
        guessed_name = guessed_name or "download.bin"
        target = (self.downloads_dir / guessed_name).resolve()
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Download file: {normalized}",
                risk=RiskLevel.HIGH,
                reason="Downloaded files can contain sensitive or unsafe content.",
                preview=f"Destination: {target}",
            )
        )
        # Never over an existing file: the name is claimed new, with -2, -3 when it is taken.
        target = reserve_new_path(self.downloads_dir, Path(guessed_name).stem, Path(guessed_name).suffix)
        try:
            with self._open(normalized) as response, target.open("wb") as handle:
                written = 0
                while chunk := response.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        raise OSError(f"it is over {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB")
                    handle.write(chunk)
        except (OSError, ValueError) as exc:   # URLError and a timeout are OSErrors
            record_failure("web/download", exc, url=normalized)
            target.unlink(missing_ok=True)     # the claimed name, half written, is not the file
            return ToolResult.failure(f"Couldn't download {normalized}: {exc}", url=normalized)
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
        parsed = urlparse(WebTool._normalize_url(candidate))
        if parsed.scheme.lower() not in WEB_SCHEMES:
            return False
        host = parsed.netloc.split("@")[-1]
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
