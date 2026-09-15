"""Street-level water flow: rain routed downhill along Chennai's OSM street network.

Streets act as the channels surface water follows. Every street vertex is a node
with a ground height from the Copernicus 30 m elevation model; water at each node
moves to the neighbour it drains to, collects along the way, and pools where the
streets dip. Drainage pipes are not modelled yet, so pooling is the upper bound.
"""

import asyncio
import heapq
import json
from functools import lru_cache
from typing import Annotated, Any, Optional

import httpx
import numpy as np
import tifffile
from fastapi import APIRouter, Query
from scipy.ndimage import minimum_filter
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from app.routes import streets, weather

router = APIRouter(prefix="/data-collection", tags=["Flood"])

DATA_DIR = streets.CACHE_PATH.parent
DEM_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
# One-degree tiles keyed by their southern latitude; Chennai straddles 13° N.
DEM_TILES = {
    12: "Copernicus_DSM_COG_10_N12_00_E080_00_DEM",
    13: "Copernicus_DSM_COG_10_N13_00_E080_00_DEM",
}
DEM_WEST_LON = 80.0
DEM_STEP_DEG = 1 / 3600

CHENNAI_CENTRE = (13.0827, 80.2707)

# ponytail: one strip width and one runoff share for the whole city; per-street
# values from a land-cover map (ESA WorldCover) are the upgrade.
CATCHMENT_WIDTH_M = 40  # road plus the frontage and roofs that drain onto it
RUNOFF_COEFFICIENT = 0.9  # share of rain on dense urban surfaces that runs off
# ponytail: stand-in for the storm drain network, which is not modelled yet: drains
# are assumed to carry away this much rain per hour everywhere. Without it a
# drizzle pooled a catchment's water into dips. Replace with the drain graph.
DRAIN_ALLOWANCE_MM_PER_H = 10

# Water leaves the network at the sea, the rivers and canals, and the edge of the
# mapped area; nodes this low sit at water level.
OUTLET_ELEVATION_M = 1.0
EDGE_MARGIN_DEG = 0.005

# Paths draining less area than this are left out: they are the many tiny side
# lanes whose flow is too small to matter and would swamp the map.
MIN_FLOW_AREA_M2 = 50_000
MIN_POND_DEPTH_M = 0.05
MIN_REPORTED_DEPTH_CM = 2

# The elevation model is a surface model: rooftops, trees and flyovers sit above the
# road. The lowest pixel within 270 m is a better ground estimate; smaller windows
# left thousands of false dips, larger ones blur streets together.
GROUND_WINDOW_PX = 9
# Dips shallower than the model's height noise are indistinguishable from flat road.
DEM_NOISE_M = 1.0
# Beyond this the 30 m model cannot resolve depth; deeper estimates are reported as
# this value and flagged.
MAX_REPORTED_DEPTH_CM = 150


def load_dem() -> dict[int, np.ndarray]:
    """Ground heights per tile, downloaded once."""
    grids = {}
    for base, name in DEM_TILES.items():
        path = DATA_DIR / f"{name}.tif"
        if not path.exists():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            response = httpx.get(DEM_URL.format(name=name), timeout=180.0)
            response.raise_for_status()
            path.write_bytes(response.content)
        grids[base] = minimum_filter(tifffile.imread(path), size=GROUND_WINDOW_PX)
    return grids


def sample_elevation(
    grids: dict[int, np.ndarray], lons: np.ndarray, lats: np.ndarray
) -> np.ndarray:
    heights = np.zeros(len(lats))
    for base, grid in grids.items():
        inside = (lats >= base) & (lats < base + 1)
        rows = ((base + 1 - lats[inside]) / DEM_STEP_DEG).astype(int)
        cols = ((lons[inside] - DEM_WEST_LON) / DEM_STEP_DEG).astype(int)
        heights[inside] = grid[
            rows.clip(0, grid.shape[0] - 1), cols.clip(0, grid.shape[1] - 1)
        ]
    return heights


