from __future__ import annotations

import importlib.util
import os
import re

from laptop_agent import nvcf
from laptop_agent.failures import record_failure
from laptop_agent.tools.base import ToolResult


# Sentence-ish boundary: terminal punctuation followed by whitespace, or a newline.
# Used to carve a streamed token feed into speakable chunks so text-to-speech can
# start on the first sentence instead of waiting for the whole reply.
_BOUNDARY = re.compile(r"[.!?…](?=\s)|\n")

# Markdown decorations that must not be read aloud (heading underlines like "=====",
# rules like "----", bullets, emphasis markers, code ticks, link brackets).
_DECOR_ONLY = re.compile(r"^[\s=\-*_~.#·•>|`]{2,}$")
_SPEAK_STRIP = re.compile(r"[`*_>#\[\]()|~]+")
# An embedded picture has nothing speakable in it, and reading its URL aloud used to
# feed a garbled "slash api slash image question mark name equals…" back into the
# microphone, which the echo guard could not match — so the agent answered itself.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# A link reads as its label; the target is noise.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
# Fenced code is not speech.
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
# An arrow reads as "to": the offline voice said "English rightward arrow Spanish" for a
# translation's heading and Magpie dropped it ("English, Spanish"), measured by sending each
# voice's audio back through Parakeet. "->" lost its ">" to the strip below and read as a dash.
_ARROW = re.compile(r"\s*(?:→|->)\s*")
# Bare URLs and paths, wherever they survive the above.
_BARE_URL = re.compile(r"(?:https?://|www\.)\S+|(?<![\w.])/\S*/\S+", re.IGNORECASE)


def clean_for_speech(text: str) -> str:
    """Strip markdown so text-to-speech doesn't read '=====' as 'equals equals…'.

    Returns '' for fragments that are purely decoration (heading underlines, rules),
    so the caller can skip speaking them entirely.
    """
    t = (text or "").strip()
    if not t or _DECOR_ONLY.match(t):
        return ""
    # Order matters: drop whole constructs before the punctuation strip breaks them apart.
    t = _CODE_FENCE.sub(" ", t)
    t = _MD_IMAGE.sub(" ", t)
    t = _MD_LINK.sub(lambda m: m.group(1), t)
    t = _BARE_URL.sub(" ", t)
    t = re.sub(r"^\s*[-*•·]\s+", "", t)          # leading bullet markers
    t = _ARROW.sub(" to ", t)
    t = _SPEAK_STRIP.sub(" ", t)                  # inline markdown markers
    t = re.sub(r"={2,}|-{3,}|\.{4,}|~{2,}", " ", t)  # leftover decorative runs
    t = re.sub(r"\s+", " ", t)
    # dropping a construct can strand a space in front of punctuation
    t = re.sub(r" ([.,!?;:])", lambda m: m.group(1), t).strip()
    return t


class SpeechChunker:
    """Turn a stream of text deltas into complete, speakable sentences.

    Feed it token deltas as they arrive; ``feed`` returns any sentences that became
    complete. Short fragments (abbreviations like "e.g.", quick "Hi.") are merged
    forward until they reach ``min_chars`` so the agent doesn't speak choppy
    one-word utterances. Call ``flush`` once the stream ends to get the tail.
    """

    def __init__(self, min_chars: int = 8) -> None:
        self.min_chars = max(0, min_chars)
        self._buf = ""
        self._pending = ""

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        self._buf += delta
        out: list[str] = []
        while True:
            match = _BOUNDARY.search(self._buf)
            if not match:
                break
            end = match.end()
            piece = self._buf[:end].strip()
            self._buf = self._buf[end:]
            self._pending = f"{self._pending} {piece}".strip() if self._pending else piece
            if len(self._pending) >= self.min_chars:
                out.append(self._pending)
                self._pending = ""
        return out

    def flush(self) -> str | None:
        tail = f"{self._pending} {self._buf}".strip() if self._pending else self._buf.strip()
        self._pending = ""
        self._buf = ""
        return tail or None


class VoiceIO:
    def speak(self, text: str) -> ToolResult:
        try:
            import pyttsx3  # type: ignore
        except ImportError:
            return ToolResult.failure("Text-to-speech requires: pip install pyttsx3")

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
        return ToolResult.success("Spoken response.")

    def listen_once(self, timeout: int = 5, phrase_time_limit: int = 12) -> ToolResult:
        try:
            import speech_recognition as sr  # type: ignore
        except ImportError:
            return ToolResult.failure("Speech recognition requires: pip install SpeechRecognition")

        recognizer = sr.Recognizer()
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.4)
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        try:
            text = recognizer.recognize_google(audio)
        except sr.UnknownValueError:
            return ToolResult.failure("I could not understand the audio.")
        except sr.RequestError as exc:
            return ToolResult.failure(f"Speech recognition failed: {exc}")
        return ToolResult.success("Heard voice input.", text=text)


