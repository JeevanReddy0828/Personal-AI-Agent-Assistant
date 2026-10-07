"""Offline Chromium baseline for tool-result speech; no model or NVIDIA calls.

Run with ``python -B tests/measure_nonstreamed_speech.py``. The fake /api/tts waits
20 + 2 * len(text) milliseconds, and a fake audio element logs all text it plays.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
CASES = json.loads((ROOT / "data" / "nonstreamed_speech_cases.json").read_text(encoding="utf-8"))["cases"]


def main() -> None:
    from playwright.sync_api import sync_playwright

    source_root = Path(os.environ.get("SPEECH_SOURCE_ROOT", str(ROOT.parent)))
    sys.path.insert(0, str(source_root / "src"))
    import laptop_agent.config as config

    config._load_dotenv = lambda *args, **kwargs: None
    for key in list(os.environ):
        if key.startswith(("OPENAI_", "OPENROUTER_", "SMTP_", "IMAP_", "GOOGLE_", "MICROSOFT_",
                           "JOBRIGHT_", "SEARCH_", "BRAVE_", "SERPER_", "SERPAPI_", "OBSIDIAN_",
                           "LAPTOP_AGENT_", "RIVA_", "NVIDIA_")):
            os.environ.pop(key, None)
    os.environ["LAPTOP_AGENT_LLM_PROVIDER"] = "heuristic"
    connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise OSError("External connections are disabled by the speech measurement")
        return connect(sock, address)

    with tempfile.TemporaryDirectory(prefix="jarvis_speech_measure_") as scratch, patch.object(socket.socket, "connect", local_only):
        os.environ["LAPTOP_AGENT_DATA_DIR"] = str(Path(scratch) / "data")
        os.environ["LAPTOP_AGENT_PORT"] = "19877"
        import laptop_agent.webui as webui
        from laptop_agent.tools.base import ToolResult
        from laptop_agent.voice import clean_for_speech

        server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    base = f"http://127.0.0.1:{server.server_port}"
                    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
                    engine_state = {"engine": "riva:magpie"}

                    def health(route) -> None:
                        body = route.fetch().json()
                        body["tts"] = {"engine": engine_state["engine"]}
                        route.fulfill(json=body)

                    page.route("**/api/health", health)
                    page.goto(base)
                    page.evaluate("""() => {
                      const realFetch = window.fetch, named = {};
                      window.__spoken = [];
                      window.fetch = (url, init) => {
                        if (!String(url).includes('/api/tts')) return realFetch(url, init);
                        const text = JSON.parse(init.body).text;
                        window.__spoken.push({kind:'ask', text});
                        return new Promise(resolve => setTimeout(() =>
                          resolve(new Response(text, {status:200})), 20 + 2 * text.length));
                      };
                      const RealBlob = window.Blob, realURL = URL.createObjectURL;
                      window.Blob = function(parts, opts) {
                        const blob = new RealBlob(parts, opts);
                        blob.spokenText = new TextDecoder().decode(parts[0]);
                        return blob;
                      };
                      URL.createObjectURL = blob => {
                        const url = realURL(blob); named[url] = blob.spokenText; return url;
                      };
                      window.Audio = function(url) {
                        return {src:url, paused:true, currentTime:0, onended:null, onerror:null,
                          play() {
                            this.paused=false;
                            window.__spoken.push({kind:'play', text:named[url], at:performance.now()});
                            setTimeout(() => { if(this.onended)this.onended(); }, 10);
                            return Promise.resolve();
                          },
                          pause() {this.paused=true;}
                        };
                      };
                      speechSynthesis.speak = u => {
                        window.__spoken.push({kind:'browser', text:u.text, at:performance.now()});
                        setTimeout(() => {if(u.onend)u.onend();}, 10);
                      };
                      listen = () => {};
                    }""")
                    for case in CASES:
                        reply = case["reply"] * case.get("repeat", 1)
                        for path in ("baseline", "sse"):
                            async def instant(*args, **kwargs):
                                return ToolResult.success(reply)

                            original = webui._orchestrator.handle
                            webui._orchestrator.handle = instant
                            try:
                                for engine in ("riva:magpie", "pyttsx3"):
                                    engine_state["engine"] = engine
                                    result = page.evaluate("""async ({reply, engine, path}) => {
                              window.__spoken.length=0;
                              ttsEngine=engine; voiceActive=true; voiceTurnReset();
                              const start=performance.now();
                              const turn=path==='baseline' ? (voiceTurnDone(reply, ttsEpoch), Promise.resolve())
                                                         : send('offline tool result');
                              while (!window.__spoken.some(e => e.kind==='play'||e.kind==='browser')
                                     && performance.now()-start<7000)
                                await new Promise(r=>setTimeout(r,5));
                              const first=window.__spoken.find(e=>e.kind==='play'||e.kind==='browser');
                              const elapsed=first ? Math.round(first.at-start) : null;
                              await turn;
                              while((speaking||ttsQueue.length)&&performance.now()-start<15000)
                                await new Promise(r=>setTimeout(r,5));
                              const spoken=window.__spoken.filter(e=>e.kind==='play'||e.kind==='browser');
                              voiceActive=false;
                              return {ms:elapsed, requests:window.__spoken.filter(e=>e.kind==='ask').length,
                                voicedWords:spoken.map(e=>e.text).join(' ').split(/\\s+/).filter(Boolean).length,
                                voicedChars:spoken.map(e=>e.text).join(' ').length,
                                voicedText:spoken.map(e=>e.text).join(' ')};
                            }""", {"reply": reply, "engine": engine, "path": path})
                                    expected = clean_for_speech(reply)
                                    result["expectedWords"] = len(expected.split())
                                    result["expectedChars"] = len(expected)
                                    result["expectedText"] = expected
                                    if path == "sse" and os.environ.get("SPEECH_EXPECT_SPLIT", "1") == "1":
                                        if result["voicedText"].split() != expected.split():
                                            raise AssertionError(f"{case['name']} {engine}: words voiced out of order or missing")
                                    if case["name"] not in ("bullets", "table"):
                                        result.pop("expectedText")
                                        result.pop("voicedText")
                                    print(json.dumps({"case": case["name"], "path": path, "engine": engine, **result}), flush=True)
                            finally:
                                webui._orchestrator.handle = original
                    page.close()
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
