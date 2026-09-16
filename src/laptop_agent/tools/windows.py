"""Arrange the desktop's windows by name: "put WhatsApp on the left and Chrome on the right".

Windows-only in practice, and the ctypes layer sits behind an injectable backend so the
whole success path is unit-tested on any OS — the same shape as `transcribe`, `websearch`
and the LLM transport.

On the risk level: moving a window is a local, reversible, non-destructive change that
sends nothing anywhere, so this is MEDIUM rather than HIGH. Launching an app is HIGH
because it starts something new; rearranging what is already on screen is not that, and a
HIGH here would put an approval click in front of every spoken "snap Chrome left", which
is the entire point of the feature. It is still audited like any other gated action.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Callable, Protocol

from laptop_agent.failures import record_failure
from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult


@dataclass(frozen=True)
class DesktopWindow:
    handle: int
    title: str
    process: str

    def matches(self, name: str) -> bool:
        """Is this the window the user meant?

        Matched on the title *and* the executable, because neither alone is enough: a
        Chrome window is titled after the page it shows ("Inbox (3) - Gmail"), and
        WhatsApp's process is `WhatsApp.exe` while its title is just "WhatsApp".
        """
        wanted = name.strip().lower()
        if not wanted:
            return False
        return wanted in self.title.lower() or wanted in self.process.lower()


class WindowBackend(Protocol):
    def list_windows(self) -> list[DesktopWindow]: ...

    def work_area(self) -> tuple[int, int, int, int]: ...

    def place(self, handle: int, left: int, top: int, width: int, height: int) -> bool: ...


# Where a name puts a window inside the work area, as fractions (left, top, width, height).
LAYOUTS: dict[str, tuple[float, float, float, float]] = {
    "left": (0.0, 0.0, 0.5, 1.0),
    "right": (0.5, 0.0, 0.5, 1.0),
    "top": (0.0, 0.0, 1.0, 0.5),
    "bottom": (0.0, 0.5, 1.0, 0.5),
    "full": (0.0, 0.0, 1.0, 1.0),
    "left third": (0.0, 0.0, 1 / 3, 1.0),
    "right third": (2 / 3, 0.0, 1 / 3, 1.0),
    "centre": (0.15, 0.08, 0.7, 0.84),
    "top left": (0.0, 0.0, 0.5, 0.5),
    "top right": (0.5, 0.0, 0.5, 0.5),
    "bottom left": (0.0, 0.5, 0.5, 0.5),
    "bottom right": (0.5, 0.5, 0.5, 0.5),
}
_ALIASES = {
    "left half": "left", "right half": "right", "top half": "top", "bottom half": "bottom",
    "maximise": "full", "maximize": "full", "fullscreen": "full", "full screen": "full",
    "center": "centre", "middle": "centre",
    "first third": "left third", "last third": "right third",
}


def normalize_layout(name: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", (name or "").strip().lower()).strip(" .,")
    cleaned = _ALIASES.get(cleaned, cleaned)
    return cleaned if cleaned in LAYOUTS else None


# Longest first, so "top left" wins over "left" and "left half" over "left".
_POSITION_PHRASES = sorted(set(LAYOUTS) | set(_ALIASES), key=len, reverse=True)
_POSITION_RE = re.compile(
    r"(?:\b(?:on|to|at|in|into)\s+)?(?:\bthe\s+)?\b("
    + "|".join(re.escape(p) for p in _POSITION_PHRASES)
    + r")\b(?:\s+side)?(?:\s+half)?",
    re.IGNORECASE,
)
# Words that carry the request rather than naming an app.
_FILLER = {
    "put", "move", "snap", "send", "place", "set", "window", "windows", "split", "screen",
    "arrange", "the", "my", "a", "an", "to", "on", "at", "in", "into", "side", "please",
    "jarvis", "and", "with", "then", "also", "half", "app", "open", "make", "show", "up",
    # Politeness and framing. Without these, "could you put my WhatsApp on left" asked for
    # a window called "could you WhatsApp" and reported it as not open.
    "hey", "hi", "hello", "ok", "okay", "could", "can", "would", "will", "you", "i", "me",
    "we", "us", "want", "need", "like", "for", "of", "is", "it", "that", "this", "now",
    "just", "both", "sides", "them", "using", "use", "function", "layout", "mode", "view",
}


def parse_placements(text: str) -> list[tuple[str, str]]:
    """("whatsapp", "left"), ("chrome", "right") out of however it was said.

    Both word orders matter: people say "put WhatsApp on the left" and they also say
    "left side WhatsApp" — the second is how this was actually asked for out loud.
    """
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []
    # Splitting on "and"/"," first cannot work: said out loud it came as "left side
    # WhatsApp right side Chrome", one clause with two positions and no conjunction. So
    # find the positions and read the gaps between them as
    #     G0 [pos0] G1 [pos1] G2 ...
    # where each position takes the name before it, or the one after when nothing precedes.
    matches = [m for m in _POSITION_RE.finditer(cleaned) if normalize_layout(m.group(1))]
    if not matches:
        return []
    gaps: list[str] = []
    cursor = 0
    for match in matches:
        gaps.append(cleaned[cursor:match.start()])
        cursor = match.end()
    gaps.append(cleaned[cursor:])

    def name_of(gap: str) -> str:
        words = [w for w in re.split(r"[^\w.+#-]+", gap) if w and w.lower() not in _FILLER]
        return " ".join(words)

    names = [name_of(gap) for gap in gaps]
    used: set[int] = set()
    placements: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        layout = normalize_layout(match.group(1))
        before, after = index, index + 1
        if names[before] and before not in used:
            chosen = before
        elif names[after] and after not in used:
            chosen = after
        else:
            continue
        used.add(chosen)
        placements.append((names[chosen], layout))
    return placements


class WindowTool:
    def __init__(self, approval_gate: ApprovalGate, backend: WindowBackend | None = None) -> None:
        self._gate = approval_gate
        self._backend = backend

    def available(self) -> bool:
        return self._resolve_backend() is not None

    def _resolve_backend(self) -> WindowBackend | None:
        if self._backend is None and sys.platform == "win32":
            self._backend = _WindowsBackend()
        return self._backend

    def list_windows(self) -> ToolResult:
        backend = self._resolve_backend()
        if backend is None:
            return ToolResult.failure("Arranging windows only works on Windows.")
        try:
            windows = backend.list_windows()
        except Exception as exc:  # a desktop query must not raise out of a tool
            record_failure("windows.list", str(exc))
            return ToolResult.failure(f"Could not read the window list: {exc}")
        if not windows:
            return ToolResult.failure("I cannot see any open windows to arrange.")
        listed = "\n".join(f"- **{w.title[:70]}** ({w.process})" for w in windows[:20])
        return ToolResult.success(
            f"{len(windows)} window(s) open:\n{listed}",
            windows=[{"title": w.title, "process": w.process} for w in windows],
        )

    def arrange(self, placements: list[tuple[str, str]]) -> ToolResult:
        """Put each named window in its layout. One approval covers the whole arrangement."""
        backend = self._resolve_backend()
        if backend is None:
            return ToolResult.failure("Arranging windows only works on Windows.")
        if not placements:
            return ToolResult.failure(
                "Name a window and where to put it — for example: window chrome left."
            )
        resolved: list[tuple[DesktopWindow, str]] = []
        unknown: list[str] = []
        try:
            windows = backend.list_windows()
        except Exception as exc:
            record_failure("windows.list", str(exc))
            return ToolResult.failure(f"Could not read the window list: {exc}")
        for name, layout_name in placements:
            layout = normalize_layout(layout_name)
            if layout is None:
                return ToolResult.failure(
                    f"I do not know the position '{layout_name}'. I can use: "
                    + ", ".join(sorted(LAYOUTS)) + "."
                )
            # Shortest title first, so "chrome" prefers a real Chrome window over one that
            # merely mentions it in a page title.
            hits = sorted((w for w in windows if w.matches(name)), key=lambda w: len(w.title))
            if not hits:
                # Fall back to the individual words, longest first. Speech brings along
                # words no filter will ever catch ("could you WhatsApp", a mis-heard
                # article), and refusing the whole request over one stray word is worse
                # than acting on its most specific word.
                for word in sorted((w for w in name.split() if len(w) > 2), key=len, reverse=True):
                    hits = sorted((w for w in windows if w.matches(word)), key=lambda w: len(w.title))
                    if hits:
                        break
            if not hits:
                unknown.append(name)
                continue
            resolved.append((hits[0], layout))
        if unknown:
            open_now = ", ".join(sorted({w.process for w in windows})[:12])
            return ToolResult.failure(
                f"I cannot find a window for {', '.join(repr(u) for u in unknown)}. "
                f"Open it first. Right now I can see: {open_now}.",
                unknown=unknown,
            )

        described = ", ".join(f"{w.title[:40]} -> {layout}" for w, layout in resolved)
        self._gate.require(
            ApprovalRequest(
                action=f"Arrange windows: {described}",
                risk=RiskLevel.MEDIUM,
                reason="Moves and resizes windows that are already open. Nothing is closed or sent anywhere.",
            )
        )
        try:
            area = backend.work_area()
        except Exception as exc:
            record_failure("windows.work_area", str(exc))
            return ToolResult.failure(f"Could not measure the screen: {exc}")
        left, top, right, bottom = area
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return ToolResult.failure(f"The screen work area looks wrong: {area}.")

        placed, failed = [], []
        for window, layout in resolved:
            fx, fy, fw, fh = LAYOUTS[layout]
            target = (left + round(width * fx), top + round(height * fy),
                      round(width * fw), round(height * fh))
            try:
                ok = backend.place(window.handle, *target)
            except Exception as exc:
                record_failure("windows.place", str(exc))
                ok = False
            (placed if ok else failed).append(f"{window.title[:40]} ({layout})")
        if not placed:
            return ToolResult.failure("I could not move " + "; ".join(failed) + ".", failed=failed)
        message = "Arranged " + "; ".join(placed) + "."
        if failed:
            message += " I could not move " + "; ".join(failed) + "."
        return ToolResult.success(message, placed=placed, failed=failed, work_area=list(area))


class _WindowsBackend:
    """The real thing: EnumWindows to find them, SetWindowPos to move them."""

    _SW_RESTORE = 9
    _SWP_NOZORDER = 0x0004
    _SWP_NOACTIVATE = 0x0010
    _SPI_GETWORKAREA = 0x0030
    _DWMWA_CLOAKED = 14
    # Shells that are visible and titled but are not windows anyone arranges. Enumerating
    # the real desktop returned all three alongside Chrome and WhatsApp.
    _NOT_AN_APP = {"program manager", "windows input experience", "nvidia geforce overlay"}

    def _user32(self):
        import ctypes

        return ctypes.windll.user32

    def list_windows(self) -> list[DesktopWindow]:
        import ctypes

        user32 = self._user32()
        found: list[DesktopWindow] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _lparam):  # pragma: no cover - needs a real desktop
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True                      # no title: a tool window, not something to arrange
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value.strip().lower() in self._NOT_AN_APP:
                return True
            if self._cloaked(hwnd):
                return True           # a UWP shell kept alive off-screen, not a real window
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append(DesktopWindow(int(hwnd), buffer.value, _process_name(int(pid.value))))
            return True

        user32.EnumWindows(_enum, 0)
        return found

    def _cloaked(self, hwnd) -> bool:  # pragma: no cover - needs a real desktop
        """DWM-cloaked: visible to EnumWindows, not actually on screen.

        This is how the UWP host windows ("Windows Input Experience") present themselves,
        and the only reliable way to tell them from a real app window.
        """
        import ctypes

        try:
            value = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, self._DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
            )
            return bool(value.value)
        except Exception:
            return False          # no dwmapi: treat every window as real rather than hide them

    def work_area(self) -> tuple[int, int, int, int]:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        # The work area, not the full screen: it excludes the taskbar, so a "full" window
        # does not hide behind it.
        if not self._user32().SystemParametersInfoW(self._SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            raise OSError("SystemParametersInfoW(SPI_GETWORKAREA) failed")
        return (rect.left, rect.top, rect.right, rect.bottom)

    def place(self, handle: int, left: int, top: int, width: int, height: int) -> bool:  # pragma: no cover
        user32 = self._user32()
        user32.ShowWindow(handle, self._SW_RESTORE)   # a minimised window cannot be positioned
        return bool(user32.SetWindowPos(handle, 0, left, top, width, height,
                                        self._SWP_NOZORDER | self._SWP_NOACTIVATE))


def _process_name(pid: int) -> str:  # pragma: no cover - needs a real process
    """The executable behind a window, or "" when it cannot be read.

    Best effort on purpose: a window owned by a process we may not query (elevated, or
    gone between the enumerate and the lookup) should still be listed by its title.
    """
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(handle)
