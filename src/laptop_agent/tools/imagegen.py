from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult, reserve_new_path

# A backend takes the model id and the request body and returns the parsed JSON
# response. Injectable so the success path is unit-tested offline, per the
# weather/websearch pattern.
ImageBackend = Callable[[str, dict], dict]

# Image generation lives on a different NVIDIA host from chat, and the model id is part
# of the path rather than the request body.
BASE_URL = "https://ai.api.nvidia.com/v1/genai"
# FLUX.2 Klein answers in ~2s on the free tier; flux.1-schnell often queues for minutes,
# which makes it a reasonable fallback but a poor default.
DEFAULT_MODEL = "black-forest-labs/flux.2-klein-4b"
# Resolutions the FLUX endpoints accept.
SIZES = {
    "square": (1024, 1024),
    "landscape": (1344, 768),
    "portrait": (768, 1344),
    "wide": (1216, 832),
    "tall": (832, 1216),
}
_MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"RIFF": "webp"}


def _extension(raw: bytes) -> str:
    for magic, ext in _MAGIC.items():
        if raw.startswith(magic):
            return ext
    return "png"


def _slug(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:limit].strip("-") or "image"


class ImageGenerationError(RuntimeError):
    """One model attempt failed; the caller may still try the fallback."""


class ImageTool:
    """Text-to-image generation via NVIDIA's hosted FLUX endpoints.

    Writes the picture under ``<data_dir>/images`` and returns Markdown that embeds it,
    so the chat renders the image inline instead of describing it. A second model can be
    configured as a fallback: these are shared free-tier endpoints, and one of them
    queueing is an ordinary event rather than a bug.
    """

    def __init__(
        self,
        api_key: str | None,
        data_dir: Path,
        model: str = DEFAULT_MODEL,
        backend: ImageBackend | None = None,
        approval_gate: ApprovalGate | None = None,
        timeout: int = 120,
        base_url: str = BASE_URL,
        fallback_model: str | None = None,
        fallback_api_key: str | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model or DEFAULT_MODEL
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.fallback_model = (fallback_model or "").strip() or None
        # A fallback model may sit behind its own key; without one it reuses the primary's.
        self.fallback_api_key = (fallback_api_key or "").strip() or self.api_key
        self.directory = Path(data_dir) / "images"
        self.timeout = timeout
        self._backend = backend or self._http_backend
        # A caller-supplied backend (tests) works without a key; the real one needs one.
        self._injected = backend is not None
        self._gate = approval_gate

    def available(self) -> bool:
        return bool(self.api_key) or self._injected

    def _key_for(self, model: str) -> str:
        return self.fallback_api_key if model == self.fallback_model else self.api_key

    def _http_backend(self, model: str, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/{model}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._key_for(model)}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        # The fallback only runs after the primary already spent its budget, so give it
        # half — a last resort must not double the time the user waits for a failure.
        budget = self.timeout if model == self.model else max(20, self.timeout // 2)
        with urllib.request.urlopen(request, timeout=budget) as response:
            return json.loads(response.read().decode("utf-8"))

    def _attempt(self, model: str, body: dict) -> bytes:
        """One model attempt, raising ImageGenerationError with a readable reason."""
        try:
            payload = self._backend(model, body)
        except urllib.error.HTTPError as exc:
            raise ImageGenerationError(f"{model} refused the request (HTTP {exc.code})") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ImageGenerationError(f"{model} did not respond ({exc})") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise ImageGenerationError(f"{model} sent something I could not read ({exc})") from exc

        artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
        encoded = ""
        if isinstance(artifacts, list) and artifacts and isinstance(artifacts[0], dict):
            encoded = str(artifacts[0].get("base64") or artifacts[0].get("b64_json") or "")
        if not encoded:
            raise ImageGenerationError(f"{model} returned no picture")
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ImageGenerationError(f"{model} returned a picture I could not decode") from exc

    def generate(self, prompt: str, shape: str = "square", seed: int | None = None, steps: int = 4) -> ToolResult:
        described = (prompt or "").strip().strip("\"'")
        if not described:
            return ToolResult.failure("What should I draw? Try 'image a brass compass on a desk'.")
        if not self.available():
            return ToolResult.failure(
                "Image generation needs an NVIDIA API key. Set OPENAI_IMAGE_KEY (or "
                "OPENAI_API_KEY) in .env — get one free at https://build.nvidia.com."
            )
        width, height = SIZES.get(shape, SIZES["square"])
        if self._gate is not None:  # network call that writes a local file -> MEDIUM
            self._gate.require(
                ApprovalRequest(
                    action=f"Generate an image of {described[:120]}",
                    risk=RiskLevel.MEDIUM,
                    reason="Image generation sends the prompt to an external service and saves a file locally.",
                )
            )
        body: dict[str, object] = {"prompt": described, "width": width, "height": height, "steps": steps}
        if seed is not None:
            body["seed"] = seed

        candidates = [self.model] + ([self.fallback_model] if self.fallback_model else [])
        problems: list[str] = []
        raw, used = b"", ""
        for candidate in candidates:
            try:
                raw, used = self._attempt(candidate, body), candidate
                break
            except ImageGenerationError as exc:
                problems.append(str(exc))
        if not raw:
            return ToolResult.failure(
                "I could not generate that picture. " + "; ".join(problems) + ".",
                attempts=problems,
            )

        path = reserve_new_path(
            self.directory, f"{_slug(described)}-{int(time.time())}", f".{_extension(raw)}"
        )
        path.write_bytes(raw)
        name = path.name
        url = f"/api/image?name={name}"
        note = "" if used == self.model else f" _(drawn with {used} — the usual model was busy)_"
        return ToolResult.success(
            f"![{described}]({url})\n\nHere is *{described}*.{note}",
            image=str(path),
            name=name,
            url=url,
            prompt=described,
            model=used,
            fell_back=used != self.model,
            width=width,
            height=height,
            bytes=len(raw),
        )
