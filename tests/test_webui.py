from __future__ import annotations

import unittest

from laptop_agent.webui import _CONFIG, _compose_command, _image_path, _probe_llm


class ComposeCommandTests(unittest.TestCase):
    def test_no_attachments_returns_command(self) -> None:
        self.assertEqual(_compose_command("hello there", []), "hello there")

    def test_bare_single_upload_routes_to_processor(self) -> None:
        self.assertEqual(_compose_command("", ["/tmp/report.csv"]), "process file /tmp/report.csv")

    def test_bare_multi_upload_uses_parallel_processor(self) -> None:
        result = _compose_command("", ["/tmp/a.csv", "/tmp/b.pdf"])
        self.assertEqual(result, "multi process file /tmp/a.csv ;; process file /tmp/b.pdf")

    def test_bare_image_upload_routes_to_vision_describe(self) -> None:
        # An image goes through the vision-first describe path (OCR fallback), not the
        # OCR-only file processor, so it works without the optional Tesseract binary.
        self.assertEqual(_compose_command("", ["/tmp/dl back.jpeg"]), "describe image /tmp/dl back.jpeg")
        self.assertEqual(_compose_command("", ["/tmp/shot.PNG"]), "describe image /tmp/shot.PNG")

    def test_bare_mixed_upload_routes_each_by_type(self) -> None:
        result = _compose_command("", ["/tmp/pic.jpg", "/tmp/data.csv"])
        self.assertEqual(result, "multi describe image /tmp/pic.jpg ;; process file /tmp/data.csv")

    def test_typed_message_keeps_command_and_appends_paths(self) -> None:
        result = _compose_command("what is the total revenue?", ["/tmp/sales.csv"])
        self.assertTrue(result.startswith("what is the total revenue?"))
        self.assertIn("/tmp/sales.csv", result)
        self.assertIn("attached file(s)", result)

    def test_ignores_blank_and_non_list_attachments(self) -> None:
        self.assertEqual(_compose_command("hi", [""]), "hi")
        self.assertEqual(_compose_command("hi", None), "hi")


class ImageRouteTests(unittest.TestCase):
    """The /api/image route resolves a bare filename inside the images directory only."""

    PNG_HEADER = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A]) + b"\n"

    def setUp(self) -> None:
        self.directory = (_CONFIG.data_dir / "images").resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.picture = self.directory / "fox-1.png"
        self.picture.write_bytes(self.PNG_HEADER)
        self.addCleanup(self.picture.unlink, True)

    def test_serves_a_generated_picture(self) -> None:
        self.assertEqual(_image_path("fox-1.png"), self.picture)

    def test_rejects_path_traversal_and_absolute_paths(self) -> None:
        for name in ("../../.env", "..\\..\\.env", "/etc/passwd", "sub/fox-1.png", ".hidden.png", ""):
            with self.subTest(name=name):
                self.assertIsNone(_image_path(name))

    def test_rejects_a_file_that_is_not_an_image_format(self) -> None:
        other = self.directory / "notes.txt"
        other.write_text("secret", encoding="utf-8")
        self.addCleanup(other.unlink, True)
        self.assertIsNone(_image_path("notes.txt"))

    def test_missing_file_is_not_found(self) -> None:
        self.assertIsNone(_image_path("nope.png"))


class LlmProbeTests(unittest.TestCase):
    """One refused ping used to show "AI unreachable" for the whole 200s warm cycle."""

    def test_a_single_refusal_is_retried(self) -> None:
        results = iter([False, True])
        calls: list[int] = []

        def ping() -> bool:
            calls.append(1)
            return next(results)

        self.assertTrue(_probe_llm(ping, delay=0))
        self.assertEqual(len(calls), 2)

    def test_a_first_success_does_not_ping_twice(self) -> None:
        calls: list[int] = []

        def ping() -> bool:
            calls.append(1)
            return True

        self.assertTrue(_probe_llm(ping, delay=0))
        self.assertEqual(len(calls), 1)

    def test_repeated_failure_reports_unreachable(self) -> None:
        self.assertFalse(_probe_llm(lambda: False, delay=0))

    def test_network_errors_are_treated_as_a_failed_ping(self) -> None:
        def ping() -> bool:
            raise TimeoutError("no route")

        self.assertFalse(_probe_llm(ping, delay=0))


if __name__ == "__main__":
    unittest.main()
