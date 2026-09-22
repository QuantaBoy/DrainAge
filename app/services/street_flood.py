"""Which stretch of which street goes under water, from the flood at each manhole.

The flood model answers at manholes: this much water, this deep, at this step. A
driver needs the answer on streets. Water that surcharges at a manhole spreads along
the road it comes up in, and how far is not a free choice: a volume V standing d deep
on a road W wide covers V / (W * d) metres of it, half each way. That reach is worked
out at every step, so a street floods outwards from the manhole as the water rises and
shrinks back as it drains.

Reach alone would wet every street in a circle. The terrain decides which of them
the water actually gets onto: the flood stands at one level - the manhole's ground plus
its depth - and a street point is under water only where the DEM puts the ground below
that level, and by the difference. Streets on a rise inside the circle stay dry; a dip
further along the road floods deeper than the manhole it came from.

Streets are OpenStreetMap ways (app/data/chennai_streets.geojson). Their vertices can
be hundreds of metres apart on a straight road, so they are densified first; the
answer is then a run of vertices, cut exactly where the water stops.

Terrain decides *where* the water goes, the manhole decides *how deep*: a street
point is never shown deeper than the water at the manhole it came from. On a slope the
water runs off rather than standing level, so a street metres below the manhole is not
under metres of water - it is wet, to about the depth the flood model found.

ponytail: "ground" is terrain.ground_level, the lowest 30 m DEM cell around a point,
standing in for road level on a surface model. It sorts low streets from high ones
well; it cannot tell one side of a road from the other.
"""

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.services import terrain

STREETS_PATH = Path(__file__).resolve().parent.parent / "data" / "chennai_streets.geojson"

# Vertices are laid at least this close along every street, so a reach is resolved
# to within this distance.
SPACING_M = 20.0
# Spatial index cell, degrees (~110 m).
CELL_DEG = 0.001
# Water reaches at least across the junction it comes up in, and no further than a
# long block: past that the flood model's single ponding level no longer holds.
MIN_REACH_M = 25.0
MAX_REACH_M = 250.0

_index: dict[str, Any] | None = None


def _metres(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Ground distance; equirectangular is within a millimetre over a few hundred metres."""
    x = (lon2 - lon1) * terrain.M_PER_DEG * math.cos(math.radians((lat1 + lat2) / 2))
    y = (lat2 - lat1) * terrain.M_PER_DEG
    return math.hypot(x, y)


def densify(coords: list[list[float]], spacing_m: float = SPACING_M) -> list[list[float]]:
    """A polyline with extra vertices so no two are further apart than the spacing."""
    out = [list(coords[0])]
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        pieces = max(1, math.ceil(_metres(lon1, lat1, lon2, lat2) / spacing_m))
        for k in range(1, pieces + 1):
            f = k / pieces
            out.append([lon1 + (lon2 - lon1) * f, lat1 + (lat2 - lat1) * f])
    return out


def _load() -> dict[str, Any]:
    """Every street, densified, with an index from grid cell to the vertices in it."""
    global _index
    if _index is not None:
        return _index

    streets: list[dict[str, Any]] = []
    cells: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    if STREETS_PATH.exists():
        collection = json.loads(STREETS_PATH.read_text(encoding="utf-8"))
        for feature in collection["features"]:
            # A bridge deck is metres above the DEM, which reads the river or canal under
            # it: every flood model would put the channel's water on the deck.
            if feature["properties"].get("bridge"):
                continue
            coords = densify(feature["geometry"]["coordinates"])
            index = len(streets)
            streets.append({"coords": coords, "name": feature["properties"].get("name"),
                            "highway": feature["properties"].get("highway"),
                            "tunnel": bool(feature["properties"].get("tunnel"))})
            for vertex, (lon, lat) in enumerate(coords):
                cells[(int(lon // CELL_DEG), int(lat // CELL_DEG))].append((index, vertex))

    _index = {"streets": streets, "cells": cells}
    return _index


def reach_m(volume_m3: float, depth_m: float) -> float:
    """How far along the road water spreads from the manhole, each way."""
    if volume_m3 <= 0 or depth_m <= 0:
        return 0.0
    covered = volume_m3 / (terrain.ROAD_WIDTH_M * depth_m)
    return min(max(covered / 2, MIN_REACH_M), MAX_REACH_M)


def water_profile(point: dict[str, Any]) -> tuple[list[float], list[float | None]]:
    """How far along the road the water reaches, and the level it stands at, per step.

    The level is the manhole's ground plus its depth; None where there is no DEM or
    no water.
    """
    reaches = [reach_m(v, d) for v, d in zip(point["volume_m3"], point["depth_m"])]
    ground_here = point.get("ground_m")
    levels = [None if ground_here is None or d <= 0 else ground_here + d for d in point["depth_m"]]
    return reaches, levels


def flooded_streets(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Street stretches under water around each flooding manhole.

    Each point carries `lon`, `lat`, `ground_m` (None where there is no DEM), and
    `depth_m` and `volume_m3`, one value per step. Returned are GeoJSON lines, one per
    contiguous stretch of one street near one point. Every vertex carries its distance
    from the manhole and its ground level, and every stretch names its manhole (`node`),
    whose reach and water level per step come from `water_profile`. A map can then cut
    the line wherever the water stops - too far along, or ground too high - at any
    step, without asking again. The per-step figures stay on the manhole: copied onto
    each of a city's twenty thousand stretches they were most of the payload.
    """
    index = _load()
    streets, cells = index["streets"], index["cells"]
    features: list[dict[str, Any]] = []

    for point in points:
        lon, lat = point["lon"], point["lat"]
        reaches, levels = water_profile(point)
        furthest = max(reaches, default=0.0)
        if furthest <= 0:
            continue
        top = max((level for level in levels if level is not None), default=None)

        # Cells to search: enough to cover the furthest reach in every direction.
        span = math.ceil(furthest / (CELL_DEG * terrain.M_PER_DEG * math.cos(math.radians(lat)))) + 1
        cx, cy = int(lon // CELL_DEG), int(lat // CELL_DEG)
        near: dict[int, dict[int, float]] = defaultdict(dict)
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                for street, vertex in cells.get((cx + dx, cy + dy), ()):
                    vlon, vlat = streets[street]["coords"][vertex]
                    distance = _metres(lon, lat, vlon, vlat)
                    if distance > furthest:
                        continue
                    ground = terrain.ground_level(vlat, vlon) if top is not None else None
                    # Above the highest the water ever stands here: never wet.
                    if ground is not None and ground >= top:
                        continue
                    near[street][vertex] = (distance, ground)

        for street, vertices in near.items():
            ordered = sorted(vertices)
            # Consecutive vertex runs: a street that bends out of reach and back in
            # gives two separate stretches, not one line across the gap.
            run = [ordered[0]]
            for vertex in ordered[1:] + [None]:
                if vertex is not None and vertex == run[-1] + 1:
                    run.append(vertex)
                    continue
                if len(run) >= 2:
                    coords = streets[street]["coords"]
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "LineString",
                                     # 5 decimals is about a metre: finer than the DEM.
                                     "coordinates": [[round(c, 5) for c in coords[i]] for i in run]},
                        "properties": {
                            "name": streets[street]["name"],
                            "highway": streets[street]["highway"],
                            "node": point.get("node"),
                            "distance_m": [round(vertices[i][0], 1) for i in run],
                            "ground_m": [None if vertices[i][1] is None else round(vertices[i][1], 2)
                                         for i in run],
                        },
                    })
                if vertex is not None:
                    run = [vertex]
    return features


