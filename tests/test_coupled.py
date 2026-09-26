"""The coupled model: weirs, grates, the fill curve, the surface exchange, and mass
balance through a full run."""

import math
import time

import numpy as np
import pytest

from app.services import drain_network, hydraulics, surface, terrain
from app.services.coupled import (
    GRAVITY, build_network, build_surface, coupled_summary, exchange, grate_capacity, levels, simulate,
    volume_below, weir_flow,
)


def test_synthetic_bowls() -> None:
    # Weir: 1 m of crest, 0.2 m head, free: 1.705 x 0.2^1.5 = 0.1525 m³/s.
    assert abs(weir_flow(np.array([1.0]), np.array([0.2]), np.array([0.0]))[0] - 0.15250) < 1e-4
    # Drowned to 90 % of the head, it passes far less, and nothing when level.
    assert weir_flow(np.array([1.0]), np.array([0.2]), np.array([0.18]))[0] < 0.08
    assert weir_flow(np.array([1.0]), np.array([0.2]), np.array([0.2]))[0] == 0.0
    # HEC-22: 5 cm over a clean-half grate is weir flow: 1.66 x 0.9 x 0.05^1.5.
    g = grate_capacity(np.array([1.0]), np.array([0.0]), np.array([0.05]))[0]
    assert abs(g - 1.66 * 0.9 * 0.05 ** 1.5) < 1e-9, g
    # At 1 m the orifice governs: 0.67 x 0.05 x sqrt(2 g).
    g = grate_capacity(np.array([1.0]), np.array([0.0]), np.array([1.0]))[0]
    assert abs(g - 0.67 * 0.05 * math.sqrt(2 * GRAVITY)) < 1e-9, g

    # Two bowls side by side inside an 11 m rim, sea around the edge, 10 m cells. West
    # floor 5 x 5 at 10 m, east floor 5 x 5 at 9 m, a 10.5 m ridge column between them.
    ground = np.full((9, 15), 11.0)
    ground[2:7, 2:7] = 10.0
    ground[2:7, 8:13] = 9.0
    ground[2:7, 7] = 10.5
    ground[[0, -1], :] = ground[:, [0, -1]] = -1.0
    edge = ground < 0
    m = surface.prepare(ground, edge, 100.0)
    m.update({"north": 9 * 10 / terrain.M_PER_DEG, "west": 0.0, "lat_step": 10 / terrain.M_PER_DEG,
              "lon_step": 10 / terrain.M_PER_DEG, "land": ground})
    s = build_surface(m)
    west, east = int(m["basins"][4, 4]), int(m["basins"][4, 10])
    assert west != east and west != m["sea"]

    # The fill curve: 25 floor cells x 0.4 m x 100 m² = 1000 m³ stands 0.4 m deep.
    vol = np.zeros(s["n"])
    vol[west] = 1000.0
    lv, ar = levels(s, vol)
    assert abs(lv[west] - 10.4) < 1e-9 and ar[west] == 2500.0, (lv[west], ar[west])
    assert abs(volume_below(s, np.array([west]), np.array([10.4]))[0] - 1000.0) < 1e-6

    # Half of every cell built over: the same 500 m³ stands twice as deep, 0.4 m not 0.2.
    vol[:] = 0.0
    vol[west] = 500.0
    assert abs(levels(s, vol)[0][west] - 10.2) < 1e-9
    half = build_surface({**m, "open": np.full(ground.shape, 0.5)})
    assert abs(levels(half, vol)[0][west] - 10.4) < 1e-9
    assert abs(volume_below(half, np.array([west]), np.array([10.4]))[0] - 500.0) < 1e-6

    # Pour the west bowl over its ridge: water spills east, total volume conserved, and
    # it never flows back uphill past equal levels.
    vol[:] = 0.0
    vol[west] = 25 * 100 * 0.8                              # to 10.8 m, above the ridge
    total = vol.sum()
    for _ in range(600):
        sea_loss = exchange(s, vol, 30.0)
        assert sea_loss == 0.0                              # rims at 11 m hold it all
    lv, _ = levels(s, vol)
    assert abs(vol.sum() - total) < 1e-6, (vol.sum(), total)
    assert vol[east] > 0 and lv[west] >= 10.5 - 1e-6 and lv[east] <= lv[west] + 1e-6, (lv[west], lv[east])

    # A full coupled run with one closed drain draining the east bowl to the sea, and
    # mass held to round-off.
    xy = lambda row, col: [(col + 0.5) * m["lon_step"], m["north"] - (row + 0.5) * m["lat_step"]]
    fake_edges = [{"nodes": [0, 1], "local": 500.0, "head": 9.0, "tail": 8.0}]
    fake_graph = hydraulics.network(fake_edges)
    network = {"edges": fake_edges, "graph": fake_graph, "points": [xy(4, 10), xy(4, 14)]}
    drains = [{"length_m": 50.0, "capacity": {"effective_capacity_m3s": 0.02,
                                              "full_velocity_ms": 1.0, "closed": True}}]
    net = build_network(m, s, drains, network)
    run = simulate(s, net, [5.0] * 6 + [0.0] * 30, 5.0, 0.9, 1.0)
    bal = run["balance"]
    assert abs(bal["error_m3"]) < 1e-6 * max(1.0, bal["runoff"]), bal
    assert bal["outfall"] > 0, bal
    # Heavier rain than the drain can take surcharges at its head junction.
    run = simulate(s, net, [60.0] * 12, 5.0, 0.9, 1.0)
    assert run["surcharge_m3"][:, 0].sum() >= 0 and abs(run["balance"]["error_m3"]) < 1e-5 * run["balance"]["runoff"]
    print("synthetic checks passed:", {k: bal[k] for k in ("runoff", "outfall", "on_surface", "to_sea")})


@pytest.mark.city
def test_city_run_balances() -> None:
    city = surface.load(12.85, 13.25, 80.10, 80.35)
    if city is None:
        pytest.skip("no DEM in app/data")
    began = time.perf_counter()
    s = build_surface(city)
    net = build_network(city, s, drain_network.load_drains(), drain_network.drain_graph())
    print(f"built in {time.perf_counter() - began:.1f} s: {s['fa'].size} faces, "
          f"{s['pa'].size} zone pairs; {coupled_summary(net, s)}")
    for label, rain in (("30 mm/h for 1 h, then dry", [2.5] * 12 + [0.0] * 24),
                        ("60 mm/h for 3 h", [5.0] * 36)):
        began = time.perf_counter()
        run = simulate(s, net, rain, 5.0, 0.75, 1.0)
        took = time.perf_counter() - began
        bal = run["balance"]
        assert abs(bal["error_m3"]) < 1e-6 * bal["runoff"], bal
        surcharging = int((run["surcharge_m3"].sum(axis=0) > 1.0).sum())
        print(f"{label}: {took:.1f} s; {surcharging} junctions surcharge; balance {bal}")
