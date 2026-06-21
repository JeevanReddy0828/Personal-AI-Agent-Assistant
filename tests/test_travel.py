from __future__ import annotations

import unittest

from laptop_agent.safety import ApprovalDenied, ApprovalGate
from laptop_agent.tools.travel import TravelTool

AUSTIN = {"results": [{"name": "Austin", "admin1": "Texas", "country": "United States",
                       "latitude": 30.27, "longitude": -97.74}]}
DALLAS = {"results": [{"name": "Dallas", "admin1": "Texas", "country": "United States",
                       "latitude": 32.78, "longitude": -96.80}]}
HOUSTON = {"results": [{"name": "Houston", "admin1": "Texas", "country": "United States",
                       "latitude": 29.76, "longitude": -95.37}]}
OSRM = {"routes": [{"distance": 312000.0, "duration": 17400.0}]}  # ~194 mi, ~4h50m
# 3-stop route: two legs, with a total distance/duration.
OSRM_TRIP = {"routes": [{"distance": 480000.0, "duration": 28800.0,
    "geometry": {"coordinates": [[-97.74, 30.27], [-97.0, 31.5], [-96.80, 32.78], [-95.37, 29.76]]},
    "legs": [
        {"distance": 312000.0, "duration": 17400.0},
        {"distance": 168000.0, "duration": 11400.0},
    ]}]}
GEOLOCATE = {"status": "success", "lat": 30.27, "lon": -97.74,
             "city": "Austin", "regionName": "Texas", "country": "United States"}
OVERPASS = {"elements": [
    {"tags": {"name": "Downtown Inn", "addr:street": "Main St"}, "lat": 30.28, "lon": -97.74},
    {"tags": {"name": "Far Motel"}, "lat": 30.40, "lon": -97.90},
    {"tags": {"tourism": "hotel"}, "lat": 30.27, "lon": -97.74},  # unnamed -> skipped
]}


def transport(routing="austin"):
    def _get(url: str):
        if "ip-api.com" in url:
            return GEOLOCATE
        if "geocoding-api" in url:
            if "Dallas" in url:
                return DALLAS
            if "Houston" in url:
                return HOUSTON
            return AUSTIN
        if "router.project-osrm" in url:
            coords = url.split("/driving/", 1)[1].split("?", 1)[0]
            return OSRM_TRIP if coords.count(";") >= 2 else OSRM
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


class TripTests(unittest.TestCase):
    def test_multi_stop_legs_and_total(self) -> None:
        result = TravelTool(transport=transport()).trip(["Austin", "Dallas", "Houston"])
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["legs"]), 2)
        self.assertEqual(result.data["legs"][0]["from"], "Austin, Texas, United States")
        self.assertEqual(result.data["legs"][1]["to"], "Houston, Texas, United States")
        self.assertEqual(result.data["total_mi"], round(480000 / 1609.34, 1))
        self.assertIn("over 2 legs", result.message)
        # Map-ready extras for the Trip panel.
        self.assertEqual(len(result.data["points"]), 3)
        self.assertEqual(len(result.data["bbox"]), 4)
        self.assertTrue(result.data["geometry"])  # route polyline present
        self.assertIn("openstreetmap.org/directions", result.data["directions"])
        self.assertEqual(result.data["directions"].count("%3B"), 2)  # 3 waypoints -> 2 separators

    def test_trip_geometry_downsampled(self) -> None:
        from laptop_agent.tools.travel import _downsample

        coords = [[i * 0.01, i * 0.02] for i in range(1000)]
        thinned = _downsample(coords, 50)
        self.assertEqual(len(thinned), 50)
        self.assertEqual(thinned[0], [0.0, 0.0])
        self.assertEqual(thinned[-1], coords[-1])

    def test_needs_two_stops(self) -> None:
        self.assertFalse(TravelTool(transport=transport()).trip(["Austin"]).ok)

    def test_unknown_stop(self) -> None:
        def _get(url):
            return {"results": []} if "geocoding-api" in url else transport()(url)
        self.assertFalse(TravelTool(transport=_get).trip(["Nowhere", "Dallas", "Houston"]).ok)


class AroundMeTests(unittest.TestCase):
    def test_here_from_ip(self) -> None:
        result = TravelTool(transport=transport()).here()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["label"], "Austin, Texas, United States")
        self.assertEqual(result.data["latitude"], 30.27)

    def test_around_uses_ip_location(self) -> None:
        result = TravelTool(transport=transport()).around("hotel")
        self.assertTrue(result.ok)
        self.assertEqual([r["name"] for r in result.data["results"]], ["Downtown Inn", "Far Motel"])

    def test_around_unknown_category(self) -> None:
        self.assertFalse(TravelTool(transport=transport()).around("dragons").ok)

    def test_here_geolocation_fails(self) -> None:
        def _get(url):
            return {"status": "fail"} if "ip-api" in url else transport()(url)
        self.assertFalse(TravelTool(transport=_get).here().ok)


class MapTests(unittest.TestCase):
    def test_map_single_place(self) -> None:
        result = TravelTool(transport=transport()).map_query("Austin")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["points"]), 1)
        self.assertIn("openstreetmap.org/export/embed", result.data["embed"])
        self.assertEqual(len(result.data["bbox"]), 4)
        self.assertIsNone(result.data["directions"])

    def test_map_route_has_two_points_and_directions(self) -> None:
        result = TravelTool(transport=transport()).map_query("Austin to Dallas")
        self.assertTrue(result.ok)
        self.assertEqual([p["role"] for p in result.data["points"]], ["origin", "destination"])
        self.assertIn("directions", result.data["directions"])
        self.assertEqual(result.data["driving_mi"], round(312000 / 1609.34, 1))

    def test_map_unknown_place(self) -> None:
        def _get(url):
            return {"results": []}
        self.assertFalse(TravelTool(transport=_get).map_query("Nowhere").ok)


class GateTests(unittest.TestCase):
    def test_gated_medium(self) -> None:
        denied = TravelTool(transport=transport(), approval_gate=ApprovalGate(lambda r: False))
        with self.assertRaises(ApprovalDenied):
            denied.distance("Austin", "Dallas")

    def test_trip_around_map_gated(self) -> None:
        denied = TravelTool(transport=transport(), approval_gate=ApprovalGate(lambda r: False))
        for call in (lambda: denied.trip(["Austin", "Dallas", "Houston"]),
                     lambda: denied.around("hotel"),
                     lambda: denied.here(),
                     lambda: denied.map_query("Austin")):
            with self.assertRaises(ApprovalDenied):
                call()


if __name__ == "__main__":
    unittest.main()
