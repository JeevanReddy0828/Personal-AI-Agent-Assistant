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


class JobsApiTests(unittest.TestCase):
    """Exercise the /api/jobs GET + POST round trip against a live server, with the
    tracker pointed at a throwaway file so the user's data is untouched."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tracker = JobTracker(Path(cls._tmp.name) / "jobs.json")
        object.__setattr__(webui._orchestrator.context, "jobs", tracker)
        cls.server = ThreadingHTTPServer((webui.HOST, 8798), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:8798"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()

    def _get(self) -> dict:
        return json.loads(urllib.request.urlopen(self.base + "/api/jobs", timeout=15).read())

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + "/api/jobs", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def test_add_update_remove_roundtrip(self) -> None:
        self.assertEqual(self._get()["jobs"], [])

        added = self._post({"action": "add", "company": "Stripe", "role": "SWE", "stage": "screen"})
        self.assertTrue(added["ok"])
        self.assertEqual(added["stats"]["total"], 1)
        job_id = added["jobs"][0]["id"]
        self.assertEqual(added["jobs"][0]["stage"], "screen")

        updated = self._post({"action": "update", "id": job_id, "stage": "offer"})
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["stats"]["offers"], 1)

        removed = self._post({"action": "remove", "id": job_id})
        self.assertTrue(removed["ok"])
        self.assertEqual(removed["stats"]["total"], 0)

    def test_add_requires_company(self) -> None:
        data = self._post({"action": "add", "company": ""})
        self.assertFalse(data["ok"])  # ValueError surfaced, snapshot still returned
        self.assertIn("stats", data)

    def test_unknown_action_rejected(self) -> None:
        try:
            self._post({"action": "frobnicate"})
            self.fail("expected HTTP 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


if __name__ == "__main__":
    unittest.main()
