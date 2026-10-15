"""Opt-in Chromium check of "Hey Jarvis" in the real page: JARVIS_BROWSER_TESTS=1.

Chromium's fake microphone plays beeps, which the page cuts into clips like speech; /api/wake is
answered by the test, so no speech model runs, and speech recognition is a stub.
"""
from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import laptop_agent.webui as webui

STUB_SPEECH = """
window.webkitSpeechRecognition = window.SpeechRecognition = class {
  start(){} stop(){} abort(){}
};
window.__mics = 0;
const realGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = (constraints) => { window.__mics++; return realGetUserMedia(constraints); };
"""


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class WakeWordInTheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.metrics_patch = patch.object(webui, "system_metrics", return_value={"cpu_percent": 1, "ram_percent": 1, "gpus": []})
        cls.metrics_patch.start()
        # Should a clip ever reach the server, it meets a fake: no speech model runs in a test.
        cls.wake_patch = patch.object(webui, "_WAKE", webui.WakeDetector(lambda clip: ("", 1.0)))
        cls.wake_patch.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.metrics_patch.stop()
        cls.wake_patch.stop()

    def open(self, wake_reply: dict, stored: str | None = None):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
        self.addCleanup(context.close)
        context.add_init_script(STUB_SPEECH)
        if stored is not None:
            context.add_init_script(f"localStorage.setItem('jarvis_wake', {json.dumps(stored)});")
        self.checks: list[str] = []

        def answer(route):
            self.checks.append(route.request.post_data or "")
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, **wake_reply}))

        # Playwright tries the last route added first: the catch-all goes in first, so /api/wake is ours.
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        context.route("**/api/wake", answer)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(self.url)
        return page

    def voicing(self, page) -> bool:
        return page.evaluate("document.body.classList.contains('voicing')")

    def test_on_by_default_a_clip_reaches_the_check_and_waking_starts_voice(self):
        page = self.open({"available": True, "wake": True, "rest": ""})
        page.wait_for_function("() => document.body.classList.contains('voicing')", timeout=20000)
        self.assertTrue(self.checks, "no clip was sent to /api/wake")
        self.assertTrue(self.checks[0].startswith('{"audio":"data:audio/wav;base64,'))
        self.assertEqual(page.get_attribute("#wakeBtn", "aria-pressed"), "true")

    def test_not_woken_stays_quiet(self):
        page = self.open({"available": True, "wake": False})
        for _ in range(40):
            if self.checks:
                break
            page.wait_for_timeout(500)
        self.assertTrue(self.checks, "no clip was sent to /api/wake")
        self.assertFalse(self.voicing(page))

    def test_switched_off_it_sends_nothing_and_remembers(self):
        page = self.open({"available": True, "wake": True}, stored="off")
        page.wait_for_timeout(3000)
        self.assertEqual(page.evaluate("window.__mics"), 0, "the microphone was opened while switched off")
        self.assertEqual(self.checks, [])
        self.assertEqual(page.get_attribute("#wakeBtn", "aria-pressed"), "false")
        page.click("#wakeBtn")
        self.assertEqual(page.evaluate("localStorage.getItem('jarvis_wake')"), "on")

    def test_saying_turn_off_the_wake_word_turns_it_off(self):
        page = self.open({"available": True, "wake": False})
        page.fill("#ta", "turn off the wake word")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.getElementById('wakeBtn').getAttribute('aria-pressed') === 'false'", timeout=5000)
        self.assertEqual(page.evaluate("localStorage.getItem('jarvis_wake')"), "off")
        self.assertIn("stopped listening", page.locator(".msg").last.inner_text())

    def test_no_local_recognition_switches_it_off_quietly(self):
        page = self.open({"available": False, "wake": False})
        page.wait_for_function("() => document.getElementById('wakeBtn').title.indexOf('needs local speech') >= 0",
                               timeout=20000)
        count = len(self.checks)
        page.wait_for_timeout(3000)
        self.assertEqual(len(self.checks), count, "kept sending clips to an engine that is not there")


if __name__ == "__main__":
    unittest.main()
