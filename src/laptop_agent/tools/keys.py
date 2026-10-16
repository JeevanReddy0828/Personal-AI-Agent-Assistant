"""`keys <action> [in <app>]` and `type <text> [in <app>]` - a shortcut or text, sent to an app.

"new tab", "scroll down", "go back" and "type hello in notepad" had no tool. When the user speaks to
J.A.R.V.I.S its own window has the focus, so a key pressed blindly lands there: each press first
brings the target forward - the app named, else the window the user was in (the first other window
in Windows' z-order) - and says which it used. Only the named shortcuts in `ACTIONS` exist, never
an arbitrary chord from the words. Shortcuts run (MEDIUM); one that can lose work, and typing,
which reaches whatever that app does with text, ask first (HIGH).
"""
from __future__ import annotations

import ctypes
import re
import sys
import time
from typing import Protocol

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.windows import DesktopWindow

VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
      "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
      "right": 0x27, "down": 0x28, "f4": 0x73, "f5": 0x75, "plus": 0xBB, "minus": 0xBD,
      **{letter: ord(letter.upper()) for letter in "acdstvwyz"}, "0": 0x30}
# name -> (keys, what it does, risk)
ACTIONS: dict[str, tuple[tuple[str, ...], str, RiskLevel]] = {
    "new tab": (("ctrl", "t"), "open a new tab", RiskLevel.MEDIUM),
    "close tab": (("ctrl", "w"), "close the tab", RiskLevel.MEDIUM),
    "reopen tab": (("ctrl", "shift", "t"), "reopen the last closed tab", RiskLevel.MEDIUM),
    "next tab": (("ctrl", "tab"), "go to the next tab", RiskLevel.MEDIUM),
    "previous tab": (("ctrl", "shift", "tab"), "go to the previous tab", RiskLevel.MEDIUM),
    "back": (("alt", "left"), "go back", RiskLevel.MEDIUM),
    "forward": (("alt", "right"), "go forward", RiskLevel.MEDIUM),
    "refresh": (("f5",), "refresh the page", RiskLevel.MEDIUM),
    "scroll down": (("pagedown",), "scroll down", RiskLevel.MEDIUM),
    "scroll up": (("pageup",), "scroll up", RiskLevel.MEDIUM),
    "top": (("ctrl", "home"), "go to the top", RiskLevel.MEDIUM),
    "bottom": (("ctrl", "end"), "go to the bottom", RiskLevel.MEDIUM),
    "zoom in": (("ctrl", "plus"), "zoom in", RiskLevel.MEDIUM),
    "zoom out": (("ctrl", "minus"), "zoom out", RiskLevel.MEDIUM),
    "reset zoom": (("ctrl", "0"), "reset the zoom", RiskLevel.MEDIUM),
    "select all": (("ctrl", "a"), "select everything", RiskLevel.MEDIUM),
    "copy": (("ctrl", "c"), "copy the selection", RiskLevel.MEDIUM),
    "paste": (("ctrl", "v"), "paste", RiskLevel.MEDIUM),
    "save": (("ctrl", "s"), "save", RiskLevel.MEDIUM),
    "enter": (("enter",), "press Enter", RiskLevel.HIGH),
    "escape": (("esc",), "press Escape", RiskLevel.MEDIUM),
    "close window": (("alt", "f4"), "close the window", RiskLevel.HIGH),
    "minimize": (("win", "down"), "minimize the window", RiskLevel.MEDIUM),
    "maximize": (("win", "up"), "maximize the window", RiskLevel.MEDIUM),
    "show desktop": (("win", "d"), "show the desktop", RiskLevel.MEDIUM),
}
MAX_TYPED = 500
_OURS = re.compile(r"j\.?a\.?r\.?v\.?i\.?s", re.IGNORECASE)


class KeysBackend(Protocol):
    def windows(self) -> list[DesktopWindow]: ...
    def focus(self, window: DesktopWindow) -> bool: ...
    def press(self, keys: tuple[str, ...]) -> None: ...
    def type_text(self, text: str) -> None: ...


