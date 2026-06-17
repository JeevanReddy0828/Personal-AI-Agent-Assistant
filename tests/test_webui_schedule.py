from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import laptop_agent.webui as webui
from laptop_agent.scheduler import SchedulerStore


class ScheduleApiTests(unittest.TestCase):
    """Exercise the /api/schedule GET + POST round trip against a live server,
    with the scheduler pointed at a throwaway file so the user's jobs are untouched."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        store = SchedulerStore(Path(cls._tmp.name) / "sched.json")
        # AgentContext is a frozen dataclass; swap the store in without rebuilding the app.
        object.__setattr__(webui._orchestrator.context, "scheduler", store)
        cls.server = ThreadingHTTPServer((webui.HOST, 8793), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:8793"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + "/api/schedule",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def _get(self) -> dict:
        return json.loads(urllib.request.urlopen(self.base + "/api/schedule", timeout=15).read())

    def test_add_list_toggle_remove(self) -> None:
        # starts empty
        self.assertEqual(self._get()["jobs"], [])

        added = self._post({"action": "add", "kind": "command", "when": "daily at 08:00", "spec": "scan files ."})
        self.assertTrue(added["ok"])
        self.assertEqual(len(added["jobs"]), 1)
        job = added["jobs"][0]
        self.assertEqual(job["kind"], "command")
        self.assertEqual(job["schedule_text"], "daily at 08:00")
        self.assertTrue(job["enabled"])
        job_id = job["id"]

        disabled = self._post({"action": "disable", "id": job_id})
        self.assertTrue(disabled["ok"])
        self.assertFalse(disabled["jobs"][0]["enabled"])

        removed = self._post({"action": "remove", "id": job_id})
        self.assertTrue(removed["ok"])
        self.assertEqual(removed["jobs"], [])

    def test_add_requires_when_and_spec(self) -> None:
        try:
            self._post({"action": "add", "kind": "command", "when": "", "spec": ""})
            self.fail("expected HTTP 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_unknown_action_rejected(self) -> None:
        try:
            self._post({"action": "frobnicate"})
            self.fail("expected HTTP 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


if __name__ == "__main__":
    unittest.main()
