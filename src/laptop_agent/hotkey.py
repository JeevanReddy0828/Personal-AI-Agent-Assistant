"""A global push-to-talk key for the desktop window: Ctrl+Alt+J, from any app.

Voice could only be started by clicking the mic, or Space once a turn was running, and only
with the window focused - so asking something while working in another app meant finding the
window first. Windows' own RegisterHotKey (ctypes, no extra package) delivers the press to a
thread here, which hands it to the page. Ctrl+Space is not used globally: Windows switches the
input language with it.
"""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Any, Callable

from laptop_agent.failures import record_failure

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
LABEL = "Ctrl+Alt+J"
_HOTKEY_ID = 0x4A41   # "JA"


class GlobalHotkey:
    """Calls `on_press` on its own thread each time Ctrl+Alt+J is pressed anywhere in Windows."""

    def __init__(self, on_press: Callable[[], None], user32: Any = None, kernel32: Any = None) -> None:
        self._on_press = on_press
        self._user32 = user32
        self._kernel32 = kernel32
        self._thread_id = 0
        self._ready = threading.Event()
        self.registered = False

    def start(self) -> bool:
        """Begin listening; False where there is no Windows hotkey to register."""
        if self._user32 is None:
            if sys.platform != "win32":
                return False
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32
        threading.Thread(target=self._loop, name="jarvis-hotkey", daemon=True).start()
        self._ready.wait(2)
        return self.registered

    def _loop(self) -> None:
        self._thread_id = self._kernel32.GetCurrentThreadId()
        # The hotkey belongs to the thread that registers it, so this thread also reads its messages.
        self.registered = bool(self._user32.RegisterHotKey(None, _HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("J")))
        self._ready.set()
        if not self.registered:
            record_failure("hotkey/register", f"{LABEL} is taken by another app")
            return
        message = wintypes.MSG()
        try:
            while self._user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam == _HOTKEY_ID:
                    try:
                        self._on_press()
                    except Exception as exc:   # one bad press must not end the listener
                        record_failure("hotkey/press", exc)
        finally:
            self._user32.UnregisterHotKey(None, _HOTKEY_ID)

    def stop(self) -> None:
        if self._thread_id:
            self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