if __name__ == "__main__":
    # A 90 m straight line needs five 18 m pieces to keep every gap within 20 m.
    line = densify([[80.2, 13.0], [80.2 + 90 / (terrain.M_PER_DEG * math.cos(math.radians(13))), 13.0]])
    assert len(line) == 6, len(line)
    assert all(_metres(*a, *b) <= SPACING_M + 1e-6 for a, b in zip(line, line[1:]))

    # 360 m³ standing 0.15 m deep on a 12 m road covers 200 m: 100 m each way.
    assert abs(reach_m(360.0, 0.15) - 100.0) < 1e-9
    assert reach_m(1.0, 0.5) == MIN_REACH_M and reach_m(1e6, 0.05) == MAX_REACH_M
    assert reach_m(0.0, 0.3) == 0.0

    # Against the real street network: a flood at Chennai Central wets the roads
    # around it, never further than its reach, and nothing when it is dry.
    if STREETS_PATH.exists():
        here = terrain.ground_level(13.0827, 80.2707)
        flat = flooded_streets([{"lon": 80.2707, "lat": 13.0827, "ground_m": None,
                                 "depth_m": [0.0, 0.2], "volume_m3": [0.0, 480.0]}])
        wet = flooded_streets([{"lon": 80.2707, "lat": 13.0827, "ground_m": here,
                                "depth_m": [0.0, 0.2], "volume_m3": [0.0, 480.0]}])
        assert wet, "no street found near Chennai Central"
        assert all(max(f["properties"]["distance_m"]) <= 100.0 + 1e-6 for f in wet)
        # Every wet point is below the water level; terrain can only remove street,
        # never add it.
        level = here + 0.2
        assert all(g < level for f in wet for g in f["properties"]["ground_m"])
        count = lambda fs: sum(len(f["geometry"]["coordinates"]) for f in fs)
        assert count(wet) <= count(flat), (count(wet), count(flat))
        assert flooded_streets([{"lon": 80.2707, "lat": 13.0827, "ground_m": here,
                                 "depth_m": [0.0], "volume_m3": [0.0]}]) == []
        names = sorted({f["properties"]["name"] for f in wet if f["properties"]["name"]})
        print(f"{len(_load()['streets'])} streets indexed; 480 m³ at 20 cm by Chennai Central: "
              f"{count(flat)} street points in reach, {count(wet)} below the water level "
              f"({len(wet)} stretches: {', '.join(names[:4]) or 'none named'})")
    print("street_flood self-check passed")
