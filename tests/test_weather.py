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


class PlaceCleaningTests(unittest.TestCase):
    """`weather in london` reached the geocoder as "in london" (and then, trimmed, as
    "in"); `weather today` asked it for a town called Today."""

    def test_the_words_around_a_place_are_not_the_place(self) -> None:
        from laptop_agent.tools.weather import clean_place

        cases = {
            "in london": "london", "today": "", "in new york tomorrow": "new york", "in": "",
            "for Dallas tomorrow": "Dallas", "this weekend in denver": "denver",
            "like in paris right now": "paris", "near me": "", "Austin, TX": "Austin, TX",
            "Tomorrowland": "Tomorrowland", "Fort Worth": "Fort Worth",
            # A day said with the place reached the geocoder whole: "austin on saturday".
            "austin on saturday": "austin", "paris next monday": "paris",
            # A bare weekday at the end may be the name (Codex's review): the tool's fallback
            # finds Tokyo, and Mount Sunday is a peak.
            "tokyo friday": "tokyo friday", "Mount Sunday": "Mount Sunday",
            "on sunday in london": "london", "boston on the weekend": "boston",
            "Friday Harbor": "Friday Harbor", "Sunday Harbour in maine": "Sunday Harbour in maine",
            "chicago saturday night": "chicago", "denver tomorrow morning": "denver",
            "saturday night in chicago": "chicago",
        }
        for raw, expected in cases.items():
            self.assertEqual(clean_place(raw), expected, raw)

    def test_the_tool_geocodes_the_cleaned_place(self) -> None:
        asked: list[str] = []

        def transport(url: str) -> dict:
            asked.append(url)
            return GEO if "geocoding" in url else FORECAST

        self.assertTrue(WeatherTool(transport=transport).forecast("in london tomorrow").ok)
        self.assertIn("name=london&", asked[0])

    def test_a_place_ending_in_a_weekday_reaches_the_geocoder_whole(self) -> None:
        """"weather in Mount Sunday" was routed as `weather Mount` and asked for "Mount"."""
        from laptop_agent.planner import HeuristicPlannerProvider

        planner = HeuristicPlannerProvider()
        self.assertEqual(planner.plan("weather in Mount Sunday", "", {}).command, "weather Mount Sunday")
        self.assertEqual(planner.plan("how hot will it be in austin on saturday", "", {}).command, "weather austin")
        for place, known, expected in (("Mount Sunday", "Mount%20Sunday", ["Mount%20Sunday"]),
                                       ("tokyo friday", "tokyo&", ["tokyo%20friday", "tokyo&"])):
            asked: list[str] = []

            def transport(url: str, known: str = known) -> dict:
                if "geocoding" not in url:
                    return FORECAST
                asked.append(url)
                return GEO if f"name={known}" in url else {"results": []}

            with self.subTest(place):
                self.assertTrue(WeatherTool(transport=transport).forecast(place).ok)
                self.assertEqual(len(asked), len(expected))
                for url, name in zip(asked, expected):
                    self.assertIn(f"name={name}", url)


if __name__ == "__main__":
    unittest.main()
