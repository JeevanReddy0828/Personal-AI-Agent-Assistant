from __future__ import annotations

import json
import threading
import unittest
import urllib.request

from http.server import ThreadingHTTPServer

import laptop_agent.webui as webui


class WindowApiTests(unittest.TestCase):
    """Exercise /api/window: a no-op in browser mode, and a gated call into
    apply_window_effects in desktop mode (stubbed so no real window is touched)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer((webui.HOST, 8795), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:8795"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def setUp(self) -> None:
        self._orig_mode = webui._DESKTOP_MODE
        self._orig_fx = webui.apply_window_effects

    def tearDown(self) -> None:
        webui._DESKTOP_MODE = self._orig_mode
        webui.apply_window_effects = self._orig_fx

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + "/api/window",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def test_browser_mode_is_noop(self) -> None:
        webui._DESKTOP_MODE = False
        calls: list = []
        webui.apply_window_effects = lambda **kw: calls.append(kw)  # must not be called
        data = self._post({"opacity": 0.5, "on_top": True})
        self.assertTrue(data["ok"])
        self.assertFalse(data["native"])
        self.assertEqual(data["applied"], {"opacity": None, "on_top": None})
        self.assertEqual(calls, [])

    def test_desktop_mode_applies_effects(self) -> None:
        webui._DESKTOP_MODE = True
        calls: list = []

        def fake(opacity=None, on_top=None):
            calls.append((opacity, on_top))
            return {"opacity": opacity, "on_top": on_top}

        webui.apply_window_effects = fake
        data = self._post({"opacity": 0.6, "on_top": True})
        self.assertTrue(data["native"])
        self.assertEqual(data["applied"], {"opacity": 0.6, "on_top": True})
        self.assertEqual(calls, [(0.6, True)])

    def test_desktop_mode_ignores_non_numeric_opacity(self) -> None:
        webui._DESKTOP_MODE = True
        captured: list = []

        def fake(opacity=None, on_top=None):
            captured.append((opacity, on_top))
            return {"opacity": opacity, "on_top": on_top}

        webui.apply_window_effects = fake
        self._post({"opacity": "loud", "on_top": False})
        self.assertEqual(captured, [(None, False)])  # bad opacity dropped, on_top kept


if __name__ == "__main__":
    unittest.main()
