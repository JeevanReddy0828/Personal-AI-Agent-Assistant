from __future__ import annotations

import json
import base64
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import laptop_agent.webui as webui
from laptop_agent.autopilot import AutopilotPlanner
from laptop_agent.safety import ApprovalDenied, ApprovalGate
from laptop_agent.tools.desktop import DesktopTool


class SecurityRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, body, extra=None, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Jarvis-Token"] = webui._API_TOKEN
        headers.update(extra or {})
        req = urllib.request.Request(self.base + "/api/command", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_local_authenticated_command(self):
        code, body = self.request({"command": "help"})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_browser_origins_and_missing_tokens_rejected(self):
        for headers in ({"Origin": "https://untrusted.example"}, {"Host": "rebind.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request({"command": "help"}, headers)[0], 403)
        self.assertEqual(self.request({"command": "help"}, token=False)[0], 403)

    def test_bad_payloads_return_errors(self):
        for payload in ([], None, {"command": []}, {"command": "help", "attachments": 3}):
            self.assertEqual(self.request(payload)[0], 400)
        self.assertEqual(self.request({"command": "help"}, {"Content-Type": "text/plain"})[0], 400)

    def test_page_prohibits_inline_events_and_framing(self):
        with urllib.request.urlopen(self.base) as response:
            policy = response.headers["Content-Security-Policy"]
            self.assertIn("script-src 'nonce-", policy)
            self.assertIn("frame-ancestors 'none'", policy)
            self.assertNotIn("unsafe-inline", policy.split("script-src", 1)[1].split(";", 1)[0])

    def test_safe_autopilot_rejects_mutation_and_recursion(self):
        for command in ("knowledge clear", "knowledge forget 1", "agent run delete things", "tasks retry failed", "reminders done 1", "helpful"):
            self.assertFalse(AutopilotPlanner.is_safe_command(command), command)
        self.assertTrue(AutopilotPlanner.is_safe_command("knowledge stats"))
        self.assertTrue(AutopilotPlanner.is_safe_command("read file README.md"))

    def test_uploads_are_isolated_and_invalid_base64_is_rejected(self):
        def upload(data):
            req = urllib.request.Request(self.base + "/api/upload", data=json.dumps({"name": "same.txt", "data": data}).encode(), headers={"Content-Type": "application/json", "X-Jarvis-Token": webui._API_TOKEN})
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read())
        first = upload(base64.b64encode(b"first").decode())
        second = upload(base64.b64encode(b"second").decode())
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(Path(first["path"]).read_bytes(), b"first")
        self.assertEqual(Path(second["path"]).read_bytes(), b"second")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            upload("not-valid-base64$$")
        self.assertEqual(raised.exception.code, 400)

    def test_transcription_removes_its_unique_temporary_recording(self):
        from laptop_agent.tools.base import ToolResult
        captured = []
        def transcribe(path):
            captured.append(Path(path))
            self.assertTrue(Path(path).exists())
            return ToolResult.success("ok", text="hello")
        req = urllib.request.Request(self.base + "/api/transcribe", data=json.dumps({"audio": base64.b64encode(b"audio").decode()}).encode(), headers={"Content-Type": "application/json", "X-Jarvis-Token": webui._API_TOKEN})
        with patch.object(webui._orchestrator.context.transcribe, "transcribe_media", side_effect=transcribe):
            with urllib.request.urlopen(req) as response:
                self.assertTrue(json.loads(response.read())["ok"])
        self.assertTrue(captured)
        self.assertFalse(captured[0].exists())

    def test_guarded_app_launch_cannot_reach_os(self):
        tool = DesktopTool(ApprovalGate(webui._guarded_approval))
        with patch("os.startfile", create=True) as launch:
            with self.assertRaises(ApprovalDenied):
                tool.open_app_or_file("not-executed.exe")
            launch.assert_not_called()
