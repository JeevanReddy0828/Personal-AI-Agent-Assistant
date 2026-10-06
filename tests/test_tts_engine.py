from __future__ import annotations

import io
import os
import sys
import types
import unittest
import wave
from unittest.mock import Mock, patch

from laptop_agent import voice
from laptop_agent.tools import transcribe


class WaitExpired(Exception):
    pass


class Pending:
    def __init__(self, audio=b"", error=None):
        self.audio, self.error = audio, error
        self.timeouts = []
        self.cancelled = False

    def result(self, timeout=None):
        self.timeouts.append(timeout)
        if self.error:
            raise self.error
        return types.SimpleNamespace(audio=self.audio)

    def cancel(self):
        self.cancelled = True
        return True


class MagpieTests(unittest.TestCase):
    """The hosted voice, against a fake Riva SDK: what it asks for, and what it hands back."""

    def setUp(self):
        self.pending = Pending(audio=b"\x01\x00" * 2205)
        self.channel = Mock()
        self.auth_args = []
        self.requests = []
        client = types.ModuleType("riva.client")

        def auth(**kwargs):
            self.auth_args.append(kwargs)
            return types.SimpleNamespace(channel=self.channel)

        def synthesize(text, **kwargs):
            self.requests.append((text, kwargs))
            if not kwargs.get("future"):
                raise AssertionError("The blocking SDK call has no deadline")
            return self.pending

        client.Auth = auth
        client.AudioEncoding = types.SimpleNamespace(LINEAR_PCM=1)
        client.SpeechSynthesisService = lambda auth: types.SimpleNamespace(synthesize=synthesize)
        parent = types.ModuleType("riva")
        parent.client = client
        grpc = types.ModuleType("grpc")
        grpc.FutureTimeoutError = WaitExpired
        environ = {key: value for key, value in os.environ.items()
                   if not key.startswith(("RIVA_", "LAPTOP_AGENT_TTS"))}
        environ["RIVA_API_KEY"] = "fixture-not-a-key"
        for fixture in (patch.dict(sys.modules, {"riva": parent, "riva.client": client, "grpc": grpc}),
                        patch.dict(os.environ, environ, clear=True)):
            fixture.start()
            self.addCleanup(fixture.stop)

    def test_the_reply_is_a_playable_wav_of_what_the_service_sent(self):
        data = voice._magpie_wav("Good evening.")
        with wave.open(io.BytesIO(data), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 22050))
            self.assertEqual(wav.readframes(wav.getnframes()), self.pending.audio)
        text, kwargs = self.requests[0]
        self.assertEqual(text, "Good evening.")
        self.assertEqual((kwargs["voice_name"], kwargs["language_code"], kwargs["sample_rate_hz"]),
                         (None, "en-US", 22050))
        metadata = dict(self.auth_args[0]["metadata_args"])
        self.assertEqual(metadata["function-id"], voice.RIVA_TTS_FUNCTION_ID)
        self.assertEqual(metadata["authorization"], "Bearer fixture-not-a-key")
        self.assertTrue(self.auth_args[0]["use_ssl"])
        self.assertTrue(self.pending.cancelled)
        self.channel.close.assert_called_once()

    def test_the_wait_is_bounded_and_grows_with_the_sentence(self):
        voice._magpie_wav("Hi.")
        voice._magpie_wav("word " * 400)
        short, long = self.pending.timeouts
        self.assertGreater(short, 0)
        self.assertGreater(long, short)
        self.assertLessEqual(long, 30)

    def test_a_stalled_call_times_out_and_still_closes_its_channel(self):
        self.pending.error = WaitExpired()
        with self.assertRaises(TimeoutError):
            voice._magpie_wav("Hello there.")
        self.assertTrue(self.pending.cancelled)
        self.channel.close.assert_called_once()

    def test_no_audio_is_an_error_not_an_empty_file(self):
        self.pending.audio = b""
        with self.assertRaises(RuntimeError):
            voice._magpie_wav("Hello there.")

    def test_the_model_voice_and_language_can_be_chosen(self):
        os.environ.update({"RIVA_TTS_FUNCTION_ID": "other-function", "RIVA_TTS_VOICE": "Magpie-Multilingual.EN-US.Aria",
                           "RIVA_TTS_LANGUAGE": "es-US"})
        voice._magpie_wav("Hola.")
        _, kwargs = self.requests[0]
        self.assertEqual((kwargs["voice_name"], kwargs["language_code"]), ("Magpie-Multilingual.EN-US.Aria", "es-US"))
        self.assertEqual(dict(self.auth_args[0]["metadata_args"])["function-id"], "other-function")

    def test_without_a_key_it_says_so_before_calling_anything(self):
        with patch.object(transcribe, "_riva_key", lambda: ""):
            with self.assertRaisesRegex(RuntimeError, "API key"):
                voice._magpie_wav("Hello.")
        self.assertEqual(self.requests, [])


