"""Opt-in Chromium check of push to talk in the real page: JARVIS_BROWSER_TESTS=1.

Speech recognition is a stub here, so nothing listens to a microphone: the check is that the
keys and the desktop hotkey's entry point turn voice on and off, and that Ctrl+Space never
reaches the Space handler that interrupts a reply.
"""
from __future__ import annotations

import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import laptop_agent.webui as webui

STUB_SPEECH = """
window.webkitSpeechRecognition = window.SpeechRecognition = class {
  constructor(){ this.started = 0; }
  start(){ this.started++; } stop(){} abort(){}
};
"""


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class PushToTalkInTheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.metrics_patch = patch.object(webui, "system_metrics", return_value={"cpu_percent": 1, "ram_percent": 1, "gpus": []})
        cls.metrics_patch.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.metrics_patch.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        self.context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        self.context.add_init_script(STUB_SPEECH)
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.page.wait_for_function("typeof window.jarvisHotkey === 'function'")

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def voicing(self) -> bool:
        return self.page.evaluate("document.body.classList.contains('voicing')")

    def test_ctrl_space_turns_voice_on_and_off(self):
        self.page.locator("body").click(position={"x": 5, "y": 5})
        self.page.keyboard.press("Control+Space")
        self.assertTrue(self.voicing())
        self.page.keyboard.press("Control+Space")
        self.assertFalse(self.voicing())

    def test_ctrl_space_works_while_typing(self):
        self.page.locator("#ta").click()
        self.page.keyboard.type("half a thought")
        self.page.keyboard.press("Control+Space")
        self.assertTrue(self.voicing())
        self.assertEqual(self.page.locator("#ta").input_value(), "half a thought", "the space was typed")

    def test_ctrl_space_never_reaches_the_space_handlers(self):
        # Bubbling listeners on the page, as the interrupt and recording handlers are, must not hear it.
        self.page.evaluate("window.__heard = 0; document.addEventListener('keydown', e => { if (e.code === 'Space') window.__heard++; });")
        self.page.keyboard.press("Control+Space")
        self.assertTrue(self.voicing())
        self.assertEqual(self.page.evaluate("window.__heard"), 0)

    def test_the_desktop_hotkey_entry_point_toggles_voice(self):
        self.page.evaluate("window.jarvisHotkey()")
        self.assertTrue(self.voicing())
        self.page.evaluate("window.jarvisHotkey()")
        self.assertFalse(self.voicing())

    def test_chromium_app_receives_a_global_press_without_a_page_key(self):
        # A fake hotkey signal, delivered over the app window's private channel; no OS key
        # or real microphone is used by this browser test.
        with patch.object(webui, "_DESKTOP_MODE", True):
            with self.page.expect_request(lambda request: "/api/hotkey?" in request.url and "after=" in request.url):
                self.page.goto(f"{self.url}/?desktop_hotkey={webui._HOTKEY_CHANNEL}")
            self.page.wait_for_function("typeof window.jarvisHotkey === 'function'")
            webui._signal_hotkey()
            self.page.wait_for_function("() => document.body.classList.contains('voicing')")
            webui._signal_hotkey()
            self.page.wait_for_function("() => !document.body.classList.contains('voicing')")


if __name__ == "__main__":
    unittest.main()
