"""Street geometry: Chennai's road network from OpenStreetMap, as GeoJSON.

The network barely changes day to day, so it is fetched from Overpass once and
cached on disk rather than queried on every dashboard load.
"""

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/data-collection", tags=["Streets"])

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "chennai_streets.geojson"

# Greater Chennai bounding box (south, west, north, east).
CHENNAI_BBOX = (12.85, 80.10, 13.25, 80.35)

# Road classes worth routing/flood-mapping; footpaths and tracks are excluded, they
# would triple the payload without adding streets a vehicle or drain overflow uses.
ROAD_TYPES = "|".join([
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "motorway_link", "trunk_link",
    "primary_link", "secondary_link", "tertiary_link",
])

OVERPASS_QUERY = f"""
[out:json][timeout:60];
way["highway"~"^({ROAD_TYPES})$"]({CHENNAI_BBOX[0]},{CHENNAI_BBOX[1]},{CHENNAI_BBOX[2]},{CHENNAI_BBOX[3]});
(._;>;);
out body;
"""


def _overpass_to_geojson(data: dict[str, Any]) -> dict[str, Any]:
    """Turn Overpass's node/way JSON into a GeoJSON FeatureCollection of lines."""
    nodes = {
        element["id"]: (element["lon"], element["lat"])
        for element in data["elements"]
        if element["type"] == "node"
    }
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [nodes[node_id] for node_id in element["nodes"]],
            },
            "properties": {
                "id": element["id"],
                "name": element.get("tags", {}).get("name"),
                "highway": element.get("tags", {}).get("highway"),
                # A bridge deck sits metres above the DEM cell under it, which is the
                # river; the flood models skip bridges rather than drown them.
                "bridge": element.get("tags", {}).get("bridge", "no") != "no",
                "tunnel": element.get("tags", {}).get("tunnel", "no") != "no",
            },
        }
        for element in data["elements"]
        if element["type"] == "way"
        # A way clipped by the bbox edge can be left with one node; a line needs at
        # least two, or GeoJSON consumers render it as a stray point marker instead.
        and len(element["nodes"]) >= 2
    ]
    return {"type": "FeatureCollection", "features": features}


WATERWAYS_PATH = CACHE_PATH.parent / "chennai_waterways.geojson"
# Rivers, canals and nullahs, a little past the box so a channel leaving it still drains.
# The rain model burns them into the DEM, where 30 m cells otherwise read every road
# embankment across a canal as a dam.
WATERWAYS_QUERY = f"""
[out:json][timeout:120];
way["waterway"~"^(river|canal|stream|drain)$"]({CHENNAI_BBOX[0] - 0.02},{CHENNAI_BBOX[1] - 0.02},{CHENNAI_BBOX[2] + 0.02},{CHENNAI_BBOX[3] + 0.02});
(._;>;);
out body;
"""


async def _overpass(query: str) -> dict[str, Any]:
    # Overpass rejects requests with no identifying User-Agent (HTTP 406).
    headers = {"User-Agent": "SIH26085-flood-dashboard/1.0"}
    try:
        async with httpx.AsyncClient(timeout=150.0, headers=headers) as client:
            response = await client.post(OVERPASS_URL, data={"data": query})
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach the OpenStreetMap Overpass API.",
        ) from exc
    if response.status_code != status.HTTP_200_OK:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Overpass API returned HTTP {response.status_code}.",
        )
    return response.json()


@router.get("/waterways", summary="Chennai rivers, canals and drains as GeoJSON")
async def get_waterways() -> dict[str, Any]:
    if WATERWAYS_PATH.exists():
        return json.loads(WATERWAYS_PATH.read_text(encoding="utf-8"))
    data = await _overpass(WATERWAYS_QUERY)
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in data["elements"] if e["type"] == "node"}
    features = []
    for element in data["elements"]:
        if element["type"] != "way":
            continue
        coords = [nodes[n] for n in element["nodes"] if n in nodes]
        if len(coords) < 2:
            continue
        tags = element.get("tags", {})
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"id": element["id"], "name": tags.get("name"),
                           "waterway": tags.get("waterway"), "tunnel": tags.get("tunnel")},
        })
    geojson = {"type": "FeatureCollection", "features": features}
    WATERWAYS_PATH.write_text(json.dumps(geojson), encoding="utf-8")
    return geojson


@router.get("/streets", summary="Chennai road network as GeoJSON")
async def get_streets() -> dict[str, Any]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    geojson = _overpass_to_geojson(await _overpass(OVERPASS_QUERY))
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(geojson), encoding="utf-8")
    return geojson
