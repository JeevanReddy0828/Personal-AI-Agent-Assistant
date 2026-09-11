from __future__ import annotations

import importlib.util
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
        self._ocr_backend = ocr_backend or _default_ocr_backend
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


# nemotron-parse reads a page rather than a line of characters: it returns typed,
# positioned regions, so a heading stays a heading. Measured on a 453KB screenshot:
# 2.6s, 54 regions, types Page-header/Title/Section-header/Text/Table/Picture/Caption.
_PARSE_MODEL = "nvidia/nemotron-parse"
# Base64 inflates by 4/3 and the endpoint rejects very large bodies; a page scan is
# comfortably under this.
_PARSE_MAX_BYTES = 6_000_000
_PARSE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}
# How a region type becomes Markdown. Anything unlisted is emitted as a plain line.
_PARSE_PREFIX = {"Title": "# ", "Section-header": "## ", "List-item": "- "}
# Furniture that repeats on every page and adds nothing to the extracted text.
# "Caption" is dropped for a different reason: the model invents them. A 453KB
# screenshot of this app's own home view returned 37 caption regions for 2 pictures,
# one of which read "Figure 1: The S-color image of the alpha-ray diffraction
# pattern..." - fabricated from a paper it was trained on. A caption without its
# figure adds nothing here even when it is real.
_PARSE_SKIP = {"Page-header", "Page-footer", "Picture", "Caption"}


def _parse_available() -> bool:
    return bool(_riva_key())


def _nemotron_parse_ocr_backend(target: Path) -> str:
    """Hosted NVIDIA document parsing, the OCR counterpart to Riva for speech.

    Tesseract returns characters; this returns a laid-out page, which is what makes an
    extracted document readable afterwards. It needs the network, so the caller falls
    back to the local engine - losing the network should cost quality, not the feature.
    """
    import base64
    import json
    import urllib.error
    import urllib.request

    key = _riva_key()
    if not key:
        raise MissingDependencyError("Hosted OCR needs an NVIDIA API key (OPENAI_API_KEY in .env).")
    raw = target.read_bytes()
    if len(raw) > _PARSE_MAX_BYTES:
        raise MissingDependencyError(
            f"{target.name} is {len(raw) // 1_000_000}MB; hosted OCR takes images under "
            f"{_PARSE_MAX_BYTES // 1_000_000}MB."
        )
    mime = _PARSE_MIME.get(target.suffix.lower(), "image/png")
    encoded = base64.b64encode(raw).decode("ascii")
    base = "https://integrate.api.nvidia.com/v1"
    try:
        from laptop_agent.config import load_config

        base = (load_config().llm_base_url or base).rstrip("/")
    except Exception:  # pragma: no cover - config is optional for this tool
        pass
    body = {
        "model": _PARSE_MODEL,
        # The model takes an image and nothing else: a text part is rejected outright
        # with "The model does not support text input".
        "messages": [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
            ]}
        ],
        "max_tokens": 4000,
        "stream": False,
    }
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.load(response)
    return _parse_regions_to_markdown(payload)


def _parse_regions_to_markdown(payload: dict) -> str:
    """Turn the model's regions into Markdown, top to bottom.

    The result arrives as a `markdown_bbox` tool call rather than message content -
    content is null - and each region carries a normalised bbox and a type.
    """
    import json

    message = (payload.get("choices") or [{}])[0].get("message") or {}
    calls = message.get("tool_calls") or []
    if not calls:
        return str(message.get("content") or "")
    try:
        regions = json.loads(calls[0]["function"]["arguments"])
    except (KeyError, ValueError, TypeError):
        return ""
    # The arguments are a list holding one list of regions.
    if regions and isinstance(regions[0], list):
        regions = regions[0]

    def position(region: object) -> tuple[float, float]:
        box = region.get("bbox") or {} if isinstance(region, dict) else {}
        return (float(box.get("ymin", 0.0) or 0.0), float(box.get("xmin", 0.0) or 0.0))

    lines: list[str] = []
    for region in sorted((r for r in regions if isinstance(r, dict)), key=position):
        kind = str(region.get("type") or "")
        if kind in _PARSE_SKIP:
            continue
        text = " ".join(str(region.get("text") or "").split())
        if text:
            lines.append(_PARSE_PREFIX.get(kind, "") + text)
    return "\n\n".join(lines)


