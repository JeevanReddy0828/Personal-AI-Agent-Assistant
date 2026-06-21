from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from collections.abc import Callable

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

# An HTTP transport returns parsed JSON for a URL. Injectable for offline tests.
HttpJson = Callable[[str], dict]

# OpenStreetMap category -> Overpass tag filter, for "find <X> near <place>".
_CATEGORY_TAGS = {
    "hotel": 'tourism=hotel', "hotels": 'tourism=hotel', "motel": 'tourism=motel',
    "hostel": 'tourism=hostel', "stay": 'tourism=hotel', "lodging": 'tourism=hotel',
    "restaurant": 'amenity=restaurant', "restaurants": 'amenity=restaurant', "food": 'amenity=restaurant',
    "cafe": 'amenity=cafe', "coffee": 'amenity=cafe',
    "bar": 'amenity=bar', "pub": 'amenity=pub',
    "gas": 'amenity=fuel', "fuel": 'amenity=fuel', "petrol": 'amenity=fuel',
    "pharmacy": 'amenity=pharmacy', "hospital": 'amenity=hospital',
    "atm": 'amenity=atm', "bank": 'amenity=bank', "parking": 'amenity=parking',
    "supermarket": 'shop=supermarket', "grocery": 'shop=supermarket',
    "gym": 'leisure=fitness_centre',
}


def _urllib_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "laptop-agent/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.8  # miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _human_duration(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"


class TravelTool:
    """Maps & travel via free, no-key OpenStreetMap services: driving distance/time
    (OSRM) and nearby places like hotels (Overpass), geocoded with Open-Meteo."""

    GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
    OSRM = "https://router.project-osrm.org/route/v1/driving"
    OVERPASS = "https://overpass-api.de/api/interpreter"

    def __init__(self, transport: HttpJson | None = None, approval_gate: ApprovalGate | None = None) -> None:
        self._get = transport or _urllib_json
        self._gate = approval_gate

    def _guard(self, action: str) -> None:
        if self._gate is not None:
            self._gate.require(
                ApprovalRequest(action=action, risk=RiskLevel.MEDIUM, reason="Travel lookups call external map services.")
            )

    def _geocode(self, place: str) -> dict | None:
        place = (place or "").strip().strip("?.!,'\"")
        if not place:
            return None
        for candidate in _geocode_candidates(place):
            try:
                geo = self._get(f"{self.GEOCODE}?name={urllib.parse.quote(candidate)}&count=1&language=en&format=json")
            except Exception:  # pragma: no cover - network failure path.
                return None
            results = geo.get("results") or []
            if results:
                spot = results[0]
                spot["label"] = ", ".join(
                    str(part) for part in (spot.get("name"), spot.get("admin1"), spot.get("country")) if part
                )
                return spot
        return None

    def distance(self, origin: str, destination: str) -> ToolResult:
        if not origin.strip() or not destination.strip():
            return ToolResult.failure("Use: distance <origin> to <destination>")
        self._guard(f"Look up the route from {origin} to {destination}")
        a, b = self._geocode(origin), self._geocode(destination)
        if a is None:
            return ToolResult.failure(f"I couldn't find '{origin}'.")
        if b is None:
            return ToolResult.failure(f"I couldn't find '{destination}'.")
        straight = _haversine_mi(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
        drive_mi = drive_dur = None
        try:
            route = self._get(f"{self.OSRM}/{a['longitude']},{a['latitude']};{b['longitude']},{b['latitude']}?overview=false")
            routes = route.get("routes") or []
            if routes:
                drive_mi = routes[0]["distance"] / 1609.34
                drive_dur = routes[0]["duration"]
        except Exception:  # pragma: no cover - network failure path.
            pass
        if drive_mi is not None:
            message = (
                f"**{a['label']} → {b['label']}**\n"
                f"- Driving: {round(drive_mi)} mi, about {_human_duration(drive_dur)}\n"
                f"- Straight-line: {round(straight)} mi"
            )
        else:
            message = f"**{a['label']} → {b['label']}** — straight-line {round(straight)} mi (no driving route found, e.g. over water)."
        return ToolResult.success(
            message,
            origin=a["label"], destination=b["label"],
            straight_line_mi=round(straight, 1),
            driving_mi=round(drive_mi, 1) if drive_mi is not None else None,
            driving_seconds=round(drive_dur) if drive_dur is not None else None,
        )

    def nearby(self, category: str, near: str, limit: int = 8, radius_m: int = 8000) -> ToolResult:
        tag = _CATEGORY_TAGS.get(category.strip().lower())
        if not tag:
            return ToolResult.failure(
                f"I can find: {', '.join(sorted({v.split('=')[1] for v in _CATEGORY_TAGS.values()}))}.",
                supported=sorted(_CATEGORY_TAGS),
            )
        self._guard(f"Find {category} near {near}")
        spot = self._geocode(near)
        if spot is None:
            return ToolResult.failure(f"I couldn't find '{near}'.")
        lat, lon = spot["latitude"], spot["longitude"]
        key, value = tag.split("=", 1)
        query = (
            f"[out:json][timeout:20];("
            f'node["{key}"="{value}"](around:{radius_m},{lat},{lon});'
            f'way["{key}"="{value}"](around:{radius_m},{lat},{lon});'
            f");out center {max(limit * 3, 20)};"
        )
        try:
            data = self._get(f"{self.OVERPASS}?data={urllib.parse.quote(query)}")
        except Exception as exc:  # pragma: no cover - network failure path.
            return ToolResult.failure(f"Couldn't reach the map service: {exc}")
        found = []
        for el in data.get("elements", []):
            tags = el.get("tags") or {}
            name = tags.get("name")
            if not name:
                continue
            elat = el.get("lat") or (el.get("center") or {}).get("lat")
            elon = el.get("lon") or (el.get("center") or {}).get("lon")
            dist = _haversine_mi(lat, lon, elat, elon) if elat and elon else None
            found.append({"name": name, "miles": round(dist, 1) if dist is not None else None,
                          "address": _osm_address(tags)})
        found = [f for f in found if f["miles"] is not None]
        found.sort(key=lambda f: f["miles"])
        found = found[:limit]
        if not found:
            return ToolResult.failure(f"No {category} found near {spot['label']}.")
        lines = [f"**{category.title()} near {spot['label']}:**"]
        for f in found:
            addr = f" — {f['address']}" if f["address"] else ""
            lines.append(f"- {f['name']} ({f['miles']} mi){addr}")
        return ToolResult.success("\n".join(lines), near=spot["label"], category=category, results=found)


def _geocode_candidates(place: str) -> list[str]:
    candidates = [place]
    import re

    trimmed = re.sub(r"[,\s]+[A-Za-z]{2,}$", "", place).strip()
    if trimmed and trimmed != place:
        candidates.append(trimmed)
    if "," in place:
        head = place.split(",", 1)[0].strip()
        if head and head not in candidates:
            candidates.append(head)
    return candidates


def _osm_address(tags: dict) -> str:
    parts = [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:city")]
    return " ".join(str(p) for p in parts if p)
