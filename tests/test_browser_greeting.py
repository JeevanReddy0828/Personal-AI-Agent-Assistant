"""Opt-in Chromium check of the greeting and the quiet end: JARVIS_BROWSER_TESTS=1.

Speech is faked end to end: /api/tts returns a short silent WAV, /api/transcribe hears nothing,
/api/wake never wakes, and the browser recognizer is a stub.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
import unittest
import wave
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import laptop_agent.webui as webui
from laptop_agent.tools.base import ToolResult

STUB_SPEECH = """
window.webkitSpeechRecognition = window.SpeechRecognition = class { start(){} stop(){} abort(){} };
"""


def silent_wav(seconds: float = 0.3) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(16000)
        clip.writeframes(bytes(int(16000 * seconds) * 2))
    return buffer.getvalue()


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class GreetingInTheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.patches = [patch.object(webui, "system_metrics", return_value={"cpu_percent": 1, "ram_percent": 1, "gpus": []}),
                       patch.object(webui, "_WAKE", webui.WakeDetector(lambda clip: ("", 1.0)))]
        for each in cls.patches:
            each.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                 "--autoplay-policy=no-user-gesture-required"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        for each in cls.patches:
            each.stop()

    def open(self, path: str):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
        self.addCleanup(context.close)
        context.add_init_script(STUB_SPEECH)
        self.spoken: list[str] = []

        def tts(route):
            self.spoken.append(json.loads(route.request.post_data or "{}").get("text", ""))
            route.fulfill(status=200, content_type="audio/wav", body=silent_wav())

        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        context.route("**/api/tts", tts)
        context.route("**/api/transcribe", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"ok": False, "text": "", "failed": False})))
        context.route("**/api/wake", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"ok": True, "available": True, "wake": False})))
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(self.url + path)
        return page

    def test_a_tab_shows_the_greeting_and_stays_quiet(self):
        page = self.open("/")
        page.wait_for_function("() => document.querySelectorAll('.msg.bot').length > 0", timeout=10000)
        self.assertRegex(page.locator(".msg.bot").last.inner_text(), r"(morning|afternoon|evening|Hey|Welcome back|Still up|Late one)")
        page.wait_for_timeout(1500)
        self.assertFalse(page.evaluate("document.body.classList.contains('voicing')"))
        self.assertEqual(self.spoken, [])

    def test_once_a_window(self):
        page = self.open("/")
        page.wait_for_function("() => document.querySelectorAll('.msg.bot').length > 0", timeout=10000)
        page.reload()
        page.wait_for_timeout(2000)
        self.assertEqual(page.locator(".msg.bot").count(), 0)

    def test_the_app_window_says_it_listens_and_goes_back_to_the_wake_word_when_quiet(self):
        page = self.open("/?app=1")
        page.wait_for_function("() => document.body.classList.contains('voicing')", timeout=10000)
        page.wait_for_function("() => !document.body.classList.contains('voicing')", timeout=45000)
        self.assertEqual(len(self.spoken), 1, "the greeting was not spoken once")
        self.assertEqual(page.get_attribute("#wakeBtn", "aria-pressed"), "true")

    def test_a_greeting_that_arrives_after_the_user_spoke_is_dropped(self):
        def slow_greeting(name, now):
            time.sleep(1.5)
            return "Late greeting sentinel."

        async def instant(*args, **kwargs):
            return ToolResult.success("Answered.")

        with patch.object(webui, "greeting", slow_greeting), patch.object(webui._orchestrator, "handle", instant):
            page = self.open("/")
            page.fill("#ta", "hello")
            page.press("#ta", "Enter")
            page.wait_for_function("() => document.querySelectorAll('.msg.bot').length > 0", timeout=10000)
            page.wait_for_timeout(2500)
        self.assertNotIn("Late greeting sentinel.", page.inner_text("#chat"))
        self.assertIn("Answered.", page.locator(".msg.bot").last.inner_text())

    def test_answering_the_greeting_keeps_it_on_screen(self):
        async def instant(*args, **kwargs):
            return ToolResult.success("Answered.")

        with patch.object(webui._orchestrator, "handle", instant):
            page = self.open("/")
            page.wait_for_function("() => document.querySelectorAll('.msg.bot').length > 0", timeout=10000)
            greeting = page.locator(".msg.bot").first.inner_text()
            page.fill("#ta", "hello")
            page.press("#ta", "Enter")
            page.wait_for_function("() => document.querySelectorAll('.msg').length >= 3", timeout=10000)
        self.assertEqual(page.locator(".msg").first.inner_text(), greeting)

    def test_a_wake_opened_talk_on_browser_speech_ends_when_quiet(self):
        # The stub recognizer never reports a result or an end, like one left open in silence.
        page = self.open("/")
        page.evaluate("localStorage.setItem('jarvis_stt', 'browser')")
        page.reload()
        page.wait_for_function("() => typeof startVoice === 'function'", timeout=10000)
        page.evaluate("startVoice('')")
        self.assertTrue(page.evaluate("document.body.classList.contains('voicing')"))
        page.wait_for_function("() => !document.body.classList.contains('voicing')", timeout=20000)

    def test_speech_that_starts_before_the_quiet_end_is_not_cut_off(self):
        # Codex, review of #298: speech has started, but the first result has not come in yet.
        page = self.open("/")
        page.evaluate("localStorage.setItem('jarvis_stt', 'browser')")
        page.reload()
        page.wait_for_function("() => typeof startVoice === 'function'", timeout=10000)
        # Ends in a value: Playwright calls an expression that evaluates to a function.
        page.evaluate("startVoice(''); window.__aborts = 0; rec.abort = () => { window.__aborts++; }; 0")
        page.wait_for_timeout(11000)
        page.evaluate("rec.onspeechstart()")
        page.wait_for_timeout(2500)
        self.assertTrue(page.evaluate("document.body.classList.contains('voicing')"), "speech in progress was ended")
        self.assertEqual(page.evaluate("window.__aborts"), 0)

    def test_speech_that_never_turns_into_words_still_ends_when_quiet(self):
        # Codex, review of #298: a false speech start - it starts and ends, and no words come.
        page = self.open("/")
        page.evaluate("localStorage.setItem('jarvis_stt', 'browser')")
        page.reload()
        page.wait_for_function("() => typeof startVoice === 'function'", timeout=10000)
        page.evaluate("startVoice(''); 0")
        page.wait_for_timeout(1000)
        page.evaluate("rec.onspeechstart(); 0")
        page.wait_for_timeout(1000)
        page.evaluate("rec.onspeechend(); 0")
        page.wait_for_function("() => !document.body.classList.contains('voicing')", timeout=20000)

    def test_a_long_utterance_with_a_delayed_first_result_is_not_cut_off(self):
        # Codex, review of #298: still speaking 13 s in, with no result yet.
        page = self.open("/")
        page.evaluate("localStorage.setItem('jarvis_stt', 'browser')")
        page.reload()
        page.wait_for_function("() => typeof startVoice === 'function'", timeout=10000)
        page.evaluate("startVoice(''); window.__aborts = 0; rec.abort = () => { window.__aborts++; }; 0")
        page.wait_for_timeout(1000)
        page.evaluate("rec.onspeechstart(); 0")
        page.wait_for_timeout(13000)
        self.assertTrue(page.evaluate("document.body.classList.contains('voicing')"), "speech in progress was ended")
        self.assertEqual(page.evaluate("window.__aborts"), 0)

    def test_the_quiet_is_counted_from_when_speech_ends(self):
        page = self.open("/")
        page.evaluate("localStorage.setItem('jarvis_stt', 'browser')")
        page.reload()
        page.wait_for_function("() => typeof startVoice === 'function'", timeout=10000)
        page.evaluate("startVoice(''); 0")
        page.wait_for_timeout(5000)
        page.evaluate("rec.onspeechstart(); 0")
        page.wait_for_timeout(6000)
        page.evaluate("rec.onspeechend(); 0")
        page.wait_for_timeout(3000)
        self.assertTrue(page.evaluate("document.body.classList.contains('voicing')"), "ended 3 s after speech")

    def test_a_recognizer_stuck_in_speech_still_ends(self):
        # A start with no end and no result, ever: the cap on unheard speech ends the talk.
        page = self.open("/")
        page.evaluate("localStorage.setItem('jarvis_stt', 'browser')")
        page.reload()
        page.wait_for_function("() => typeof startVoice === 'function'", timeout=10000)
        page.evaluate("startVoice(''); 0")
        page.wait_for_timeout(1000)
        page.evaluate("rec.onspeechstart(); 0")
        page.wait_for_function("() => !document.body.classList.contains('voicing')", timeout=40000)

    def test_with_the_wake_word_off_the_app_window_only_shows_it(self):
        context_script = "localStorage.setItem('jarvis_wake', 'off');"
        page = self.open("/?app=1#")
        page.evaluate(context_script)
        page.evaluate("sessionStorage.clear()")
        page.reload()
        page.wait_for_function("() => document.querySelectorAll('.msg.bot').length > 0", timeout=10000)
        page.wait_for_timeout(1500)
        self.assertFalse(page.evaluate("document.body.classList.contains('voicing')"))


if __name__ == "__main__":
    unittest.main()
