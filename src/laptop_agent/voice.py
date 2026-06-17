from __future__ import annotations

import re

from laptop_agent.tools.base import ToolResult


# Sentence-ish boundary: terminal punctuation followed by whitespace, or a newline.
# Used to carve a streamed token feed into speakable chunks so text-to-speech can
# start on the first sentence instead of waiting for the whole reply.
_BOUNDARY = re.compile(r"[.!?…](?=\s)|\n")


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
