"""Routing on the real street graph: Dijkstra and A* agree, and water reroutes."""

import time

import numpy as np
import pytest

from app.services.routing import _metres, astar, describe, dijkstra, load, nearest_node, weights


@pytest.mark.city
def test_central_to_guindy() -> None:
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
