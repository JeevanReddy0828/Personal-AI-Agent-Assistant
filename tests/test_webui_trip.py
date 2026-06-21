from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import laptop_agent.webui as webui
from laptop_agent.tools.travel import TravelTool

AUSTIN = {"results": [{"name": "Austin", "admin1": "Texas", "country": "US", "latitude": 30.27, "longitude": -97.74}]}
DALLAS = {"results": [{"name": "Dallas", "admin1": "Texas", "country": "US", "latitude": 32.78, "longitude": -96.80}]}
HOUSTON = {"results": [{"name": "Houston", "admin1": "Texas", "country": "US", "latitude": 29.76, "longitude": -95.37}]}
OSRM_TRIP = {"routes": [{"distance": 480000.0, "duration": 28800.0,
    "geometry": {"coordinates": [[-97.74, 30.27], [-96.80, 32.78], [-95.37, 29.76]]},
    "legs": [{"distance": 312000.0, "duration": 17400.0}, {"distance": 168000.0, "duration": 11400.0}]}]}


def _transport(url: str):
    if "geocoding-api" in url:
        return DALLAS if "Dallas" in url else HOUSTON if "Houston" in url else AUSTIN
    if "router.project-osrm" in url:
        return OSRM_TRIP
    return {}


class TripApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        object.__setattr__(webui._orchestrator, "_travel_tool_cache", TravelTool(transport=_transport))
        cls.server = ThreadingHTTPServer((webui.HOST, 8797), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:8797"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        object.__setattr__(webui._orchestrator, "_travel_tool_cache", None)

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + "/api/trip",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def test_plans_multi_stop(self) -> None:
        data = self._post({"stops": ["Austin", "Dallas", "Houston"]})
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["legs"]), 2)
        self.assertEqual(len(data["points"]), 3)
        self.assertTrue(data["geometry"])
        self.assertIn("openstreetmap.org/directions", data["directions"])

    def test_needs_two_stops(self) -> None:
        data = self._post({"stops": ["Austin"]})
        self.assertFalse(data["ok"])

    def test_blank_stops_dropped(self) -> None:
        data = self._post({"stops": ["Austin", "   ", ""]})
        self.assertFalse(data["ok"])  # only one real stop left


if __name__ == "__main__":
    unittest.main()
