"""Hydraulics: Manning capacity, normal depth, the Rational method and the drain graph."""

from app.services.hydraulics import (
    KACHA_N, MAX_SLOPE, MIN_SLOPE, OBSTACLE_FACTOR, accumulate, condition_factor, discharge,
    load_band, longitudinal_slope, manning_n, network, normal_depth, rational_inflow, snap_nodes,
    steady_route,
)


def test_all() -> None:
    # A 1 m x 1 m concrete channel at 1 in 400: A=1, P=3, R=1/3.
    q = discharge(1.0, 1.0, 0.0025, 0.015, closed=False, full_depth=1.0)
    assert abs(q - 1.602) < 0.01, q
    # The same discharge must put the water back at the top of the section.
    assert abs(normal_depth(q, 1.0, 1.0, 0.0025, 0.015, False) - 1.0) < 0.01
    # Half the discharge runs shallower than half full (the section is more efficient full).
    assert 0.3 < normal_depth(q / 2, 1.0, 1.0, 0.0025, 0.015, False) < 0.65
    # More than the section can take has no normal depth at all.
    assert normal_depth(q * 2, 1.0, 1.0, 0.0025, 0.015, False) is None

    # One hectare at 50 mm/h with C=0.75: 0.75 * 50 * 10000 / 3.6e6 m³/s.
    assert abs(rational_inflow(10_000, 50) - 0.10417) < 0.0001

    # A closed box wets its soffit too, so it is rougher than the open channel.
    assert discharge(1.0, 1.0, 0.0025, 0.015, True, 1.0) < q

    assert longitudinal_slope(13.0, 12.0, 400)[0] == 0.0025
    assert longitudinal_slope(13.0, 13.0, 400)[0] == MIN_SLOPE     # no measurable fall
    assert longitudinal_slope(13.0, 3.0, 20)[0] == MAX_SLOPE       # 1 in 2 is not Chennai
    assert longitudinal_slope("", 12.0, 400)[0] == MIN_SLOPE
    assert manning_n("Concrete Wall")[0] == 0.015
    assert manning_n("Concrete", "Kacha")[0] == KACHA_N
    assert condition_factor("Bad", "Water Tank")[0] == 0.6 * OBSTACLE_FACTOR
    assert condition_factor("Good", "NA")[0] == 1.0

    # A chain of three drains, each collecting 100 m², ends up carrying all 300.
    chain = [
        {"nodes": [1, 2], "local": 100.0, "head": 13.0, "tail": 12.0},
        {"nodes": [2, 3], "local": 100.0, "head": 12.0, "tail": 11.0},
        {"nodes": [3, 4], "local": 100.0, "head": 11.0, "tail": 10.0},
    ]
    assert accumulate(chain) == [100.0, 200.0, 300.0]
    # Two branches meeting the same trunk: the trunk carries both.
    fork = chain + [{"nodes": [5, 2], "local": 50.0, "head": 13.5, "tail": 12.0}]
    assert accumulate(fork) == [100.0, 250.0, 350.0, 50.0]
    # A side drain joining part-way along a trunk still hands its water over.
    tee = [
        {"nodes": [1, 2, 3], "local": 100.0, "head": 13.0, "tail": 11.0},
        {"nodes": [9, 2], "local": 40.0, "head": 13.2, "tail": 12.5},
    ]
    assert accumulate(tee) == [140.0, 40.0]
    # Vertices metres apart snap to one junction; a grid would have split them.
    ids, points = snap_nodes([[[80.2, 13.05], [80.2001, 13.0501]],
                              [[80.20005, 13.05005], [80.3, 13.1]]])
    assert ids[0][0] == ids[1][0], ids
    assert ids[0][1] != ids[1][1]
    # Every node id indexes a real position, which is what the flood model pours at.
    assert len(points) == len({i for line in ids for i in line})
    # Two sheet drains 8 m apart (either side of a street) stay apart; a CSV drain
    # ending between them joins one.
    side = 8 / 111_000
    lhs, rhs = [[80.2, 13.05], [80.201, 13.05]], [[80.2, 13.05 + side], [80.201, 13.05 + side]]
    ids, _ = snap_nodes([lhs, rhs, [[80.1995, 13.05 + side / 2], [80.2, 13.05 + side / 2]]], exact=[1, 1, None])
    assert ids[0][0] != ids[1][0] and ids[2][1] in (ids[0][0], ids[1][0]), ids
    # The same pair drawn on two different sheets joins: one boundary drain.
    ids, _ = snap_nodes([lhs, rhs], exact=[1, 2])
    assert ids[0][0] == ids[1][0], ids
    assert points[ids[0][0]][0] == 80.2

    pipe = [
        {"nodes": [1, 2], "local": 10_000.0, "head": 14.0, "tail": 13.0},
        {"nodes": [2, 3], "local": 0.0, "head": 13.0, "tail": 12.0},
        {"nodes": [3, 4], "local": 0.0, "head": 12.0, "tail": 11.0},
    ]
    graph = network(pipe)
    assert graph["downstream"] == [1, 2, None] and graph["order"] == [0, 1, 2]
    assert accumulate(pipe, graph) == [10_000.0, 10_000.0, 10_000.0]
    # A drain whose last two points snapped to its outlet does not carry its own water
    # away: the drain below still takes it.
    stutter = [{**pipe[0], "nodes": [1, 2, 2]}, dict(pipe[1]), dict(pipe[2])]
    assert network(stutter)["downstream"] == [1, 2, None]

    # Thiruvalluvar Street-2, both sides: LHS ends where RHS begins, RHS ends part-way
    # along LHS. Only the downhill hand-over survives (RHS 15.703 -> LHS 15.655 would
    # be fine, LHS 15.655 -> RHS 15.703 is uphill), so there is no loop.
    sides = [
        {"nodes": [1, 2, 3, 4], "local": 60.0, "head": 15.769, "tail": 15.655},    # LHS
        {"nodes": [4, 5, 2], "local": 60.0, "head": 15.717, "tail": 15.703},       # RHS
    ]
    graph = network(sides)
    assert graph["downstream"] == [None, 0] and sorted(graph["order"]) == [0, 1], graph
    # A loop with no surveyed inverts is cut, not left to circulate.
    blind = [{**edge, "head": None, "tail": None} for edge in sides]
    assert len(network(blind)["order"]) == 2

    assert load_band(0.2) == "clear" and load_band(0.9) == "at capacity"
    assert load_band(1.4) == "overflowing"


def test_steady_route_counts_spill_once() -> None:
    # 1.0 m³/s into a chain whose middle drain carries 0.4: it spills 0.6, and the
    # drain below receives 0.4 and spills nothing more.
    chain = [
        {"nodes": [1, 2], "local": 0.0, "head": 13.0, "tail": 12.0},
        {"nodes": [2, 3], "local": 0.0, "head": 12.0, "tail": 11.0},
        {"nodes": [3, 4], "local": 0.0, "head": 11.0, "tail": 10.0},
    ]
    arriving, spill = steady_route([1.0, 0.0, 0.0], [5.0, 0.4, 0.3], network(chain))
    assert arriving == [1.0, 1.0, 0.4]
    assert abs(spill[1] - 0.6) < 1e-12 and abs(spill[2] - 0.1) < 1e-12
    # Water is conserved: what leaves by the last drain plus all spill is what came in.
    assert abs(min(arriving[2], 0.3) + sum(spill) - 1.0) < 1e-12
