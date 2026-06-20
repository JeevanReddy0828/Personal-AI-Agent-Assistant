from __future__ import annotations

import re

from laptop_agent.tools.base import ToolResult


# Sentence-ish boundary: terminal punctuation followed by whitespace, or a newline.
# Used to carve a streamed token feed into speakable chunks so text-to-speech can
# start on the first sentence instead of waiting for the whole reply.
_BOUNDARY = re.compile(r"[.!?…](?=\s)|\n")

# Markdown decorations that must not be read aloud (heading underlines like "=====",
# rules like "----", bullets, emphasis markers, code ticks, link brackets).
_DECOR_ONLY = re.compile(r"^[\s=\-*_~.#·•>|`]{2,}$")
_SPEAK_STRIP = re.compile(r"[`*_>#\[\]()|~]+")


def clean_for_speech(text: str) -> str:
    """Strip markdown so text-to-speech doesn't read '=====' as 'equals equals…'.

    Returns '' for fragments that are purely decoration (heading underlines, rules),
    so the caller can skip speaking them entirely.
    """
    t = (text or "").strip()
    if not t or _DECOR_ONLY.match(t):
        return ""
    t = re.sub(r"^\s*[-*•·]\s+", "", t)          # leading bullet markers
    t = _SPEAK_STRIP.sub(" ", t)                  # inline markdown markers
    t = re.sub(r"={2,}|-{3,}|\.{4,}|~{2,}", " ", t)  # leftover decorative runs
    t = re.sub(r"\s+", " ", t).strip()
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


def synthesize_wav(text: str, backend=None) -> bytes | None:
    """Return spoken-audio WAV bytes for ``text`` server-side (offline TTS).

    Returns None when the text is empty or no engine is available, so the web
    layer can fall back gracefully instead of crashing. The engine call sits
    behind an injectable ``backend`` so tests run without pyttsx3 installed.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    render = backend or _pyttsx3_wav
    try:
        data = render(cleaned)
    except ImportError:
        return None
    except Exception:  # pragma: no cover - depends on the live engine.
        return None
    return data or None
