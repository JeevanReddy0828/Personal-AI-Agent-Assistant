"""Requested browser recordings, saved separately from temporary speech clips."""
from __future__ import annotations

import io
import re
import secrets
import wave
from pathlib import Path
from decimal import Decimal

MAX_SECONDS = 120
MAX_WAV_BYTES = 44 + MAX_SECONDS * 16000 * 2
# "for up to 20 seconds" and a trailing "please?" are how the request is actually said; the
# planner removes the polite prefix and turns spoken numbers into digits before this runs.
_RECORD = re.compile(
    r"(?:record|start\s+(?:a\s+)?(?:voice\s+)?recording)"
    r"(?:\s+(?:my\s+)?(?:voice|audio)|\s+(?:a\s+)?voice\s+(?:note|memo))?"
    r"(?:\s+(?:for\s+)?(?:up\s*to\s+)?(-?\d+(?:\.\d+)?)"
    r"\s*(seconds?|secs?|s|minutes?|mins?|m)?)?(?:\s*,?\s*please)?[.!?]*", re.I)


def recording_seconds(text: str) -> Decimal | None:
    match = _RECORD.fullmatch(text.strip())
    if not match:
        return None
    seconds = Decimal(match[1] or '20')
    return seconds * (60 if (match[2] or '').lower().startswith('m') else 1)


def save_recording(data_dir: Path, raw: bytes) -> dict:
    if len(raw) > MAX_WAV_BYTES:
        raise ValueError('Recording exceeds 120 seconds.')
    if len(raw) < 44 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise ValueError('Recording must be PCM WAV.')
    if int.from_bytes(raw[4:8], 'little') + 8 != len(raw):
        raise ValueError('Incomplete WAV recording.')
    try:
        with wave.open(io.BytesIO(raw), 'rb') as audio:
            frames = audio.getnframes()
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, 'NONE'):
                raise ValueError('Recording must be 16 kHz mono 16-bit PCM WAV.')
            if not 0 < frames <= MAX_SECONDS * 16000 or len(audio.readframes(frames)) != frames * 2:
                raise ValueError('Empty, incomplete or overlong recording.')
    except (wave.Error, EOFError) as exc:
        raise ValueError('Invalid WAV recording.') from exc
    directory = data_dir / 'recordings'
    directory.mkdir(parents=True, exist_ok=True)
    name = 'recording-' + secrets.token_hex(16) + '.wav'
    with (directory / name).open('xb') as file:
        file.write(raw)
    return {'name': name, 'seconds': frames / 16000}
