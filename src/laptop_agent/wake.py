"""Hearing "Hey Jarvis": whether a short clip of speech opens with the wake word, and what follows.

Voice started only from a click or a key. Jeevan chose a microphone that is on by default, with
a switch to turn it off (2026-10-15). The page keeps it open, cuts each burst of speech into a
clip and posts it to /api/wake; this decides, **on this laptop only**: local Whisper, never a
hosted engine, so what is said near the laptop leaves it only after "Hey Jarvis", when the normal
voice path takes over. Only an opening "Jarvis" wakes - "I told Jarvis…" in conversation does not -
and a clip Whisper itself rates as probably not speech is ignored rather than guessed at.
"""
from __future__ import annotations

import importlib.util
import io
import os
import re
import threading
import wave
from typing import Callable

from laptop_agent.failures import record_failure

SAMPLE_RATE = 16000
MAX_SECONDS = 8
MAX_WAV_BYTES = 44 + SAMPLE_RATE * 2 * MAX_SECONDS
NO_SPEECH_ABOVE = 0.6
# Whisper writes the name several ways; each is accepted only at the very start.
_WAKE = re.compile(
    r"^[\s\W]*(?:(?:hey|hi|hello|ok(?:ay)?|yo)[\s,.!]+)?(?:jarvis|jarvas|jervis|javis|jar\s+vis)\b[\s,.!?:;-]*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

Recognize = Callable[[bytes], tuple[str, float]]


def heard(text: str) -> tuple[bool, str]:
    """(woken, the request said after the wake word), from a transcript."""
    found = _WAKE.match(" ".join((text or "").split()))
    if not found:
        return False, ""
    return True, found.group("rest").strip(" .,!?")


def pcm_samples(wav: bytes) -> bytes:
    """The 16-bit samples of a 16 kHz mono WAV, or ValueError for anything else."""
    with wave.open(io.BytesIO(wav)) as clip:
        if (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError("the wake check takes 16 kHz mono 16-bit WAV")
        return clip.readframes(clip.getnframes())


def whisper_recognizer() -> Recognize | None:
    """Local Whisper on the samples themselves (no ffmpeg per clip), or None when it is not installed."""
    if importlib.util.find_spec("whisper") is None or importlib.util.find_spec("numpy") is None:
        return None
    lock = threading.Lock()

    def recognize(wav: bytes) -> tuple[str, float]:
        import numpy

        from laptop_agent.tools.transcribe import _load_whisper_model

        audio = numpy.frombuffer(pcm_samples(wav), dtype="<i2").astype(numpy.float32) / 32768.0
        name = os.environ.get("LAPTOP_AGENT_WAKE_MODEL") or os.environ.get("LAPTOP_AGENT_WHISPER_MODEL", "base")
        with lock:   # one clip at a time: the model is shared with the voice loop
            result = _load_whisper_model(name).transcribe(audio, language="en", fp16=False,
                                                          condition_on_previous_text=False)
        segments = [segment for segment in result.get("segments") or [] if isinstance(segment, dict)]
        no_speech = min((float(segment.get("no_speech_prob", 0.0)) for segment in segments), default=1.0)
        return str(result.get("text") or ""), no_speech

    return recognize


class WakeDetector:
    def __init__(self, recognize: Recognize | None) -> None:
        self._recognize = recognize

    @property
    def available(self) -> bool:
        return self._recognize is not None

    def check(self, wav: bytes) -> dict[str, object]:
        if self._recognize is None:
            return {"available": False, "wake": False,
                    "message": "The wake word needs local speech recognition: pip install openai-whisper."}
        if not wav or len(wav) > MAX_WAV_BYTES:
            return {"available": True, "wake": False, "message": "clip empty or too long"}
        try:
            text, no_speech = self._recognize(wav)
        except ValueError as exc:
            return {"available": True, "wake": False, "message": str(exc)}
        except Exception as exc:   # a broken engine must not take the listener down
            record_failure("wake/recognize", exc)
            return {"available": True, "wake": False, "message": "the wake check failed"}
        if no_speech > NO_SPEECH_ABOVE:
            return {"available": True, "wake": False}
        woke, rest = heard(text)
        return {"available": True, "wake": woke, "rest": rest}
