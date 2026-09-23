"""Flood-safe routing over Chennai's streets: Dijkstra and A* on the road graph.

The street network (OpenStreetMap, 80,206 roads) becomes a graph. Its nodes are
junctions, the points where two roads share an OSM node, plus the dead ends. Its
edges are the stretches of road between them, each with its length and shape. A
route is the cheapest path through that graph, and the cost of a stretch is its
length made dearer by the water forecast on it:

  * dry, or under 5 cm: its length
  * 5-30 cm: its length x (1 + depth_cm / 10), so 20 cm costs three times the
    distance, and a longer dry road wins over a short wet one
  * 30 cm or more: impassable, as the flood layers mark it, and left out

Both algorithms find the same cheapest path. Dijkstra settles junctions outward in
order of cost until it reaches the destination. A* adds the straight-line distance
still to go, which never overestimates (no road is shorter than a straight line and
water only adds cost), so it heads for the destination and settles far fewer
junctions to find the same answer.

ponytail: one-way streets are not in the cached street layer, so every road is
two-way. That is right for emergency vehicles and wrong for commuters on the
city's one-way grid; the Overpass query in app/routes/streets.py would need the
oneway tag to fix it.
"""

import heapq
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from app.services import terrain

STREETS_PATH = Path(__file__).resolve().parent.parent / "data" / "chennai_streets.geojson"

WET_CM = 5.0
IMPASSABLE_CM = 30.0
# How much dearer each centimetre of water makes a road: 10 cm doubles its cost.
# A calibration knob: how far a driver will go round rather than through.
CM_PER_EXTRA_LENGTH = 10.0
# Flood depth is read along each stretch this often.
SAMPLE_M = 30.0

M_LAT = terrain.M_PER_DEG
M_LON = terrain.M_PER_DEG * math.cos(math.radians(13.05))

_graph: dict[str, Any] | None = None


def _metres(a: list[float], b: list[float]) -> float:
    return math.hypot((b[0] - a[0]) * M_LON, (b[1] - a[1]) * M_LAT)


def load() -> dict[str, Any]:
    """The street graph, built once: junctions, the stretches between them, an index."""
    global _graph
    if _graph is not None:
        return _graph

    roads = json.loads(STREETS_PATH.read_text(encoding="utf-8"))["features"]
    key = lambda c: (round(c[0], 7), round(c[1], 7))

    # A vertex two roads share, or a road's end, is a junction; the rest is shape.
    seen: dict[tuple, int] = {}
    for road in roads:
        coords = road["geometry"]["coordinates"]
        for c in coords:
            seen[key(c)] = seen.get(key(c), 0) + 1
        for c in (coords[0], coords[-1]):
            seen[key(c)] = seen.get(key(c), 0) + 2

    node_id: dict[tuple, int] = {}
    points: list[list[float]] = []
    edges: list[dict[str, Any]] = []
    adjacency: list[list[tuple[int, int]]] = []

    def node(c: list[float]) -> int:
        k = key(c)
        if k not in node_id:
            node_id[k] = len(points)
            points.append([c[0], c[1]])
            adjacency.append([])
        return node_id[k]

    for road in roads:
        coords = road["geometry"]["coordinates"]
        props = road["properties"]
        start = 0
        for i in range(1, len(coords)):
            if seen[key(coords[i])] < 2 and i < len(coords) - 1:
                continue
            shape = coords[start:i + 1]
            a, b = node(shape[0]), node(shape[-1])
            if a != b:
                index = len(edges)
                edges.append({
                    "a": a, "b": b, "shape": shape,
                    "length_m": sum(_metres(p, q) for p, q in zip(shape, shape[1:])),
                    "name": props.get("name"),
                    "highway": props.get("highway"),
                    # The DEM under a bridge is the river: its "water" is not on the deck.
                    "bridge": bool(props.get("bridge")),
                })
                adjacency[a].append((b, index))
                adjacency[b].append((a, index))
            start = i

    from scipy.spatial import cKDTree

    xy = np.asarray(points)
    _graph = {
        "points": points,
        "edges": edges,
        "adjacency": adjacency,
        "length": np.array([e["length_m"] for e in edges]),
        "tree": cKDTree(np.c_[xy[:, 0] * M_LON, xy[:, 1] * M_LAT]),
    }
    return _graph


