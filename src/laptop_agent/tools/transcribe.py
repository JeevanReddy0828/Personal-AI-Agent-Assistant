from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from laptop_agent.tools.base import ToolResult


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


class MissingDependencyError(RuntimeError):
    """Raised by a backend when its optional engine is not installed."""


# An OCR backend turns an image path into extracted text.
OcrBackend = Callable[[Path], str]
# An ASR backend turns a media path into a dict with at least a "text" key.
AsrBackend = Callable[[Path], dict[str, object]]


class TranscribeTool:
    """Extract text from images (OCR) and audio/video (speech-to-text).

    Both operations are read-only and run locally, so they do not require an
    approval gate. The heavy engines are optional: when an engine is missing the
    tool returns a clear failure with an install hint instead of raising. The
    actual engine call sits behind an injectable backend so the success path can
    be exercised without the engine installed.
    """

    def __init__(self, ocr_backend: OcrBackend | None = None, asr_backend: AsrBackend | None = None) -> None:
        self._ocr_backend = ocr_backend or _builtin_ocr_backend
        self._asr_backend = asr_backend or _default_asr_backend

    def ocr_image(self, path: str, max_chars: int = 20000) -> ToolResult:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return ToolResult.failure(f"Image does not exist: {target}")
        if target.suffix.lower() not in IMAGE_EXTENSIONS:
            return ToolResult.failure(
                f"OCR supports image files, not {target.suffix or 'unknown'}.",
                supported=sorted(IMAGE_EXTENSIONS),
            )
        try:
            text = self._ocr_backend(target)
        except MissingDependencyError as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # pragma: no cover - depends on the live engine.
            return ToolResult.failure(f"OCR failed: {exc}")

        cleaned = text.strip()
        return ToolResult.success(
            f"Extracted {len(cleaned)} character(s) of text from {target.name}.",
            path=str(target),
            text=cleaned[:max_chars],
            char_count=len(cleaned),
            truncated=len(cleaned) > max_chars,
        )

    def transcribe_media(self, path: str, max_chars: int = 40000) -> ToolResult:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return ToolResult.failure(f"Media file does not exist: {target}")
        if target.suffix.lower() not in MEDIA_EXTENSIONS:
            return ToolResult.failure(
                f"Transcription supports audio/video files, not {target.suffix or 'unknown'}.",
                supported=sorted(MEDIA_EXTENSIONS),
            )
        try:
            result = self._asr_backend(target)
        except MissingDependencyError as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # pragma: no cover - depends on the live engine.
            return ToolResult.failure(f"Transcription failed: {exc}")

        text = str(result.get("text", "")).strip()
        segments = result.get("segments") or []
        return ToolResult.success(
            f"Transcribed {target.name} into {len(text)} character(s).",
            path=str(target),
            kind="video" if target.suffix.lower() in VIDEO_EXTENSIONS else "audio",
            text=text[:max_chars],
            char_count=len(text),
            truncated=len(text) > max_chars,
            language=result.get("language"),
            engine=result.get("engine"),
            segment_count=len(segments) if isinstance(segments, list) else 0,
        )


def _builtin_ocr_backend(target: Path) -> str:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise MissingDependencyError(
            "Image OCR requires: pip install pytesseract pillow (and the Tesseract OCR binary on PATH)."
        ) from exc
    try:
        with Image.open(target) as image:
            return pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError as exc:  # type: ignore[attr-defined]
        raise MissingDependencyError(
            "Tesseract OCR binary not found. Install it and ensure it is on PATH "
            "(Windows: https://github.com/UB-Mannheim/tesseract/wiki)."
        ) from exc


# Whisper models are expensive to load (hundreds of MB from disk). Cache by name so
# the voice loop loads once and stays warm — otherwise every utterance reloads it.
_WHISPER_MODELS: dict[str, object] = {}


def _load_whisper_model(model_name: str):
    model = _WHISPER_MODELS.get(model_name)
    if model is None:
        import whisper  # type: ignore

        model = whisper.load_model(model_name)
        _WHISPER_MODELS[model_name] = model
    return model


def warm_whisper() -> bool:
    """Pre-load the speech model so the first voice turn isn't slow. Returns False
    (no-op) when Whisper isn't installed, so callers can warm it best-effort."""
    try:
        import whisper  # type: ignore  # noqa: F401
    except ImportError:
        return False
    try:
        _load_whisper_model(os.environ.get("LAPTOP_AGENT_WHISPER_MODEL", "base"))
        return True
    except Exception:  # pragma: no cover - depends on the live engine/model download.
        return False


# --- Vosk: lightweight offline speech-to-text (~50MB model, no PyTorch, no ffmpeg).
# Reads 16-bit mono PCM WAV with the stdlib `wave` module, so the packaged app can
# stay small. Whisper remains available for higher accuracy.
_VOSK_MODELS: dict[str, object] = {}


