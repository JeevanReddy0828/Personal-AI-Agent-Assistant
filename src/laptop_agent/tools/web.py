from __future__ import annotations

import shutil
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
        webbrowser.open(normalized)
        return ToolResult.success(f"Opened URL: {normalized}", url=normalized)

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
