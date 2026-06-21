from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import laptop_agent.webui as webui
from laptop_agent.tools.travel import TravelTool

AUSTIN = {"results": [{"name": "Austin", "admin1": "Texas", "country": "United States",
                       "latitude": 30.27, "longitude": -97.74}]}
DALLAS = {"results": [{"name": "Dallas", "admin1": "Texas", "country": "United States",
                       "latitude": 32.78, "longitude": -96.80}]}
OSRM = {"routes": [{"distance": 312000.0, "duration": 17400.0}]}


def _transport(url: str):
    if "geocoding-api" in url:
        return DALLAS if "Dallas" in url else AUSTIN
    if "router.project-osrm" in url:
        return OSRM
    return {}


class MapApiTests(unittest.TestCase):
    """Exercise /api/map against a live server with an offline travel transport,
    so geocoding/routing never touch the network."""

    @classmethod
    def setUpClass(cls) -> None:
        # Inject a stubbed travel tool into the orchestrator's lazy cache (no gate).
        object.__setattr__(webui._orchestrator, "_travel_tool_cache",
                           TravelTool(transport=_transport))
        cls.server = ThreadingHTTPServer((webui.HOST, 8794), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:8794"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        object.__setattr__(webui._orchestrator, "_travel_tool_cache", None)

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + "/api/map",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def test_single_place(self) -> None:
        data = self._post({"query": "Austin"})
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["points"]), 1)
        self.assertIn("openstreetmap.org/export/embed", data["embed"])

    def test_route(self) -> None:
        data = self._post({"query": "Austin to Dallas"})
        self.assertTrue(data["ok"])
        self.assertEqual([p["role"] for p in data["points"]], ["origin", "destination"])
        self.assertIsNotNone(data["directions"])
        self.assertEqual(data["driving_mi"], round(312000 / 1609.34, 1))

    def test_empty_query(self) -> None:
        data = self._post({"query": ""})
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()
