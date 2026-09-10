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
from laptop_agent.tools.base import ToolResult

# A backend takes the request body and returns the parsed JSON response. Injectable so
# the success path is unit-tested offline, per the weather/websearch pattern.
ImageBackend = Callable[[dict], dict]

BASE_URL = "https://ai.api.nvidia.com/v1/genai"
# FLUX.2 Klein answers in ~2s on the free NVIDIA tier; flux.1-schnell queues for minutes.
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


class ImageTool:
    """Text-to-image generation via NVIDIA's hosted FLUX endpoints.

    Writes the picture under ``<data_dir>/images`` and returns Markdown that embeds it,
    so the chat renders the image inline instead of describing it.
    """

    def __init__(
        self,
        api_key: str | None,
        data_dir: Path,
        model: str = DEFAULT_MODEL,
        backend: ImageBackend | None = None,
        approval_gate: ApprovalGate | None = None,
        timeout: int = 120,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model or DEFAULT_MODEL
        self.directory = Path(data_dir) / "images"
        self.timeout = timeout
        self._backend = backend or self._http_backend
        # A caller-supplied backend (tests) works without a key; the real one needs one.
        self._injected = backend is not None
        self._gate = approval_gate

    def available(self) -> bool:
        return bool(self.api_key) or self._injected

    def _http_backend(self, body: dict) -> dict:
        request = urllib.request.Request(
            f"{BASE_URL}/{self.model}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

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
        try:
            payload = self._backend(body)
        except urllib.error.HTTPError as exc:
            return ToolResult.failure(f"The image service refused the request (HTTP {exc.code}).")
        except (urllib.error.URLError, TimeoutError) as exc:
            return ToolResult.failure(f"Could not reach the image service: {exc}. It may be busy — try again.")
        except (json.JSONDecodeError, ValueError) as exc:
            return ToolResult.failure(f"The image service sent something I could not read: {exc}")

        artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
        encoded = ""
        if isinstance(artifacts, list) and artifacts and isinstance(artifacts[0], dict):
            encoded = str(artifacts[0].get("base64") or artifacts[0].get("b64_json") or "")
        if not encoded:
            return ToolResult.failure("The image service returned no picture.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            return ToolResult.failure("The image service returned a picture I could not decode.")

        name = f"{_slug(described)}-{int(time.time())}.{_extension(raw)}"
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / name
        path.write_bytes(raw)
        url = f"/api/image?name={name}"
        return ToolResult.success(
            f"![{described}]({url})\n\nHere is *{described}*.",
            image=str(path),
            name=name,
            url=url,
            prompt=described,
            model=self.model,
            width=width,
            height=height,
            bytes=len(raw),
        )
