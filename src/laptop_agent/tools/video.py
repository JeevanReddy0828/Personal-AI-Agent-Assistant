"""Get a clip into the shape the hosted video model accepts.

Measured against `nvidia/nemotron-3.5-super-vl-120b-a12b` on the hosted endpoint: an MP4 sent
inline as a base64 `video_url` is read at 4MB (13s) and 8MB (34s); at 27MB the connection is
cut before any answer. The model reads a fixed handful of frames (about 2.5k tokens) whether
the clip is 20 or 60 seconds, so resolution and length buy nothing past a small file. Only MP4
was tried, so every other container goes through ffmpeg too.

ffmpeg is optional, like every other heavy dependency here: without it a clip that is already a
small MP4 still works, and anything else says what is missing instead of failing in the upload.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

MAX_VIDEO_BYTES = 8 * 1024 * 1024
_SHRINK_TIMEOUT = 300


def _megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:.1f}MB"


def prepare_clip(
    source: Path,
    scratch: Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[Path | None, str]:
    """(the file to send, "") or (None, why it cannot be sent)."""
    size = source.stat().st_size
    if source.suffix.lower() == ".mp4" and size <= MAX_VIDEO_BYTES:
        return source, ""
    needs = (f"is {_megabytes(size)} and the limit is {_megabytes(MAX_VIDEO_BYTES)}"
             if size > MAX_VIDEO_BYTES else f"is a {source.suffix.lower()} file and only MP4 is sent as it is")
    if which("ffmpeg") is None:
        return None, f"That clip {needs}, and ffmpeg is not installed to convert it. Install ffmpeg, or send a small MP4."
    out = scratch / "clip.mp4"
    command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-an",
               "-vf", "scale='min(854,iw)':-2", "-r", "8", "-c:v", "libx264", "-crf", "32",
               "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    try:
        done = runner(command, capture_output=True, text=True, timeout=_SHRINK_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"ffmpeg could not convert that clip: {exc}"
    if done.returncode != 0 or not out.is_file():
        return None, "ffmpeg could not read that file as a video."
    if out.stat().st_size > MAX_VIDEO_BYTES:
        return None, (f"Even shrunk the clip is {_megabytes(out.stat().st_size)}; "
                      f"the limit is {_megabytes(MAX_VIDEO_BYTES)}. Try a shorter clip.")
    return out, ""
