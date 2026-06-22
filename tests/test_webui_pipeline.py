from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import laptop_agent.webui as webui
from laptop_agent.jobs import JobTracker


class PipelineApiTests(unittest.TestCase):
    """Exercise the /api/pipeline GET + POST round trip against a live server, with the
    tracker pointed at a throwaway file so the user's data is untouched."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tracker = JobTracker(Path(cls._tmp.name) / "jobs.json")
        object.__setattr__(webui._orchestrator.context, "jobs", cls.tracker)
        cls.server = ThreadingHTTPServer((webui.HOST, 8800), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:8800"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()

    def _get(self) -> dict:
        return json.loads(urllib.request.urlopen(self.base + "/api/pipeline", timeout=15).read())

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + "/api/pipeline", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def test_resume_set_and_live_scoring(self) -> None:
        # A lead with a description but no resume -> no ATS score.
        self.tracker.add("Acme", role="ML Engineer", stage="lead",
                         description="Python, Kubernetes and machine learning required.")
        snap = self._get()
        self.assertFalse(snap["resume"]["present"])

        # Set the base resume -> scoring lights up.
        set_resume = self._post({"action": "resume", "text": "- Built ML systems in Python"})
        self.assertTrue(set_resume["ok"])
        self.assertTrue(set_resume["resume"]["present"])
        lead = next(j for j in set_resume["jobs"] if j["company"] == "Acme")
        self.assertIn("ats", lead)
        self.assertGreaterEqual(lead["ats"]["score"], 0)

    def test_tailor_without_description_fails_gracefully(self) -> None:
        self._post({"action": "resume", "text": "- Built things in Python"})
        added = self.tracker.add("Globex", role="SWE", stage="lead")  # no description
        data = self._post({"action": "tailor", "id": added["id"]})
        self.assertFalse(data["ok"])  # surfaced, snapshot still returned
        self.assertIn("jobs", data)

    def test_unknown_action_rejected(self) -> None:
        try:
            self._post({"action": "frobnicate"})
            self.fail("expected HTTP 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


if __name__ == "__main__":
    unittest.main()
