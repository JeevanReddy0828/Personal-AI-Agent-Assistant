from __future__ import annotations

import re
from collections.abc import Callable

from laptop_agent.tools.base import ToolResult


class MissingTranscriptError(RuntimeError):
    """Raised when the transcript engine (optional extra) is not installed."""


# A transcript backend turns a video id into the full transcript text. Injectable so
# the success path is unit-tested offline without the optional dependency.
TranscriptBackend = Callable[[str], str]

_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")


def _default_transcript(video_id: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except ImportError as exc:
        raise MissingTranscriptError(
            "YouTube transcripts need: pip install youtube-transcript-api"
        ) from exc
    chunks = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
    return " ".join(str(chunk.get("text", "")) for chunk in chunks if chunk.get("text"))


class YouTubeTool:
    """Fetch a YouTube video's transcript so the agent can summarize / answer about it."""

    def __init__(self, transcript_backend: TranscriptBackend | None = None) -> None:
        self._fetch = transcript_backend or _default_transcript

    @staticmethod
    def extract_id(url: str) -> str | None:
        text = (url or "").strip().strip("<>'\"")
        match = _ID_RE.search(text)
        if match:
            return match.group(1)
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
            return text
        return None

    def transcript(self, url: str) -> ToolResult:
        video_id = self.extract_id(url)
        if not video_id:
            return ToolResult.failure("That doesn't look like a YouTube link or video id.")
        try:
            raw = self._fetch(video_id)
        except MissingTranscriptError as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # pragma: no cover - depends on the live service / captions.
            return ToolResult.failure(f"Couldn't get a transcript for that video (no captions?): {exc}")
        text = " ".join((raw or "").split())
        if not text:
            return ToolResult.failure("That video has no usable transcript or captions.")
        return ToolResult.success(
            f"Fetched transcript ({len(text)} characters).",
            video_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            transcript=text,
        )
