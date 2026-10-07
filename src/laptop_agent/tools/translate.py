"""Translation through NVIDIA's hosted Riva model (riva-translate-1.6b), over the same gRPC
host as Parakeet speech recognition. Stdlib-only at import, so the planner can read the
language list without the `riva` extra installed."""
from __future__ import annotations

import os
import re
from collections.abc import Callable

from laptop_agent import nvcf
from laptop_agent.failures import record_failure
from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

# The function id is what selects the model on NVIDIA's host, so it is overridable like the
# speech ones. riva-translate-4b-instruct-v2 ignores its target language through this
# endpoint and megatron-1b-nmt is not callable on this account (both measured 2026-10-06).
RIVA_NMT_FUNCTION_ID = "0778f2eb-b64d-45e7-acae-7dd9b9b35b4d"
RIVA_NMT_NAME = "ai-riva-translate-1_6b"

# Every language the model reports in its own config, by the names people say. There is no
# source detection on the service: an empty or "auto" source is refused.
LANGUAGES: dict[str, str] = {
    "arabic": "ar", "bulgarian": "bg", "czech": "cs", "danish": "da", "german": "de",
    "greek": "el", "english": "en", "spanish": "es-US", "latin american spanish": "es-US",
    "mexican spanish": "es-US", "castilian": "es-ES", "castilian spanish": "es-ES",
    "european spanish": "es-ES", "spanish from spain": "es-ES", "estonian": "et",
    "finnish": "fi", "french": "fr", "hindi": "hi", "croatian": "hr", "hungarian": "hu",
    "indonesian": "id", "italian": "it", "japanese": "ja", "korean": "ko", "lithuanian": "lt",
    "latvian": "lv", "dutch": "nl", "norwegian": "no", "polish": "pl", "portuguese": "pt-BR",
    "brazilian portuguese": "pt-BR", "european portuguese": "pt-PT", "romanian": "ro",
    "russian": "ru", "slovak": "sk", "slovenian": "sl", "swedish": "sv", "thai": "th",
    "turkish": "tr", "ukrainian": "uk", "vietnamese": "vi", "chinese": "zh-CN",
    "mandarin": "zh-CN", "simplified chinese": "zh-CN", "traditional chinese": "zh-TW",
    "taiwanese": "zh-TW",
}
_NAMES = {"es-US": "Spanish", "es-ES": "Spanish (Spain)", "pt-BR": "Portuguese",
          "pt-PT": "Portuguese (Portugal)", "zh-CN": "Chinese", "zh-TW": "Traditional Chinese"}

# Named so a request for one gets a straight answer instead of reaching the router as prose.
UNSUPPORTED = frozenset({
    "telugu", "tamil", "kannada", "malayalam", "bengali", "bangla", "marathi", "gujarati",
    "punjabi", "urdu", "nepali", "hebrew", "persian", "farsi", "swahili", "tagalog", "filipino",
    "malay", "catalan", "irish", "welsh", "icelandic", "serbian", "bosnian", "albanian",
    "armenian", "georgian", "latin", "cantonese",
})