def priority_flood(
    elevation: list[float], neighbours: list[list[int]], outlets: list[int]
) -> tuple[list[int], list[float], list[int]]:
    """Drainage over a graph, from its outlets uphill (Barnes et al., Priority-Flood).

    Returns each node's downstream neighbour (-1 at outlets), its filled height (the
    level water must reach before it can drain away, so filled minus ground is how
    deep it can pool), and the processing order, which runs from downstream to
    upstream.
    """
    count = len(elevation)
    downstream = [-1] * count
    filled = list(elevation)
    done = [False] * count
    order = []

    def spread(heap: list[tuple[float, int]]) -> None:
        heapq.heapify(heap)
        while heap:
            level, node = heapq.heappop(heap)
            order.append(node)
            for neighbour in neighbours[node]:
                if done[neighbour]:
                    continue
                done[neighbour] = True
                downstream[neighbour] = node
                filled[neighbour] = max(elevation[neighbour], level)
                heapq.heappush(heap, (filled[neighbour], neighbour))

    for node in outlets:
        done[node] = True
    spread([(elevation[node], node) for node in outlets])

    # A street cluster that reaches no outlet drains from its own lowest point.
    for node in sorted(range(count), key=elevation.__getitem__):
        if not done[node]:
            done[node] = True
            spread([(elevation[node], node)])
    return downstream, filled, order


def accumulate(own: list[float], downstream: list[int], order: list[int]) -> list[float]:
    """Total of `own` from each node and everything that drains through it."""
    total = list(own)
    for node in reversed(order):
        if downstream[node] >= 0:
            total[downstream[node]] += total[node]
    return total


@lru_cache(maxsize=1)
def build_network() -> dict[str, Any]:
    """Drainage paths and pond sites for the street network; rain-independent."""
    geojson = json.loads(streets.CACHE_PATH.read_text(encoding="utf-8"))

    # Streets share a node exactly where OSM joins them, which is where the
    # coordinates are identical.
    index: dict[tuple[float, float], int] = {}
    lons, lats, starts, ends, names = [], [], [], [], []
    edge_street: dict[tuple[int, int], Optional[str]] = {}
    for feature in geojson["features"]:
        name = feature["properties"]["name"]
        previous = None
        for lon, lat in feature["geometry"]["coordinates"]:
            node = index.setdefault((lon, lat), len(lons))
            if node == len(lons):
                lons.append(lon)
                lats.append(lat)
            if previous is not None and previous != node:
                starts.append(previous)
                ends.append(node)
                edge_street[(previous, node)] = edge_street[(node, previous)] = name
            previous = node
    del geojson, index

    lon, lat = np.array(lons), np.array(lats)
    start, end = np.array(starts), np.array(ends)
    count = len(lon)

    dx = (lon[end] - lon[start]) * 111_320 * np.cos(np.radians(lat[start]))
    dy = (lat[end] - lat[start]) * 110_540
    half_strip = np.hypot(dx, dy) * CATCHMENT_WIDTH_M / 2
    area = np.zeros(count)
    np.add.at(area, start, half_strip)
    np.add.at(area, end, half_strip)

    elevation = sample_elevation(load_dem(), lon, lat)
    south, west, north, east = streets.CHENNAI_BBOX
    at_edge = (
        (lat < south + EDGE_MARGIN_DEG) | (lat > north - EDGE_MARGIN_DEG)
        | (lon < west + EDGE_MARGIN_DEG) | (lon > east - EDGE_MARGIN_DEG)
    )
    outlets = np.flatnonzero(at_edge | (elevation <= OUTLET_ELEVATION_M))

    neighbours: list[list[int]] = [[] for _ in range(count)]
    for a, b in zip(starts, ends):
        neighbours[a].append(b)
        neighbours[b].append(a)

    downstream, filled, order = priority_flood(
        elevation.tolist(), neighbours, outlets.tolist()
    )
    drained = np.array(accumulate(area.tolist(), downstream, order))
    downstream_arr = np.array(downstream)

    # Drainage paths: runs of nodes between sources, confluences and outlets.
    significant = (drained >= MIN_FLOW_AREA_M2) & (downstream_arr >= 0)
    inflows = np.bincount(downstream_arr[significant], minlength=count)
    paths = []
    for head in np.flatnonzero(significant & (inflows != 1)):
        path, node = [head], head
        while True:
            node = downstream[node]
            path.append(node)
            if not significant[node] or inflows[node] != 1:
                break
        paths.append({
            "coordinates": [[lon[i], lat[i]] for i in path],
            "drained_m2": float(drained[path[-2]]),
            "name": edge_street.get((path[0], path[1])),
        })

    # Ponds: connected nodes lying below the level they must fill to before spilling.
    depth = np.array(filled) - elevation - DEM_NOISE_M
    wet = np.flatnonzero(depth >= MIN_POND_DEPTH_M)
    wet_index = np.full(count, -1)
    wet_index[wet] = np.arange(len(wet))
    both_wet = (wet_index[start] >= 0) & (wet_index[end] >= 0)
    links = coo_matrix(
        (np.ones(both_wet.sum()), (wet_index[start][both_wet], wet_index[end][both_wet])),
        shape=(len(wet), len(wet)),
    )
    pond_count, pond_of = connected_components(links, directed=False)
    ponds = []
    for pond in range(pond_count):
        members = wet[pond_of == pond]
        deepest = members[np.argmax(depth[members])]
        ponds.append((
            lon[deepest], lat[deepest], depth[deepest],
            drained[members].max(), area[members].sum(),
        ))
    pond_lon, pond_lat, capacity, inflow_area, pond_area = map(np.array, zip(*ponds))

    return {
        "paths": paths,
        "ponds": {
            "lon": pond_lon, "lat": pond_lat, "capacity_m": capacity,
            "inflow_m2": inflow_area, "area_m2": pond_area,
        },
        "stats": {
            "street_nodes": count,
            "street_segments": len(starts),
            "outlets": len(outlets),
            "drainage_paths": len(paths),
            "pond_sites": pond_count,
        },
    }


