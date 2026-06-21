from __future__ import annotations

import os
import sys
from collections.abc import Callable

# Best-effort native window tweaks for the desktop app window: a translucent
# "see-through HUD" (window alpha) and an always-on-top pin. Implemented with
# Windows ctypes. Everything degrades to a graceful no-op off-Windows or when our
# window isn't found — never raises.
#
# IMPORTANT: we only ever touch a top-level window that BOTH belongs to this
# process AND carries our title. Matching by title alone is unsafe — an unrelated
# third-party app can share the name and would otherwise get modified.

WINDOW_TITLE = "J.A.R.V.I.S"

# win32 constants
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_LWA_ALPHA = 0x00000002
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001


def _find_own_hwnd(title: str) -> int:
    """The visible top-level window owned by THIS process whose title matches.
    Returns 0 if there's no such window (so we never touch another app)."""
    import ctypes

    user32 = ctypes.windll.user32
    our_pid = os.getpid()
    match = {"hwnd": 0}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _lparam):  # pragma: no cover - exercised only with a real window
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != our_pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == title:
            match["hwnd"] = hwnd
            return False  # stop enumerating
        return True

    user32.EnumWindows(_enum, 0)
    return int(match["hwnd"])


def apply_window_effects(
    opacity: float | None = None,
    on_top: bool | None = None,
    title: str = WINDOW_TITLE,
    finder: Callable[[str], int] | None = None,
) -> dict[str, object]:
    """Apply window alpha (``opacity`` in 0..1) and/or an always-on-top pin
    (``on_top``) to *our* desktop window. Returns what was actually applied; values
    stay ``None`` when nothing happened (off-Windows, window missing, or error)."""
    applied: dict[str, object] = {"opacity": None, "on_top": None}
    if sys.platform != "win32":
        return applied
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = (finder or _find_own_hwnd)(title)
        if not hwnd:
            return applied
        if opacity is not None:
            alpha = max(0, min(255, int(round(float(opacity) * 255))))
            style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_LAYERED)
            if user32.SetLayeredWindowAttributes(hwnd, 0, alpha, _LWA_ALPHA):
                applied["opacity"] = round(float(opacity), 3)
        if on_top is not None:
            insert_after = _HWND_TOPMOST if on_top else _HWND_NOTOPMOST
            if user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE):
                applied["on_top"] = bool(on_top)
    except Exception:  # pragma: no cover - defensive; native calls must never crash chat
        pass
    return applied