_LANGUAGE = "|".join(sorted((re.escape(name) for name in (*LANGUAGES, *UNSUPPORTED)), key=len, reverse=True))
_REQUEST = re.compile(
    rf"^(?P<text>.+?)\s+(?:from\s+(?P<src1>{_LANGUAGE})\s+)?(?:to|into|in)\s+(?P<target>{_LANGUAGE})"
    rf"(?:\s+from\s+(?P<src2>{_LANGUAGE}))?(?:\s+please)?[\s?.!]*$",
    re.IGNORECASE,
)
# "this into French: the meeting is at noon" - the language first, then the text after a colon.
# Read before the form above, since the text may itself end in a language ("...: I speak English").
_LEADING = re.compile(
    rf"^(?:(?:this|that|it|the\s+following)\s+)?(?:from\s+(?P<src1>{_LANGUAGE})\s+)?(?:to|into|in)\s+"
    rf"(?P<target>{_LANGUAGE})(?:\s+from\s+(?P<src2>{_LANGUAGE}))?(?:\s+please)?\s*:\s*(?P<text>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_QUOTES = "\"'“”‘’"

# Scripts that name their language, so a translation into English needs no model to guess
# the source. Kana before Han: Japanese is written with both.
_SCRIPTS: tuple[tuple[str, str], ...] = (
    ("[぀-ヿ]", "ja"), ("[가-힯ᄀ-ᇿ]", "ko"), ("[一-鿿]", "zh-CN"),
    ("[ऀ-ॿ]", "hi"), ("[฀-๿]", "th"), ("[؀-ۿ]", "ar"),
    ("[Ͱ-Ͽ]", "el"), ("[іїєґ]", "uk"), ("[Ѐ-ӿ]", "ru"),
)
_UNSUPPORTED_SCRIPTS: tuple[tuple[str, str], ...] = (
    ("[ఀ-౿]", "Telugu"), ("[஀-௿]", "Tamil"), ("[ಀ-೿]", "Kannada"),
    ("[ഀ-ൿ]", "Malayalam"), ("[ঀ-৿]", "Bengali"), ("[઀-૿]", "Gujarati"),
    ("[਀-੿]", "Punjabi"), ("[֐-׿]", "Hebrew"),
)

_MAX_CHARS = 5000
_PIECE = 400
_TIMEOUT = 15.0

# (texts, source code, target code) -> translations, one per text. Injectable so the
# success path is tested offline without the riva extra.
Translator = Callable[[list[str], str, str], list[str]]


def language_code(name: str) -> str | None:
    return LANGUAGES.get(re.sub(r"\s+", " ", (name or "").strip().lower()))


def language_name(code: str) -> str:
    if code in _NAMES:
        return _NAMES[code]
    return next((name.title() for name, value in LANGUAGES.items() if value == code), code)


def parse_translation(text: str) -> tuple[str, str, str | None] | None:
    """`<text> to|into|in <language> [from <language>]`, `<text> from <language> to
    <language>`, or `to <language>: <text>`, as (text, target, source). None when no language
    closes the sentence or leads a colon, so a sentence that only starts with "translate" can
    go to the router instead."""
    found = _LEADING.match((text or "").strip()) or _REQUEST.match((text or "").strip())
    if not found:
        return None
    body = found.group("text").strip()
    if len(body) >= 2 and body[0] in _QUOTES and body[-1] in _QUOTES:
        body = body[1:-1].strip()
    if not body:
        return None
    source = found.group("src1") or found.group("src2")
    return body, found.group("target").lower(), source.lower() if source else None


def script_language(text: str) -> str | None:
    """The language a non-Latin script settles, or None for Latin or unknown script."""
    for pattern, code in _SCRIPTS:
        if re.search(pattern, text):
            return code
    return None


def _unsupported_script(text: str) -> str | None:
    for pattern, name in _UNSUPPORTED_SCRIPTS:
        if re.search(pattern, text):
            return name
    return None


def _pieces(text: str) -> list[list[tuple[str, str]]]:
    """Bound backend pieces; keep the separator before each piece for reconstruction."""
    lines: list[list[tuple[str, str]]] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            lines.append([])
            continue
        pieces: list[tuple[str, str]] = []
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            separator = " " if pieces else ""
            while sentence:
                if len(sentence) <= _PIECE:
                    if pieces and separator == " " and len(pieces[-1][1]) + len(sentence) + 1 <= _PIECE:
                        previous_separator, previous = pieces[-1]
                        pieces[-1] = (previous_separator, f"{previous} {sentence}")
                    else:
                        pieces.append((separator, sentence))
                    break
                cut = sentence.rfind(" ", 0, _PIECE + 1)
                if cut > 0:
                    pieces.append((separator, sentence[:cut]))
                    sentence = sentence[cut + 1:]
                    separator = " "
                else:
                    pieces.append((separator, sentence[:_PIECE]))
                    sentence = sentence[_PIECE:]
                    separator = ""
        lines.append(pieces)
    return lines


def _riva_translate(texts: list[str], source: str, target: str) -> list[str]:
    import grpc  # type: ignore
    import riva.client  # type: ignore

    from laptop_agent.tools.transcribe import RIVA_SERVER, _riva_key

    key = _riva_key()
    if not key:
        raise RuntimeError("Translation needs an NVIDIA API key in RIVA_API_KEY or OPENAI_API_KEY.")
    server = os.environ.get("RIVA_SERVER", RIVA_SERVER).strip() or RIVA_SERVER

    def attempt(function_id: str) -> list[str]:
        auth = riva.client.Auth(
            uri=server,
            use_ssl=True,
            metadata_args=[["function-id", function_id], ["authorization", f"Bearer {key}"]],
        )
        pending = None
        try:
            pending = riva.client.NeuralMachineTranslationClient(auth).translate(
                texts, "", source, target, future=True)
            try:
                response = pending.result(timeout=_TIMEOUT)
            except grpc.FutureTimeoutError as exc:
                raise TimeoutError("The translation service did not answer in time.") from exc
        finally:
            try:
                if pending is not None:
                    pending.cancel()
            finally:
                auth.channel.close()
        return [item.text for item in response.translations]

    return nvcf.call(RIVA_NMT_NAME, RIVA_NMT_FUNCTION_ID, "RIVA_NMT_FUNCTION_ID", key, attempt)


class TranslateTool:
    """`translate <text> to <language> [from <language>]`, by NVIDIA's hosted model."""

    def __init__(self, backend: Translator | None = None, approval_gate: ApprovalGate | None = None,
                 detect: Callable[[str], str | None] | None = None) -> None:
        self._translate = backend or _riva_translate
        self._gate = approval_gate
        # Names the language of Latin-script text bound for English; the orchestrator backs
        # it with the fast model tier.
        self._detect = detect

    def translate(self, text: str, target: str, source: str | None = None) -> ToolResult:
        text = (text or "").strip()
        if not text:
            return ToolResult.failure("What should I translate?")
        if len(text) > _MAX_CHARS:
            return ToolResult.failure(f"That is too long to translate in one go; send up to {_MAX_CHARS:,} characters.")
        for name in (target, source):
            if name and name.lower() in UNSUPPORTED:
                return self._unsupported(name.title())
        target_code = language_code(target)
        if target_code is None:
            return ToolResult.failure(f"I can't translate into {target}. {self._known()}")
        source_code = language_code(source) if source else None
        if source and source_code is None:
            return ToolResult.failure(f"I can't translate from {source}. {self._known()}")
        if source_code is None:
            missing = _unsupported_script(text)
            if missing:
                return self._unsupported(missing)
            source_code = script_language(text)
        if source_code is None:
            source_code = "en" if target_code != "en" else self._guess(text)
        if source_code is None:
            return ToolResult.failure(
                f"Which language is that in? Say it like: translate {text[:60]} from French to English.")
        if source_code == target_code:
            return ToolResult.success(f"That is already {language_name(target_code)}: {text}",
                                      translation=text, source=source_code, target=target_code)
        if self._gate is not None:  # it leaves the laptop -> MEDIUM, like the other network reads
            self._gate.require(ApprovalRequest(
                action=f"Translate text into {language_name(target_code)}",
                risk=RiskLevel.MEDIUM,
                reason="Translation sends the text to NVIDIA's hosted model.",
            ))
        lines = _pieces(text)
        flat = [piece for line in lines for _, piece in line]
        try:
            out = self._translate(flat, source_code, target_code)
        except ImportError:
            return ToolResult.failure("Translation needs: pip install nvidia-riva-client "
                                      "(or install this app's 'riva' extra).")
        except Exception as exc:
            record_failure("translate", exc)
            return ToolResult.failure(nvcf.describe(exc, "NVIDIA's translation service")
                                      or f"The translation service failed: {exc}")
        if len(out) != len(flat):
            error = RuntimeError(f"expected {len(flat)} translations, got {len(out)}")
            record_failure("translate/shape", error)
            return ToolResult.failure("The translation service returned an incomplete answer.")
        answers = iter(out)
        result = "\n".join("".join(separator + next(answers).strip() for separator, _ in line)
                           for line in lines).strip()
        if not result:
            return ToolResult.failure("The translation came back empty.")
        heading = f"{language_name(source_code)} → {language_name(target_code)}"
        return ToolResult.success(f"{heading}:\n\n{result}", translation=result, source=source_code,
                                  target=target_code)

    def _guess(self, text: str) -> str | None:
        if self._detect is None:
            return None
        try:
            named = self._detect(text)
        except Exception as exc:
            record_failure("translate/detect", exc)
            return None
        return language_code(re.sub(r"[^a-z ]+", "", (named or "").lower()))

    @staticmethod
    def _unsupported(name: str) -> ToolResult:
        return ToolResult.failure(f"{name} isn't one of the languages the translation model knows. "
                                  + TranslateTool._known())

    @staticmethod
    def _known() -> str:
        names = sorted({language_name(code) for code in LANGUAGES.values()})
        return "It knows: " + ", ".join(names) + "."
