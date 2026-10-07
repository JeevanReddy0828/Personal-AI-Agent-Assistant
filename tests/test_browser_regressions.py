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

NEWLINE = chr(10)
BACKSLASH = chr(92)
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

    def test_gpu_unknown_usage_is_not_zero_and_fallback_is_labelled_3d(self):
        self.page.route("**/api/metrics", lambda route: route.fulfill(json={
            "cpu_percent": 12, "ram_percent": 30, "gpus": [
                {"name": "GPU 2", "util_kind": "3D", "util_percent": None,
                 "mem_used_mb": None, "mem_total_mb": 4096}]}))
        self.page.locator("#railStatus").click()
        self.wait_js("document.getElementById('metrics').textContent.includes('GPU 2 (3D)')")
        rendered = self.page.locator("#metrics").inner_text()
        self.assertIn("GPU 2 (3D)", rendered)
        self.assertEqual(rendered.count("n/a"), 2)

    def test_overview_and_drawer_distinguish_named_and_unknown_adapters(self):
        gpus=[{"name":"AMD Radeon Graphics","util_kind":"3D","util_percent":12},
              {"name":"NVIDIA GeForce RTX 4060","util_kind":"3D","util_percent":37}]
        self.page.route("**/api/metrics",lambda route:route.fulfill(json={"gpus":gpus}))
        self.page.evaluate("async()=>{await loadOverview();await loadMetrics();}")
        for area in ("#ovMetrics","#metrics"):
            labels=self.page.locator(area+" .top span").all_text_contents()
            self.assertIn("AMD Radeon Graphics (3D)",labels)
            self.assertIn("NVIDIA GeForce RTX 4060 (3D)",labels)
        gpus[:]=[{"util_kind":"3D","util_percent":None},{"name":" ","util_kind":"3D","util_percent":0}]
        self.page.evaluate("async()=>{await loadOverview();await loadMetrics();}")
        for area in ("#ovMetrics","#metrics"):
            labels=self.page.locator(area+" .top span").all_text_contents()
            self.assertIn("GPU 1 (3D)",labels)
            self.assertIn("GPU 2 (3D)",labels)
        gpus[:]=[{"name":"<img src=x onerror=alert(1)>","util_percent":0}]
        self.page.evaluate("async()=>{await loadOverview();await loadMetrics();}")
        self.assertEqual(self.page.locator("#ovMetrics img,#metrics img").count(),0)
        self.assertIn("<img",self.page.locator("#ovMetrics").inner_text())

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
        # Chats are saved once the page knows whose they are, so the damaged value may still be
        # there for a moment after the send: unparsed yet is not a failure.
        self.wait_js("(()=>{try{return JSON.parse(localStorage.jarvis_sessions)[0].msgs.length===2}catch(e){return false}})()")

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

    def test_display_maths_becomes_a_real_fraction(self):
        # A division arrived as literal "\[ \frac{754}{86982} \approx 0.008668 \]".
        block = "Gives:" + NEWLINE * 2 + BACKSLASH + "[" + NEWLINE + BACKSLASH + "frac{754}{86982} "             + BACKSLASH + "approx 0.008668" + NEWLINE + BACKSLASH + "]"
        self.page.evaluate("md=>renderMsg('bot', md)", block)
        md = self.page.locator('.msg .md')
        self.assertEqual(md.locator('.mathblock .frac').count(), 1)
        self.assertEqual(md.locator('.frac .num').inner_text(), "754")
        self.assertEqual(md.locator('.frac .den').inner_text(), "86982")
        self.assertIn("≈ 0.008668", md.inner_text())
        self.assertNotIn(BACKSLASH, md.inner_text())

    def test_inline_maths_renders_within_a_sentence(self):
        block = "Exact value: " + BACKSLASH + "( " + BACKSLASH + "frac{377}{43491} " + BACKSLASH + ")"
        self.page.evaluate("md=>renderMsg('bot', md)", block)
        md = self.page.locator('.msg .md')
        self.assertEqual(md.locator('.math .frac').count(), 1)
        self.assertIn("Exact value:", md.inner_text())
        self.assertNotIn(BACKSLASH, md.inner_text())

    def test_maths_symbols_and_scripts_convert(self):
        block = BACKSLASH + "( 3 " + BACKSLASH + "times 5 " + BACKSLASH + "le 20, x^{2}, "             + BACKSLASH + "sqrt{16} " + BACKSLASH + ")"
        self.page.evaluate("md=>renderMsg('bot', md)", block)
        text = self.page.locator('.msg .md').inner_text()
        for token in ("×", "≤", "x²", "√(16)"):
            self.assertIn(token, text)

    def test_a_windows_path_outside_maths_is_left_alone(self):
        # Conversion is deliberately confined to delimiters: a path is not an equation.
        block = "Open " + BACKSLASH.join(["C:", "new", "table.txt"]) + " to check."
        self.page.evaluate("md=>renderMsg('bot', md)", block)
        self.assertIn(BACKSLASH.join(["C:", "new", "table.txt"]), self.page.locator('.msg .md').inner_text())

    def test_an_er_diagram_is_drawn_as_svg(self):
        block = (
            "```mermaid" + NEWLINE + "erDiagram" + NEWLINE
            + "    USERS {" + NEWLINE + "        int id PK" + NEWLINE + "        varchar name" + NEWLINE + "    }" + NEWLINE
            + "    ORDERS {" + NEWLINE + "        int id PK" + NEWLINE + "        int user_id FK" + NEWLINE + "    }" + NEWLINE
            + "    USERS ||--o{ ORDERS : places" + NEWLINE + "```"
        )
        self.page.evaluate("md=>renderMsg('bot', md)", block)
        self.assertEqual(self.page.locator('.msg .dgm svg').count(), 1)
        text = self.page.locator('.msg .dgm').inner_text()
        for token in ("USERS", "ORDERS", "id : int PK", "places"):
            self.assertIn(token, text)
        # the relationship is a real line, not just a label
        self.assertGreaterEqual(self.page.locator('.msg .dgm svg > path').count(), 1)

    def test_a_flowchart_reads_from_its_entry_point(self):
        # A cycle used to invert the layering and put the entry state at the bottom.
        block = (
            "```mermaid" + NEWLINE + "flowchart TD" + NEWLINE
            + "    A[Slow Start] --> B[Congestion Avoidance]" + NEWLINE
            + "    B -->|3 dup ACKs| C[Fast Retransmit]" + NEWLINE
            + "    C --> B" + NEWLINE
            + "    B -->|timeout| A" + NEWLINE + "```"
        )
        self.page.evaluate("md=>renderMsg('bot', md)", block)
        order = self.page.evaluate("""() => [...document.querySelectorAll('.msg .dgm text')]
            .filter(t=>['Slow Start','Congestion Avoidance','Fast Retransmit'].includes(t.textContent))
            .sort((a,b)=>(+a.getAttribute('y'))-(+b.getAttribute('y')))
            .map(t=>t.textContent)""")
        self.assertEqual(order, ["Slow Start", "Congestion Avoidance", "Fast Retransmit"])

    def test_an_unsupported_diagram_stays_a_readable_code_block(self):
        block = "```mermaid" + NEWLINE + "pie title Votes" + NEWLINE + '    "A" : 10' + NEWLINE + "```"
        self.page.evaluate("md=>renderMsg('bot', md)", block)
        self.assertEqual(self.page.locator('.msg .dgm').count(), 0)
        self.assertIn("pie title Votes", self.page.locator('.msg .md pre').inner_text())

    def test_a_model_cannot_smuggle_a_base64_image(self):
        # One reply arrived carrying a fabricated data:image/png;base64 blob.
        self.page.evaluate("""() => {
            renderMsg('bot', '![fake](data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==)');
        }""")
        self.assertEqual(self.page.locator('.msg .md img').count(), 0)

    def test_a_broken_generated_image_leaves_no_phantom_save(self):
        # A model sometimes writes its own image link to a file that was never generated.
        # That rendered as a zero-height image with a Save control and nothing to save.
        self.page.evaluate("""() => {
            renderMsg('bot', '![diagram](/api/image?name=does-not-exist-123.png)\\n\\nHere is a diagram.');
        }""")
        self.wait_js("document.querySelectorAll('.msg .md img').length===0")
        self.assertEqual(self.page.locator('.msg .md .figure').count(), 0)
        self.assertEqual(self.page.locator('.msg .md .oact').count(), 0)
        self.assertIn("Here is a diagram.", self.page.locator('.msg .md').inner_text())

    def test_a_picture_reply_gets_a_save_action(self):
        from laptop_agent.webui import _CONFIG
        directory = (_CONFIG.data_dir / 'images').resolve()
        directory.mkdir(parents=True, exist_ok=True)
        picture = directory / 'fox-1.png'
        # a 1x1 PNG, so the image actually loads and keeps its Save control
        picture.write_bytes(bytes.fromhex(
            '89504e470d0a1a0a0000000d494844520000000100000001080600000'
            '01f15c4890000000a49444154789c6360000002000100ffff0300000600'
            '0557bfabd40000000049454e44ae426082'))
        self.addCleanup(picture.unlink, True)
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

    def test_speakable_matches_the_server_word_for_word(self):
        """`clean_for_speech` (Python) and `speakable()` (JS) implement the same rules and
        had already drifted: "- a bullet point" kept its marker on the client and lost it
        on the server, so the two halves of the voice loop said different things about the
        same reply. Both sides now assert against tests/data/speech_cases.json."""
        import json

        shared = json.loads(
            (Path(__file__).resolve().parent / "data" / "speech_cases.json").read_text(encoding="utf-8")
        )["cases"]
        produced = self.page.evaluate(
            "cases => Object.fromEntries(cases.map(c => [c, speakable(c)]))",
            arg=list(shared.keys()),
        )
        mismatched = {
            case: {"server": expected, "client": produced.get(case)}
            for case, expected in shared.items()
            if produced.get(case) != expected
        }
        self.assertEqual(mismatched, {}, f"speakable() has drifted from clean_for_speech: {mismatched}")

    def test_the_echo_guard_rejects_our_own_voice_and_keeps_the_users(self):
        """Reading an image URL aloud produced "slash api slash image question mark name
        equals…", which the echo guard could not match, so the microphone heard it, counted
        it as a spoken interruption, and drew the picture again — one request became four."""
        outcome = self.page.evaluate(
            """() => {
                rememberSpoken('Here is a red fox in snow.');
                rememberSpoken('The weather in Kurnool is overcast.');
                return {
                    exact:      isEcho('Here is a red fox in snow'),
                    partial:    isEcho('here is a red fox'),
                    mostWords:  isEcho('here is a red fox in the snow'),
                    userSpeech: isEcho('stop and draw a cat instead'),
                    shortWord:  isEcho('stop'),
                    empty:      isEcho(''),
                    // The microphone does not hear sentence boundaries: the end of one
                    // sentence and the start of the next arrive as one transcript.
                    straddle:   isEcho('red fox in snow the weather'),
                    userAbout:  isEcho('what is the weather in hyderabad tomorrow')
                };
            }"""
        )
        self.assertTrue(outcome["exact"], "our own sentence must be recognised as echo")
        self.assertTrue(outcome["partial"], "a fragment of our own sentence is still echo")
        self.assertTrue(outcome["mostWords"], "a near-match of our own sentence is echo")
        self.assertFalse(outcome["userSpeech"], "the user's own interruption must get through")
        self.assertFalse(outcome["shortWord"], "a single word must not be eaten as echo")
        self.assertFalse(outcome["empty"])
        # Straddling two of our sentences matched neither well enough, so the reply was
        # answered as if the user had said it - the loop, reported twice.
        self.assertTrue(outcome["straddle"], "a transcript spanning two of our sentences is echo")
        self.assertFalse(outcome["userAbout"], "a user question sharing a few of our words is theirs")

    def test_sending_works_without_a_secure_context(self):
        """Reached over http on a LAN address — how a phone reaches it — the page is not a
        secure context and `crypto.randomUUID` is undefined. send() called it on its first
        line, threw, and the send button did nothing: no request, no error, no clue."""
        outcome = self.page.evaluate(
            """async () => {
                // randomUUID lives on Crypto.prototype, so `delete crypto.randomUUID` does
                // nothing and the test passes against the bug. Shadow it on the instance.
                const saved = Object.getPrototypeOf(crypto).randomUUID;
                Object.defineProperty(crypto, 'randomUUID', {value: undefined, configurable: true});
                if (typeof crypto.randomUUID === 'function') {
                    return { wellFormed: false, threw: 'could not simulate an insecure context', reachedTheServer: null, id: '' };
                }
                const id = uuid();
                let called = null;
                const realFetch = window.fetch;
                window.fetch = (url, opts) => {
                    if (String(url).indexOf('/api/stream') >= 0) {
                        called = String(url);
                        return Promise.resolve(new Response('', {status: 200}));
                    }
                    return realFetch(url, opts);
                };
                let threw = null;
                try { await send('hello'); } catch (e) { threw = String(e); }
                window.fetch = realFetch;
                Object.defineProperty(crypto, 'randomUUID', {value: saved, configurable: true});
                return { id: id, wellFormed: /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id),
                         reachedTheServer: called, threw: threw };
            }"""
        )
        self.assertTrue(outcome["wellFormed"], "uuid() fallback produced " + repr(outcome["id"]))
        self.assertIsNone(outcome["threw"], "send() threw without crypto.randomUUID")
        self.assertIsNotNone(outcome["reachedTheServer"], "the message never left the page")

    def test_a_forecast_is_drawn_with_its_band_only_when_measured(self):
        """ANALYTICS-03's chart: a forecast result is drawn from its numbers, and the band
        only when every lower and upper bound exists. Drives the real send() loop against a
        synthetic stream, and asserts a box on screen rather than an attribute."""
        outcome = self.page.evaluate(
            """async () => {
                const NL = String.fromCharCode(10);
                const realFetch = window.fetch;
                const done = (bounded) => ({type: 'done', ok: true, message: '**Revenue: the next 3 months**',
                    data: {labels: ['2025-01', '2025-02', '2025-03'],
                           series: {labels: ['2024-10', '2024-11', '2024-12'], values: [10, 12, 11], period: 'month'},
                           forecast: {enough_data: true, points: [12, 13, 14],
                                      lower: bounded ? [11, 11.5, 12] : [11, 11.5, 12],
                                      upper: bounded ? [13, 14.5, 16] : [13, null, 16]}}});
                const turn = async (bounded) => {
                    window.fetch = (url, opts) => String(url).indexOf('/api/stream') >= 0
                        ? Promise.resolve(new Response('data: ' + JSON.stringify(done(bounded)) + NL + NL, {status: 200}))
                        : realFetch(url, opts);
                    try { await send('forecast revenue in sales.csv'); } finally { window.fetch = realFetch; }
                    const charts = document.querySelectorAll('.msg .fchart');
                    const chart = charts[charts.length - 1];
                    if (!chart) return null;
                    const box = chart.getBoundingClientRect();
                    return {height: box.height, width: box.width, band: !!chart.querySelector('.fband'),
                            line: !!chart.querySelector('.fline'), history: !!chart.querySelector('.fhist')};
                };
                return {bounded: await turn(true), partial: await turn(false),
                        charts: document.querySelectorAll('.msg .fchart').length};
            }"""
        )
        self.assertIsNotNone(outcome["bounded"], "no chart was drawn for a forecast")
        self.assertGreater(outcome["bounded"]["height"], 50)
        self.assertTrue(outcome["bounded"]["band"] and outcome["bounded"]["line"] and outcome["bounded"]["history"])
        self.assertIsNotNone(outcome["partial"])
        self.assertFalse(outcome["partial"]["band"], "a band was drawn with an upper bound missing")
        self.assertEqual(outcome["charts"], 2)
        self.assertEqual(self.errors, [])

    def test_a_one_step_range_shows_and_huge_flat_values_stay_finite(self):
        """Codex's review of #161. A one-step range drawn as a polygon had two corners at the
        same x, so it had no area and did not show; and a flat series at 1e20 drew every
        point at NaN, because 1e20 + 1 is 1e20 again and the scale divided by zero."""
        outcome = self.page.evaluate(
            """() => {
                const draw = (values, point, low, high) => {
                    const chart = forecastChart({labels: ['next'],
                        series: {labels: values.map((_, i) => 'p' + i), values},
                        forecast: {enough_data: true, points: [point], lower: [low], upper: [high]}});
                    document.body.appendChild(chart);
                    const band = chart.querySelector('.fband');
                    const box = band ? band.getBBox() : null;
                    const result = {area: box ? box.width * box.height : 0,
                                    finite: !/NaN|Infinity/.test(chart.outerHTML)};
                    chart.remove();
                    return result;
                };
                const small = forecastChart({labels: ['next'], series: {labels: ['a', 'b', 'c'],
                    values: [0.0031, 0.0029, 0.0034]}, forecast: {enough_data: true, points: [0.0032],
                    lower: [null], upper: [null]}});
                const ticks = [...small.querySelectorAll('text[text-anchor="end"]')]
                    .filter(t => +t.getAttribute('x') < 52).map(t => t.textContent);   // the y-axis gutter
                return {oneStep: draw([10, 12, 11], 12, 9, 15), huge: draw([1e20, 1e20], 1e20, 1e20, 1e20), ticks};
            }"""
        )
        self.assertGreater(outcome["oneStep"]["area"], 0, "a one-step range has no visible area")
        self.assertTrue(outcome["oneStep"]["finite"])
        self.assertTrue(outcome["huge"]["finite"], "a flat series at 1e20 drew NaN coordinates")
        # Review of #159: two decimals labelled every gridline of a small series 0.
        self.assertEqual(outcome["ticks"], ["0.0029", "0.00315", "0.0034"])
        self.assertEqual(self.errors, [])

    def test_a_reopened_chat_draws_its_forecast_again(self):
        """A saved message kept only the tool digest, cut at 2,000 characters, so reopening a
        chat lost every forecast chart. It now keeps the chart's own small payload."""
        outcome = self.page.evaluate(
            """async () => {
                const NL = String.fromCharCode(10), realFetch = window.fetch;
                const values = Array.from({length: 200}, (_, i) => 100 + i + (i % 7));
                const done = {type: 'done', ok: true, message: '**Revenue: the next 3 months**',
                    data: {labels: ['n1', 'n2', 'n3'],
                           series: {labels: values.map((_, i) => 'p' + i), values, period: 'month'},
                           forecast: {enough_data: true, points: [301, 302, 303],
                                      lower: [290, 291, 292], upper: [310, 312, 314]}}};
                window.fetch = (url, opts) => String(url).indexOf('/api/stream') >= 0
                    ? Promise.resolve(new Response('data: ' + JSON.stringify(done) + NL + NL, {status: 200}))
                    : realFetch(url, opts);
                try { await send('forecast Revenue in sales.csv'); } finally { window.fetch = realFetch; }
                const drawn = () => { const charts = document.querySelectorAll('.msg .fchart');
                    return charts.length ? charts[charts.length - 1].textContent : null; };
                const live = drawn();
                const saved = curSession().msgs.filter(m => m.chart).pop();
                loadSession(current);
                return {live, reopened: drawn(), kept: saved ? saved.chart.series.values.length : null,
                        size: saved ? JSON.stringify(saved.chart).length : null};
            }"""
        )
        self.assertIn("last 24 of 200", outcome["live"])
        self.assertEqual(outcome["reopened"], outcome["live"], "the reopened chat drew a different chart, or none")
        self.assertEqual(outcome["kept"], 48)
        self.assertLess(outcome["size"], 4000)
        self.assertEqual(self.errors, [])

    def test_copying_works_without_a_secure_context(self):
        """navigator.clipboard is also absent outside a secure context. One Copy button
        used it unguarded and reported 'Blocked' on a phone."""
        outcome = self.page.evaluate(
            """async () => {
                const saved = navigator.clipboard;
                try {
                    Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true});
                    const ok = await copyText('some text');
                    return { returned: typeof ok, threw: null };
                } catch (e) {
                    return { returned: null, threw: String(e) };
                } finally {
                    Object.defineProperty(navigator, 'clipboard', {value: saved, configurable: true});
                }
            }"""
        )
        self.assertIsNone(outcome["threw"], "copyText threw without navigator.clipboard")
        self.assertEqual(outcome["returned"], "boolean")

    def test_stopping_speech_stops_the_rest_of_the_answer(self):
        """Pressing Space silenced the sentence being spoken and then carried straight on
        with the next one: the turn was still streaming, and clearing the queue did nothing
        about the `tts` events still arriving. Drives the real send() loop against a
        synthetic stream, so the epoch gate in the shipped code is what is under test."""
        outcome = self.page.evaluate(
            """async () => {
                const NL = String.fromCharCode(10);
                const spoken = [];
                speechSynthesis.speak = (u) => { spoken.push(u.text); setTimeout(() => { if (u.onend) u.onend(); }, 5); };
                listen = () => {};                       // the microphone is not what this tests
                let push = null, close = null;
                const realFetch = window.fetch;
                window.fetch = (url, opts) => {
                    if (String(url).indexOf('/api/stream') >= 0) {
                        const body = new ReadableStream({ start(c) {
                            const enc = new TextEncoder();
                            push = (ev) => c.enqueue(enc.encode('data: ' + JSON.stringify(ev) + NL + NL));
                            close = () => { try { c.close(); } catch (e) {} };
                        }});
                        return Promise.resolve(new Response(body, { status: 200 }));
                    }
                    return realFetch(url, opts);
                };
                voiceActive = true;
                const turn = send('explain csv files');
                await new Promise(r => setTimeout(r, 200));
                push({ type: 'tts', text: 'First sentence of the answer.' });
                await new Promise(r => setTimeout(r, 200));
                const beforeStop = spoken.length;

                interruptNow();                          // Space / the Interrupt button

                push({ type: 'tts', text: 'Second sentence that must never be spoken.' });
                push({ type: 'tts', text: 'Third sentence that must never be spoken.' });
                push({ type: 'done', ok: true, message: 'A whole reply that must not be spoken either.' });
                close();
                await new Promise(r => setTimeout(r, 400));
                try { await turn; } catch (e) {}
                await new Promise(r => setTimeout(r, 300));
                window.fetch = realFetch; voiceActive = false;
                return { beforeStop: beforeStop, after: spoken.length, spoken: spoken };
            }"""
        )
        self.assertEqual(outcome["beforeStop"], 1, "the first sentence should have been spoken")
        self.assertEqual(
            outcome["after"], 1,
            "speech continued after the stop: " + repr(outcome["spoken"]),
        )

    def test_server_speech_can_be_interrupted_by_talking(self):
        """Server STT records instead of running the browser recognizer, so `bargeStart`
        returned immediately whenever `useServerStt()` was true — which is the default as
        soon as the server has an engine. Nothing could interrupt by voice at all. Barge-in
        now watches the microphone level, learning how loud our own output leaks past echo
        cancellation before treating anything as the user."""
        outcome = self.page.evaluate(
            """async () => {
                const FRAME = 4096, RATE = 48000;
                navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {} }] });
                let proc = null;
                window.AudioContext = function () {
                    this.sampleRate = RATE;
                    this.createMediaStreamSource = () => ({ connect() {}, disconnect() {} });
                    this.createGain = () => ({ gain: { value: 0 }, connect() {}, disconnect() {} });
                    this.createScriptProcessor = () => { proc = { onaudioprocess: null, connect() {}, disconnect() {} }; return proc; };
                    this.close = () => {};
                };
                window.webkitAudioContext = window.AudioContext;
                const feed = (peak, frames) => {
                    if (!proc || !proc.onaudioprocess) return;   // not listening at this moment
                    for (let i = 0; i < frames; i++) {
                        const ch = new Float32Array(FRAME);
                        for (let j = 0; j < FRAME; j++) ch[j] = (j % 2) ? peak : -peak;
                        proc.onaudioprocess({ inputBuffer: { getChannelData: () => ch } });
                    }
                };
                sttServer = true; sttChosen = true; sttEngine = 'test-engine';   // the server-STT path
                voiceActive = true; speaking = true; bargeReset();
                bargeStart();
                await new Promise(r => setTimeout(r, 60));
                const armed = !!proc;
                if (!armed) { voiceActive = false; speaking = false;
                    return { armed: false, heldThroughOurOwnVoice: false, stopped: false }; }
                const epoch0 = ttsEpoch;
                const sent = [];
                const realSend = send, realFetch = window.fetch;
                send = async (q) => { sent.push(q); };
                let heard = 'G men.';
                window.fetch = async (url, init) => (String(url).includes('/api/transcribe')
                    ? { ok: true, json: async () => ({ ok: true, text: heard }) }
                    : realFetch(url, init));
                const wait = ms => new Promise(r => setTimeout(r, ms));
                // The reply is still being fetched: the room is quiet. Learning here set
                // the bar at the floor, and our own voice then cleared it - the loop.
                feed(0.001, 8);
                const audio = { paused: false, currentTime: 0.5, src: '',
                                pause() { this.paused = true; }, play() { this.paused = false; return Promise.resolve(); } };
                activeAudio = audio;                 // playback starts
                feed(0.06, 6);                       // our own voice, learned as the floor
                feed(0.06, 6);                       // still only us: must not trigger
                const heldThroughOurOwnVoice = (ttsEpoch === epoch0 && speaking === true && !audio.paused);
                feed(0.35, 4);                       // something loud
                const pausedNotKilled = (audio.paused && ttsEpoch === epoch0 && speaking === true);
                await wait(1100); feed(0.001, 1);    // silence ends it; it transcribes as noise
                await wait(150);
                const resumedOnNoise = (!audio.paused && ttsEpoch === epoch0 && speaking === true && sent.length === 0);
                await wait(80);                      // listening again for a real interruption
                heard = 'stop and tell me the weather';
                feed(0.06, 6); feed(0.35, 4);        // the user talks over the reply
                await wait(1100); feed(0.001, 1);
                await wait(150);
                const stopped = (ttsEpoch > epoch0 && speaking === false && sent[0] === heard);
                try { bargeStop(); } catch (e) {}
                send = realSend; window.fetch = realFetch;
                activeAudio = null; voiceActive = false; speaking = false;
                return { armed: armed, heldThroughOurOwnVoice: heldThroughOurOwnVoice, pausedNotKilled: pausedNotKilled,
                         resumedOnNoise: resumedOnNoise, stopped: stopped, sent: sent };
            }"""
        )
        self.assertTrue(outcome["armed"], "barge-in never armed in server-STT mode")
        self.assertTrue(
            outcome["heldThroughOurOwnVoice"],
            "our own speech leaking into the mic triggered a barge-in",
        )
        # A loud moment only pauses: the recipe used to end at "cilant" and "G men." was
        # answered as a question.
        self.assertTrue(outcome["pausedNotKilled"], "a loud moment killed the reply instead of pausing it")
        self.assertTrue(outcome["resumedOnNoise"], "a garbled two-word transcript was answered: " + repr(outcome["sent"]))
        self.assertTrue(outcome["stopped"], "talking over the reply did not stop it: " + repr(outcome["sent"]))

    # A fake microphone for the server-STT voice paths, left on window.__rig. `feed` pushes
    # 4096-sample frames into whichever capture is listening and says whether one took
    # them - checked per frame, since a capture can stop part way through. /api/transcribe
    # answers with `rig.heard`, and send() is recorded rather than run.
    _VOICE_RIG = """() => {
        const FRAME = 4096, RATE = 48000;
        const rig = { proc: null, heard: '', sent: [] };
        navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {} }] });
        window.AudioContext = function () {
            this.sampleRate = RATE;
            this.createMediaStreamSource = () => ({ connect() {}, disconnect() {} });
            this.createGain = () => ({ gain: { value: 0 }, connect() {}, disconnect() {} });
            this.createScriptProcessor = () => { rig.proc = { onaudioprocess: null, connect() {}, disconnect() {} }; return rig.proc; };
            this.close = () => {};
        };
        window.webkitAudioContext = window.AudioContext;
        rig.feed = (peak, frames) => {
            let took = false;
            for (let i = 0; i < frames; i++) {
                if (!rig.proc || !rig.proc.onaudioprocess) break;
                const ch = new Float32Array(FRAME);
                for (let j = 0; j < FRAME; j++) ch[j] = (j % 2) ? peak : -peak;
                rig.proc.onaudioprocess({ inputBuffer: { getChannelData: () => ch } });
                took = true;
            }
            return took;
        };
        rig.wait = ms => new Promise(r => setTimeout(r, ms));
        // Sound, then the second of quiet that ends an utterance and sends it to be heard.
        rig.say = async (peak, frames) => {
            rig.feed(peak, frames);
            await rig.wait(1100); rig.feed(0.001, 1); await rig.wait(200);
        };
        const realFetch = window.fetch;
        window.fetch = async (url, init) => (String(url).includes('/api/transcribe')
            ? { ok: true, json: async () => (rig.reply || { ok: true, text: rig.heard }) }
            : realFetch(url, init));
        send = async (q) => { rig.sent.push(q); };
        sttServer = true; sttChosen = true; sttEngine = 'test-engine';
        // The page re-reads its engine from /api/health on load and every 12s. CI has no
        // engine, so an answer landing mid-test sent listen() and bargeStart() to the
        // browser recognizer and nothing reached this microphone; a laptop with an engine
        // never shows it. Keep the server path for the whole test.
        setSttEngine = () => {};
        window.__rig = rig;
    }"""

    def test_a_cough_is_not_an_interruption(self):
        """A loud moment only pauses the reply until its words are heard, but it counted
        toward the three-in-25s switch as if it had interrupted: three coughs during one
        reply switched voice interruption off for the session - silently, since the
        notice goes to the hidden voice panel. Only an interruption that goes through
        counts now. False pauses get their own limit instead: every sentence re-arms
        barge-in, so an echo that kept clearing the bar would pause the reply to its end."""
        self.page.evaluate(self._VOICE_RIG)
        outcome = self.page.evaluate(
            """async () => {
                const rig = window.__rig;
                const playing = () => ({ paused: false, currentTime: 0.5, src: '',
                    pause() { this.paused = true; }, play() { this.paused = false; return Promise.resolve(); } });
                voiceActive = true; speaking = true; bargeReset();
                let audio = activeAudio = playing();
                const coughs = [];
                for (let i = 0; i < 3; i++) {
                    bargeStart(); await rig.wait(60);   // each sentence arms barge-in again
                    rig.feed(0.06, 6);                  // our own voice, learned as the floor
                    const heard = rig.feed(0.35, 4);    // a cough, which transcribes as nothing
                    const paused = audio.paused;
                    await rig.say(0.001, 1);
                    coughs.push({ heard: heard, paused: paused, resumed: !audio.paused });
                }
                const offAfterCoughs = bargeOff;
                // The next reply, and this time the user really does cut in.
                voiceTurnReset(); speaking = true;      // what send() does as a voice turn starts
                audio = activeAudio = playing();
                bargeStart(); await rig.wait(60);
                rig.heard = 'stop and tell me the weather';
                rig.feed(0.06, 6);
                await rig.say(0.35, 4);
                const out = { coughs: coughs, offAfterCoughs: offAfterCoughs, sent: rig.sent.slice(),
                              stoppedForTheUser: speaking === false };
                try { bargeStop(); } catch (e) {}
                activeAudio = null; voiceActive = false; speaking = false;
                return out;
            }"""
        )
        coughs = outcome["coughs"]
        self.assertFalse(outcome["offAfterCoughs"], "coughs switched voice interruption off: " + repr(coughs))
        for cough in coughs[:2]:
            self.assertTrue(cough["paused"] and cough["resumed"], "a cough should pause the reply, then resume it: " + repr(coughs))
        self.assertFalse(
            coughs[2]["heard"] or coughs[2]["paused"],
            "a third false pause in one reply: the rest of the reply should play through: " + repr(coughs),
        )
        self.assertEqual(outcome["sent"], ["stop and tell me the weather"], "the next reply could not be interrupted")
        self.assertTrue(outcome["stoppedForTheUser"], "the interruption was sent but the reply kept speaking")

    def test_clicks_seconds_apart_are_not_speech(self):
        """Server-STT listening waits for a quarter second of sound before it treats the
        room as speech, but it added loud frames up across any gap, so three clicks
        seconds apart - typing, a mouse - reached 256ms, were transcribed ("Properly.")
        and answered. A quiet gap over 250ms now starts the count again, the rule
        barge-in already used."""
        self.page.evaluate(self._VOICE_RIG)
        outcome = self.page.evaluate(
            """async () => {
                const rig = window.__rig;
                speechEndedAt = performance.now() - 5000;   // nothing of ours is still in the air
                voiceActive = true; speaking = false; recognizing = false;
                rig.heard = 'Properly.';
                listen(); await rig.wait(60);
                const listening = rig.feed(0.001, 1);
                for (let i = 0; i < 3; i++) { rig.feed(0.3, 1); await rig.wait(400); rig.feed(0.001, 1); }
                await rig.say(0.001, 1);
                const afterClicks = rig.sent.slice();
                rig.heard = 'what is the weather tomorrow';
                await rig.say(0.3, 4);                      // the same loudness, held a third of a second
                const out = { listening: listening, afterClicks: afterClicks, afterSpeech: rig.sent.slice() };
                try { if (captureStop) captureStop(); } catch (e) {}
                voiceActive = false; recognizing = false;
                return out;
            }"""
        )
        self.assertTrue(outcome["listening"], "server-STT listening never opened the microphone")
        self.assertEqual(outcome["afterClicks"], [], "clicks seconds apart were answered as speech")
        self.assertEqual(outcome["afterSpeech"], ["what is the weather tomorrow"], "a real sentence was not heard")

    def test_server_listening_does_not_answer_our_own_words(self):
        """The browser recognizer always dropped a transcript that was really our own
        voice. The server-STT listening turn had no such check, so anything it caught of
        us - a reminder read aloud, the tail of a reply - was answered as the user."""
        self.page.evaluate(self._VOICE_RIG)
        outcome = self.page.evaluate(
            """async () => {
                const rig = window.__rig;
                speechEndedAt = performance.now() - 5000;
                voiceActive = true; speaking = false; recognizing = false;
                rememberSpoken('Your reminder: call the dentist about Thursday.');
                listen(); await rig.wait(60);
                rig.heard = 'your reminder call the dentist about thursday';
                await rig.say(0.3, 4);                      // us, heard back
                rig.heard = 'what time is it in london';
                await rig.say(0.3, 4);                      // the user, heard only if it listened again
                const out = { sent: rig.sent.slice() };
                try { if (captureStop) captureStop(); } catch (e) {}
                voiceActive = false; recognizing = false;
                return out;
            }"""
        )
        self.assertNotIn(
            "your reminder call the dentist about thursday", outcome["sent"],
            "our own reminder, heard back, was answered as the user",
        )
        self.assertEqual(outcome["sent"], ["what time is it in london"], "the user's own question was not heard")

    def test_server_listening_skips_the_tail_of_our_reply(self):
        """Speakers - Bluetooth ones especially - are still playing our last words for a
        few hundred ms after the browser reports playback ended. The browser path always
        ignored that window; the server-STT path heard it, and a tail garbled past the
        echo check ("Properly.") was answered."""
        self.page.evaluate(self._VOICE_RIG)
        outcome = self.page.evaluate(
            """async () => {
                const rig = window.__rig;
                voiceActive = true; speaking = false; recognizing = false;
                listen(); await rig.wait(60);
                speechEndedAt = performance.now();          // our reply has only just ended
                rig.heard = 'Properly.';
                await rig.say(0.3, 4);                      // ...and is still in the air
                const afterTail = rig.sent.slice();
                rig.heard = 'and what about tomorrow';
                await rig.say(0.3, 4);                      // the user, once it has passed
                const out = { afterTail: afterTail, afterUser: rig.sent.slice() };
                try { if (captureStop) captureStop(); } catch (e) {}
                voiceActive = false; recognizing = false;
                return out;
            }"""
        )
        self.assertEqual(outcome["afterTail"], [], "the tail of our own reply was answered as the user")
        self.assertEqual(outcome["afterUser"], ["and what about tomorrow"], "the user was not heard after the tail")

    def _magpie_tab(self, engine="riva:magpie", query=""):
        """The page as a browser tab sees it when /api/health names `engine` as the server's
        voice. The recognizer is inert (`window.__recs` holds each one made) and the speech
        engine stays the browser's, so nothing here touches a real microphone."""
        self.page.add_init_script("""
            window.SpeechRecognition = window.webkitSpeechRecognition = class {
                constructor() { (window.__recs = window.__recs || []).push(this); }
                start() {} stop() {} abort() {}
            };""")

        def health(route):
            response = route.fetch()
            body = response.json()
            body["tts"] = {"engine": engine}
            body["stt"] = {"engine": None}
            route.fulfill(response=response, json=body)

        self.page.route("**/api/health", health)
        self.page.goto(self.url + "/" + query)
        self.wait_js("e => ttsEngine === e", arg=engine)

    # /api/tts and the audio element, faked and left on window.__tab: each request is logged
    # as `ask <text>` and answered after `tab.delay` ms (503 for a text in `tab.fail`); each
    # audio element made is kept in `tab.audios` with the text it carries, and plays until
    # the test ends it. The browser's own voice logs `browser <text>` and ends at once.
    _MAGPIE_RIG = """() => {
        const tab = { log: [], audios: [], delay: 50, fail: new Set(), refuse: false };
        tab.wait = ms => new Promise(r => setTimeout(r, ms));
        const realFetch = window.fetch;
        window.fetch = (url, init) => {
            if (String(url).indexOf('/api/tts') < 0) return realFetch(url, init);
            const text = JSON.parse(init.body).text;
            tab.log.push('ask ' + text);
            return new Promise(r => setTimeout(() => r(tab.fail.has(text)
                ? new Response('{"ok":false}', { status: 503 })
                : new Response(text, { status: 200 })),
                typeof tab.delay === 'function' ? tab.delay(text) : tab.delay));
        };
        // Which sentence an audio element carries: its bytes are the text, read as the blob is made.
        const RealBlob = window.Blob, realCreate = URL.createObjectURL, named = {};
        window.Blob = function (parts, opts) {
            const blob = new RealBlob(parts, opts);
            try { blob.text_ = new TextDecoder().decode(parts[0]); } catch (e) {}
            return blob;
        };
        URL.createObjectURL = blob => { const url = realCreate(blob); named[url] = blob.text_; return url; };
        window.Audio = function (src) {
            const a = { src: src, text: named[src], paused: true, currentTime: 0, onended: null, onerror: null,
                play() {
                    if (tab.refuse) return Promise.reject(new DOMException('no gesture', 'NotAllowedError'));
                    if (this.currentTime === 0) tab.log.push('play ' + this.text);   // not a resume
                    this.paused = false; this.currentTime = 0.1;
                    return Promise.resolve();
                },
                pause() { this.paused = true; } };
            tab.audios.push(a);
            return a;
        };
        tab.end = i => { const a = tab.audios[i]; if (a && a.onended) a.onended(); };
        speechSynthesis.speak = u => { tab.log.push('browser ' + u.text); setTimeout(() => { if (u.onend) u.onend(); }, 5); };
        listen = () => { tab.log.push('listen'); };
        window.__tab = tab;
    }"""

    def test_a_tab_speaks_through_magpie_and_fetches_the_next_sentence_ahead(self):
        """A tab spoke with speechSynthesis even when the server had NVIDIA's Magpie, which
        only the app window used. Each Magpie sentence costs ~0.7s, so the next one is
        fetched while the current one plays instead of after it ends."""
        self._magpie_tab()
        self.page.evaluate(self._MAGPIE_RIG)
        outcome = self.page.evaluate(
            """async () => {
                const tab = window.__tab;
                voiceActive = true; voiceTurnReset();
                enqueueTTS('First sentence.'); enqueueTTS('Second sentence.'); enqueueTTS('Third sentence.');
                voiceTurnDone('', ttsEpoch);
                await tab.wait(200);
                const whilePlayingFirst = tab.log.slice();
                tab.end(0); await tab.wait(10);    // the second is already here: no wait for it
                const rightAfterFirst = tab.log.slice();
                tab.end(1); await tab.wait(200); tab.end(2); await tab.wait(1000);
                const out = { whilePlayingFirst, rightAfterFirst, log: tab.log.slice() };
                voiceActive = false;
                return out;
            }"""
        )
        self.assertEqual(outcome["whilePlayingFirst"], ["ask First sentence.", "ask Second sentence.", "play First sentence."])
        self.assertEqual(outcome["rightAfterFirst"][-2:], ["ask Third sentence.", "play Second sentence."],
                         "the second sentence waited to be fetched: " + repr(outcome["rightAfterFirst"]))
        self.assertEqual(outcome["log"], [
            "ask First sentence.", "ask Second sentence.", "play First sentence.", "ask Third sentence.",
            "play Second sentence.", "play Third sentence.", "listen"])

    def test_the_voice_follows_the_server_engine(self):
        """Magpie is used in a tab; the offline pyttsx3 voice is not (the browser's are
        better), so a tab keeps its own voice. The app window has no other voice at all."""
        cases = {}
        for engine, query in (("pyttsx3", ""), (None, "?app=1")):
            self._magpie_tab(engine, query)
            self.page.evaluate(self._MAGPIE_RIG)
            cases[(engine, query)] = self.page.evaluate(
                """async () => {
                    const tab = window.__tab;
                    voiceActive = true; voiceTurnReset();
                    enqueueTTS('Hello there.');
                    await tab.wait(150);
                    voiceActive = false;
                    return tab.log;
                }"""
            )
        self.assertEqual(cases[("pyttsx3", "")], ["browser Hello there."])
        self.assertEqual(cases[(None, "?app=1")], ["ask Hello there.", "play Hello there."])

    def test_a_sentence_magpie_cannot_voice_is_said_by_the_browser(self):
        """A failed /api/tts used to skip the sentence, which is all the app window can do.
        A tab has a voice of its own, so that one sentence is said in it, and the next goes
        back to Magpie. The same when the tab refuses to play audio at all."""
        self._magpie_tab()
        self.page.evaluate(self._MAGPIE_RIG)
        outcome = self.page.evaluate(
            """async () => {
                const tab = window.__tab;
                tab.fail.add('Second sentence.');
                voiceActive = true; voiceTurnReset();
                enqueueTTS('First sentence.'); enqueueTTS('Second sentence.'); enqueueTTS('Third sentence.');
                await tab.wait(200); tab.end(0);
                await tab.wait(200); tab.end(1);
                await tab.wait(200);
                const failed = tab.log.filter(l => !l.startsWith('ask '));
                tab.log.length = 0; tab.refuse = true; voiceTurnReset();
                enqueueTTS('Autoplay is refused.');
                await tab.wait(200);
                const refused = tab.log.filter(l => !l.startsWith('ask '));
                voiceActive = false;
                return { failed, refused, speaking };
            }"""
        )
        self.assertEqual(outcome["failed"], ["play First sentence.", "browser Second sentence.", "play Third sentence."])
        self.assertEqual(outcome["refused"], ["browser Autoplay is refused."])

    def test_a_stop_drops_hosted_audio_already_on_its_way(self):
        """Space cleared the queue and moved the epoch on, but the sentence whose audio was
        still being fetched was not checked against either, so it played after the stop,
        over the listening turn. Holds in the app window too, which had the same gap."""
        for query in ("", "?app=1"):
            with self.subTest(window=query or "tab"):
                self._magpie_tab("riva:magpie", query)
                self.page.evaluate(self._MAGPIE_RIG)
                outcome = self.page.evaluate(
                    """async () => {
                        const tab = window.__tab;
                        voiceActive = true; voiceTurnReset();
                        tab.delay = 200;
                        enqueueTTS('A sentence still being made.');
                        await tab.wait(50);
                        interruptNow();                   // Space, while its audio is on the way
                        await tab.wait(400);
                        const duringFetch = tab.log.filter(l => l.startsWith('play') || l.startsWith('browser'));
                        tab.delay = 30; voiceTurnReset();
                        enqueueTTS('One sentence playing.'); enqueueTTS('The next, already fetched.');
                        await tab.wait(150);
                        const playing = tab.audios[tab.audios.length - 1];
                        interruptNow();                   // Space, while it plays
                        await tab.wait(300);
                        const out = { duringFetch, paused: playing.paused, speaking,
                                      played: tab.log.filter(l => l.startsWith('play') || l.startsWith('browser')) };
                        voiceActive = false;
                        return out;
                    }"""
                )
                self.assertEqual(outcome["duringFetch"], [], "audio fetched before the stop was played after it")
                self.assertTrue(outcome["paused"])
                self.assertFalse(outcome["speaking"])
                self.assertEqual(outcome["played"], ["play One sentence playing."])

    def test_talking_over_magpie_in_a_tab_stops_it(self):
        """Barge-in both ways while Magpie plays in a tab: the browser recognizer, and the
        server-STT microphone level, which pauses the audio element and resumes it on noise.
        The sentence fetched ahead must not play after either."""
        self._magpie_tab()
        self.page.evaluate(self._MAGPIE_RIG)
        recognizer = self.page.evaluate(
            """async () => {
                const tab = window.__tab, sent = [];
                send = async q => { sent.push(q); };
                voiceActive = true; voiceTurnReset(); bargeReset();
                enqueueTTS('The first sentence of a long answer.'); enqueueTTS('A second that must never play.');
                await tab.wait(150);
                barge.onresult({ results: [[{ transcript: 'stop and tell me the weather' }]] });
                await tab.wait(400);
                const out = { paused: tab.audios[0].paused, speaking, sent,
                              played: tab.log.filter(l => l.startsWith('play') || l.startsWith('browser')) };
                voiceActive = false;
                return out;
            }"""
        )
        self.assertEqual(recognizer["sent"], ["stop and tell me the weather"])
        self.assertTrue(recognizer["paused"])
        self.assertFalse(recognizer["speaking"])
        self.assertEqual(recognizer["played"], ["play The first sentence of a long answer."])

        self._magpie_tab()
        self.page.evaluate(self._VOICE_RIG)
        self.page.evaluate(self._MAGPIE_RIG)
        level = self.page.evaluate(
            """async () => {
                const rig = window.__rig, tab = window.__tab;
                voiceActive = true; voiceTurnReset(); bargeReset();
                enqueueTTS('Here is the first part of the recipe.'); enqueueTTS('And this part must never play.');
                await rig.wait(150);
                const audio = tab.audios[0];
                rig.feed(0.06, 6);                       // our own voice, learned as the floor
                rig.feed(0.35, 4);                       // something loud
                const paused = audio.paused && speaking;
                rig.heard = 'G men.'; await rig.say(0.001, 1);
                const resumed = !audio.paused && speaking;
                await rig.wait(80);
                rig.heard = 'stop and tell me the weather';
                rig.feed(0.06, 6); await rig.say(0.35, 4);
                await rig.wait(300);
                const out = { paused, resumed, stopped: audio.paused && !speaking, sent: rig.sent.slice(),
                              played: tab.log.filter(l => l.startsWith('play') || l.startsWith('browser')) };
                try { bargeStop(); } catch (e) {}
                voiceActive = false;
                return out;
            }"""
        )
        self.assertTrue(level["paused"], "a loud moment did not pause Magpie's audio")
        self.assertTrue(level["resumed"], "noise did not resume the reply")
        self.assertTrue(level["stopped"], "talking over the reply did not stop it")
        self.assertEqual(level["sent"], ["stop and tell me the weather"])
        self.assertEqual(level["played"], ["play Here is the first part of the recipe."])

    def test_a_real_hosted_reply_plays_to_the_end_in_a_tab(self):
        """End to end in Chromium, nothing faked on the page: the real /api/tts returns a
        real WAV and a real audio element plays each sentence through and moves on."""
        import wave

        asked = []

        def backend(text):
            asked.append(text)
            out = io.BytesIO()
            with wave.open(out, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(22050)
                wav.writeframes(b"\x00\x08" * 5512)     # a quarter second
            return out.getvalue()

        with patch.object(webui, "_TTS_BACKEND", backend):
            self._magpie_tab()
            self.page.locator("#ta").click()            # a real gesture, as clicking Voice would be
            outcome = self.page.evaluate(
                """async () => {
                    const log = [];
                    speechSynthesis.speak = u => { log.push('browser ' + u.text); setTimeout(() => { if (u.onend) u.onend(); }, 5); };
                    listen = () => { log.push('listen'); };
                    voiceActive = true; voiceTurnReset();
                    enqueueTTS('First sentence.'); enqueueTTS('Second sentence.');
                    voiceTurnDone('', ttsEpoch);
                    const t0 = performance.now();
                    while (!log.includes('listen') && performance.now() - t0 < 5000) await new Promise(r => setTimeout(r, 25));
                    voiceActive = false;
                    return { log, ms: Math.round(performance.now() - t0) };
                }"""
            )
        # Both requests are out at once (the second is fetched ahead), so the threaded server may
        # take either first; the order they PLAY in is the queue's, held by the fake-rig test.
        self.assertEqual(sorted(asked), ["First sentence.", "Second sentence."])
        self.assertEqual(outcome["log"], ["listen"], "a sentence did not play through: " + repr(outcome))

    def test_long_tool_reply_uses_server_chunks_with_prefetch_and_browser_fallback(self):
        """A real voice SSE turn hands each final-result sentence to the existing page queue."""
        first = "The first section records the work completed and the tests that passed."
        second = "The second section explains the remaining risk and who will review it."
        third = "The third section gives the next action and the expected user result."
        fourth = "The final section confirms that every relevant sentence was heard in order."
        reply = " ".join((first, second, third, fourth, first, third))

        async def instant(*args, **kwargs):
            return ToolResult.success(reply)

        self._magpie_tab()
        self.page.evaluate(self._MAGPIE_RIG)
        with patch.object(webui._orchestrator, "handle", instant):
            outcome = self.page.evaluate(
                """async ({reply, second}) => {
                    const tab=window.__tab;
                    tab.delay=text=>20+2*text.length;
                    tab.fail.add(second);
                    voiceActive=true;
                    const ending=setInterval(()=>{
                      for(const a of tab.audios){
                        if(!a.paused&&!a.ended){a.ended=true;setTimeout(()=>{if(a.onended)a.onended();},70);}
                      }
                    },5);
                    const t0=performance.now();
                    const turn=send('offline tool result');
                    while(!tab.log.some(l=>l.startsWith('play '))&&performance.now()-t0<5000)
                      await tab.wait(5);
                    const firstMs=Math.round(performance.now()-t0), duringFirst=tab.log.slice();
                    await turn;
                    while(!tab.log.includes('listen')&&performance.now()-t0<9000)await tab.wait(10);
                    clearInterval(ending);
                    const spoken=tab.log.filter(l=>l.startsWith('play ')||l.startsWith('browser '))
                                        .map(l=>l.replace(/^(play|browser) /,''));
                    const result={firstMs, duringFirst, spoken, log:tab.log.slice()};
                    voiceActive=false;return result;
                }""",
                {"reply": reply, "second": second},
            )
        self.assertLess(outcome["firstMs"], 800, outcome)
        self.assertEqual([entry for entry in outcome["log"] if entry.startswith("ask ")],
                         ["ask " + sentence for sentence in (first, second, third, fourth, first, third)],
                         "the final done message was synthesized again")
        self.assertIn("ask " + second, outcome["duringFirst"], "the next sentence was not prefetched")
        self.assertIn("browser " + second, outcome["log"], "a failed sentence lost its fallback")
        self.assertEqual(" ".join(outcome["spoken"]).split(), reply.split())

    def test_stop_drops_a_long_tool_reply_whose_first_chunk_is_still_fetching(self):
        reply = "A long tool result should be spoken sentence by sentence. " * 12

        async def instant(*args, **kwargs):
            return ToolResult.success(reply)

        self._magpie_tab()
        self.page.evaluate(self._MAGPIE_RIG)
        with patch.object(webui._orchestrator, "handle", instant):
            outcome = self.page.evaluate(
                """async () => {
                    const tab=window.__tab;
                    tab.delay=300;
                    voiceActive=true;
                    const turn=send('offline tool result');
                    while(!tab.log.some(l=>l.startsWith('ask ')))await tab.wait(5);
                    interruptNow();
                    await turn;await tab.wait(450);
                    const result={log:tab.log.slice(), speaking, queued:ttsQueue.length};
                    voiceActive=false;return result;
                }"""
            )
        self.assertTrue(any(entry.startswith("ask ") for entry in outcome["log"]))
        self.assertFalse(any(entry.startswith(("play ", "browser ")) for entry in outcome["log"]))
        self.assertFalse(outcome["speaking"])
        self.assertEqual(outcome["queued"], 0)

    # The voice notice, if one is showing: its text, and whether it is really on screen -
    # a box, inside the window, and not painted over by anything else.
    _VOICE_NOTICE = """() => {
        const card = document.querySelector('#remtray [data-voice-notice]');
        if (!card) return null;
        const r = card.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { text: card.textContent, height: r.height,
                 inside: r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight,
                 uncovered: !!hit && card.contains(hit),
                 count: document.querySelectorAll('#remtray [data-voice-notice]').length };
    }"""

    def test_a_voice_notice_is_on_screen_in_every_layout(self):
        """Voice notices were written into #vtrans, inside a panel that has been display:none
        since June, so voice interruption switching itself off told nobody. They go to the
        reminder tray now, which is fixed to the window. Asserted as a real, uncovered box in
        every layout - not `hidden === false`, which is how the meter shipped invisible."""
        self.page.evaluate("() => { voiceActive = true; bargeReset(); bargeAllowed(); bargeAllowed(); bargeAllowed(); voiceActive = false; }")
        layouts = (
            ("1440", {"width": 1440, "height": 950}, ""),
            ("1440 compact", {"width": 1440, "height": 950}, "setCompact(true)"),
            ("1440 orb focus", {"width": 1440, "height": 950}, "setCompact(false); setOrbFocus(true, false, true)"),
            ("1000", {"width": 1000, "height": 800}, "setOrbFocus(false, false, true)"),
            ("700", {"width": 700, "height": 900}, ""),
            ("390", {"width": 390, "height": 844}, ""),
        )
        for name, size, setup in layouts:
            with self.subTest(layout=name):
                self.page.set_viewport_size(size)
                if setup:
                    self.page.evaluate("() => { " + setup + "; }")
                seen = self.page.evaluate(self._VOICE_NOTICE)
                self.assertIsNotNone(seen, name + ": voice interruption switched off with no notice at all")
                self.assertIn("voice interruption is off", seen["text"])
                self.assertGreater(seen["height"], 0, name + ": the notice has no box on screen")
                self.assertTrue(seen["inside"], name + ": the notice runs off the window")
                self.assertTrue(seen["uncovered"], name + ": something is drawn over the notice")

        self.page.set_viewport_size({"width": 1440, "height": 950})
        self.page.evaluate("() => { voiceNotice('first'); voiceNotice('second'); }")
        self.assertEqual(self.page.evaluate(self._VOICE_NOTICE)["count"], 1, "notices stacked instead of replacing")
        self.page.click("#remtray [data-voice-notice] .apbtn")
        self.assertIsNone(self.page.evaluate(self._VOICE_NOTICE), "Dismiss left the notice up")
        self.page.evaluate("() => { voiceNotice('stale'); bargeReset(); }")
        self.assertIsNone(self.page.evaluate(self._VOICE_NOTICE),
                          "restarting voice, or Space, left a notice that no longer holds")

    def test_a_blocked_microphone_says_so(self):
        """The server-STT listening turn wrote "Microphone permission is needed" into the
        hidden panel, and voice just went quiet."""
        self.page.evaluate(self._VOICE_RIG)
        self.page.evaluate("""async () => {
            navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('denied', 'NotAllowedError'); };
            voiceActive = true; speaking = false; recognizing = false;
            listen(); await window.__rig.wait(60);
            voiceActive = false; recognizing = false;
        }""")
        seen = self.page.evaluate(self._VOICE_NOTICE)
        self.assertIsNotNone(seen, "a blocked microphone left no notice on screen")
        self.assertIn("Microphone permission is needed for voice.", seen["text"])
        self.assertGreater(seen["height"], 0)

    def test_a_broken_speech_engine_says_so_and_silence_does_not(self):
        """/api/transcribe answers `ok: false` both when it heard nothing and when there is
        no engine at all. Only the second deserves a notice - surfacing every `ok: false`
        would put a card up after every quiet moment."""
        self.page.evaluate(self._VOICE_RIG)
        outcome = self.page.evaluate("""async () => {
            const rig = window.__rig;
            speechEndedAt = performance.now() - 5000;
            voiceActive = true; speaking = false; recognizing = false;
            rig.reply = { ok: false, text: '', message: 'Transcribed voice.wav: no speech found.', failed: false };
            listen(); await rig.wait(60);
            await rig.say(0.3, 4);                          // heard nothing
            const afterSilence = !!document.querySelector('#remtray [data-voice-notice]');
            rig.reply = { ok: false, text: '', message: 'Speech-to-text needs an engine: pip install laptop-agent[stt]', failed: true };
            await rig.say(0.3, 4);                          // there is no engine
            const out = { afterSilence: afterSilence, sent: rig.sent.slice() };
            try { if (captureStop) captureStop(); } catch (e) {}
            voiceActive = false; recognizing = false;
            return out;
        }""")
        self.assertFalse(outcome["afterSilence"], "hearing nothing put up a notice")
        seen = self.page.evaluate(self._VOICE_NOTICE)
        self.assertIsNotNone(seen, "a missing speech engine left no notice on screen")
        self.assertIn("pip install", seen["text"])
        self.assertEqual(outcome["sent"], [], "a failed transcription was answered")

    def test_the_browser_recognizer_says_why_voice_stopped(self):
        """A blocked microphone ends voice mode, and the only sign was the Voice pill
        turning off - the reason went to the hidden panel."""
        # SR is captured when the page loads, so the fake has to exist before the script runs.
        self.page.add_init_script("""
            window.SpeechRecognition = window.webkitSpeechRecognition = class {
                start() { if (window.__recThrows) throw new Error('the device is busy'); }
                stop() {} abort() {}
            };""")
        self.page.reload()
        outcome = self.page.evaluate("""() => {
            sttServer = false; sttChosen = true;            // the browser recognizer, whatever /api/health says
            voiceActive = true; speaking = false; recognizing = false;
            listen();
            rec.onerror({ error: 'not-allowed' });
            const blocked = document.querySelector('#remtray [data-voice-notice]');
            const out = { blocked: blocked ? blocked.textContent : null, endedVoice: voiceActive === false };
            voiceNotice('');                                // so the next notice has to be the new one
            window.__recThrows = true;
            voiceActive = true; recognizing = false;
            listen();
            const busy = document.querySelector('#remtray [data-voice-notice]');
            out.busy = busy ? busy.textContent : null;
            voiceActive = false; recognizing = false;
            return out;
        }""")
        self.assertIsNotNone(outcome["blocked"], "a blocked microphone ended voice with no notice")
        self.assertIn("Microphone blocked", outcome["blocked"])
        self.assertTrue(outcome["endedVoice"])
        self.assertIsNotNone(outcome["busy"], "a microphone that would not start left no notice")
        self.assertIn("Could not start the microphone: the device is busy", outcome["busy"])

    def test_voice_panel_shows_the_microphone_level_against_the_threshold(self):
        """Barge-in was fixed twice and still reported as not working, because the level it
        needs was a constant inside a closure — nobody could see what the microphone heard.
        The voice panel now meters it live, and the floor under the threshold is a slider,
        so the number can be tuned from the room instead of from source."""
        outcome = self.page.evaluate(
            """async () => {
                const FRAME = 4096, RATE = 48000;
                navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {} }] });
                let proc = null;
                window.AudioContext = function () {
                    this.sampleRate = RATE;
                    this.createMediaStreamSource = () => ({ connect() {}, disconnect() {} });
                    this.createGain = () => ({ gain: { value: 0 }, connect() {}, disconnect() {} });
                    this.createScriptProcessor = () => { proc = { onaudioprocess: null, connect() {}, disconnect() {} }; return proc; };
                    this.close = () => {};
                };
                window.webkitAudioContext = window.AudioContext;
                // One frame every ~85ms, the cadence a 4096-sample buffer really arrives at.
                // Fed back to back the meter's paint throttle would swallow all but the first.
                const feed = async (peak, frames) => {
                    for (let i = 0; i < frames; i++) {
                        const ch = new Float32Array(FRAME);
                        for (let j = 0; j < FRAME; j++) ch[j] = (j % 2) ? peak : -peak;
                        proc.onaudioprocess({ inputBuffer: { getChannelData: () => ch } });
                        await new Promise(r => setTimeout(r, 90));
                    }
                };
                const box = document.getElementById('vmeter');
                const now = () => document.getElementById('vmnow').textContent;
                const trig = () => document.getElementById('vmtrig').textContent;
                const width = () => document.getElementById('vmfill').style.width;
                const over = () => document.getElementById('vmfill').parentNode.classList.contains('over');

                // The slider sets the floor, and says so in the popover.
                const range = document.getElementById('bargeRange');
                range.value = '80';
                range.dispatchEvent(new Event('input'));
                const label = document.getElementById('bargeVal').textContent;

                // On screen, not merely hidden=false. The meter used to live inside
                // #voice, which has been display:none since f6a145d dropped the written
                // overlay — so it reported itself shown while rendering nothing at all,
                // and this test passed throughout.
                const onScreen = () => box.getBoundingClientRect().height > 0
                    && getComputedStyle(box).visibility !== 'hidden';

                const hiddenBefore = box.hidden;
                sttServer = true; sttChosen = true; sttEngine = 'test-engine';
                voiceActive = true; speaking = true; bargeReset();
                bargeStart();
                await new Promise(r => setTimeout(r, 60));
                if (!proc) { voiceActive = false; speaking = false;
                    return { armed: false }; }
                const shownWhileArmed = !box.hidden;
                const paintedWhileArmed = onScreen();
                const threshold = trig();

                await feed(0.02, 6);                 // our own voice, learned as the leak
                const quiet = { now: now(), width: width(), over: over() };
                await feed(0.30, 1);                 // someone talking, but not yet 220ms
                const loud = { now: now(), width: width(), over: over() };

                try { bargeStop(); } catch (e) {}
                const hiddenAfter = box.hidden;
                voiceActive = false; speaking = false;
                return { armed: true, label: label, threshold: threshold,
                         hiddenBefore: hiddenBefore, shownWhileArmed: shownWhileArmed,
                         paintedWhileArmed: paintedWhileArmed, paintedAfter: onScreen(),
                         hiddenAfter: hiddenAfter, quiet: quiet, loud: loud };
            }"""
        )
        self.assertTrue(outcome["armed"], "barge-in never armed in server-STT mode")
        self.assertTrue(outcome["hiddenBefore"], "the meter is on screen when nothing is listening")
        self.assertTrue(outcome["shownWhileArmed"], "the meter stayed hidden while barge-in listened")
        self.assertTrue(outcome["paintedWhileArmed"],
                        "the meter reported itself shown but rendered nothing on screen")
        self.assertFalse(outcome["paintedAfter"], "the meter was still drawn after the microphone went")
        self.assertTrue(outcome["hiddenAfter"], "the meter outlived the microphone")
        self.assertEqual(outcome["label"], "0.080", "the slider does not report the level it set")
        self.assertEqual(
            outcome["threshold"], "0.080",
            "the meter shows a threshold the slider did not set: " + repr(outcome["threshold"]),
        )
        self.assertEqual(outcome["quiet"]["now"], "0.020", "the meter misreports a quiet room")
        self.assertFalse(outcome["quiet"]["over"], "our own leakage was shown as loud enough to cut in")
        self.assertEqual(outcome["loud"]["now"], "0.300", "the meter misreports a raised voice")
        self.assertTrue(outcome["loud"]["over"], "a voice well over the threshold was not shown as over it")
        self.assertGreater(
            float(outcome["loud"]["width"].rstrip("%")),
            float(outcome["quiet"]["width"].rstrip("%")),
            "the bar did not grow when the room got louder",
        )

    def test_the_microphone_meter_is_readable_where_the_presence_panel_is_not(self):
        """#121 finally put the meter on screen — at the foot of `.stage`. But `.stage` is
        display:none under `body.compact` and at both width breakpoints, so the number the
        0.045 barge-in floor has to be tuned against was still unreadable on a small laptop,
        on a phone, and for anyone using the compact-layout toggle. The dock is fixed to the
        viewport now and moves to sit above the composer wherever the presence panel is not
        on screen. Asserts a real box, not `hidden === false`: an element inside a
        display:none parent reports `hidden` as false, which is exactly how the meter
        shipped invisible for three months."""
        armed = self.page.evaluate(
            """async () => {
                navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {} }] });
                let proc = null;
                window.AudioContext = function () {
                    this.sampleRate = 48000;
                    this.createMediaStreamSource = () => ({ connect() {}, disconnect() {} });
                    this.createGain = () => ({ gain: { value: 0 }, connect() {}, disconnect() {} });
                    this.createScriptProcessor = () => { proc = { onaudioprocess: null, connect() {}, disconnect() {} }; return proc; };
                    this.close = () => {};
                };
                window.webkitAudioContext = window.AudioContext;
                sttServer = true; sttChosen = true; sttEngine = 'test-engine';
                voiceActive = true; speaking = true; bargeReset(); bargeStart();
                await new Promise(r => setTimeout(r, 80));
                return !!proc;
            }"""
        )
        self.assertTrue(armed, "barge-in never armed in server-STT mode")
        geometry = """() => {
            const meter = document.getElementById('vmeter');
            const box = document.querySelector('.composer .box');
            const m = meter.getBoundingClientRect(), b = box.getBoundingClientRect();
            return { height: m.height, width: m.width, top: m.top, bottom: m.bottom,
                     left: m.left, right: m.right, boxTop: b.top,
                     visibility: getComputedStyle(meter).visibility,
                     stage: getComputedStyle(document.querySelector('.stage')).display,
                     stageRect: document.querySelector('.stage').getBoundingClientRect().toJSON() };
        }"""
        try:
            # Every layout that takes the presence panel away: the compact toggle, the
            # tablet breakpoint and a phone.
            for label, width, height, compact in (
                ("compact toggle", 1440, 950, True),
                ("tablet width", 1000, 900, False),
                ("phone width", 390, 844, False),
            ):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.evaluate("on => setCompact(on)", arg=compact)
                self.page.wait_for_timeout(120)
                seen = self.page.evaluate(geometry)
                self.assertEqual(seen["stage"], "none",
                                 f"{label}: the presence panel is on screen, so this proves nothing")
                self.assertGreater(seen["height"], 0, f"{label}: the meter has no box on screen")
                self.assertGreater(seen["width"], 0, f"{label}: the meter has no box on screen")
                self.assertNotEqual(seen["visibility"], "hidden", f"{label}: the meter is invisible")
                # Readable means not sitting on top of the composer, and not off the edge.
                self.assertLessEqual(seen["bottom"], seen["boxTop"] + 1,
                                     f"{label}: the meter covers the composer")
                self.assertGreaterEqual(seen["left"], 0, f"{label}: the meter runs off the left edge")
                self.assertLessEqual(seen["right"], width, f"{label}: the meter runs off the right edge")
                self.assertGreater(seen["top"], 0, f"{label}: the meter is above the top of the window")
            # …and the default layout still reads it over the presence panel, where #121 put it.
            self.page.set_viewport_size({"width": 1440, "height": 950})
            self.page.evaluate("setCompact(false)")
            self.page.wait_for_timeout(120)
            wide = self.page.evaluate(geometry)
            self.assertNotEqual(wide["stage"], "none", "the presence panel vanished at 1440px")
            self.assertGreater(wide["height"], 0, "the meter lost its box over the presence panel")
            self.assertGreaterEqual(wide["left"], wide["stageRect"]["x"],
                                    "the meter no longer sits over the presence panel")
            self.assertLessEqual(wide["right"], wide["stageRect"]["x"] + wide["stageRect"]["width"],
                                 "the meter spilled out of the presence panel and over the chat")
        finally:
            self.page.evaluate("try{bargeStop();}catch(e){} voiceActive=false; speaking=false;")

    def _motion_page(self):
        """A page that actually animates. The shared context is reduced_motion="reduce",
        where orb focus deliberately jumps straight to the end state."""
        ctx = self.browser.new_context(viewport={"width": 1440, "height": 950},
                                       reduced_motion="no-preference")
        ctx.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        page = ctx.new_page()
        page.goto(self.url)
        self.addCleanup(ctx.close)
        return page

    # Records the drawn sphere per frame: drawSphere clears the canvas, then plots every
    # particle with arc(), so the spread of those x values is the sphere's width on screen
    # and their midpoint is its centre. That is the thing the eye follows, and the only
    # thing that actually animates — the layout underneath snaps.
    _TRACK_ORB = """() => {
        const cv = document.getElementById('core'), ctx = cv.getContext('2d');
        const arc = ctx.arc.bind(ctx), clear = ctx.clearRect.bind(ctx);
        let lo = 1e9, hi = -1e9;
        window.__frames = [];
        ctx.arc = (x, y, r, a, b) => { if (x < lo) lo = x; if (x > hi) hi = x; return arc(x, y, r, a, b); };
        ctx.clearRect = (a, b, c, d) => {
            if (hi > -1e9) window.__frames.push({ w: hi - lo, cx: (hi + lo) / 2 });
            lo = 1e9; hi = -1e9; return clear(a, b, c, d);
        };
    }"""

    def test_orb_focus_hides_the_chat_and_grows_the_orb_smoothly(self):
        """Hiding the chat has to grow the orb into the window, and back again. Chromium
        will not interpolate this grid (measured: the stage jumped 374px -> 1440px in one
        frame with a 500ms transition on it), so the sphere's centre and radius are eased
        in the canvas loop instead. This asserts it passes through the middle rather than
        cutting to the end."""
        page = self._motion_page()
        page.evaluate(self._TRACK_ORB)
        page.wait_for_timeout(200)
        outcome = page.evaluate(
            """async () => {
                const wait = ms => new Promise(r => setTimeout(r, ms));
                const last = () => window.__frames[window.__frames.length - 1];
                const stage = document.querySelector('.stage');
                const btn = document.getElementById('orbBtn');
                const docked = last();
                const dockedPos = getComputedStyle(stage).position;

                btn.click();
                const mark = window.__frames.length;
                await wait(900);
                const focused = last();
                const focusedPos = getComputedStyle(stage).position;
                const during = window.__frames.slice(mark, window.__frames.length - 2);
                const chatHidden = getComputedStyle(document.querySelector('main.chatcol')).opacity;
                const railHidden = getComputedStyle(document.querySelector('.left')).opacity;

                btn.click();
                await wait(900);
                const back = last();

                // frames strictly between the two end states, on width and on centre
                const lo = docked.w + (focused.w - docked.w) * 0.15;
                const hi = docked.w + (focused.w - docked.w) * 0.85;
                const tween = during.filter(f => f.w > lo && f.w < hi).length;
                const jumps = [];
                for (let i = 1; i < during.length; i++) jumps.push(Math.abs(during[i].w - during[i-1].w));
                return {
                    docked: docked, focused: focused, back: back,
                    dockedPos: dockedPos, focusedPos: focusedPos,
                    chatHidden: chatHidden, railHidden: railHidden,
                    tween: tween, frames: during.length,
                    biggestJump: jumps.length ? Math.max.apply(null, jumps) : 0,
                    classAfter: document.body.classList.contains('orbfocus'),
                };
            }"""
        )
        self.assertEqual(outcome["dockedPos"], "relative", "the stage was already an overlay")
        self.assertEqual(outcome["focusedPos"], "fixed", "the stage did not take over the window")
        self.assertEqual(outcome["chatHidden"], "0", "the chat is still visible in orb focus")
        self.assertEqual(outcome["railHidden"], "0", "the rail is still visible in orb focus")
        self.assertGreater(
            outcome["focused"]["w"], outcome["docked"]["w"] * 1.6,
            "the orb barely grew: " + repr((outcome["docked"], outcome["focused"])),
        )
        # Three, not six: this counts rendered frames, so it scales with whatever frame
        # rate the machine manages. A 60fps desktop puts ~13 frames in this band; the CI
        # runner draws ~34fps and put 5 there, failing a threshold tuned on a laptop.
        # Cutting straight to the end gives 0, so 3 still separates the two decisively,
        # and the largest-jump assertion below is the real guard on smoothness.
        self.assertGreaterEqual(
            outcome["tween"], 3,
            "the orb cut to its new size instead of easing there — only "
            + str(outcome["tween"]) + " intermediate frames of " + str(outcome["frames"]),
        )
        self.assertLess(
            outcome["biggestJump"], (outcome["focused"]["w"] - outcome["docked"]["w"]) * 0.5,
            "one frame moved most of the distance, so the growth is not smooth",
        )
        self.assertFalse(outcome["classAfter"], "orb focus did not come back off")
        self.assertLess(
            abs(outcome["back"]["w"] - outcome["docked"]["w"]), outcome["docked"]["w"] * 0.15,
            "the orb did not return to its docked size: " + repr((outcome["docked"], outcome["back"])),
        )

    def test_orb_focus_lands_even_when_no_frame_is_ever_drawn(self):
        """requestAnimationFrame is throttled to nothing when the window is occluded
        (measured in a real embedded pane: 0 frames in 300ms with visibilityState still
        'visible'). The easing runs in the canvas loop, so without a timer of its own the
        class would stay on with the chat faded to zero and no way back."""
        page = self._motion_page()
        outcome = page.evaluate(
            """async () => {
                const wait = ms => new Promise(r => setTimeout(r, ms));
                const btn = document.getElementById('orbBtn');
                const real = window.requestAnimationFrame;
                window.requestAnimationFrame = () => 0;      // nothing will be drawn again
                await wait(80);
                btn.click();
                await wait(900);
                const onClass = document.body.classList.contains('orbfocus');
                btn.click();
                await wait(900);
                const offClass = document.body.classList.contains('orbstage');
                // The class comes off when the orb lands; the chat then fades back over
                // its own transition, so wait for that rather than guessing a total.
                const col = document.querySelector('main.chatcol');
                let chat = getComputedStyle(col).opacity;
                for (let i = 0; i < 40 && chat !== '1'; i++) { await wait(50); chat = getComputedStyle(col).opacity; }
                window.requestAnimationFrame = real;
                return { onClass: onClass, offClass: offClass, chat: chat };
            }"""
        )
        self.assertTrue(outcome["onClass"], "orb focus never engaged")
        self.assertFalse(
            outcome["offClass"],
            "the overlay was stuck on with no frames to end it",
        )
        self.assertEqual(outcome["chat"], "1", "the chat never came back")

    def test_leaving_the_chat_view_while_focused_does_not_strand_the_page(self):
        """The other views hide the stage, so the canvas loop stops and the easing would
        never finish — leaving the body class on and the chat at opacity 0 on a page with
        no orb to explain why. Switching away has to land the layout immediately."""
        page = self._motion_page()
        outcome = page.evaluate(
            """async () => {
                const wait = ms => new Promise(r => setTimeout(r, ms));
                document.getElementById('orbBtn').click();
                await wait(700);
                const focused = document.body.classList.contains('orbfocus');
                location.hash = '#/overview';
                await wait(300);
                const away = { cls: document.body.className,
                               chat: getComputedStyle(document.querySelector('main.chatcol')).opacity,
                               btn: document.getElementById('orbBtn').style.display };
                location.hash = '#/chat';
                await wait(700);
                const col = document.querySelector('main.chatcol');
                let chat = getComputedStyle(col).opacity;
                for (let i = 0; i < 40 && chat !== '1'; i++) { await wait(50); chat = getComputedStyle(col).opacity; }
                return { focused: focused, away: away, backCls: document.body.className, backChat: chat };
            }"""
        )
        self.assertTrue(outcome["focused"], "orb focus never engaged")
        self.assertEqual(
            outcome["away"]["cls"], "",
            "orb focus survived a view switch that hides the orb: " + repr(outcome["away"]),
        )
        self.assertEqual(outcome["away"]["btn"], "none", "the orb button is offered on a view with no orb")
        self.assertEqual(outcome["backCls"], "", "orb focus came back on by itself")
        self.assertEqual(outcome["backChat"], "1", "the chat stayed hidden after returning to it")

    def test_escape_leaves_orb_focus(self):
        """Orb focus hides the chat and the rail, so the only things on screen are the orb
        and the header. Esc is the habitual way out of a mode, and it must not need the
        user to find the one small header button again."""
        page = self._motion_page()
        outcome = page.evaluate(
            """async () => {
                const wait = ms => new Promise(r => setTimeout(r, ms));
                document.getElementById('orbBtn').click();
                await wait(700);
                const before = document.body.classList.contains('orbfocus');
                document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                await wait(900);
                const col = document.querySelector('main.chatcol');
                let chat = getComputedStyle(col).opacity;
                for (let i = 0; i < 40 && chat !== '1'; i++) { await wait(50); chat = getComputedStyle(col).opacity; }
                let saved = null; try { saved = localStorage.getItem('hudOrbFocus'); } catch (e) {}
                return { before: before, after: document.body.className, chat: chat, saved: saved };
            }"""
        )
        self.assertTrue(outcome["before"], "orb focus never engaged")
        self.assertEqual(outcome["after"], "", "Escape did not leave orb focus")
        self.assertEqual(outcome["chat"], "1", "the chat did not come back")
        self.assertEqual(outcome["saved"], "0", "leaving by Escape did not stick for the next load")

    def test_leaving_orb_focus_costs_the_same_as_entering_it(self):
        """One class used to carry both the intent and the overlay, and it only came off
        once the sphere had landed. Measured: entering faded the chat out over 500ms, but
        leaving sat still for 520ms and only then brought it back, finishing at 1100ms —
        and the ambient glow, sized as a percentage of a stage whose box changes when the
        overlay drops, snapped 760px to 248px in one frame on the way out."""
        page = self._motion_page()
        outcome = page.evaluate(
            """async () => {
                const wait = ms => new Promise(r => setTimeout(r, ms));
                const col = document.querySelector('main.chatcol');
                const stage = document.querySelector('.stage');
                const glow = () => parseFloat(getComputedStyle(stage, '::before').width);
                const btn = document.getElementById('orbBtn');
                const overlaid = () => document.body.classList.contains('orbstage');

                btn.click();
                await wait(900);
                const focusedGlow = glow();

                btn.click();
                const trail = [];
                for (let i = 0; i < 30; i++) {
                    trail.push({ t: i * 50, op: +getComputedStyle(col).opacity,
                                 glow: glow(), over: overlaid() });
                    await wait(50);
                }
                const drop = trail.findIndex(r => !r.over);      // overlay released here
                return { focusedGlow: focusedGlow, drop: drop,
                         beforeDrop: drop > 0 ? trail[drop - 1] : null,
                         settled: trail[trail.length - 1],
                         atHalf: trail[Math.min(5, trail.length - 1)] };
            }"""
        )
        self.assertGreater(outcome["drop"], 0, "the overlay never came off")
        before, settled = outcome["beforeDrop"], outcome["settled"]
        self.assertEqual(settled["op"], 1, "the chat never came back")
        # The chat has to move WITH the orb, not wait for it: by the time the overlay is
        # released it should be nearly back, not still invisible.
        self.assertGreater(
            before["op"], 0.85,
            "the chat was still hidden when the orb landed, so leaving takes twice as long "
            "as entering: " + repr(before),
        )
        # And the glow must already be its docked size, or releasing the overlay pops it.
        self.assertLess(
            abs(before["glow"] - settled["glow"]), 20,
            "the ambient glow jumped when the overlay was released: "
            + repr((before["glow"], settled["glow"])),
        )
        self.assertGreater(
            outcome["focusedGlow"], settled["glow"] * 1.5,
            "the glow did not grow with the orb at all",
        )

    def test_voice_can_be_started_while_the_orb_is_focused(self):
        """Orb focus hides the whole chat column, and the Voice pill lives in the composer.
        So voice could be ENDED from orb focus — Interrupt and End voice are in the stage —
        but never started: a click at the pill's own coordinates landed on the canvas."""
        outcome = self.page.evaluate(
            """async () => {
                const wait = ms => new Promise(r => setTimeout(r, ms));
                const hit = el => {
                    const r = el.getBoundingClientRect();
                    const t = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                    return t === el || el.contains(t);
                };
                const label = el => [...el.querySelectorAll('.vlabel')]
                    .filter(s => getComputedStyle(s).display !== 'none')
                    .map(s => s.textContent).join('');
                document.getElementById('orbBtn').click();
                await wait(400);
                const pill = document.getElementById('voiceBtn');
                const orbBtn = document.getElementById('orbVoiceBtn');
                const reachable = !!orbBtn && hit(orbBtn);
                const offLabel = label(orbBtn);
                orbBtn.click();
                await wait(150);
                const voicing = document.body.classList.contains('voicing');
                const onLabel = label(orbBtn);
                const stillReachable = hit(orbBtn);
                orbBtn.click();
                await wait(150);
                return { focused: document.body.classList.contains('orbfocus'),
                         pillReachable: hit(pill),
                         orbBtnReachable: reachable,
                         voicing: voicing, offLabel: offLabel, onLabel: onLabel,
                         stillReachable: stillReachable,
                         endedAgain: document.body.classList.contains('voicing'),
                         pillTracks: pill.classList.contains('on') };
            }"""
        )
        self.assertTrue(outcome["focused"], "orb focus never engaged")
        self.assertFalse(outcome["pillReachable"],
                         "the composer pill is reachable in orb focus — this test proves nothing")
        self.assertTrue(outcome["orbBtnReachable"], "the orb-focus voice button cannot be clicked")
        self.assertTrue(outcome["voicing"], "clicking it did not start voice")
        self.assertEqual(outcome["offLabel"], "Start voice")
        self.assertEqual(outcome["onLabel"], "End voice")
        # It toggles both ways rather than handing off to the .voice panel, which has been
        # display:none since f6a145d dropped the written overlay — End voice and Interrupt
        # are in that panel, so there is nothing on screen to hand off to.
        self.assertTrue(outcome["stillReachable"], "it vanished once voice was on, stranding the mode")
        self.assertFalse(outcome["endedAgain"], "a second click did not end voice")
        self.assertFalse(outcome["pillTracks"], "the composer pill did not follow the same state")


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class RecordingBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.playwright.stop()
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        from dataclasses import replace
        from laptop_agent.tools.transcribe import TranscribeTool
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.speech_calls = []
        def speech(path):
            self.speech_calls.append(Path(path))
            return {"text": "Remember to buy milk", "engine": "fixture", "segments": []}
        self.config_patch = patch.object(webui, "_CONFIG", replace(webui._CONFIG, data_dir=self.root))
        self.speech_patch = patch.object(webui._orchestrator, "context", replace(
            webui._orchestrator.context, transcribe=TranscribeTool(asr_backend=speech)))
        self.metrics_patch = patch.object(webui, "system_metrics", return_value={"cpu_percent": 0, "ram_percent": 0, "gpus": []})
        for fixture in (self.config_patch, self.speech_patch, self.metrics_patch):
            fixture.start(); self.addCleanup(fixture.stop)
        self.context = self.browser.new_context(permissions=["microphone"], reduced_motion="reduce")
        self.context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)

    wait_js = BrowserRegressions.wait_js

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def start(self, text="record 1"):
        self.page.evaluate("text=>void send(text)", text)
        self.page.locator(".recorder-live").wait_for(state="visible")
        self.wait_js("()=>document.querySelector('.recorder-live p').textContent.startsWith('Recording')")
        box = self.page.locator(".recorder-live").bounding_box()
        self.assertGreater(box["width"], 100)
        self.assertGreater(box["height"], 25)

    def saved(self):
        self.page.locator(".recording-card audio").wait_for(state="visible")
        files = list((self.root/"recordings").glob("*.wav"))
        self.assertEqual(len(files), 1)
        self.assertEqual(self.page.locator(".recorder-live").count(), 0)
        self.assertEqual(self.speech_calls, [])
        return files[0]

    def test_timeout_saves_one_clip_then_explicit_transcription_joins_history(self):
        import wave
        self.start()
        target = self.saved()
        with wave.open(str(target), 'rb') as audio:
            self.assertGreater(audio.getnframes()/audio.getframerate(), .3)
            self.assertLessEqual(audio.getnframes()/audio.getframerate(), 1)
        self.page.get_by_role("button", name="Transcribe recording").click()
        self.wait_js("()=>chat.textContent.includes('Remember to buy milk')")
        self.assertEqual(len(self.speech_calls), 1)
        self.assertTrue(self.speech_calls[0].samefile(target))
        history = self.page.evaluate("sessionHistory(curSession())")
        self.assertTrue(any(turn["role"] == "assistant" and "Remember to buy milk" in turn["text"] for turn in history))
        original = self.page.evaluate("current")
        self.page.reload()
        # Saved chats load once /api/me says whose they are (AUTH-01), as the rail fills for a person.
        self.wait_js("()=>chatKey!==null")
        self.page.evaluate("id=>loadSession(id)", original)
        self.page.locator(".recording-card audio").wait_for(state="visible")
        self.assertIn("Remember to buy milk", self.page.locator("#chat").inner_text())

    def test_manual_stop_saves_partial_clip_only_once(self):
        import wave
        self.start("record my voice for 20 seconds")
        self.page.wait_for_timeout(350)
        self.page.locator(".recorder-live button").click()
        target = self.saved()
        self.page.keyboard.press("Space")
        with wave.open(str(target), 'rb') as audio:
            self.assertGreater(audio.getnframes(), 0)
            self.assertLess(audio.getnframes()/audio.getframerate(), 5)
        self.assertEqual(len(list((self.root/"recordings").glob("*.wav"))), 1)

    def test_space_stop_survives_switching_chats_and_keeps_original_ownership(self):
        self.start("record 20")
        first = self.page.evaluate("current")
        self.page.evaluate("newSession()")
        self.assertTrue(self.page.locator(".recorder-live").is_visible())
        self.page.wait_for_timeout(350)
        self.page.keyboard.press("Space")
        self.wait_js("()=>activeRecording===null")
        self.assertEqual(self.page.locator(".recording-card").count(), 0)
        self.page.evaluate("id=>loadSession(id)", first)
        self.saved()
        self.page.get_by_role("button", name="Transcribe recording").click()
        self.wait_js("()=>chat.textContent.includes('Remember to buy milk')")

    def test_unavailable_microphone_is_visible_and_saves_nothing(self):
        self.page.evaluate("Object.defineProperty(navigator,'mediaDevices',{value:undefined,configurable:true})")
        self.page.evaluate("void send('record 1')")
        self.wait_js("()=>chat.textContent.includes('secure connection')")
        box = self.page.locator('.msg.bot .md').last.bounding_box()
        self.assertGreater(box['height'], 10)
        self.assertFalse((self.root/'recordings').exists())
        self.assertIsNone(self.page.evaluate("activeRecording"))

    def test_cancel_while_permission_pending_stops_late_stream_without_saving(self):
        self.page.evaluate("""()=>{
            const real=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia=opts=>new Promise(resolve=>{
                window.releaseRecordingMic=async()=>{window.lateStream=await real(opts);resolve(window.lateStream);};
            });
        }""")
        self.page.evaluate("void send('record 1')")
        self.page.locator('.recorder-live button').click()
        self.page.evaluate("releaseRecordingMic()")
        self.wait_js("()=>window.lateStream.getTracks().every(t=>t.readyState==='ended')")
        self.assertFalse((self.root/'recordings').exists())
        self.assertIn('No audio was saved', self.page.locator('#chat').inner_text())