def _default_ocr_backend(target: Path) -> str:
    """Hosted parsing when a key is present, the local engine otherwise.

    Same shape as the speech path: prefer the accurate hosted engine, fall through to
    local on any failure so an offline laptop still reads its own screenshots.
    """
    engine = os.environ.get("LAPTOP_AGENT_OCR", "auto").strip().lower()
    if engine == "tesseract":
        return _builtin_ocr_backend(target)
    if engine == "parse":
        return _nemotron_parse_ocr_backend(target)
    if _parse_available():
        try:
            text = _nemotron_parse_ocr_backend(target)
            if text.strip():
                return text
        except Exception:
            pass
    return _builtin_ocr_backend(target)


def ocr_engine_name() -> str | None:
    """Which OCR engine an image would actually reach, for /api/health."""
    engine = os.environ.get("LAPTOP_AGENT_OCR", "auto").strip().lower()
    if engine == "parse":
        return "nemotron-parse" if _parse_available() else None
    if engine == "tesseract":
        return "tesseract" if importlib.util.find_spec("pytesseract") is not None else None
    if _parse_available():
        return "nemotron-parse"
    return "tesseract" if importlib.util.find_spec("pytesseract") is not None else None


def _builtin_ocr_backend(target: Path) -> str:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise MissingDependencyError(
            "Image OCR requires: pip install pytesseract pillow (and the Tesseract OCR binary on PATH), "
            "or set an NVIDIA API key in .env to use hosted OCR instead."
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
    if importlib.util.find_spec("whisper") is None:
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
    if importlib.util.find_spec("vosk") is None:
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


# NVIDIA's hosted speech models are Riva gRPC, not the REST catalog: /v1/audio/transcriptions
# returns 404 on both API hosts. Server and function id are overridable because the id
# identifies the model (this one is parakeet-tdt-0.6b-v2).
RIVA_SERVER = "grpc.nvcf.nvidia.com:443"
RIVA_ASR_FUNCTION_ID = "d3fe9151-442b-4204-a70d-5fcc597fd610"


def _riva_key() -> str:
    for name in ("RIVA_API_KEY", "NVIDIA_API_KEY", "OPENAI_API_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    # The key normally arrives from .env, which only load_config() reads. Anything that
    # reaches this tool without having loaded the config would otherwise fall back to a
    # slower local engine for no reason.
    try:
        from laptop_agent.config import load_config

        return (load_config().llm_api_key or "").strip()
    except Exception:  # pragma: no cover - config is optional for this tool
        return ""


def _riva_available() -> bool:
    return importlib.util.find_spec("riva") is not None and bool(_riva_key())


def _riva_asr_backend(target: Path) -> dict[str, object]:
    """Hosted NVIDIA Parakeet over Riva gRPC: the most accurate engine here, and the
    fastest (~1s), but it needs the network and only takes PCM WAV."""
    import wave

    try:
        import riva.client  # type: ignore
    except ImportError as exc:
        raise MissingDependencyError(
            "Cloud speech recognition needs: pip install nvidia-riva-client "
            "(or install this app's 'riva' extra)."
        ) from exc
    key = _riva_key()
    if not key:
        raise MissingDependencyError(
            "Cloud speech recognition needs an NVIDIA API key in RIVA_API_KEY or OPENAI_API_KEY."
        )
    try:
        wf = wave.open(str(target), "rb")
    except (wave.Error, EOFError) as exc:
        raise RuntimeError(f"Cloud speech recognition needs PCM WAV audio: {exc}") from exc
    with wf:
        if wf.getsampwidth() != 2:
            raise RuntimeError("Cloud speech recognition needs 16-bit PCM WAV audio.")
        channels, rate = wf.getnchannels(), wf.getframerate()
        audio = wf.readframes(wf.getnframes())
    if not audio:
        raise RuntimeError("That audio file is empty.")

    server = os.environ.get("RIVA_SERVER", RIVA_SERVER).strip() or RIVA_SERVER
    function_id = os.environ.get("RIVA_ASR_FUNCTION_ID", RIVA_ASR_FUNCTION_ID).strip() or RIVA_ASR_FUNCTION_ID
    language = os.environ.get("RIVA_ASR_LANGUAGE", "en-US").strip() or "en-US"
    auth = riva.client.Auth(
        uri=server,
        use_ssl=True,
        metadata_args=[["function-id", function_id], ["authorization", f"Bearer {key}"]],
    )
    config = riva.client.RecognitionConfig(
        encoding=riva.client.AudioEncoding.LINEAR_PCM,
        language_code=language,
        max_alternatives=1,
        enable_automatic_punctuation=True,
        sample_rate_hertz=rate,
        audio_channel_count=channels,
    )
    response = riva.client.ASRService(auth).offline_recognize(audio, config)
    text = " ".join(
        result.alternatives[0].transcript for result in response.results if result.alternatives
    ).strip()
    return {"text": text, "segments": [], "language": language, "engine": "riva:parakeet"}


def _default_asr_backend(target: Path) -> dict[str, object]:
    """Pick the STT engine: LAPTOP_AGENT_STT=riva|vosk|whisper, or 'auto' (default),
    which prefers hosted Parakeet when it is usable, then the lightweight Vosk when a
    model is present, else Whisper.

    Riva only accepts PCM WAV, so 'auto' skips it for other media, and a failed cloud
    call falls through to a local engine rather than losing the transcription — the
    point of a local-first app is that the network is optional."""
    engine = os.environ.get("LAPTOP_AGENT_STT", "auto").strip().lower()
    if engine == "riva":
        return _riva_asr_backend(target)
    if engine == "vosk":
        return _vosk_asr_backend(target)
    if engine == "whisper":
        return _builtin_asr_backend(target)
    if target.suffix.lower() == ".wav" and _riva_available():
        try:
            return _riva_asr_backend(target)
        except (MissingDependencyError, RuntimeError, OSError):
            pass
    if _vosk_available():
        return _vosk_asr_backend(target)
    return _builtin_asr_backend(target)


def stt_engine_name() -> str | None:
    """Which speech engine a recording would actually reach, or None if there is none.

    The web page uses this to decide whether to record and post audio to the server
    instead of trusting the browser's own recognizer."""
    engine = os.environ.get("LAPTOP_AGENT_STT", "auto").strip().lower()
    if engine == "riva":
        return "riva:parakeet" if _riva_available() else None
    if engine == "vosk":
        return "vosk" if _vosk_available() else None
    if engine == "whisper":
        return "whisper" if importlib.util.find_spec("whisper") is not None else None
    if _riva_available():
        return "riva:parakeet"
    if _vosk_available():
        return "vosk"
    return "whisper" if importlib.util.find_spec("whisper") is not None else None


def warm_stt() -> bool:
    """Pre-load whichever STT engine is selected so the first voice turn isn't slow."""
    engine = os.environ.get("LAPTOP_AGENT_STT", "auto").strip().lower()
    # Riva is a network call with nothing to pre-load, and it is what 'auto' reaches for
    # first, so there is no local model to warm in that case.
    if engine == "riva":
        return _riva_available()
    if engine in ("vosk", "auto") and _vosk_available():
        try:
            _load_vosk_model(_resolve_vosk_model_path())  # type: ignore[arg-type]
            return True
        except Exception:  # pragma: no cover - depends on the model files.
            pass
    if engine == "vosk":
        return False
    if engine == "auto" and _riva_available():
        return True
    return warm_whisper()


def _builtin_asr_backend(target: Path) -> dict[str, object]:
    if importlib.util.find_spec("whisper") is None:
        raise MissingDependencyError(
            "Media transcription requires: pip install openai-whisper (and ffmpeg on PATH)."
        )
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
