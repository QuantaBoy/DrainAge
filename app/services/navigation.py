"""Flood-safe trips: place search, and the route round the water the forecast puts on
the streets.

A place name becomes a point inside Chennai (OpenStreetMap Nominatim, with the street
layer as the fallback). A trip is the cheapest path through the street graph
(app/services/routing.py), each road costed by the water the flood forecast has on it
while the trip is under way, set against the plain shortest path so the page can show
what was avoided and why. Dijkstra and A* are both run and reported: the same route,
and how much less of the city A* had to search to find it.
"""

import asyncio
import time
from typing import Any, Callable

import httpx

from app import config
from app.errors import ServiceError
from app.services import forecast, routing

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy asks for an identifying User-Agent and a light touch; every
# answer is cached, and each search is limited to the Chennai box.
_places: dict[str, list[dict[str, Any]]] = {}
_PLACES_LIMIT = 500

# A trip is costed against the deepest water its roads carry from the moment it leaves
# until this long after: a cross-city drive takes about that, and water that rises
# while you are on the way is still in the way.
# ponytail: one window for every trip; timing each stretch by when the car reaches it
# is the refinement.
TRIP_WINDOW_MIN = 30
AVERAGE_SPEED_KMH = 20.0          # Chennai's peak-hour traffic, for the minutes shown
# A start or destination further than this from any road is not on the street layer.
MAX_SNAP_M = 1000


def street_matches(q: str, limit: int) -> list[dict[str, Any]]:
    """Streets in the road layer whose name contains the query: the fallback search."""
    wanted = q.strip().lower()
    found: dict[str, dict[str, Any]] = {}
    for edge in routing.load()["edges"]:
        name = edge["name"]
        if name and wanted in name.lower() and name not in found:
            mid = edge["shape"][len(edge["shape"]) // 2]
            found[name] = {"name": name, "lat": mid[1], "lon": mid[0], "source": "street layer"}
            if len(found) >= limit:
                break
    return list(found.values())


async def geocode(q: str) -> list[dict[str, Any]]:
    """Places in Chennai matching a name: Nominatim's, or streets of that name when it
    is down or finds nothing."""
    key = q.strip().lower()
    if key in _places:
        return _places[key]
    south, west, north, east = config.CHENNAI_BBOX
    results: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": config.USER_AGENT}) as client:
            response = await client.get(NOMINATIM_URL, params={
                "q": q, "format": "jsonv2", "limit": 6,
                "viewbox": f"{west},{north},{east},{south}", "bounded": 1,
                "countrycodes": "in",
            })
        if response.status_code == 200:
            results = [{"name": place.get("display_name", q).split(", Chennai")[0],
                        "lat": float(place["lat"]), "lon": float(place["lon"]),
                        "source": "OpenStreetMap Nominatim"}
                       for place in response.json()]
    except httpx.RequestError:
        pass
    if not results:
        results = await asyncio.to_thread(street_matches, q, 6)

    if len(_places) >= _PLACES_LIMIT:
        _places.pop(next(iter(_places)))
    _places[key] = results
    return results


def snap(lat: float, lon: float, which: str) -> tuple[int, float]:
    """The junction nearest a point, and how far away it is."""
    node, off = routing.nearest_node(lat, lon)
    if off > MAX_SNAP_M:
        raise ServiceError(422, f"The {which} is {off:.0f} m from the nearest road in the Chennai street layer")
    return node, off


def _timed(search: Callable, source: int, target: int, cost: Any) -> dict[str, Any]:
    began = time.perf_counter()
    found = search(source, target, cost)
    found["ms"] = round((time.perf_counter() - began) * 1000, 1)
    return found


def _minutes(route: dict[str, Any] | None) -> None:
    if route:
        route["minutes"] = round(route["length_km"] / AVERAGE_SPEED_KMH * 60)


def plan_route(start: tuple[float, float], end: tuple[float, float], leave_in_min: int,
               rain: dict[str, Any], runoff_coeff: float, drain_condition: float,
               algorithm: str) -> dict[str, Any]:
    """The flood-safe route and the plain shortest one, for a trip leaving in
    `leave_in_min` minutes under `rain` (app.services.rainfall.report_steps).

    Runs in a worker thread: the storm run behind it can take seconds of CPU.
    """
    source, source_off = snap(*start, "start")
    target, target_off = snap(*end, "destination")

    step_min = rain["step_minutes"]
    steps = len(rain["rain_mm_h"])
    first = min(max(leave_in_min // step_min - 1, 0), steps - 1)
    last = min(first + TRIP_WINDOW_MIN // step_min, steps - 1)
    depth = forecast.road_depths_cm(rain, runoff_coeff, drain_condition, first, last)

    safe_cost = routing.weights(depth)
    runs = {"dijkstra": _timed(routing.dijkstra, source, target, safe_cost),
            "astar": _timed(routing.astar, source, target, safe_cost)}
    chosen = runs[algorithm]
    shortest = routing.astar(source, target, routing.weights(None))

    plain = routing.describe(shortest, source, depth) if shortest["found"] else None
    flooded = routing.flooded_on(shortest, depth) if shortest["found"] else []
    safe = routing.describe(chosen, source, depth) if chosen["found"] else None
    _minutes(safe)
    _minutes(plain)

    def clock(i: int) -> str | None:
        return (rain["ends"][i] or "")[11:16] or None

    return {
        "from": {"lat": start[0], "lon": start[1], "snapped_m": round(source_off, 0)},
        "to": {"lat": end[0], "lon": end[1], "snapped_m": round(target_off, 0)},
        "rain": {key: rain[key] for key in ("mode", "source", "total_mm")},
        "drain_condition": drain_condition,
        "trip_window": {
            "from_min": (first + 1) * step_min,
            "to_min": (last + 1) * step_min,
            "from_clock": clock(first), "to_clock": clock(last),
        },
        "algorithm": algorithm,
        "safe_route": safe,
        "no_safe_route": None if safe else
            "Every way there crosses water over 30 cm in this window: the destination is cut off."
            if plain else
            "No road in the street layer joins these two points, flooded or not.",
        "shortest_route": plain,
        "shortest_route_flooded": {"type": "FeatureCollection", "features": flooded},
        "avoided": {
            "flooded_km_on_shortest": round(plain["wet_m"] / 1000, 2) if plain else 0.0,
            "impassable_stretches_on_shortest": sum(1 for f in flooded if f["properties"]["impassable"]),
            "extra_km": round(safe["length_km"] - plain["length_km"], 2) if safe and plain else None,
        },
        "search": {
            name: {"settled_junctions": r["settled"], "ms": r["ms"],
                   "cost_km": round(r["cost"] / 1000, 3) if r["found"] else None}
            for name, r in runs.items()
        },
        "thresholds": {"wet_cm": routing.WET_CM, "impassable_cm": routing.IMPASSABLE_CM},
    }
