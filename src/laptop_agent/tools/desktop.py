from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult, reserve_new_path


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
                risk=RiskLevel.HIGH,
                reason="Launching apps/files may execute local programs or reveal private content.",
            )
        )
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except OSError as exc:
            # An app that is not installed is an answer, not a crash.
            return ToolResult.failure(f"I couldn't open {target!r}: {exc.strerror or exc}. Is it installed?")
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
        if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".gif"}:
            target = target.with_name(target.name + ".png")
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Take screenshot: {target}",
                risk=RiskLevel.MEDIUM,
                reason="Screenshots can contain private information.",
            )
        )
        # Never over an existing file. MEDIUM runs without a click in the web app, and
        # `screenshot report.docx` wrote a picture over the user's document without asking.
        target = reserve_new_path(target.parent, target.stem, target.suffix)
        try:
            backend(target)
        except Exception:
            target.unlink(missing_ok=True)   # the claimed name, left empty, is not a screenshot
            raise
        return ToolResult.success(f"Saved screenshot: {target}", path=str(target))
