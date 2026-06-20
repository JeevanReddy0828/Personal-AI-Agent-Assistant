from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult


class WebTool:
    def __init__(self, approval_gate: ApprovalGate, downloads_dir: Path) -> None:
        self.approval_gate = approval_gate
        self.downloads_dir = downloads_dir

    def open_url(self, url: str) -> ToolResult:
        normalized = self._normalize_url(url)
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
        normalized = self._normalize_url(url)
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
