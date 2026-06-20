from __future__ import annotations

import base64
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import laptop_agent.webui as webui
from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.transcribe import TranscribeTool


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
            self.base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        return urllib.request.urlopen(req, timeout=15)

    def test_transcribe_returns_text(self) -> None:
        audio = "data:audio/webm;base64," + base64.b64encode(b"fake-opus-bytes").decode()
        resp = self._post("/api/transcribe", {"audio": audio, "ext": "webm"})
        body = json.loads(resp.read())
        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "turn on the lights")

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

    def test_tts_unavailable_engine_returns_503(self) -> None:
        webui._TTS_BACKEND = lambda text: None  # simulate no engine / failure
        try:
            self._post("/api/tts", {"text": "hello"})
            self.fail("expected HTTP 503")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 503)


if __name__ == "__main__":
    unittest.main()