def _resolve_vosk_model_path() -> str | None:
    explicit = os.environ.get("VOSK_MODEL") or os.environ.get("LAPTOP_AGENT_VOSK_MODEL")
    if explicit and Path(explicit).exists():
        return explicit
    bases = []
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).parent)
    bases.append(Path.cwd())
    bases.append(Path(__file__).resolve().parents[3])  # repo root (src/laptop_agent/tools/..)
    for base in bases:
        models = base / "models"
        if models.is_dir():
            for child in sorted(models.iterdir()):
                if child.is_dir() and "vosk" in child.name.lower():
                    return str(child)
    return None


def _load_vosk_model(path: str):
    model = _VOSK_MODELS.get(path)
    if model is None:
        from vosk import Model  # type: ignore

        model = Model(path)
        _VOSK_MODELS[path] = model
    return model


def _vosk_available() -> bool:
    try:
        import vosk  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return _resolve_vosk_model_path() is not None


def _vosk_asr_backend(target: Path) -> dict[str, object]:
    import json as _json
    import wave

    try:
        from vosk import KaldiRecognizer  # type: ignore
    except ImportError as exc:
        raise MissingDependencyError(
            "Lightweight speech needs: pip install vosk, plus a model in a 'models' folder "
            "(e.g. vosk-model-small-en-us-0.15 from https://alphacephei.com/vosk/models)."
        ) from exc
    model_path = _resolve_vosk_model_path()
    if not model_path:
        raise MissingDependencyError(
            "No Vosk model found. Put one in a 'models' folder next to the app "
            "(e.g. vosk-model-small-en-us-0.15) or set VOSK_MODEL to its path."
        )
    model = _load_vosk_model(model_path)
    try:
        wf = wave.open(str(target), "rb")
    except (wave.Error, EOFError) as exc:
        raise RuntimeError(f"Vosk needs 16-bit mono PCM WAV audio: {exc}") from exc
    with wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError("Vosk needs 16-bit mono PCM WAV audio.")
        recognizer = KaldiRecognizer(model, wf.getframerate())
        parts: list[str] = []
        while True:
            frames = wf.readframes(4000)
            if not frames:
                break
            if recognizer.AcceptWaveform(frames):
                parts.append(str(_json.loads(recognizer.Result()).get("text", "")))
        parts.append(str(_json.loads(recognizer.FinalResult()).get("text", "")))
    text = " ".join(part for part in parts if part).strip()
    return {"text": text, "segments": [], "language": "en", "engine": f"vosk:{Path(model_path).name}"}


def _default_asr_backend(target: Path) -> dict[str, object]:
    """Pick the STT engine: LAPTOP_AGENT_STT=vosk|whisper, or 'auto' (default) which
    prefers the lightweight Vosk when a model is present, else Whisper."""
    engine = os.environ.get("LAPTOP_AGENT_STT", "auto").strip().lower()
    if engine == "vosk":
        return _vosk_asr_backend(target)
    if engine == "whisper":
        return _builtin_asr_backend(target)
    if _vosk_available():
        return _vosk_asr_backend(target)
    return _builtin_asr_backend(target)


def warm_stt() -> bool:
    """Pre-load whichever STT engine is selected so the first voice turn isn't slow."""
    engine = os.environ.get("LAPTOP_AGENT_STT", "auto").strip().lower()
    if engine in ("vosk", "auto") and _vosk_available():
        try:
            _load_vosk_model(_resolve_vosk_model_path())  # type: ignore[arg-type]
            return True
        except Exception:  # pragma: no cover - depends on the model files.
            pass
    if engine == "vosk":
        return False
    return warm_whisper()


def _builtin_asr_backend(target: Path) -> dict[str, object]:
    try:
        import whisper  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise MissingDependencyError(
            "Media transcription requires: pip install openai-whisper (and ffmpeg on PATH)."
        ) from exc
    model_name = os.environ.get("LAPTOP_AGENT_WHISPER_MODEL", "base")
    model = _load_whisper_model(model_name)
    # Pin the language (default English). Without this, Whisper auto-detects per clip
    # and on short utterances often guesses wrong, transcribing English speech as
    # garbled Turkish/French/etc. Set LAPTOP_AGENT_WHISPER_LANG=auto to re-enable
    # detection, or to another code (e.g. "es") for a different language.
    lang = os.environ.get("LAPTOP_AGENT_WHISPER_LANG", "en").strip().lower()
    options: dict[str, object] = {"fp16": False}
    if lang and lang != "auto":
        options["language"] = lang
    result = model.transcribe(str(target), **options)
    segments = [
        {"start": segment.get("start"), "end": segment.get("end"), "text": str(segment.get("text", "")).strip()}
        for segment in result.get("segments", [])
        if isinstance(segment, dict)
    ]
    return {
        "text": result.get("text", ""),
        "segments": segments,
        "language": result.get("language"),
        "engine": f"openai-whisper:{model_name}",
    }
