from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable

from laptop_agent.tools.base import ToolResult

# WMO weather interpretation codes (Open-Meteo) -> plain English.
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}

# An HTTP transport fetches a URL and returns parsed JSON. Injectable so the success
# path is unit-tested offline, per the websearch/research pattern.
HttpJson = Callable[[str], dict]


def _urllib_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "laptop-agent/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _r(value: object) -> object:
    try:
        return round(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return value


class WeatherTool:
    """Real current conditions + multi-day forecast via Open-Meteo (free, no API key)."""

    GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, transport: HttpJson | None = None) -> None:
        self._get = transport or _urllib_json

    def forecast(self, location: str, days: int = 3) -> ToolResult:
        place = (location or "").strip().strip("?.!,'\"")
        if not place:
            return ToolResult.failure("Where? Try 'weather in Austin'.")
        # Open-Meteo's geocoder matches a bare city best, so fall back from the full
        # string to forms without a trailing state/country code, e.g. "Austin TX" -> "Austin".
        candidates = [place]
        trimmed = re.sub(r"[,\s]+[A-Za-z]{2,}$", "", place).strip()
        if trimmed and trimmed != place:
            candidates.append(trimmed)
        if "," in place:
            head = place.split(",", 1)[0].strip()
            if head and head not in candidates:
                candidates.append(head)
        matches: list = []
        for candidate in candidates:
            try:
                geo = self._get(f"{self.GEOCODE}?name={urllib.parse.quote(candidate)}&count=1&language=en&format=json")
            except Exception as exc:  # pragma: no cover - network failure path.
                return ToolResult.failure(f"Couldn't reach the weather service: {exc}")
            matches = geo.get("results") or []
            if matches:
                break
        if not matches:
            return ToolResult.failure(f"I couldn't find a place called '{place}'.")
        spot = matches[0]
        label = ", ".join(str(part) for part in (spot.get("name"), spot.get("admin1"), spot.get("country")) if part)
        url = (
            f"{self.FORECAST}?latitude={spot['latitude']}&longitude={spot['longitude']}"
            "&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&timezone=auto&forecast_days={max(1, min(days, 7))}&temperature_unit=fahrenheit&wind_speed_unit=mph"
        )
        try:
            data = self._get(url)
        except Exception as exc:  # pragma: no cover - network failure path.
            return ToolResult.failure(f"Couldn't reach the weather service: {exc}")

        current = data.get("current") or {}
        code = _safe_int(current.get("weather_code"))
        now_desc = _WMO.get(code, "unknown conditions")
        daily = data.get("daily") or {}
        times = daily.get("time") or []
        codes = daily.get("weather_code") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        precip = daily.get("precipitation_probability_max") or []
        out_days = []
        for i, day in enumerate(times):
            out_days.append({
                "date": day,
                "summary": _WMO.get(_safe_int(codes[i] if i < len(codes) else None), "unknown"),
                "high": highs[i] if i < len(highs) else None,
                "low": lows[i] if i < len(lows) else None,
                "precip_chance": precip[i] if i < len(precip) else None,
            })

        lines = [
            f"**{label}** — {now_desc}, {_r(current.get('temperature_2m'))}°F "
            f"(feels like {_r(current.get('apparent_temperature'))}°F), "
            f"humidity {current.get('relative_humidity_2m')}%, wind {_r(current.get('wind_speed_10m'))} mph."
        ]
        for day in out_days:
            chance = f", {day['precip_chance']}% precip" if day["precip_chance"] is not None else ""
            lines.append(f"- {day['date']}: {day['summary']}, high {_r(day['high'])}° / low {_r(day['low'])}°F{chance}")
        return ToolResult.success(
            "\n".join(lines),
            location=label,
            current={**current, "description": now_desc},
            daily=out_days,
        )


def _safe_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return -1