def nearest_node(lat: float, lon: float) -> tuple[int, float]:
    """The junction closest to a place, and how far off it is, metres."""
    graph = load()
    distance, index = graph["tree"].query([lon * M_LON, lat * M_LAT])
    return int(index), float(distance)


def edge_depths_cm(model: dict[str, Any], depth_grid_m: np.ndarray) -> np.ndarray:
    """The deepest forecast water along every stretch of road, cm.

    Sample points are laid along each stretch once and mapped to the rain model's
    grid cells; after that a new forecast is one gather and one max per stretch.
    """
    graph = load()
    if graph.get("cells_for") != id(model):
        from app.services.street_flood import densify

        rows_n, cols_n = model["surface"].shape
        cells, owners = [], []
        for index, edge in enumerate(graph["edges"]):
            if edge["bridge"]:
                continue
            xy = np.asarray(densify(edge["shape"], SAMPLE_M))
            rows = np.floor((model["north"] - xy[:, 1]) / model["lat_step"]).astype(np.int64)
            cols = np.floor((xy[:, 0] - model["west"]) / model["lon_step"]).astype(np.int64)
            inside = (rows >= 0) & (rows < rows_n) & (cols >= 0) & (cols < cols_n)
            flat = rows[inside] * cols_n + cols[inside]
            cells.append(flat)
            owners.append(np.full(flat.size, index))
        graph["cells"] = np.concatenate(cells)
        graph["owners"] = np.concatenate(owners)
        graph["cells_for"] = id(model)

    depth = np.zeros(len(graph["edges"]))
    np.maximum.at(depth, graph["owners"], depth_grid_m.ravel()[graph["cells"]] * 100.0)
    return depth


def warm(model: dict[str, Any]) -> None:
    """Build the graph and map its roads onto the rain model's grid, ahead of use."""
    edge_depths_cm(model, np.zeros_like(model["surface"]))


def weights(depth_cm: np.ndarray | None) -> np.ndarray:
    """What each stretch costs to drive: length, dearer when wet, inf when impassable."""
    graph = load()
    length = graph["length"]
    if depth_cm is None:
        return length.copy()
    wet = np.where(depth_cm >= WET_CM, depth_cm, 0.0)
    cost = length * (1.0 + wet / CM_PER_EXTRA_LENGTH)
    cost[depth_cm >= IMPASSABLE_CM] = np.inf
    return cost


def _search(source: int, target: int, cost: np.ndarray, guided: bool) -> dict[str, Any]:
    """Dijkstra (guided=False) or A* (guided=True) from one junction to another."""
    graph = load()
    points, adjacency = graph["points"], graph["adjacency"]
    goal = points[target]
    guess = (lambda n: _metres(points[n], goal)) if guided else (lambda n: 0.0)

    best = {source: 0.0}
    came: dict[int, tuple[int, int]] = {}
    queue = [(guess(source), 0.0, source)]
    settled: set[int] = set()
    while queue:
        _, so_far, here = heapq.heappop(queue)
        if here in settled:
            continue
        settled.add(here)
        if here == target:
            break
        for there, edge in adjacency[here]:
            step = cost[edge]
            if not math.isfinite(step):
                continue
            total = so_far + step
            if total < best.get(there, math.inf):
                best[there] = total
                came[there] = (here, edge)
                heapq.heappush(queue, (total + guess(there), total, there))

    if target not in settled:
        return {"found": False, "settled": len(settled)}
    path_edges, node = [], target
    while node != source:
        node, edge = came[node]
        path_edges.append(edge)
    path_edges.reverse()
    return {"found": True, "cost": best[target], "edges": path_edges, "settled": len(settled)}


