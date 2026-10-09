"""Nothing run through the test harness may reach this desktop.

A URL handed to the OS is opened by the browser, not by this process, so no socket guard
sees it, and a media key is pressed on the real keyboard. The harness's fake YouTube lookup
once answered with a real song, and every probe or sweep that imported the harness outside
`run_tests.py` - which alone switched these off - opened it in the browser and pressed the
volume keys (2026-09-27, and again 2026-10-09). Each is now a stub that succeeds and does
nothing, switched off by importing the harness itself, whoever imports it.
"""

from __future__ import annotations

import ctypes
import os
import webbrowser


def inert(*args: object, **kwargs: object) -> bool:
    return True


def make_desktop_inert() -> None:
    webbrowser.open = inert
    if hasattr(os, "startfile"):
        os.startfile = inert
    if hasattr(ctypes, "windll"):
        ctypes.windll.user32.keybd_event = inert