@router.get("/street-flow", summary="Surface water flow and pooling along Chennai streets")
async def get_street_flow(
    rain_mm: Annotated[
        Optional[float],
        Query(ge=0, le=500, description="Rain over the window, mm; omit to use the forecast"),
    ] = None,
    hours: Annotated[int, Query(ge=1, le=3, description="Nowcast window, hours")] = 3,
) -> dict[str, Any]:
    if not streets.CACHE_PATH.exists():
        await streets.get_streets()
    network = await asyncio.to_thread(build_network)

    source = "scenario"
    if rain_mm is None:
        forecast = await weather.get_forecast(
            lat=CHENNAI_CENTRE[0], lon=CHENNAI_CENTRE[1], hours=hours
        )
        rain_mm = round(sum(hour["rain_mm"] for hour in forecast["hours"]), 1)
        source = "forecast"
    surface_mm = max(0.0, rain_mm - DRAIN_ALLOWANCE_MM_PER_H * hours)
    runoff_m = surface_mm / 1000 * RUNOFF_COEFFICIENT

    ponds = network["ponds"]
    # Water reaching a pond spreads over its streets, but cannot rise past the
    # level where it spills onward.
    depth_cm = 100 * np.minimum(
        ponds["capacity_m"], runoff_m * ponds["inflow_m2"] / ponds["area_m2"]
    )
    shown = np.flatnonzero(depth_cm >= MIN_REPORTED_DEPTH_CM)

    return {
        "rain_mm": rain_mm,
        "hours": hours,
        "rain_source": source,
        "runoff_coefficient": RUNOFF_COEFFICIENT,
        "drainage_modelled": False,
        "drain_allowance_mm_per_h": DRAIN_ALLOWANCE_MM_PER_H,
        "surface_water_mm": round(surface_mm, 1),
        "network": network["stats"],
        "flows": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    # Coordinates run downhill, in the direction the water moves.
                    "geometry": {"type": "LineString", "coordinates": path["coordinates"]},
                    "properties": {
                        "name": path["name"],
                        "drained_ha": round(path["drained_m2"] / 10_000, 1),
                        "volume_m3": round(path["drained_m2"] * runoff_m, 1),
                    },
                }
                for path in network["paths"]
            ],
        },
        "ponds": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [float(ponds["lon"][i]), float(ponds["lat"][i])],
                    },
                    "properties": {
                        "depth_cm": min(round(float(depth_cm[i])), MAX_REPORTED_DEPTH_CM),
                        "capped": bool(depth_cm[i] > MAX_REPORTED_DEPTH_CM),
                    },
                }
                for i in shown[np.argsort(-depth_cm[shown])]
            ],
        },
    }


if __name__ == "__main__":
    # A street of five nodes draining to an outlet at node 4, with a dip at node 2
    # that holds water until it reaches the height of node 3.
    heights = [5.0, 3.0, 1.0, 2.0, 0.0]
    line = [[1], [0, 2], [1, 3], [2, 4], [3]]
    down, level, sequence = priority_flood(heights, line, [4])
    assert down == [1, 2, 3, 4, -1], down
    assert level[2] == 2.0 and level[0] == 5.0, level
    assert accumulate([1.0] * 5, down, sequence) == [1, 2, 3, 4, 5]

    # Two separate clusters, one without an outlet, still all drain somewhere.
    down, _, sequence = priority_flood([2.0, 1.0, 4.0, 3.0], [[1], [0], [3], [2]], [1])
    assert down == [1, -1, 3, -1] and len(sequence) == 4, down
    print("flow self-check passed")
