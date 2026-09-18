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
                    empty:      isEcho('')
                };
            }"""
        )
        self.assertTrue(outcome["exact"], "our own sentence must be recognised as echo")
        self.assertTrue(outcome["partial"], "a fragment of our own sentence is still echo")
        self.assertTrue(outcome["mostWords"], "a near-match of our own sentence is echo")
        self.assertFalse(outcome["userSpeech"], "the user's own interruption must get through")
        self.assertFalse(outcome["shortWord"], "a single word must not be eaten as echo")
        self.assertFalse(outcome["empty"])

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
                feed(0.02, 6);                       // our own voice, learned as the floor
                feed(0.02, 6);                       // still only us: must not trigger
                const heldThroughOurOwnVoice = (ttsEpoch === epoch0 && speaking === true);
                feed(0.35, 4);                       // the user starts talking
                const stopped = (ttsEpoch > epoch0 && speaking === false);
                try { bargeStop(); } catch (e) {}
                voiceActive = false; speaking = false;
                return { armed: armed, heldThroughOurOwnVoice: heldThroughOurOwnVoice, stopped: stopped };
            }"""
        )
        self.assertTrue(outcome["armed"], "barge-in never armed in server-STT mode")
        self.assertTrue(
            outcome["heldThroughOurOwnVoice"],
            "our own speech leaking into the mic triggered a barge-in",
        )
        self.assertTrue(outcome["stopped"], "talking over the reply did not stop it")

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

                const hiddenBefore = box.hidden;
                sttServer = true; sttChosen = true; sttEngine = 'test-engine';
                voiceActive = true; speaking = true; bargeReset();
                bargeStart();
                await new Promise(r => setTimeout(r, 60));
                if (!proc) { voiceActive = false; speaking = false;
                    return { armed: false }; }
                const shownWhileArmed = !box.hidden;
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
                         hiddenAfter: hiddenAfter, quiet: quiet, loud: loud };
            }"""
        )
        self.assertTrue(outcome["armed"], "barge-in never armed in server-STT mode")
        self.assertTrue(outcome["hiddenBefore"], "the meter is on screen when nothing is listening")
        self.assertTrue(outcome["shownWhileArmed"], "the meter stayed hidden while barge-in listened")
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
