from __future__ import annotations

from laptop_agent.tools.base import ToolResult


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
