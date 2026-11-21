from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_orchestrator
from test_document import SAMPLE

import laptop_agent.webui as webui
from laptop_agent.cancellation import cancel
from laptop_agent.model_status import BROKEN, DEGRADED, ModelStatus
from laptop_agent.planner import Planner
from laptop_agent.tools.document import DocumentTool
from laptop_agent.tools.imagegen import ImageTool
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
        self.picture.write_bytes(bytes([0x89]) + b"PNG")
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


class KeepWarmStatusTests(unittest.TestCase):
    """The keep-warm ping recorded every failure without its reason, which reads as busy:
    a tier already known to be broken was demoted, retried after 60s instead of 900, and
    dropped from the file that carries it across a restart."""

    def refresh(self, outcome: tuple[str, str] | None) -> Path:
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "model_status.json"
        status = ModelStatus(path)
        status.record("fast", False, reason=BROKEN, detail="that model id has been retired")

        def ping(on_failure=None) -> bool:
            if outcome is None:
                return True
            if on_failure is not None:
                on_failure(*outcome)
            return False

        planner = SimpleNamespace(provider=SimpleNamespace(ping=ping))
        with (
            patch.object(webui._orchestrator, "planner", planner),
            patch.object(webui._orchestrator, "model_status", status),
            patch.object(webui, "_probe_llm", partial(_probe_llm, delay=0)),
            patch.dict(webui._LLM_STATUS),
        ):
            webui._refresh_llm_status()
        return path

    def test_a_ping_that_fails_the_same_way_keeps_the_tier_broken(self) -> None:
        path = self.refresh((BROKEN, "that model id has been retired - HTTP 410"))
        self.assertEqual(ModelStatus(path).broken_tiers(), ["fast"])

    def test_a_busy_ping_is_still_busy_and_a_good_one_clears_it(self) -> None:
        busy = self.refresh((DEGRADED, "the model did not answer in time"))
        self.assertEqual(ModelStatus(busy).broken_tiers(), [])
        self.assertEqual(ModelStatus(self.refresh(None)).broken_tiers(), [])


class RenderedPageTests(unittest.TestCase):
    """The page is 179KB and every placeholder in it is fixed for the life of the process,
    so it was being rendered and sent in full on every single load."""

    def test_the_page_is_rendered_once(self) -> None:
        from laptop_agent.webui import _rendered_page

        first_body, first_etag = _rendered_page()
        second_body, second_etag = _rendered_page()
        self.assertIs(first_body, second_body, "the page was re-rendered")
        self.assertEqual(first_etag, second_etag)

    def test_the_etag_is_quoted_and_no_placeholders_survive(self) -> None:
        from laptop_agent.webui import _rendered_page

        body, etag = _rendered_page()
        self.assertTrue(etag.startswith('"') and etag.endswith('"'), etag)
        self.assertNotIn(b"{{", body, "a placeholder was left unrendered")


class PortGuardTests(unittest.TestCase):
    """`allow_reuse_address` is needed so TIME_WAIT does not block a restart, but on Windows
    it also lets a second process bind a port already being served. Two instances ran at
    once, answering at random and holding separate approval and passcode state."""

    def test_a_served_port_is_detected(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from laptop_agent.webui import _already_serving

        class Quiet(BaseHTTPRequestHandler):
            def do_GET(self):  # pragma: no cover - only needs to accept a connection
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertTrue(_already_serving("127.0.0.1", port))
            self.assertTrue(_already_serving("0.0.0.0", port), "a wildcard bind must probe loopback")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertFalse(_already_serving("127.0.0.1", port), "a closed port must look free")


class SlowToolStatusTests(unittest.TestCase):
    """A slow tool streams a fixed `status` line before its result, and Stop saves nothing."""

    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.orchestrator = self.build(self.root / "data")
        original = webui._orchestrator
        webui._orchestrator = self.orchestrator
        self.addCleanup(setattr, webui, "_orchestrator", original)

    def _stream(self, command: str, request_id: str = "slow-tool-test") -> list[dict]:
        req = urllib.request.Request(
            self.base + "/api/stream",
            data=json.dumps({"command": command, "history": []}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Jarvis-Token": webui._API_TOKEN,
                "X-Jarvis-Request": request_id,
            },
        )
        body = urllib.request.urlopen(req, timeout=15).read().decode()
        blocks = [block.strip() for block in body.split("\n\n")]
        return [json.loads(block[5:]) for block in blocks if block.startswith("data:")]

    def assert_status_then_result(self, events: list[dict], status: str, private: str) -> dict:
        kinds = [event["type"] for event in events]
        self.assertIn("status", kinds, events)
        self.assertLess(kinds.index("status"), kinds.index("done"))
        shown = [event for event in events if event["type"] == "status"]
        self.assertEqual(shown, [{"type": "status", "text": status}])
        self.assertNotIn(private, json.dumps(shown).lower())
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertTrue(done["ok"], done["message"])
        return done

    def test_writing_a_document_says_so_without_the_request(self) -> None:
        self.orchestrator._document_tool_cache = DocumentTool(data_dir=self.root, writer=lambda prompt: SAMPLE)
        events = self._stream("document a brief on the quarterly lunch plan as markdown")
        self.assert_status_then_result(events, "Writing the document", "lunch")

    def test_drawing_a_picture_says_so_without_the_prompt(self) -> None:
        jpeg = base64.b64encode(bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(32)).decode()
        self.orchestrator._image_tool_cache = ImageTool(
            api_key="", data_dir=self.root, backend=lambda model, body: {"artifacts": [{"base64": jpeg}]}
        )
        events = self._stream("image a red fox asleep in the snow")
        self.assert_status_then_result(events, "Drawing the picture", "fox")

    def test_describing_an_image_says_so_without_the_path(self) -> None:
        class FakeVision:
            def describe_image(self, path, prompt, model=None):
                return "A photo of a receipt."

        picture = self.root / "holiday_receipt.png"
        picture.write_bytes(bytes([0x89]) + b"PNG")
        self.orchestrator.vision_planner = Planner(FakeVision())
        events = self._stream(f"describe image {picture}")
        done = self.assert_status_then_result(events, "Looking at the image", "receipt")
        self.assertEqual(done["message"], "A photo of a receipt.")

    def test_a_quick_command_streams_no_status(self) -> None:
        events = self._stream("calc 2 + 2")
        self.assertNotIn("status", [event["type"] for event in events])

    def test_stop_while_the_document_is_written_saves_no_file(self) -> None:
        def writer(prompt: str) -> str:
            cancel("stop-mid-document")  # Stop pressed while the model was still writing
            return SAMPLE

        self.orchestrator._document_tool_cache = DocumentTool(data_dir=self.root, writer=writer)
        events = self._stream("document a brief on rate limits as markdown", request_id="stop-mid-document")
        self.assertEqual([event["type"] for event in events], ["status"])
        self.assertEqual(list((self.root / "documents").glob("*")), [])


if __name__ == "__main__":
    unittest.main()
