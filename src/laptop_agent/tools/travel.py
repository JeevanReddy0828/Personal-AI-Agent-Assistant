from __future__ import annotations

import json
import math
import re
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
    # Free, no-key IP geolocation for "what's around me". Approximate (city level).
    GEOLOCATE = "http://ip-api.com/json/"

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
        route = self._route([(a["longitude"], a["latitude"]), (b["longitude"], b["latitude"])])
        drive_mi = route["distance"] / 1609.34 if route else None
        drive_dur = route["duration"] if route else None
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
            origin_lat=a["latitude"], origin_lon=a["longitude"],
            destination_lat=b["latitude"], destination_lon=b["longitude"],
            straight_line_mi=round(straight, 1),
            driving_mi=round(drive_mi, 1) if drive_mi is not None else None,
            driving_seconds=round(drive_dur) if drive_dur is not None else None,
        )

    def _route(self, coords: list[tuple[float, float]], geometry: bool = False) -> dict | None:
        """Driving route through (lon, lat) waypoints via OSRM. Returns the first
        route (with per-leg ``legs``) or None on failure. With ``geometry=True`` the
        route also carries a GeoJSON ``geometry`` (for drawing the line on a map)."""
        path = ";".join(f"{lon},{lat}" for lon, lat in coords)
        query = "overview=full&geometries=geojson" if geometry else "overview=false"
        try:
            data = self._get(f"{self.OSRM}/{path}?{query}")
        except Exception:  # pragma: no cover - network failure path.
            return None
        routes = data.get("routes") or []
        return routes[0] if routes else None

    def trip(self, stops: list[str]) -> ToolResult:
        """Multi-stop trip: chain driving legs through every stop in order, with a
        per-leg breakdown and totals. Falls back to straight-line if OSRM fails."""
        stops = [s.strip() for s in (stops or []) if s and s.strip()]
        if len(stops) < 2:
            return ToolResult.failure("Use: trip <stop1> to <stop2> to <stop3> …")
        self._guard("Plan a trip through " + ", ".join(stops))
        spots = []
        for stop in stops:
            spot = self._geocode(stop)
            if spot is None:
                return ToolResult.failure(f"I couldn't find '{stop}'.")
            spots.append(spot)
        labels = [s["label"] for s in spots]
        points = [{"label": s["label"], "latitude": s["latitude"], "longitude": s["longitude"]} for s in spots]
        box = _bbox([(s["latitude"], s["longitude"]) for s in spots])
        directions = (
            "https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route="
            + "%3B".join(f"{s['latitude']}%2C{s['longitude']}" for s in spots)
        )
        lines = ["**Trip plan:** " + " → ".join(labels)]
        legs_data: list[dict] = []
        route = self._route([(s["longitude"], s["latitude"]) for s in spots], geometry=True)
        if route and route.get("legs"):
            for i, leg in enumerate(route["legs"]):
                mi, dur = leg["distance"] / 1609.34, leg["duration"]
                lines.append(f"- {labels[i]} → {labels[i + 1]}: {round(mi)} mi, ~{_human_duration(dur)}")
                legs_data.append({"from": labels[i], "to": labels[i + 1],
                                  "miles": round(mi, 1), "seconds": round(dur)})
            total_mi, total_dur = route["distance"] / 1609.34, route["duration"]
            lines.append(f"**Total: {round(total_mi)} mi, ~{_human_duration(total_dur)}** over {len(route['legs'])} legs")
            geometry = _downsample(((route.get("geometry") or {}).get("coordinates") or []), 240)
            return ToolResult.success("\n".join(lines), stops=labels, legs=legs_data, points=points,
                                      bbox=box, geometry=geometry, directions=directions,
                                      total_mi=round(total_mi, 1), total_seconds=round(total_dur))
        total = 0.0
        for i in range(len(spots) - 1):
            mi = _haversine_mi(spots[i]["latitude"], spots[i]["longitude"],
                               spots[i + 1]["latitude"], spots[i + 1]["longitude"])
            total += mi
            lines.append(f"- {labels[i]} → {labels[i + 1]}: {round(mi)} mi straight-line")
            legs_data.append({"from": labels[i], "to": labels[i + 1], "miles": round(mi, 1), "seconds": None})
        lines.append(f"**Total: {round(total)} mi straight-line** (no driving route found)")
        return ToolResult.success("\n".join(lines), stops=labels, legs=legs_data, points=points,
                                  bbox=box, geometry=[], directions=directions,
                                  total_mi=round(total, 1), total_seconds=None)

    def nearby(self, category: str, near: str, limit: int = 8, radius_m: int = 8000) -> ToolResult:
        tag = _CATEGORY_TAGS.get(category.strip().lower())
        if not tag:
            return self._unknown_category()
        self._guard(f"Find {category} near {near}")
        spot = self._geocode(near)
        if spot is None:
            return ToolResult.failure(f"I couldn't find '{near}'.")
        return self._nearby_at(category, tag, spot["latitude"], spot["longitude"], spot["label"], limit, radius_m)

    def here(self) -> ToolResult:
        """Approximate current location from the public IP (no key, city level)."""
        self._guard("Look up your approximate location by IP")
        spot = self._locate_self()
        if spot is None:
            return ToolResult.failure("I couldn't determine your location from your IP address.")
        return ToolResult.success(
            f"You appear to be near **{spot['label']}** (approximate, by IP).",
            label=spot["label"], latitude=spot["latitude"], longitude=spot["longitude"],
        )

    def around(self, category: str, limit: int = 8, radius_m: int = 8000) -> ToolResult:
        """Like ``nearby`` but anchored to the user's IP-derived location."""
        tag = _CATEGORY_TAGS.get(category.strip().lower())
        if not tag:
            return self._unknown_category()
        self._guard(f"Find {category} around your current location")
        spot = self._locate_self()
        if spot is None:
            return ToolResult.failure("I couldn't determine your location from your IP address.")
        return self._nearby_at(category, tag, spot["latitude"], spot["longitude"], spot["label"], limit, radius_m)

    def map_query(self, query: str) -> ToolResult:
        """Resolve a place (or ``A to B`` route) into map-ready coordinates: an
        OpenStreetMap embed URL, a bounding box, and (for routes) a directions link."""
        query = (query or "").strip()
        if not query:
            return ToolResult.failure("Use: map <place>  ·  map <origin> to <destination>")
        self._guard(f"Show {query} on the map")
        split = re.search(r"\s+(?:to|->|→)\s+", query, re.IGNORECASE)
        if split:
            origin, destination = query[: split.start()].strip(), query[split.end() :].strip()
            a, b = self._geocode(origin), self._geocode(destination)
            if a is None:
                return ToolResult.failure(f"I couldn't find '{origin}'.")
            if b is None:
                return ToolResult.failure(f"I couldn't find '{destination}'.")
            points = [
                {"label": a["label"], "latitude": a["latitude"], "longitude": a["longitude"], "role": "origin"},
                {"label": b["label"], "latitude": b["latitude"], "longitude": b["longitude"], "role": "destination"},
            ]
            box = _bbox([(a["latitude"], a["longitude"]), (b["latitude"], b["longitude"])])
            route = self._route([(a["longitude"], a["latitude"]), (b["longitude"], b["latitude"])])
            drive_mi = route["distance"] / 1609.34 if route else None
            drive_dur = route["duration"] if route else None
            message = f"Map: {a['label']} → {b['label']}"
            if drive_mi is not None:
                message += f" — {round(drive_mi)} mi, ~{_human_duration(drive_dur)} by car"
            return ToolResult.success(
                message, points=points, bbox=box,
                embed=_embed_url(box, b["latitude"], b["longitude"]),
                directions=(
                    "https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route="
                    f"{a['latitude']}%2C{a['longitude']}%3B{b['latitude']}%2C{b['longitude']}"
                ),
                driving_mi=round(drive_mi, 1) if drive_mi is not None else None,
                driving_seconds=round(drive_dur) if drive_dur is not None else None,
            )
        spot = self._geocode(query)
        if spot is None:
            return ToolResult.failure(f"I couldn't find '{query}'.")
        point = {"label": spot["label"], "latitude": spot["latitude"], "longitude": spot["longitude"], "role": "place"}
        box = _bbox([(spot["latitude"], spot["longitude"])])
        return ToolResult.success(
            f"Map: {spot['label']}", points=[point], bbox=box,
            embed=_embed_url(box, spot["latitude"], spot["longitude"]), directions=None,
        )

    @staticmethod
    def knows_category(category: str) -> bool:
        return (category or "").strip().lower() in _CATEGORY_TAGS

    def _unknown_category(self) -> ToolResult:
        return ToolResult.failure(
            f"I can find: {', '.join(sorted({v.split('=')[1] for v in _CATEGORY_TAGS.values()}))}.",
            supported=sorted(_CATEGORY_TAGS),
        )

    def _locate_self(self) -> dict | None:
        try:
            data = self._get(self.GEOLOCATE)
        except Exception:  # pragma: no cover - network failure path.
            return None
        if not data or data.get("status") == "fail":
            return None
        lat, lon = data.get("lat"), data.get("lon")
        if lat is None or lon is None:
            return None
        label = ", ".join(
            str(part) for part in (data.get("city"), data.get("regionName"), data.get("country")) if part
        )
        return {"latitude": lat, "longitude": lon, "label": label or "your location"}

    def _nearby_at(self, category: str, tag: str, lat: float, lon: float,
                   label: str, limit: int, radius_m: int) -> ToolResult:
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
            return ToolResult.failure(f"No {category} found near {label}.")
        lines = [f"**{category.title()} near {label}:**"]
        for f in found:
            addr = f" — {f['address']}" if f["address"] else ""
            lines.append(f"- {f['name']} ({f['miles']} mi){addr}")
        return ToolResult.success("\n".join(lines), near=label, category=category, results=found)


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


