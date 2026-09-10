"""Opt-in isolated Chromium checks: JARVIS_BROWSER_TESTS=1 python tests/run_tests.py."""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import laptop_agent.webui as webui
from laptop_agent.tools.base import ToolResult


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class BrowserRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.metrics_patch = patch.object(webui, "system_metrics", return_value={"cpu_percent": 12, "ram_percent": 30, "gpus": []})
        cls.metrics_patch.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        cls.server.daemon_threads = False
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.metrics_patch.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 950}, reduced_motion="reduce")
        self.context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)

    def wait_js(self, expression, arg=None):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if self.page.evaluate(expression, arg=arg):
                return
            self.page.wait_for_timeout(25)
        self.fail("Browser condition did not complete: " + expression)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def test_four_views_at_mobile_tablet_and_desktop_widths(self):
        for width in (390, 700, 1100, 1440):
            self.page.set_viewport_size({"width": width, "height": 950})
            for view in ("chat", "overview", "jobs", "pipeline"):
                # Jobs and Pipeline are off the nav but still routable, so drive every
                # view through the hash router rather than only the ones with a button.
                self.page.evaluate("v=>{location.hash='#/'+v;}", view)
                self.wait_js("v=>document.body.dataset.view===v", arg=view)
                dims = self.page.evaluate("({width:innerWidth,scroll:document.documentElement.scrollWidth})")
                self.assertLessEqual(dims["scroll"], dims["width"], (width, view, dims))
                if view == "chat":
                    box = self.page.locator("#ta").bounding_box()
                    self.assertIsNotNone(box)
                    self.assertGreaterEqual(box["x"], 0)
                    self.assertLessEqual(box["x"] + box["width"], width)
        artifacts = Path(__file__).resolve().parents[1] / "docs" / "review"
        artifacts.mkdir(parents=True, exist_ok=True)
        self.page.evaluate("()=>{location.hash='#/chat';}")
        self.page.screenshot(path=str(artifacts / "desktop.png"))
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.screenshot(path=str(artifacts / "mobile.png"))

    def test_markdown_and_attachment_names_cannot_create_event_attributes(self):
        self.page.evaluate('''() => {
            renderMsg('bot', '[click](https://example.com/" onmouseover="window.__xss=1)
                <img src=x onerror=window.__xss=2>'.replace(/\\n\\s+/g,' '));
            renderMsg('user','attachment',['<img src=x onerror=window.__xss=3>']);
        }'''.replace('1)\n                ', '1) '))
        self.assertEqual(self.page.locator('.msg [onmouseover],.msg [onerror],.msg img').count(), 0)
        self.assertIsNone(self.page.evaluate("window.__xss"))

    def test_reply_stays_with_original_session_when_chats_switch(self):
        started, release = threading.Event(), threading.Event()
        async def delayed(*args, **kwargs):
            started.set()
            release.wait(5)
            return ToolResult.success("Answer for the first chat")
        with patch.object(webui._orchestrator, "handle", delayed):
            self.page.evaluate("void send('First chat prompt')")
            self.page.expose_function("testStarted", started.is_set)
            self.wait_js("async()=>await testStarted()")
            first = self.page.evaluate("JSON.parse(localStorage.jarvis_sessions)[0].id")
            self.page.evaluate("newSession()")
            release.set()
            self.wait_js("JSON.parse(localStorage.jarvis_sessions).some(s=>s.msgs.some(m=>m.text==='Answer for the first chat'))")
            sessions = self.page.evaluate("JSON.parse(localStorage.jarvis_sessions)")
            self.assertEqual(sessions[0]["msgs"], [])
            original = next(s for s in sessions if s["id"] == first)
            self.assertEqual(original["msgs"][-1]["text"], "Answer for the first chat")

    def test_corrupt_chat_history_does_not_break_composer(self):
        self.page.evaluate("localStorage.jarvis_sessions='{broken'")
        self.page.reload()
        self.page.evaluate("void send('help')")
        self.wait_js("JSON.parse(localStorage.jarvis_sessions)[0].msgs.length===2")

    def test_stop_reaches_backend_and_prevents_followup(self):
        from laptop_agent.cancellation import check_cancelled, OperationCancelled
        entered, stopped = threading.Event(), threading.Event()
        async def work(*args, **kwargs):
            entered.set()
            try:
                for _ in range(200):
                    check_cancelled()
                    await asyncio.sleep(.02)
            except OperationCancelled:
                stopped.set()
                raise
            return ToolResult.success("should not finish")
        with patch.object(webui._orchestrator, "handle", work):
            self.page.evaluate("void send('long task')")
            self.page.expose_function("testEntered", entered.is_set)
            self.wait_js("async()=>await testEntered()")
            self.page.evaluate("stopGen()")
            self.page.expose_function("testStopped", stopped.is_set)
            self.wait_js("async()=>await testStopped()")

    def test_native_voice_end_releases_capture_and_audio(self):
        state = self.page.evaluate("""() => {
            let capture=0,pause=0,revoke=0;
            const nativeRevoke=URL.revokeObjectURL;
            URL.revokeObjectURL=()=>revoke++;
            captureStop=()=>capture++;
            activeAudio={pause:()=>pause++,src:'blob:test'};
            activeAudioURL='blob:test';
            voiceActive=true;
            endVoice();
            URL.revokeObjectURL=nativeRevoke;
            return {capture,pause,revoke,active:voiceActive,audio:activeAudio};
        }""")
        self.assertEqual(state, {"capture": 1, "pause": 1, "revoke": 1, "active": False, "audio": None})

    def test_profile_fields_round_trip_in_pipeline(self):
        self.page.evaluate("()=>{location.hash='#/pipeline';}")
        self.wait_js("document.body.dataset.view==='pipeline' && profileLoaded")
        self.page.get_by_text('Resume contact and certifications', exact=True).click()
        self.page.locator('#rsContact').fill('candidate@example.com · 555-123-4567')
        self.page.locator('#rsCerts').fill('Source certification')
        self.page.locator('#rsProfileSave').click()
        self.wait_js("document.getElementById('pipeMsg').textContent.includes('profile saved')")
        self.page.reload()
        self.wait_js("profileLoaded")
        self.assertEqual(self.page.locator('#rsContact').input_value(), 'candidate@example.com · 555-123-4567')

    def test_server_speech_is_preferred_when_the_server_has_an_engine(self):
        state = self.page.evaluate("""() => {
            localStorage.removeItem('jarvis_stt'); sttChosen=false; sttServer=false;
            setSttEngine('riva:parakeet');
            return {use:useServerStt(), note:document.getElementById('sttNote').textContent,
                    checked:document.getElementById('sttServer').getAttribute('aria-checked')};
        }""")
        self.assertTrue(state["use"])
        self.assertEqual(state["checked"], "true")
        self.assertIn("riva:parakeet", state["note"])

    def test_no_server_engine_leaves_the_browser_recognizer_in_charge(self):
        state = self.page.evaluate("""() => {
            localStorage.removeItem('jarvis_stt'); sttChosen=false;
            setSttEngine(null);
            return {use:useServerStt(), disabled:document.getElementById('sttServer').disabled};
        }""")
        self.assertFalse(state["use"])
        self.assertTrue(state["disabled"])

    def test_choosing_the_browser_recognizer_sticks(self):
        state = self.page.evaluate("""() => {
            localStorage.removeItem('jarvis_stt'); sttChosen=false; sttServer=false;
            setSttEngine('riva:parakeet');
            document.getElementById('sttServer').click();
            return {use:useServerStt(), stored:localStorage.getItem('jarvis_stt')};
        }""")
        self.assertFalse(state["use"])
        self.assertEqual(state["stored"], "browser")
        # an explicit choice survives the next health poll
        again = self.page.evaluate("()=>{setSttEngine('riva:parakeet');return useServerStt();}")
        self.assertFalse(again)

    def test_a_table_reply_gets_copy_and_csv_actions(self):
        self.page.evaluate("""() => {
            renderMsg('bot', '| Planet | Moons |\\n|---|---|\\n| Earth | 1 |\\n| Mars, red | 2 |');
        }""")
        self.assertEqual(self.page.locator('.msg .md table').count(), 1)
        labels = self.page.locator('.msg .tableacts .oact').all_text_contents()
        self.assertEqual(labels, ["Copy", "CSV"])
        # a field holding a comma must survive the round trip as one quoted cell
        csv = self.page.evaluate("toCSV(tableToRows(document.querySelector('.msg .md table')))")
        self.assertIn('"Mars, red",2', csv)
        self.assertTrue(csv.startswith("Planet,Moons"))

    def test_a_document_reply_is_a_real_download_link(self):
        self.page.evaluate("""() => {
            renderMsg('bot', '[REST vs GraphQL](/api/document?name=rest-vs-graphql-1.pdf) — PDF ready.');
        }""")
        link = self.page.locator('.msg .md a.doclink')
        self.assertEqual(link.count(), 1)
        self.assertEqual(link.get_attribute('href'), '/api/document?name=rest-vs-graphql-1.pdf')
        self.assertEqual(link.get_attribute('download'), 'rest-vs-graphql-1.pdf')
        self.assertEqual(self.page.locator('.msg .md a.doclink .dockind').inner_text(), 'PDF')

    def test_a_link_cannot_smuggle_a_javascript_url(self):
        self.page.evaluate("""() => {
            renderMsg('bot', '[click](javascript:alert(1)) and [x](/ok" onclick="window.__xss=1)');
        }""")
        hrefs = self.page.evaluate("[...document.querySelectorAll('.msg .md a')].map(a=>a.getAttribute('href'))")
        self.assertTrue(all(h is None or h.startswith(('/', 'http')) for h in hrefs), hrefs)
        self.assertEqual(self.page.locator('.msg .md [onclick]').count(), 0)
        self.assertIsNone(self.page.evaluate("window.__xss"))

    def test_a_picture_reply_gets_a_save_action(self):
        self.page.evaluate("""() => {
            renderMsg('bot', '![a fox](/api/image?name=fox-1.png)\\n\\nHere is a fox.');
        }""")
        self.assertEqual(self.page.locator('.msg .md .figure img').count(), 1)
        self.assertEqual(self.page.locator('.msg .md .figure .oact').inner_text(), "Save")

    def test_decorating_twice_does_not_stack_actions(self):
        self.page.evaluate("""() => {
            renderMsg('bot', '| A |\\n|---|\\n| 1 |');
            decorate(document.querySelector('.msg .md'));
            decorate(document.querySelector('.msg .md'));
        }""")
        self.assertEqual(self.page.locator('.msg .tableacts').count(), 1)

    def test_a_deleted_chat_leaves_storage_and_opens_another(self):
        self.page.evaluate("""() => {
            sessions=[{id:'a',title:'First',msgs:[{role:'user',text:'one'}]},
                      {id:'b',title:'Second',msgs:[{role:'user',text:'two'}]}];
            current='b'; saveSessions(); renderSessions();
        }""")
        self.page.locator('.sessrow', has_text="Second").locator('.sessdel').click()
        stored = self.page.evaluate("JSON.parse(localStorage.jarvis_sessions).map(s=>s.id)")
        self.assertEqual(stored, ["a"])
        # deleting the open chat must leave a chat on screen, not a blank panel
        self.assertEqual(self.page.evaluate("current"), "a")

    def test_an_incognito_chat_is_never_written_to_storage(self):
        self.page.evaluate("()=>{sessions=[];current=null;saveSessions();}")
        self.page.locator('#newGhost').click()
        self.page.evaluate("""() => {
            const s=curSession(); s.title='Secret'; s.msgs.push({role:'user',text:'private'}); saveSessions();
        }""")
        stored = self.page.evaluate("JSON.parse(localStorage.jarvis_sessions||'[]')")
        self.assertEqual(stored, [])
        self.assertTrue(self.page.evaluate("document.body.classList.contains('ghosting')"))
        # …but it is a usable chat while it is open
        self.assertEqual(self.page.evaluate("curSession().msgs.length"), 1)

    def test_an_ordinary_chat_is_still_saved_alongside_an_incognito_one(self):
        self.page.evaluate("()=>{sessions=[];current=null;saveSessions();}")
        self.page.locator('#newGhost').click()
        self.page.locator('#newChat').click()
        self.page.evaluate("()=>{curSession().title='Kept';saveSessions();}")
        stored = self.page.evaluate("JSON.parse(localStorage.jarvis_sessions).map(s=>s.title)")
        self.assertEqual(stored, ["Kept"])
        self.assertFalse(self.page.evaluate("document.body.classList.contains('ghosting')"))

    def test_mobile_chat_drawer_and_new_chat_suggestions(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator('#mobileChats').click()
        self.assertTrue(self.page.locator('#newChat').is_visible())
        self.page.locator('#newChat').click()
        self.assertFalse(self.page.locator('#newChat').is_visible())
        self.page.locator('.scard').first.click()
        self.wait_js("JSON.parse(localStorage.jarvis_sessions)[0].msgs.length===2")

    def test_pdf_is_one_page_and_overflow_preserves_previous_file(self):
        from laptop_agent.copilot import render_resume_html
        from laptop_agent.tools.resume_pdf import render_html_to_pdf
        from pypdf import PdfReader
        # Run async PDF export in a separate thread from Playwright's sync loop.
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as raw, ThreadPoolExecutor(max_workers=1) as pool:
            path = Path(raw) / "resume.pdf"
            html = render_resume_html("Test Candidate", "test@example.com", "", {
                "summary": "Python developer", "experiences": [{"company": "Acme", "bullets": ["Built Python APIs"]}]
            })
            result = pool.submit(lambda: asyncio.run(render_html_to_pdf(html, path))).result()
            self.assertTrue(result.ok, result.message)
            previous = path.read_bytes()
            self.assertEqual(len(PdfReader(io.BytesIO(previous)).pages), 1)
            self.assertIn("test@example.com", PdfReader(io.BytesIO(previous)).pages[0].extract_text())
            overflow = html.replace("Built Python APIs", "<br>".join(["Long evidence line"] * 250))
            result = pool.submit(lambda: asyncio.run(render_html_to_pdf(overflow, path))).result()
            self.assertFalse(result.ok)
            self.assertEqual(path.read_bytes(), previous)