# A TTS backend turns text into WAV bytes. Injectable so the success path is tested
# offline without a speech engine installed.
SpeechBackend = "Callable[[str], bytes]"


def _pyttsx3_wav(text: str) -> bytes:
    """Render text to a WAV file with the offline SAPI/espeak engine and read it back."""
    import tempfile
    from pathlib import Path

    import pyttsx3  # type: ignore

    engine = pyttsx3.init()
    dest = Path(tempfile.gettempdir()) / f"laptop_agent_tts_{abs(hash(text)) % 10_000_000}.wav"
    engine.save_to_file(text, str(dest))
    engine.runAndWait()
    try:
        return dest.read_bytes()
    finally:
        try:
            dest.unlink()
        except OSError:
            pass


# NVIDIA's hosted Magpie voice, on the same Riva gRPC host as Parakeet speech recognition.
# The function id is what selects the model, so it is overridable like the ASR one.
RIVA_TTS_FUNCTION_ID = "877104f7-e885-42b9-8de8-f6e4c6303969"
RIVA_TTS_NAME = "ai-magpie-tts-multilingual"
_MAGPIE_RATE = 22050


def _magpie_timeout(text: str) -> float:
    # Measured at about 4.6x faster than real time; a long sentence must not be cut off,
    # and a stalled call must hand over to the offline voice before the listener gives up.
    return min(30.0, 5.0 + len(text) / 50)


def _magpie_wav(text: str) -> bytes:
    """Hosted NVIDIA Magpie over Riva gRPC: a natural voice in ~0.4s per sentence."""
    import io
    import wave

    import grpc  # type: ignore
    import riva.client  # type: ignore

    from laptop_agent.tools.transcribe import RIVA_SERVER, _riva_key

    key = _riva_key()
    if not key:
        raise RuntimeError("The hosted voice needs an NVIDIA API key in RIVA_API_KEY or OPENAI_API_KEY.")
    server = os.environ.get("RIVA_SERVER", RIVA_SERVER).strip() or RIVA_SERVER
    voice = os.environ.get("RIVA_TTS_VOICE", "").strip() or None
    language = os.environ.get("RIVA_TTS_LANGUAGE", "en-US").strip() or "en-US"

    def attempt(function_id: str) -> bytes:
        auth = riva.client.Auth(
            uri=server,
            use_ssl=True,
            metadata_args=[["function-id", function_id], ["authorization", f"Bearer {key}"]],
        )
        pending = None
        try:
            pending = riva.client.SpeechSynthesisService(auth).synthesize(
                text, voice_name=voice, language_code=language, sample_rate_hz=_MAGPIE_RATE,
                encoding=riva.client.AudioEncoding.LINEAR_PCM, future=True,
            )
            try:
                return pending.result(timeout=_magpie_timeout(text)).audio
            except grpc.FutureTimeoutError as exc:
                raise TimeoutError("The hosted voice did not answer in time.") from exc
        finally:
            try:
                if pending is not None:
                    pending.cancel()
            finally:
                auth.channel.close()

    audio = nvcf.call(RIVA_TTS_NAME, RIVA_TTS_FUNCTION_ID, "RIVA_TTS_FUNCTION_ID", key, attempt)
    if not audio:
        raise RuntimeError("The hosted voice returned no audio.")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_MAGPIE_RATE)
        wav.writeframes(audio)
    return out.getvalue()


def _tts_choice() -> str:
    return os.environ.get("LAPTOP_AGENT_TTS", "auto").strip().lower()


def _default_tts_backend(text: str) -> bytes:
    """LAPTOP_AGENT_TTS=riva|offline, or 'auto' (default): the hosted voice when it is
    usable, and the offline one when it is not or a call fails, so losing the network
    costs the voice its quality, never its speech."""
    from laptop_agent.tools.transcribe import _riva_available

    engine = _tts_choice()
    if engine == "riva":
        return _magpie_wav(text)
    if engine != "offline" and _riva_available():
        try:
            return _magpie_wav(text)
        except Exception as exc:
            record_failure("tts/magpie", exc)
    return _pyttsx3_wav(text)


def tts_engine_name() -> str | None:
    """Which voice /api/tts would use, or None if it has none."""
    from laptop_agent.tools.transcribe import _riva_available

    engine = _tts_choice()
    if engine != "offline" and _riva_available():
        return "riva:magpie"
    if engine != "riva" and importlib.util.find_spec("pyttsx3") is not None:
        return "pyttsx3"
    return None


def synthesize_wav(text: str, backend=None) -> bytes | None:
    """Return spoken-audio WAV bytes for ``text`` server-side.

    Returns None when the text is empty or no engine is available, so the web
    layer can fall back gracefully instead of crashing. The engine call sits
    behind an injectable ``backend`` so tests run without a speech engine installed.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    render = backend or _default_tts_backend
    try:
        data = render(cleaned)
    except ImportError:
        return None
    except Exception as exc:  # pragma: no cover - depends on the live engine.
        record_failure("tts/render", exc)
        return None
    return data or None
