from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import laptop_agent.webui as webui
from laptop_agent.reasoning import AgentRunResult, AgentRunTracker, AgentStep


class AgentRunsApiTests(unittest.TestCase):
    """Exercise /api/agent-runs against a live server with isolated run history."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls._original_tracker = webui._orchestrator.context.agent_runs
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        object.__setattr__(webui._orchestrator.context, "agent_runs", cls._original_tracker)
        cls._tmp.cleanup()

    def setUp(self) -> None:
        safe_name = self.id().replace(".", "_")
        tracker = AgentRunTracker(Path(self._tmp.name) / f"{safe_name}.json")
        # AgentContext is a frozen dataclass; swap the tracker in without rebuilding the app.
        object.__setattr__(webui._orchestrator.context, "agent_runs", tracker)
        self.tracker = tracker

    def _get(self) -> dict:
        return json.loads(urllib.request.urlopen(self.base + "/api/agent-runs", timeout=15).read())

    def test_empty_returns_no_runs(self) -> None:
        self.assertEqual(self._get(), {"ok": True, "runs": []})

    def test_lists_recorded_agent_run(self) -> None:
        self.tracker.record_run(
            AgentRunResult(
                goal="summarize <vault>",
                final_answer="Done <safely>.",
                status="ok",
                steps=[
                    AgentStep(
                        index=0,
                        thought="Need files",
                        command="scan files .",
                        status="ok",
                        message="[ok] scanned",
                    ),
                    AgentStep(
                        index=1,
                        thought="Report result",
                        command="notes status",
                        status="failed",
                        message="[failed] no vault",
                    ),
                ],
            )
        )

        data = self._get()

        self.assertTrue(data["ok"])
        self.assertEqual(len(data["runs"]), 1)
        run = data["runs"][0]
        self.assertEqual(run["goal"], "summarize <vault>")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["final_answer"], "Done <safely>.")
        self.assertEqual(run["step_count"], 2)
        self.assertEqual(run["ok_count"], 1)
        self.assertEqual(run["failed_count"], 1)
        self.assertEqual(len(run["steps"]), 2)
        self.assertEqual(run["steps"][0]["command"], "scan files .")
        self.assertEqual(run["steps"][1]["status"], "failed")

    def test_post_not_supported(self) -> None:
        req = urllib.request.Request(self.base + "/api/agent-runs", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(req, timeout=15)
        self.assertEqual(raised.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
