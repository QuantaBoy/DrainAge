"""Chennai's roads and waterways from OpenStreetMap, as GeoJSON files on disk.

Neither changes day to day, so each is fetched from Overpass once and kept in app/data;
the street index, the router and the terrain conditioning all read those files.
"""

import json
from pathlib import Path
from typing import Any

import httpx

from app import config
from app.errors import Unavailable, UpstreamError

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Road classes worth routing and flood-mapping; footpaths and tracks are left out, they
# would triple the payload without adding streets a vehicle or drain overflow uses.
ROAD_TYPES = "|".join([
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "motorway_link", "trunk_link",
    "primary_link", "secondary_link", "tertiary_link",
])

_SOUTH, _WEST, _NORTH, _EAST = config.CHENNAI_BBOX
STREETS_QUERY = f"""
[out:json][timeout:60];
way["highway"~"^({ROAD_TYPES})$"]({_SOUTH},{_WEST},{_NORTH},{_EAST});
(._;>;);
out body;
"""
# Rivers, canals and nullahs, a little past the box so a channel leaving it still drains.
# The terrain burns them into the DEM, where 30 m cells otherwise read every road
# embankment across a canal as a dam.
WATERWAYS_QUERY = f"""
[out:json][timeout:120];
way["waterway"~"^(river|canal|stream|drain)$"]({_SOUTH - 0.02},{_WEST - 0.02},{_NORTH + 0.02},{_EAST + 0.02});
(._;>;);
out body;
"""


async def _overpass(query: str) -> dict[str, Any]:
    # Overpass rejects requests with no identifying User-Agent (HTTP 406).
    try:
        async with httpx.AsyncClient(timeout=150.0, headers={"User-Agent": config.USER_AGENT}) as client:
            response = await client.post(OVERPASS_URL, data={"data": query})
    except httpx.RequestError as exc:
        raise Unavailable("Could not reach the OpenStreetMap Overpass API.") from exc
    if response.status_code != 200:
        raise UpstreamError(f"Overpass API returned HTTP {response.status_code}.")
    return response.json()


def _lines(data: dict[str, Any], properties) -> dict[str, Any]:
    """Overpass node/way JSON as a FeatureCollection of lines."""
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in data["elements"] if e["type"] == "node"}
    features = []
    for element in data["elements"]:
        if element["type"] != "way":
            continue
        coords = [nodes[n] for n in element["nodes"] if n in nodes]
        # A way clipped by the box edge can be left with one node; a line needs two, or
        # GeoJSON consumers draw it as a stray point marker.
        if len(coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"id": element["id"], **properties(element.get("tags", {}))},
        })
    return {"type": "FeatureCollection", "features": features}


def _road(tags: dict[str, str]) -> dict[str, Any]:
    return {
        "name": tags.get("name"),
        "highway": tags.get("highway"),
        # A bridge deck sits metres above the DEM cell under it, which is the river;
        # the flood model skips bridges rather than drown them.
        "bridge": tags.get("bridge", "no") != "no",
        "tunnel": tags.get("tunnel", "no") != "no",
    }


def _waterway(tags: dict[str, str]) -> dict[str, Any]:
    return {"name": tags.get("name"), "waterway": tags.get("waterway"), "tunnel": tags.get("tunnel")}


async def _cached(path: Path, query: str, properties) -> Path:
    if not path.exists():
        geojson = _lines(await _overpass(query), properties)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(geojson), encoding="utf-8")
    return path


async def streets_file() -> Path:
    """The road network GeoJSON, fetched on first use."""
    return await _cached(config.STREETS_GEOJSON, STREETS_QUERY, _road)


async def waterways_file() -> Path:
    """The rivers, canals and drains GeoJSON, fetched on first use."""
    return await _cached(config.WATERWAYS_GEOJSON, WATERWAYS_QUERY, _waterway)
