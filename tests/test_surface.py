"""Terrain storage zones, pit filling, depth read-back and the street timeline."""

import numpy as np

from app.services.surface import _fill_pits, depth_grid, prepare, timeline


def test_zones_pits_and_depths() -> None:
    # Two dips either side of a low ridge, inside a rim, the sea around the edge: two
    # zones, not one, so each fills on its own until water tops the ridge.
    ground = np.full((9, 15), 11.0)
    ground[2:7, 2:7] = 10.0
    ground[2:7, 8:13] = 9.0
    ground[2:7, 7] = 10.5
    ground[[0, -1], :] = ground[:, [0, -1]] = -1.0
    m = prepare(ground, ground < 0, 100.0)
    west, east = int(m["basins"][4, 4]), int(m["basins"][4, 10])
    assert west != east and m["sea"] not in (west, east), m["basins"]
    assert m["basins"][0, 0] == m["sea"]
    # A 1 cm dip is noise and seeds nothing.
    flat = np.full((5, 5), 5.0)
    flat[2, 2] = 4.99
    flat[[0, -1], :] = flat[:, [0, -1]] = 0.0
    assert prepare(flat, flat == 0.0, 100.0)["sea"] == 1

    # Pit filling: a one-cell pit 2 m deep is noise and goes; a 25-cell hollow stays; the
    # same pit under a mapped water body stays.
    field = np.full((12, 12), 10.0)
    field[2, 2] = 8.0
    field[5:10, 5:10] = 9.0
    edge12 = np.zeros_like(field, dtype=bool)
    edge12[[0, -1], :] = edge12[:, [0, -1]] = True
    filled, n = _fill_pits(field.copy(), edge12, None)
    assert filled[2, 2] == 10.0 and filled[7, 7] == 9.0 and n == 1, (filled[2, 2], filled[7, 7], n)
    pond = np.zeros_like(field)
    pond[2, 2] = 1.0
    assert _fill_pits(field.copy(), edge12, pond)[0][2, 2] == 8.0
    # The same 25-cell hollow 2 m deep is too deep for its size: filled.
    deep = field.copy()
    deep[5:10, 5:10] = 8.0
    assert _fill_pits(deep, edge12, None)[0][7, 7] == 10.0

    m.update({"land": ground})
    level = np.full(m["sea"] + 1, -np.inf)
    level[west] = 10.1
    depth = depth_grid(m, level)
    assert abs(depth[4, 4] - 0.1) < 1e-9 and depth[4, 10] == 0.0 and depth[0, 0] == 0.0

    t = timeline(np.r_[0.0, 0.06, 0.4, 0.2, 0.01, 0.0], 5.0, 0.05, 0.30)
    assert t == {"floods_at_min": 10, "impassable_at_min": 15, "peak_at_min": 15, "clears_at_min": 25}, t
    assert timeline(np.zeros(4), 5.0, 0.05, 0.3)["floods_at_min"] is None