class EngineChoiceTests(unittest.TestCase):
    """Which voice speaks: the hosted one when it can, the offline one when it cannot."""

    def setUp(self):
        self.calls = []
        self.available = True
        self.failed = []
        environ = {key: value for key, value in os.environ.items() if key != "LAPTOP_AGENT_TTS"}
        for fixture in (
            patch.dict(os.environ, environ, clear=True),
            patch.object(transcribe, "_riva_available", lambda: self.available),
            patch.object(voice, "_magpie_wav", lambda text: (self.calls.append("magpie"), b"hosted")[1]),
            patch.object(voice, "_pyttsx3_wav", lambda text: (self.calls.append("offline"), b"offline")[1]),
            patch.object(voice, "record_failure", lambda source, exc: self.failed.append(source)),
        ):
            fixture.start()
            self.addCleanup(fixture.stop)

    def test_auto_prefers_the_hosted_voice(self):
        self.assertEqual(voice.synthesize_wav("Hello."), b"hosted")
        self.assertEqual(self.calls, ["magpie"])

    def test_auto_falls_back_to_the_offline_voice_and_records_why(self):
        def dead(text):
            self.calls.append("magpie")
            raise TimeoutError("The hosted voice did not answer in time.")

        with patch.object(voice, "_magpie_wav", dead):
            self.assertEqual(voice.synthesize_wav("Hello."), b"offline")
        self.assertEqual(self.calls, ["magpie", "offline"])
        self.assertEqual(self.failed, ["tts/magpie"])

    def test_auto_without_the_hosted_voice_goes_straight_to_the_offline_one(self):
        self.available = False
        self.assertEqual(voice.synthesize_wav("Hello."), b"offline")
        self.assertEqual(self.calls, ["offline"])

    def test_offline_never_sends_text_off_the_laptop(self):
        os.environ["LAPTOP_AGENT_TTS"] = "offline"
        self.assertEqual(voice.synthesize_wav("Hello."), b"offline")
        self.assertEqual(self.calls, ["offline"])

    def test_riva_means_only_the_hosted_voice(self):
        os.environ["LAPTOP_AGENT_TTS"] = "riva"

        def dead(text):
            raise TimeoutError("The hosted voice did not answer in time.")

        with patch.object(voice, "_magpie_wav", dead):
            self.assertIsNone(voice.synthesize_wav("Hello."))
        self.assertEqual(self.calls, [])

    def test_the_engine_name_matches_the_choice(self):
        present = lambda name: object() if name == "pyttsx3" else None  # noqa: E731
        with patch.object(voice.importlib.util, "find_spec", present):
            self.assertEqual(voice.tts_engine_name(), "riva:magpie")
            os.environ["LAPTOP_AGENT_TTS"] = "offline"
            self.assertEqual(voice.tts_engine_name(), "pyttsx3")
            os.environ["LAPTOP_AGENT_TTS"] = "riva"
            self.available = False
            self.assertIsNone(voice.tts_engine_name())
            os.environ["LAPTOP_AGENT_TTS"] = "auto"
            self.assertEqual(voice.tts_engine_name(), "pyttsx3")


if __name__ == "__main__":
    unittest.main()
