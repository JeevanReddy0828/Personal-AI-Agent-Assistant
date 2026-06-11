from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult


class DesktopTool:
    def __init__(self, approval_gate: ApprovalGate) -> None:
        self.approval_gate = approval_gate

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
        try:
            import pyautogui  # type: ignore
        except ImportError:
            return ToolResult.failure("Screenshot support requires: pip install pyautogui pillow")
        target = Path(output_path).expanduser().resolve()
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Take screenshot: {target}",
                risk=RiskLevel.MEDIUM,
                reason="Screenshots can contain private information.",
            )
        )
        image = pyautogui.screenshot()
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        return ToolResult.success(f"Saved screenshot: {target}", path=str(target))
