from __future__ import annotations

import base64
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import laptop_agent.webui as webui
from laptop_agent.recordings import MAX_WAV_BYTES, save_recording
from laptop_agent.tools.transcribe import TranscribeTool


def wav(seconds=.1, channels=1, rate=16000):
    out = io.BytesIO()
    with wave.open(out, 'wb') as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\0\0' * int(seconds * rate) * channels)
    return out.getvalue()


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = patch.object(webui, '_CONFIG', replace(webui._CONFIG, data_dir=self.root))
        config.start(); self.addCleanup(config.stop)
        self.calls = []
        def backend(path):
            self.calls.append(Path(path))
            return {'text': 'Remember to buy milk', 'engine': 'fixture', 'segments': []}
        speech = patch.object(webui._orchestrator, 'context', replace(webui._orchestrator.context, transcribe=TranscribeTool(asr_backend=backend)))
        speech.start(); self.addCleanup(speech.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), webui.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, route, payload=None, token=True):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Jarvis-Token'] = webui._API_TOKEN
        request = urllib.request.Request(self.url+route,
            data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def save(self):
        status, _, body = self.request('/api/recordings', {'audio': base64.b64encode(wav()).decode()})
        self.assertEqual(status, 200, body)
        return json.loads(body)['recording']

    def test_save_is_local_then_explicit_transcription_and_private_playback(self):
        recording = self.save()
        target = self.root/'recordings'/recording['name']
        self.assertEqual(target.read_bytes(), wav())
        self.assertEqual(recording['seconds'], .1)
        self.assertEqual(self.calls, [])
        status, headers, body = self.request('/api/recording?name='+recording['name'])
        self.assertEqual((status, body), (200, wav()))
        self.assertEqual(headers['Cache-Control'], 'private, no-store')
        status, headers, _ = self.request('/api/recording?download=1&name='+recording['name'])
        self.assertIn('attachment;', headers['Content-Disposition'])
        status, _, body = self.request('/api/recordings/transcribe', {'name': recording['name']})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['text'], 'Remember to buy milk')
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0].samefile(target))
        self.assertTrue(target.exists())

    def test_mutations_require_token(self):
        for route, payload in (('/api/recordings', {'audio': base64.b64encode(wav()).decode()}),
                               ('/api/recordings/transcribe', {'name': 'anything.wav'})):
            status, _, _ = self.request(route, payload, token=False)
            self.assertEqual(status, 403)
        self.assertFalse((self.root/'recordings').exists())
        self.assertEqual(self.calls, [])

    def test_bad_audio_rejected_before_any_file_is_created(self):
        for raw in (b'not wav', wav()[:-2], wav(channels=2), wav(rate=8000), wav(seconds=0),
                    wav(seconds=121), b'x'*(MAX_WAV_BYTES+1)):
            with self.subTest(length=len(raw)):
                status, _, _ = self.request('/api/recordings', {'audio': base64.b64encode(raw).decode()})
                self.assertIn(status, (400, 413))
        self.assertFalse((self.root/'recordings').exists())

    def test_body_limit_and_invalid_base64(self):
        with patch.object(webui, 'MAX_REQUEST_BYTES', 10):
            self.assertEqual(self.request('/api/recordings', {'audio': 'x'*50})[0], 400)
        self.assertEqual(self.request('/api/recordings', {'audio': '!!!'})[0], 400)

    def test_transcription_failure_preserves_file_and_path_traversal_fails(self):
        recording = self.save()
        with patch.object(webui._orchestrator.context.transcribe, '_asr_backend', side_effect=RuntimeError('engine offline')):
            status, _, body = self.request('/api/recordings/transcribe', {'name': recording['name']})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)['ok'])
        self.assertTrue((self.root/'recordings'/recording['name']).exists())
        for name in ('../outside.wav', '..\\outside.wav', '/tmp/outside.wav'):
            self.assertEqual(self.request('/api/recordings/transcribe', {'name': name})[0], 404)
            self.assertEqual(self.request('/api/recording?name='+name)[0], 404)

    def test_maximum_and_unique_names(self):
        first = save_recording(self.root, wav(seconds=120))
        second = save_recording(self.root, wav(seconds=120))
        self.assertEqual(first['seconds'], 120)
        self.assertNotEqual(first['name'], second['name'])
