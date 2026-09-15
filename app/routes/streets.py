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
            },
        }
        for element in data["elements"]
        if element["type"] == "way"
        # A way clipped by the bbox edge can be left with one node; a line needs at
        # least two, or GeoJSON consumers render it as a stray point marker instead.
        and len(element["nodes"]) >= 2
    ]
    return {"type": "FeatureCollection", "features": features}


@router.get("/streets", summary="Chennai road network as GeoJSON")
async def get_streets() -> dict[str, Any]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    # Overpass rejects requests with no identifying User-Agent (HTTP 406).
    headers = {"User-Agent": "SIH26085-flood-dashboard/1.0"}
    try:
        async with httpx.AsyncClient(timeout=90.0, headers=headers) as client:
            response = await client.post(OVERPASS_URL, data={"data": OVERPASS_QUERY})
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

    geojson = _overpass_to_geojson(response.json())
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(geojson), encoding="utf-8")
    return geojson
