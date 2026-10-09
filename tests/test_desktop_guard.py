"""Importing the test harness switches the desktop off, outside the runner too.

`run_tests.py` alone made the browser, app launches and media keys inert, so a probe or review
script that imported the harness directly opened YouTube and pressed the volume keys on this
laptop, again and again (2026-09-27, 2026-10-09). Each import is checked in a fresh
interpreter, the way such a script runs.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = """
import ctypes, os, sys, webbrowser
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import {module}
from desktop_guard import inert
assert webbrowser.open is inert, "webbrowser.open is live"
assert getattr(os, "startfile", inert) is inert, "os.startfile is live"
assert not hasattr(ctypes, "windll") or ctypes.windll.user32.keybd_event is inert, "keybd_event is live"
print("inert")
"""


class HarnessImportTests(unittest.TestCase):
    def test_importing_the_harness_switches_the_desktop_off(self) -> None:
        for module in ("test_everyday_requests", "test_orchestrator"):
            with self.subTest(module):
                result = subprocess.run(
                    [sys.executable, "-B", "-c", CHECK.format(module=module), str(ROOT / "src"), str(ROOT / "tests")],
                    capture_output=True, text=True, timeout=180, cwd=ROOT,
                )
                self.assertEqual(result.stdout.strip(), "inert", result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
