from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import laptop_agent.webui as webui
from laptop_agent.copilot import JobCopilot


class CopilotApiTests(unittest.TestCase):
    """Exercise /api/copilot. The copilot is forced to no-LLM (deterministic ATS only)
    so the test never makes a network call regardless of the local .env."""

    @classmethod
    def setUpClass(cls) -> None:
        webui._orchestrator._copilot_cache = JobCopilot(decide=None)
        cls.server = ThreadingHTTPServer((webui.HOST, 8799), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:8799"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        webui._orchestrator._copilot_cache = None

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + "/api/copilot", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def test_tailor_returns_ats_and_missing(self) -> None:
        data = self._post({
            "resume_text": "- Built a Python FastAPI service with Redis.",
            "job_text": "Need Python, FastAPI, and AWS experience.",
            "company": "Acme", "role": "Backend",
        })
        self.assertTrue(data["ok"])
        self.assertIn("score", data["ats"])
        self.assertIn("aws", [m.lower() for m in data["missing"]])
        self.assertFalse(data["used_llm"])
        self.assertIn("ATS match", data["message"])

    def test_missing_inputs_rejected(self) -> None:
        self.assertFalse(self._post({"resume_text": "", "job_text": "Need Python."})["ok"])


if __name__ == "__main__":
    unittest.main()