class WindowsKeys:  # pragma: no cover - needs a real desktop
    _KEYUP, _UNICODE, _INPUT_KEYBOARD = 0x0002, 0x0004, 1

    def __init__(self) -> None:
        from laptop_agent.tools.windows import _WindowsBackend

        self._windows = _WindowsBackend()
        self._user32 = ctypes.windll.user32

    def windows(self) -> list[DesktopWindow]:
        return self._windows.list_windows()

    def focus(self, window: DesktopWindow) -> bool:
        if self._user32.IsIconic(window.handle):
            self._user32.ShowWindow(window.handle, 9)            # SW_RESTORE
        # Windows lets the foreground move only after a key press it saw; a bare Alt is that press.
        self._user32.keybd_event(VK["alt"], 0, 0, 0)
        self._user32.keybd_event(VK["alt"], 0, self._KEYUP, 0)
        moved = bool(self._user32.SetForegroundWindow(window.handle))
        time.sleep(0.15)
        return moved

    def press(self, keys: tuple[str, ...]) -> None:
        for key in keys:
            self._user32.keybd_event(VK[key], 0, 0, 0)
        for key in reversed(keys):
            self._user32.keybd_event(VK[key], 0, self._KEYUP, 0)

    def type_text(self, text: str) -> None:
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
            _anonymous_ = ("u",)
            _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

        events = []
        for char in text:
            for flags in (self._UNICODE, self._UNICODE | self._KEYUP):
                event = INPUT(type=self._INPUT_KEYBOARD)
                event.ki = KEYBDINPUT(0, ord(char), flags, 0, 0)
                events.append(event)
        array = (INPUT * len(events))(*events)
        self._user32.SendInput(len(events), array, ctypes.sizeof(INPUT))


def keys_backend() -> KeysBackend | None:
    return WindowsKeys() if sys.platform == "win32" else None


class KeysTool:
    def __init__(self, approval_gate: ApprovalGate, backend: KeysBackend | None) -> None:
        self._gate = approval_gate
        self._backend = backend

    def _target(self, app: str) -> tuple[DesktopWindow | None, str]:
        others = [window for window in self._backend.windows() if not _OURS.search(window.title)]
        if app:
            ranked = sorted((rank, index, window) for index, window in enumerate(others)
                            if (rank := window.match_rank(app)) is not None)
            if not ranked:
                return None, f"I can't see a window for {app!r}. Is it open?"
            return ranked[0][2], ""
        if not others:
            return None, "There's no other window open to send that to."
        return others[0], ""

    def _send(self, app: str, describe: str, risk: RiskLevel, preview: str, act) -> ToolResult:
        if self._backend is None:
            return ToolResult.failure("Pressing keys in other apps is built for Windows only so far.")
        window, why = self._target(app.strip())
        if window is None:
            return ToolResult.failure(why)
        self._gate.require(ApprovalRequest(action=f"{describe[0].upper()}{describe[1:]} in {window.title}", risk=risk,
                                           reason="The keys go to that window as if you pressed them.", preview=preview))
        if not self._backend.focus(window):
            return ToolResult.failure(f"Windows wouldn't bring {window.title} to the front, so nothing was pressed.")
        act()
        return ToolResult.success(f"Done: {describe} in {window.title}.", window=window.title)

    def shortcut(self, action: str, app: str = "") -> ToolResult:
        found = ACTIONS.get(action.strip().lower())
        if found is None:
            return ToolResult.failure("I know these: " + ", ".join(ACTIONS) + ".")
        keys, describe, risk = found
        return self._send(app, describe, risk, "+".join(keys), lambda: self._backend.press(keys))

    def type_text(self, text: str, app: str = "") -> ToolResult:
        # One line only: a newline would press Enter, and in a terminal that runs the line.
        text = " ".join(text.split())
        if not text:
            return ToolResult.failure("What should I type?")
        if len(text) > MAX_TYPED:
            return ToolResult.failure(f"That's over {MAX_TYPED} characters; paste it instead.")
        return self._send(app, "type the text", RiskLevel.HIGH, text, lambda: self._backend.type_text(text))