def dijkstra(source: int, target: int, cost: np.ndarray) -> dict[str, Any]:
    return _search(source, target, cost, guided=False)


def astar(source: int, target: int, cost: np.ndarray) -> dict[str, Any]:
    return _search(source, target, cost, guided=True)


def describe(path: dict[str, Any], source: int, depth_cm: np.ndarray | None) -> dict[str, Any]:
    """A found path as a line to draw and the figures a driver wants."""
    graph = load()
    edges, points = graph["edges"], graph["points"]
    line: list[list[float]] = [points[source]]
    here = source
    roads: list[str] = []
    wet_m = 0.0
    deepest = 0.0
    for index in path["edges"]:
        edge = edges[index]
        shape = edge["shape"] if edge["a"] == here else edge["shape"][::-1]
        line.extend(shape[1:])
        here = edge["b"] if edge["a"] == here else edge["a"]
        if edge["name"] and (not roads or roads[-1] != edge["name"]):
            roads.append(edge["name"])
        if depth_cm is not None:
            deepest = max(deepest, float(depth_cm[index]))
            if depth_cm[index] >= WET_CM:
                wet_m += edge["length_m"]
    length = float(sum(edges[i]["length_m"] for i in path["edges"]))
    return {
        "geometry": {"type": "LineString", "coordinates": [[round(x, 6), round(y, 6)] for x, y in line]},
        "length_km": round(length / 1000, 2),
        "roads": roads,
        "deepest_cm": round(deepest, 1),
        "wet_m": round(wet_m, 0),
    }


def flooded_on(path: dict[str, Any], depth_cm: np.ndarray) -> list[dict[str, Any]]:
    """The stretches of a path that are under water, as lines with their depth."""
    edges = load()["edges"]
    out = []
    for index in path["edges"]:
        if depth_cm[index] >= WET_CM:
            edge = edges[index]
            out.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[round(x, 6), round(y, 6)] for x, y in edge["shape"]]},
                "properties": {"name": edge["name"], "depth_cm": round(float(depth_cm[index]), 1),
                               "impassable": bool(depth_cm[index] >= IMPASSABLE_CM)},
            })
    return out


if __name__ == "__main__":
    import time

    began = time.perf_counter()
    graph = load()
    print(f"{len(graph['points'])} junctions, {len(graph['edges'])} stretches, "
          f"built in {time.perf_counter() - began:.1f} s")

    # Chennai Central to Guindy, dry: both algorithms must agree on the cost.
    a, _ = nearest_node(13.0827, 80.2707)
    b, _ = nearest_node(13.0067, 80.2206)
    dry = weights(None)
    d, s = dijkstra(a, b, dry), astar(a, b, dry)
    assert d["found"] and s["found"]
    assert abs(d["cost"] - s["cost"]) < 1e-6, (d["cost"], s["cost"])
    # A* settles fewer junctions for the same answer: that is what the guide buys.
    assert s["settled"] < d["settled"], (s["settled"], d["settled"])
    route = describe(s, a, None)
    straight = _metres(graph["points"][a], graph["points"][b]) / 1000
    assert straight <= route["length_km"] < straight * 2, (straight, route["length_km"])
    print(f"Central -> Guindy: {route['length_km']} km by road ({straight:.1f} km straight); "
          f"Dijkstra settled {d['settled']}, A* {s['settled']}")

    # Flooding a road on the route pushes the route off it, or makes it dearer.
    wet_depth = np.zeros(len(graph["edges"]))
    wet_depth[s["edges"][len(s["edges"]) // 2]] = 50.0
    rerouted = astar(a, b, weights(wet_depth))
    assert rerouted["found"] and s["edges"][len(s["edges"]) // 2] not in rerouted["edges"]
    assert rerouted["cost"] >= s["cost"]
    # Shallow water costs more than dry but stays usable.
    assert weights(np.full(len(graph["edges"]), 20.0))[0] == graph["length"][0] * 3.0
    print("routing self-check passed")
