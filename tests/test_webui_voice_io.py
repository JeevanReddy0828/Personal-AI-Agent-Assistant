from __future__ import annotations

import base64
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import laptop_agent.webui as webui
from laptop_agent.tools.transcribe import MissingDependencyError, TranscribeTool


class VoiceIoApiTests(unittest.TestCase):
    """Server-side STT (/api/transcribe) and TTS (/api/tts) for the native window,
    exercised against a live server with injected engines (no real audio stack)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._orig_tts = webui._TTS_BACKEND
        cls._orig_transcribe = webui._orchestrator.context.transcribe
        # STT: a fake recognizer that ignores audio content and returns fixed text.
        object.__setattr__(
            webui._orchestrator.context,
            "transcribe",
            TranscribeTool(asr_backend=lambda path: {"text": "turn on the lights", "engine": "fake", "segments": []}),
        )
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        webui._TTS_BACKEND = cls._orig_tts
        object.__setattr__(webui._orchestrator.context, "transcribe", cls._orig_transcribe)

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "X-Jarvis-Token": webui._API_TOKEN}
        )
        return urllib.request.urlopen(req, timeout=15)

    def test_native_query_string_serves_page(self) -> None:
        # The native window loads /?app=1; routing must ignore the query (regression:
        # it used to 404 with "not found").
        resp = urllib.request.urlopen(self.base + "/?app=1", timeout=15)
        body = resp.read().decode()
        self.assertEqual(resp.status, 200)
        self.assertIn("J.A.R.V.I.S", body)

    def test_transcribe_returns_text(self) -> None:
        audio = "data:audio/webm;base64," + base64.b64encode(b"fake-opus-bytes").decode()
        resp = self._post("/api/transcribe", {"audio": audio, "ext": "webm"})
        body = json.loads(resp.read())
        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "turn on the lights")

    def test_transcribe_tells_a_broken_engine_from_silence(self) -> None:
        """Both come back `ok: false`, so the page could not tell "nothing was said" from
        "there is no speech engine" - and only the second is worth putting on screen."""
        def answer_with(backend) -> dict:
            object.__setattr__(webui._orchestrator.context, "transcribe", TranscribeTool(asr_backend=backend))
            audio = base64.b64encode(b"RIFF\x00\x00\x00\x00WAVEfake").decode()
            return json.loads(self._post("/api/transcribe", {"audio": audio, "ext": "wav"}).read())

        def no_engine(path):
            raise MissingDependencyError("Speech-to-text needs an engine: pip install laptop-agent[stt]")

        self.addCleanup(object.__setattr__, webui._orchestrator.context, "transcribe",
                        webui._orchestrator.context.transcribe)
        silence = answer_with(lambda path: {"text": "", "engine": "fake", "segments": []})
        broken = answer_with(no_engine)
        heard = answer_with(lambda path: {"text": "turn on the lights", "engine": "fake", "segments": []})

        self.assertIn("failed", broken, "the response cannot say whether the engine failed")
        self.assertFalse(silence["ok"])
        self.assertFalse(silence["failed"], "hearing nothing was reported as a broken engine")
        self.assertFalse(broken["ok"])
        self.assertTrue(broken["failed"], "a missing engine was reported as silence")
        self.assertIn("pip install", broken["message"])
        self.assertTrue(heard["ok"])
        self.assertFalse(heard["failed"])

    def test_transcribe_rejects_empty(self) -> None:
        try:
            self._post("/api/transcribe", {"audio": ""})
            self.fail("expected HTTP 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_tts_returns_wav(self) -> None:
        webui._TTS_BACKEND = lambda text: b"RIFF\x00\x00\x00\x00WAVEfake:" + text.encode()
        resp = self._post("/api/tts", {"text": "All systems online."})
        self.assertEqual(resp.headers.get("Content-Type"), "audio/wav")
        data = resp.read()
        self.assertTrue(data.startswith(b"RIFF"))
        self.assertIn(b"All systems online.", data)

    def test_health_names_the_voice_so_a_tab_can_choose_it(self) -> None:
        # A browser tab speaks through /api/tts only when this says Magpie.
        saved = webui._LLM_STATUS.pop("tts", None)
        try:
            with patch.object(webui, "tts_engine_name", return_value="riva:magpie"):
                body = json.loads(urllib.request.urlopen(self.base + "/api/health", timeout=15).read())
        finally:
            webui._LLM_STATUS.pop("tts", None)
            if saved is not None:
                webui._LLM_STATUS["tts"] = saved
        self.assertEqual(body["tts"], {"engine": "riva:magpie"})

    def test_tts_unavailable_engine_returns_503(self) -> None:
        webui._TTS_BACKEND = lambda text: None  # simulate no engine / failure
        try:
            self._post("/api/tts", {"text": "hello"})
            self.fail("expected HTTP 503")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 503)


if __name__ == "__main__":
    unittest.main()
