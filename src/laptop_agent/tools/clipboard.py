"""`clipboard`, `clipboard copy <text>`, `clipboard ask <question>` - the text on the clipboard.

"summarize what I copied" and "copy that" had nowhere to go: the model router answered from
nothing, or invented a shell command. The system clipboard is read and written here with no
extra package (the Win32 API through ctypes; pbpaste/pbcopy or wl-paste/xclip elsewhere), behind
a backend the tests replace, so no test reads or overwrites the real clipboard.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from typing import Protocol

from laptop_agent.access import is_personal
from laptop_agent.failures import record_failure
from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
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

    def __init__(self, user32=None, kernel32=None) -> None:
        if user32 is not None:
            self._user32, self._kernel32 = user32, kernel32
            return
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        self._user32.GetClipboardData.restype = ctypes.c_void_p
        self._user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        self._user32.SetClipboardData.restype = ctypes.c_void_p
        self._user32.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
                                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        self._user32.CreateWindowExW.restype = ctypes.c_void_p
        self._user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalLock.restype = ctypes.c_void_p
        self._kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        self._kernel32.GlobalAlloc.restype = ctypes.c_void_p
        self._kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

    def _open(self, owner=None) -> None:
        # A clipboard manager holds it for a few milliseconds at a time; one refusal is not "in use".
        for attempt in range(10):
            if self._user32.OpenClipboard(owner):
                return
            time.sleep(0.03)
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
        # Opened without a window, EmptyClipboard leaves no owner and SetClipboardData then fails, so a
        # copy cleared what was there and put nothing back (Codex's review, from Microsoft's docs). A
        # hidden window owns it for the moment it takes.
        owner = self._user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
        if not owner:
            self._kernel32.GlobalFree(handle)
            raise OSError("Could not make a window to own the clipboard.")
        given = False
        try:
            self._open(owner)
            try:
                self._user32.EmptyClipboard()
                given = bool(self._user32.SetClipboardData(self._CF_UNICODETEXT, handle))
                if not given:
                    raise OSError("The clipboard refused the text.")
            finally:
                self._user32.CloseClipboard()
        finally:
            if not given:
                self._kernel32.GlobalFree(handle)   # Windows owns the memory only once it took it
            self._user32.DestroyWindow(owner)


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
    # wl-clipboard only under Wayland: on X11 it fails every call while xclip would have worked.
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste") and shutil.which("wl-copy"):
        return CommandClipboard(["wl-paste", "--no-newline"], ["wl-copy"])
    if shutil.which("xclip"):
        return CommandClipboard(["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard"])
    return None


# What a backend can raise: a busy Windows clipboard, xclip/wl-paste exiting non-zero on an empty
# or non-text clipboard, image bytes that are not text. Each is a sentence for the user, never the
# generic "unexpected error".
_BACKEND_ERRORS = (OSError, subprocess.SubprocessError, UnicodeDecodeError, ValueError)


class ClipboardTool:
    """The laptop's clipboard, through the approval gate like the screen and the windows: reading is
    LOW and writing MEDIUM (it replaces what was copied), both audited. A personal account never
    reaches it, here rather than at each caller - a path that forgot the check read it."""

    def __init__(self, backend: ClipboardBackend | None, approval_gate: ApprovalGate | None = None) -> None:
        self._backend = backend
        self._gate = approval_gate

    def _unreachable(self) -> ToolResult:
        return ToolResult.failure("I can't reach a clipboard on this machine. On Linux, install wl-clipboard or xclip.")

    def content(self) -> ToolResult:
        """What is copied, as `data["text"]`; a failure says why it could not be read, so "no
        clipboard program installed" is never reported as "your clipboard is empty"."""
        if is_personal():
            return ToolResult.failure("Reading the laptop's clipboard needs a developer account.")
        if self._backend is None:
            return self._unreachable()
        if self._gate is not None:
            self._gate.require(ApprovalRequest(action="Read the clipboard", risk=RiskLevel.LOW,
                                               reason="Reads the text you copied, on this laptop."))
        try:
            text = self._backend.read()
        except _BACKEND_ERRORS as exc:
            record_failure("clipboard/read", exc)
            return ToolResult.failure(f"I couldn't read the clipboard: {exc}" if isinstance(exc, OSError) and
                                      "clipboard" in str(exc) else "I couldn't read the clipboard as text.")
        return ToolResult.success("read", text=text)

    def show(self) -> ToolResult:
        read = self.content()
        if not read.ok:
            return read
        text = str(read.data["text"]).strip()
        if not text:
            return ToolResult.success("Your clipboard has no text on it.", chars=0)
        shown = text if len(text) <= MAX_SHOWN else text[:MAX_SHOWN] + f"\n\n… ({len(text):,} characters in all)"
        return ToolResult.success(f"On your clipboard:\n\n{shown}", chars=len(text))

    def copy(self, text: str) -> ToolResult:
        if is_personal():
            return ToolResult.failure("Changing the laptop's clipboard needs a developer account.")
        if self._backend is None:
            return self._unreachable()
        if not text.strip():
            return ToolResult.failure("There is nothing to copy.")
        preview = text.strip().splitlines()[0][:80]
        if self._gate is not None:
            self._gate.require(ApprovalRequest(action="Replace what is on the clipboard", risk=RiskLevel.MEDIUM,
                                               reason="Puts this text on the clipboard; what was copied before is gone.",
                                               preview=text[:500]))
        try:
            self._backend.write(text)
        except _BACKEND_ERRORS as exc:
            record_failure("clipboard/write", exc)
            return ToolResult.failure("I couldn't put that on the clipboard: " + str(exc))
        return ToolResult.success(f"Copied to your clipboard: {preview}" + ("…" if len(text.strip()) > len(preview) else ""),
                                  chars=len(text))
