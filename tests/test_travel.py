from __future__ import annotations

import unittest

from laptop_agent.safety import ApprovalDenied, ApprovalGate
from laptop_agent.tools.travel import TravelTool

AUSTIN = {"results": [{"name": "Austin", "admin1": "Texas", "country": "United States",
                       "latitude": 30.27, "longitude": -97.74}]}
DALLAS = {"results": [{"name": "Dallas", "admin1": "Texas", "country": "United States",
                       "latitude": 32.78, "longitude": -96.80}]}
OSRM = {"routes": [{"distance": 312000.0, "duration": 17400.0}]}  # ~194 mi, ~4h50m
OVERPASS = {"elements": [
    {"tags": {"name": "Downtown Inn", "addr:street": "Main St"}, "lat": 30.28, "lon": -97.74},
    {"tags": {"name": "Far Motel"}, "lat": 30.40, "lon": -97.90},
    {"tags": {"tourism": "hotel"}, "lat": 30.27, "lon": -97.74},  # unnamed -> skipped
]}


def transport(routing="austin"):
    def _get(url: str):
        if "geocoding-api" in url:
            return DALLAS if "Dallas" in url else AUSTIN
        if "router.project-osrm" in url:
            return OSRM
        if "overpass" in url:
            return OVERPASS
        return {}
    return _get


class DistanceTests(unittest.TestCase):
    def test_driving_distance_and_eta(self) -> None:
        result = TravelTool(transport=transport()).distance("Austin", "Dallas")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["driving_mi"], round(312000 / 1609.34, 1))
        self.assertIn("4h 50m", result.message)
        self.assertIn("Austin, Texas", result.message)

    def test_unknown_origin(self) -> None:
        def _get(url):
            return {"results": []}
        self.assertFalse(TravelTool(transport=_get).distance("Nowhere", "Dallas").ok)


class NearbyTests(unittest.TestCase):
    def test_hotels_sorted_by_distance_named_only(self) -> None:
        result = TravelTool(transport=transport()).nearby("hotel", "Austin")
        self.assertTrue(result.ok)
        names = [r["name"] for r in result.data["results"]]
        self.assertEqual(names, ["Downtown Inn", "Far Motel"])  # unnamed dropped, nearest first

    def test_unknown_category(self) -> None:
        self.assertFalse(TravelTool(transport=transport()).nearby("dragons", "Austin").ok)


class GateTests(unittest.TestCase):
    def test_gated_medium(self) -> None:
        denied = TravelTool(transport=transport(), approval_gate=ApprovalGate(lambda r: False))
        with self.assertRaises(ApprovalDenied):
            denied.distance("Austin", "Dallas")


if __name__ == "__main__":
    unittest.main()
