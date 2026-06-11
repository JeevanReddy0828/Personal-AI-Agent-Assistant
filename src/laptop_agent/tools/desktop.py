from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult


# A screenshot backend captures the current screen and writes it to the given path.
ScreenshotBackend = Callable[[Path], None]


class DesktopTool:
    def __init__(self, approval_gate: ApprovalGate, screenshot_backend: ScreenshotBackend | None = None) -> None:
        self.approval_gate = approval_gate
        self._screenshot_backend = screenshot_backend

    def open_app_or_file(self, target: str) -> ToolResult:
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Open app or file: {target}",
                risk=RiskLevel.MEDIUM,
                reason="Launching apps/files may execute local programs or reveal private content.",
            )
        )
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return ToolResult.success(f"Opened: {target}")

    def screenshot(self, output_path: str) -> ToolResult:
        backend = self._screenshot_backend
        if backend is None:
            try:
                import pyautogui  # type: ignore
            except ImportError:
                return ToolResult.failure("Screenshot support requires: pip install pyautogui pillow")

            def backend(path: Path) -> None:
                pyautogui.screenshot().save(str(path))

        target = Path(output_path).expanduser().resolve()
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Take screenshot: {target}",
                risk=RiskLevel.MEDIUM,
                reason="Screenshots can contain private information.",
            )
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        backend(target)
        return ToolResult.success(f"Saved screenshot: {target}", path=str(target))
