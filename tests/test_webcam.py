from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.tools.transcribe import MissingDependencyError
from laptop_agent.tools.webcam import WebcamTool


class WebcamToolTests(unittest.TestCase):
    def test_capture_success_with_injected_backend(self) -> None:
        def backend(device: int, dest: Path) -> Path:
            dest.write_bytes(b"\x89PNG\r\n\x1a\n-fake-frame")
            return dest

        with tempfile.TemporaryDirectory() as raw:
            dest = Path(raw) / "frame.png"
            result = WebcamTool(capture_backend=backend).capture(device=1, dest=str(dest))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["device"], 1)
            self.assertEqual(result.data["path"], str(dest))
            self.assertGreater(result.data["bytes"], 0)

    def test_missing_dependency_returns_install_hint(self) -> None:
        def backend(device: int, dest: Path) -> Path:
            raise MissingDependencyError("Webcam capture requires: pip install laptop-agent[vision]")

        result = WebcamTool(capture_backend=backend).capture()
        self.assertFalse(result.ok)
        self.assertIn("vision", result.message)  # hint points at the optional extra

    def test_empty_frame_is_failure(self) -> None:
        def backend(device: int, dest: Path) -> Path:
            dest.write_bytes(b"")  # camera returned nothing
            return dest

        with tempfile.TemporaryDirectory() as raw:
            result = WebcamTool(capture_backend=backend).capture(dest=str(Path(raw) / "empty.png"))
            self.assertFalse(result.ok)
            self.assertIn("no image", result.message.lower())

    def test_backend_exception_is_caught(self) -> None:
        def backend(device: int, dest: Path) -> Path:
            raise RuntimeError("device busy")

        result = WebcamTool(capture_backend=backend).capture()
        self.assertFalse(result.ok)
        self.assertIn("device busy", result.message)


if __name__ == "__main__":
    unittest.main()
