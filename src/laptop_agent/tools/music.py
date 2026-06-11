from __future__ import annotations

import ctypes
import sys
from pathlib import Path

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
        if target.startswith(("http://", "https://")):
            return self.web.open_url(target)
        path = Path(target).expanduser()
        if path.exists():
            return self.desktop.open_app_or_file(str(path.resolve()))
        return ToolResult.failure("Music target was not a URL or existing local file/folder.", target=target)

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
