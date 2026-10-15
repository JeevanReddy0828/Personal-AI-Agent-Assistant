"""`clipboard`, `clipboard copy <text>`, `clipboard ask <question>` - the text on the clipboard.

"summarize what I copied" and "copy that" had nowhere to go: the model router answered from
nothing, or invented a shell command. The system clipboard is read and written here with no
extra package (the Win32 API through ctypes; pbpaste/pbcopy or wl-paste/xclip elsewhere), behind
a backend the tests replace, so no test reads or overwrites the real clipboard.
"""
from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
from typing import Protocol

from laptop_agent.tools.base import ToolResult

MAX_SHOWN = 4000
MAX_ASKED = 30_000


class ClipboardBackend(Protocol):
    def read(self) -> str: ...

    def write(self, text: str) -> None: ...


class MemoryClipboard:
    """A clipboard of its own: what the tests, and the test harness, use instead of the real one."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    def read(self) -> str:
        return self.text

    def write(self, text: str) -> None:
        self.text = text


class WindowsClipboard:
    _CF_UNICODETEXT = 13
    _GMEM_MOVEABLE = 0x0002

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.GetClipboardData.restype = ctypes.c_void_p
        self._user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        self._user32.SetClipboardData.restype = ctypes.c_void_p
        self._kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalLock.restype = ctypes.c_void_p
        self._kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        self._kernel32.GlobalAlloc.restype = ctypes.c_void_p

    def _open(self) -> None:
        if not self._user32.OpenClipboard(None):
            raise OSError("The clipboard is in use by another app; try again in a moment.")

    def read(self) -> str:
        self._open()
        try:
            handle = self._user32.GetClipboardData(self._CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = self._kernel32.GlobalLock(handle)
            try:
                return ctypes.wstring_at(pointer) if pointer else ""
            finally:
                self._kernel32.GlobalUnlock(handle)
        finally:
            self._user32.CloseClipboard()

    def write(self, text: str) -> None:
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        handle = self._kernel32.GlobalAlloc(self._GMEM_MOVEABLE, size)
        if not handle:
            raise OSError("Could not allocate memory for the clipboard.")
        pointer = self._kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, size)
        self._kernel32.GlobalUnlock(handle)
        self._open()
        try:
            self._user32.EmptyClipboard()
            if not self._user32.SetClipboardData(self._CF_UNICODETEXT, handle):
                raise OSError("The clipboard refused the text.")
        finally:
            self._user32.CloseClipboard()


class CommandClipboard:
    """macOS and Linux: the platform's own clipboard programs."""

    def __init__(self, paste: list[str], copy: list[str]) -> None:
        self._paste, self._copy = paste, copy

    def read(self) -> str:
        return subprocess.run(self._paste, capture_output=True, text=True, timeout=5, check=True).stdout

    def write(self, text: str) -> None:
        subprocess.run(self._copy, input=text, text=True, timeout=5, check=True)


def system_clipboard() -> ClipboardBackend | None:
    """This machine's clipboard, or None when it has no way to reach one."""
    if sys.platform == "win32":
        return WindowsClipboard()
    if sys.platform == "darwin":
        return CommandClipboard(["pbpaste"], ["pbcopy"])
    if shutil.which("wl-paste") and shutil.which("wl-copy"):
        return CommandClipboard(["wl-paste", "--no-newline"], ["wl-copy"])
    if shutil.which("xclip"):
        return CommandClipboard(["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard"])
    return None


class ClipboardTool:
    def __init__(self, backend: ClipboardBackend | None) -> None:
        self._backend = backend

    def _unreachable(self) -> ToolResult:
        return ToolResult.failure("I can't reach a clipboard on this machine. On Linux, install wl-clipboard or xclip.")

    def text(self) -> str | None:
        """What is copied, or None when the clipboard cannot be read."""
        if self._backend is None:
            return None
        return self._backend.read()

    def show(self) -> ToolResult:
        if self._backend is None:
            return self._unreachable()
        text = self._backend.read().strip()
        if not text:
            return ToolResult.success("Your clipboard has no text on it.", chars=0)
        shown = text if len(text) <= MAX_SHOWN else text[:MAX_SHOWN] + f"\n\n… ({len(text):,} characters in all)"
        return ToolResult.success(f"On your clipboard:\n\n{shown}", chars=len(text))

    def copy(self, text: str) -> ToolResult:
        if self._backend is None:
            return self._unreachable()
        if not text.strip():
            return ToolResult.failure("There is nothing to copy.")
        self._backend.write(text)
        preview = text.strip().splitlines()[0][:80]
        return ToolResult.success(f"Copied to your clipboard: {preview}" + ("…" if len(text.strip()) > len(preview) else ""),
                                  chars=len(text))
