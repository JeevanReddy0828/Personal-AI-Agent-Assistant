"""The wake word: whether a clip opens with "Hey Jarvis", decided locally, and /api/wake around it.

No test runs a real speech model: the recognizer is a fake that returns a transcript.
"""

from __future__ import annotations

import base64
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import laptop_agent.webui as webui
from laptop_agent import wake
from laptop_agent.failures import FAILURES
from laptop_agent.wake import MAX_WAV_BYTES, WakeDetector, heard, pcm_samples


def wav(seconds: float = 1.0, rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(bytes(int(rate * seconds) * 2 * channels))
    return buffer.getvalue()


class HeardTests(unittest.TestCase):
    def test_an_opening_jarvis_wakes_and_keeps_what_follows(self) -> None:
        self.assertEqual(heard(" Hey Jarvis, what time is it?"), (True, "what time is it"))
        self.assertEqual(heard("Jarvis."), (True, ""))
        self.assertEqual(heard("okay jarvis open my calendar"), (True, "open my calendar"))
        self.assertEqual(heard("Hey Jervis"), (True, ""))

    def test_jarvis_in_the_middle_of_talk_does_not(self) -> None:
        for text in ("I told Jarvis about it yesterday.", "The weather is lovely today.", "", "Jarvisland is a place",
                     "hey there jarvis"):
            with self.subTest(text):
                self.assertFalse(heard(text)[0])


class DetectorTests(unittest.TestCase):
    def detector(self, text: str, no_speech: float = 0.05) -> WakeDetector:
        return WakeDetector(lambda clip: (text, no_speech))

    def test_a_clip_saying_it_wakes(self) -> None:
        self.assertEqual(self.detector(" Hey Jarvis, set a timer").check(wav()),
                         {"available": True, "wake": True, "rest": "set a timer"})

    def test_what_whisper_thinks_is_not_speech_never_wakes(self) -> None:
        self.assertFalse(self.detector("Hey Jarvis", no_speech=0.9).check(wav())["wake"])

    def test_without_local_recognition_it_says_so(self) -> None:
        result = WakeDetector(None).check(wav())
        self.assertEqual((result["available"], result["wake"]), (False, False))

    def test_empty_and_overlong_clips_are_not_checked(self) -> None:
        calls: list[bytes] = []
        detector = WakeDetector(lambda clip: calls.append(clip) or ("Jarvis", 0.0))
        self.assertFalse(detector.check(b"")["wake"])
        self.assertFalse(detector.check(b"x" * (MAX_WAV_BYTES + 1))["wake"])
        self.assertEqual(calls, [])

    def test_a_broken_engine_is_recorded_not_raised(self) -> None:
        def broken(clip: bytes):
            raise RuntimeError("model gone")

        self.assertFalse(WakeDetector(broken).check(wav())["wake"])
        self.assertEqual(FAILURES.recent(1)[0]["where"], "wake/recognize")

    def test_only_16k_mono_16_bit_is_read(self) -> None:
        self.assertEqual(len(pcm_samples(wav(0.5))), 16000)
        for clip in (wav(rate=44100), wav(channels=2)):
            with self.assertRaises(ValueError):
                pcm_samples(clip)

    def test_whisper_missing_means_no_recognizer(self) -> None:
        with patch.object(wake.importlib.util, "find_spec", return_value=None):
            self.assertIsNone(wake.whisper_recognizer())


class WakeEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, payload: dict) -> dict:
        request = urllib.request.Request(self.base + "/api/wake", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", "X-Jarvis-Token": webui._API_TOKEN})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            return {"status": error.code}

    def test_a_clip_is_checked_by_the_local_detector(self) -> None:
        heard_clips: list[bytes] = []
        detector = WakeDetector(lambda clip: heard_clips.append(clip) or ("Hey Jarvis, what's up", 0.0))
        clip = wav()
        with patch.object(webui, "_WAKE", detector):
            result = self.post({"audio": "data:audio/wav;base64," + base64.b64encode(clip).decode()})
        self.assertEqual((result["wake"], result["rest"]), (True, "what's up"))
        self.assertEqual(heard_clips, [clip])

    def test_unreadable_audio_is_a_bad_request(self) -> None:
        with patch.object(webui, "_WAKE", WakeDetector(lambda clip: ("Jarvis", 0.0))):
            self.assertEqual(self.post({"audio": "not base64!!"}), {"status": 400})

    def test_a_personal_account_may_use_it(self) -> None:
        self.assertIn("/api/wake", webui._PERSONAL_ROUTES)


if __name__ == "__main__":
    unittest.main()
