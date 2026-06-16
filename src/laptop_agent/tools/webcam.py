from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.transcribe import MissingDependencyError


# A capture backend writes one webcam frame to ``dest`` and returns the path it wrote.
CaptureBackend = Callable[[int, Path], Path]


class WebcamTool:
    """Capture a single still frame from a webcam.

    Reading from the camera is a local, read-only capture, so it does not need an
    approval gate (the orchestrator still narrates that it looked). The capture
    engine (OpenCV) is an optional extra: when it is missing the tool returns a
    clear install hint instead of raising. The engine call sits behind an
    injectable backend so the success path is unit-tested without a camera.
    """

    def __init__(self, capture_backend: CaptureBackend | None = None) -> None:
        self._capture_backend = capture_backend or _builtin_capture_backend

    def capture(self, device: int = 0, dest: str | None = None) -> ToolResult:
        if dest:
            target = Path(dest).expanduser()
        else:
            handle = tempfile.NamedTemporaryFile(prefix="laptop_agent_webcam_", suffix=".png", delete=False)
            handle.close()
            target = Path(handle.name)
        try:
            written = self._capture_backend(device, target)
        except MissingDependencyError as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # pragma: no cover - depends on live hardware.
            return ToolResult.failure(f"Webcam capture failed: {exc}")

        path = Path(written)
        if not path.exists() or path.stat().st_size == 0:
            return ToolResult.failure(
                "Webcam returned no image — is a camera connected and not in use by another app?"
            )
        return ToolResult.success(
            f"Captured a webcam frame to {path.name}.",
            path=str(path),
            device=device,
            bytes=path.stat().st_size,
        )


def _builtin_capture_backend(device: int, dest: Path) -> Path:
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise MissingDependencyError(
            "Webcam capture requires: pip install laptop-agent[vision]  (installs opencv-python)."
        ) from exc

    camera = cv2.VideoCapture(device)
    try:
        if not camera.isOpened():
            raise MissingDependencyError(
                f"Could not open webcam device {device}. Check it is connected and not in use."
            )
        ok, frame = camera.read()
        if not ok or frame is None:
            raise RuntimeError("camera did not return a frame")
        dest.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dest), frame)
    finally:
        camera.release()
    return dest