def _bbox(points: list[tuple[float, float]], pad: float = 0.08) -> list[float]:
    """A [min_lon, min_lat, max_lon, max_lat] box around (lat, lon) points, with a
    minimum span so a single point still renders a sensible zoom, plus 15% margin."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    min_lat, max_lat, min_lon, max_lon = min(lats), max(lats), min(lons), max(lons)
    if max_lat - min_lat < pad:
        min_lat, max_lat = min_lat - pad, max_lat + pad
    if max_lon - min_lon < pad:
        min_lon, max_lon = min_lon - pad, max_lon + pad
    dlat, dlon = (max_lat - min_lat) * 0.15, (max_lon - min_lon) * 0.15
    return [round(min_lon - dlon, 5), round(min_lat - dlat, 5),
            round(max_lon + dlon, 5), round(max_lat + dlat, 5)]


def _downsample(coords: list, max_points: int) -> list:
    """Thin a polyline to at most max_points, always keeping the first and last."""
    if len(coords) <= max_points or max_points < 2:
        return [[round(float(c[0]), 5), round(float(c[1]), 5)] for c in coords]
    step = (len(coords) - 1) / (max_points - 1)
    picked = [coords[round(i * step)] for i in range(max_points)]
    return [[round(float(c[0]), 5), round(float(c[1]), 5)] for c in picked]


def _embed_url(box: list[float], marker_lat: float, marker_lon: float) -> str:
    return (
        "https://www.openstreetmap.org/export/embed.html?"
        f"bbox={box[0]}%2C{box[1]}%2C{box[2]}%2C{box[3]}"
        f"&layer=mapnik&marker={marker_lat}%2C{marker_lon}"
    )
