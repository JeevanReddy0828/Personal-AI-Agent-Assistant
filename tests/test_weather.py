from __future__ import annotations

import unittest

from laptop_agent.safety import ApprovalDenied, ApprovalGate
from laptop_agent.tools.weather import WeatherTool


def fake_transport(geo: dict, forecast: dict):
    def _get(url: str) -> dict:
        return geo if "geocoding" in url else forecast
    return _get


GEO = {"results": [{"name": "Austin", "admin1": "Texas", "country": "United States",
                    "latitude": 30.27, "longitude": -97.74}]}
FORECAST = {
    "current": {"temperature_2m": 92.4, "apparent_temperature": 99.1, "relative_humidity_2m": 44,
                "wind_speed_10m": 8.6, "weather_code": 1},
    "daily": {"time": ["2026-06-20", "2026-06-21", "2026-06-22"],
              "weather_code": [1, 95, 80], "temperature_2m_max": [97.2, 95.0, 88.4],
              "temperature_2m_min": [74.1, 73.0, 70.2], "precipitation_probability_max": [5, 60, 30]},
}


class WeatherTests(unittest.TestCase):
    def test_forecast_formats_current_and_days(self) -> None:
        tool = WeatherTool(transport=fake_transport(GEO, FORECAST))
        result = tool.forecast("Austin")
        self.assertTrue(result.ok)
        self.assertIn("Austin, Texas, United States", result.message)
        self.assertIn("mainly clear", result.message)   # weather_code 1
        self.assertIn("92", result.message)              # current temp rounded
        self.assertEqual(result.data["location"], "Austin, Texas, United States")
        self.assertEqual(len(result.data["daily"]), 3)
        self.assertEqual(result.data["daily"][1]["summary"], "thunderstorm")  # code 95
        self.assertEqual(result.data["daily"][1]["precip_chance"], 60)

    def test_unknown_place(self) -> None:
        tool = WeatherTool(transport=fake_transport({"results": []}, {}))
        result = tool.forecast("Nowheresville XYZ")
        self.assertFalse(result.ok)

    def test_empty_location(self) -> None:
        self.assertFalse(WeatherTool(transport=fake_transport(GEO, FORECAST)).forecast("  ").ok)

    def test_gated_network_read(self) -> None:
        # Allowed gate -> works; denying gate -> blocked before any network call.
        ok_tool = WeatherTool(transport=fake_transport(GEO, FORECAST), approval_gate=ApprovalGate(lambda r: True))
        self.assertTrue(ok_tool.forecast("Austin").ok)
        denied = WeatherTool(transport=fake_transport(GEO, FORECAST), approval_gate=ApprovalGate(lambda r: False))
        with self.assertRaises(ApprovalDenied):
            denied.forecast("Austin")


if __name__ == "__main__":
    unittest.main()
