"""Flood-safe routes: from one place to another, round the water the forecast puts
on the streets.

Two endpoints. /geocode turns a typed place name into a point inside Chennai.
/route finds the cheapest path through the street graph (app/services/routing.py),
with each road costed by the water the coupled flood nowcast has on it for the
trip, and sets it against the plain shortest path so the page can show what was
avoided and why. Both Dijkstra and A* are run and reported: the same route, and how
much less of the city A* had to search to find it.
"""

import asyncio
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.routes import flood
from app.routes.streets import CHENNAI_BBOX
from app.services import hydraulics, rain_ponding, routing

router = APIRouter(prefix="/data-collection", tags=["Navigation"])

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy asks for an identifying User-Agent and a light touch; every
# answer is cached, and each search is limited to the Chennai box.
USER_AGENT = "SIH26085-flood-dashboard/1.0"
_places: dict[str, list[dict[str, Any]]] = {}
_PLACES_LIMIT = 500

# A trip is costed against the deepest water its roads carry from the moment it
# leaves until this long after: a cross-city drive takes about that, and water that
# rises while you are on the way is still in the way.
# ponytail: one window for every trip; timing each stretch by when the car reaches it
# is the refinement.
TRIP_WINDOW_MIN = 30

AVERAGE_SPEED_KMH = 20.0          # Chennai's peak-hour traffic, for the minutes shown


def _local_matches(q: str, limit: int) -> list[dict[str, Any]]:
    """Streets in our own road layer whose name contains the query: the fallback."""
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


@router.get("/geocode", summary="Places in Chennai matching a name")
async def geocode(q: str = Query(..., min_length=2, max_length=120)) -> dict[str, Any]:
    key = q.strip().lower()
    if key in _places:
        return {"query": q, "results": _places[key]}

    south, west, north, east = CHENNAI_BBOX
    results: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(NOMINATIM_URL, params={
                "q": q, "format": "jsonv2", "limit": 6,
                "viewbox": f"{west},{north},{east},{south}", "bounded": 1,
                "countrycodes": "in",
            })
        if response.status_code == 200:
            for place in response.json():
                results.append({
                    "name": place.get("display_name", q).split(", Chennai")[0],
                    "lat": float(place["lat"]), "lon": float(place["lon"]),
                    "source": "OpenStreetMap Nominatim",
                })
    except httpx.RequestError:
        pass
    if not results:
        # Nominatim down or no match: a street of that name in our own layer.
        results = await asyncio.to_thread(_local_matches, q, 6)

    if len(_places) >= _PLACES_LIMIT:
        _places.pop(next(iter(_places)))
    _places[key] = results
    return {"query": q, "results": results}


def _timed(search, source: int, target: int, cost) -> dict[str, Any]:
    began = time.perf_counter()
    found = search(source, target, cost)
    found["ms"] = round((time.perf_counter() - began) * 1000, 1)
    return found


@router.get("/route", summary="The flood-safe route between two places, 0-3 h ahead")
async def get_route(
    from_lat: float = Query(..., ge=12.0, le=14.0),
    from_lon: float = Query(..., ge=79.5, le=81.0),
    to_lat: float = Query(..., ge=12.0, le=14.0),
    to_lon: float = Query(..., ge=79.5, le=81.0),
    leave_in_min: int = Query(0, ge=0, le=180, description="When the trip starts, minutes from now"),
    rain_mm_h: float | None = flood._RAIN_MM_H,
    drain_condition: float = flood._DRAIN_CONDITION,
    runoff_coeff: float = Query(hydraulics.RUNOFF_COEFF, gt=0, le=1),
    algorithm: str = Query("astar", pattern="^(astar|dijkstra)$"),
) -> dict[str, Any]:
    source, source_off = await asyncio.to_thread(routing.nearest_node, from_lat, from_lon)
    target, target_off = await asyncio.to_thread(routing.nearest_node, to_lat, to_lon)
    for name, off in (("start", source_off), ("destination", target_off)):
        if off > 1000:
            raise HTTPException(status_code=422, detail=f"The {name} is {off:.0f} m from the "
                                                        "nearest road in the Chennai street layer")

    rain = await flood._rain_steps(from_lat, from_lon, rain_mm_h)

    def run() -> dict[str, Any]:
        model = flood._coupled_system()["model"]
        levels = flood._run_coupled(rain, runoff_coeff, drain_condition)["levels"]
        step_min = rain["step_minutes"]
        first = min(max(leave_in_min // step_min - 1, 0), len(levels) - 1)
        last = min(first + TRIP_WINDOW_MIN // step_min, len(levels) - 1)
        depth = routing.edge_depths_cm(model, rain_ponding.depth_grid(model, levels[first:last + 1].max(axis=0)))

        safe_cost = routing.weights(depth)
        dry_cost = routing.weights(None)
        runs = {
            "dijkstra": _timed(routing.dijkstra, source, target, safe_cost),
            "astar": _timed(routing.astar, source, target, safe_cost),
        }
        chosen = runs[algorithm]
        shortest = routing.astar(source, target, dry_cost)
        return {"depth": depth, "runs": runs, "chosen": chosen, "shortest": shortest,
                "window": (first, last)}

    result = await asyncio.to_thread(run)
    depth, chosen, shortest = result["depth"], result["chosen"], result["shortest"]
    first, last = result["window"]

    plain = routing.describe(shortest, source, depth) if shortest["found"] else None
    flooded = routing.flooded_on(shortest, depth) if shortest["found"] else []
    safe = routing.describe(chosen, source, depth) if chosen["found"] else None
    if safe:
        safe["minutes"] = round(safe["length_km"] / AVERAGE_SPEED_KMH * 60)
    if plain:
        plain["minutes"] = round(plain["length_km"] / AVERAGE_SPEED_KMH * 60)

    clock = lambda i: (rain["ends"][i] or "")[11:16] or None
    return {
        "from": {"lat": from_lat, "lon": from_lon, "snapped_m": round(source_off, 0)},
        "to": {"lat": to_lat, "lon": to_lon, "snapped_m": round(target_off, 0)},
        "rain": {k: rain[k] for k in ("mode", "source", "total_mm")},
        "drain_condition": drain_condition,
        "trip_window": {
            "from_min": (first + 1) * rain["step_minutes"],
            "to_min": (last + 1) * rain["step_minutes"],
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
            for name, r in result["runs"].items()
        },
        "thresholds": {"wet_cm": routing.WET_CM, "impassable_cm": routing.IMPASSABLE_CM},
    }
