"""Hydraulics of the GCC storm water drain network.

Two different questions are answered here, and the website keeps them apart because
they mean different things:

* **Hydraulic capacity** - what the built drain *can* carry. Steady uniform flow at
  full section, from Manning's equation with the surveyed width, depth, longitudinal
  slope (from the two invert levels) and a roughness taken from the wall material.
  This is a property of the structure and does not change with the weather.

* **Hydrodynamic state** - what the drain is *doing right now*. For the rainfall
  falling on its catchment, the inflow by the Rational method, the depth that flow
  settles at (normal depth, solved from the same Manning equation), the velocity and
  Froude number there, and how much of the capacity above is used.

Every drain also carries the runoff of everything upstream of it, so the catchment
that matters is accumulated along the network, not the strip beside one segment.
"""

import math
from collections import defaultdict
from typing import Any

GRAVITY = 9.81

# Manning's n for the wall materials the GCC survey records. These are field values
# for drains in service, not laboratory ones: an urban drain carries silt, grit and
# litter, and its walls are rarely clean.
MANNING_N = (
    ("brick", 0.017),
    ("stone", 0.020),
    ("masonry", 0.020),
    ("soil", 0.030),
    ("earth", 0.030),
    ("concrete", 0.015),
    ("conctrete", 0.015),   # the survey's own spelling, in 77 rows
    ("rcc", 0.015),
)
DEFAULT_N = 0.017
KACHA_N = 0.030             # unlined (kacha) drain, whatever the wall says

# Surveyed slopes run from about 1:5000 to 1:50. Below the floor the invert levels
# are too close to separate from survey error; above the ceiling, on ground as flat
# as Chennai's, the pair is almost certainly a data-entry error.
MIN_SLOPE = 0.0005
MAX_SLOPE = 0.05

# Share of the built capacity still available, by surveyed condition. A drain the
# survey marks "Bad" is silted, broken or both.
CONDITION_FACTOR = {"good": 1.0, "bad": 0.6}
DEFAULT_CONDITION_FACTOR = 0.85
# A junction box, water tank or pole standing in the drain blocks part of the section.
OBSTACLE_FACTOR = 0.8
NO_OBSTACLE = {"", "na", "nil", "no", "none"}

# Each drain takes the runoff of a strip alongside it: the carriageway plus the plots
# that drain onto it, both sides.
# ponytail: one constant strip; replace with GCC's sub-catchment polygons if they
# ever ship, which is the only way to get this right street by street.
STRIP_WIDTH_M = 60.0
# Dense, largely paved urban catchment (IS 1742 / CPHEEO range 0.70-0.95 for built-up
# areas); Chennai's surveyed wards are close to fully impervious.
RUNOFF_COEFF = 0.75

# Two surveyed points this close belong to the same junction. The survey draws a
# side drain ending "at" a trunk from a separate traverse, so the two points are
# metres apart, never identical; 15 m is the width of the road they meet in.
SNAP_M = 15.0
SNAP_DEG = SNAP_M / 111_000       # degrees of latitude; longitude at 13°N is within 3%
# Two ward-sheet drains share a junction only where their points coincide.
EXACT_SNAP_DEG = 0.5 / 111_000

# How full a drain is before it is called stressed, then overloaded.
LOAD_BANDS = ((0.5, "clear"), (0.85, "filling"), (1.0, "at capacity"))


def manning_n(material: str, pucca_kacha: str = "") -> tuple[float, str]:
    """Roughness for one drain, with the material it was read from."""
    if (pucca_kacha or "").strip().lower() == "kacha":
        return KACHA_N, "unlined (kacha)"
    text = (material or "").strip().lower()
    for keyword, value in MANNING_N:
        if keyword in text:
            return value, material.strip()
    return DEFAULT_N, "material not recorded"


def condition_factor(status: str, obstacles: str = "") -> tuple[float, list[str]]:
    """How much of the built section is still usable, and why it was reduced."""
    key = (status or "").strip().lower()
    factor = CONDITION_FACTOR.get(key, DEFAULT_CONDITION_FACTOR)
    notes = []
    if key in CONDITION_FACTOR:
        notes.append(f"condition {status.strip().lower()}")
    else:
        notes.append("condition not surveyed")
    blocked = (obstacles or "").strip().lower() not in NO_OBSTACLE
    if blocked:
        factor *= OBSTACLE_FACTOR
        notes.append(f"obstruction: {obstacles.strip()}")
    return factor, notes


def longitudinal_slope(invert_start: Any, invert_end: Any, length_m: Any) -> tuple[float, str]:
    """Bed slope from the two surveyed invert levels, with how it was arrived at.

    Water runs downhill either way, so the magnitude is what carries flow; a reverse
    gradient is reported by `flow_direction` in the drains route, not hidden here.
    """
    try:
        fall = abs(float(invert_start) - float(invert_end))
        length = float(length_m)
    except (TypeError, ValueError):
        return MIN_SLOPE, "assumed: invert levels unusable"
    if length <= 0:
        return MIN_SLOPE, "assumed: length unusable"
    slope = fall / length
    if slope < MIN_SLOPE:
        return MIN_SLOPE, "assumed: surveyed fall below survey accuracy"
    if slope > MAX_SLOPE:
        return MAX_SLOPE, f"capped: surveyed fall gives 1 in {1 / slope:.0f}"
    return slope, f"surveyed: 1 in {1 / slope:.0f}"


def wetted(width: float, flow_depth: float, closed: bool, full_depth: float) -> tuple[float, float]:
    """Flow area and wetted perimeter of a rectangular drain at a given depth.

    A closed drain running full is in contact with its soffit as well, so it wets the
    whole box; below that, and in an open drain, the water surface is free.
    """
    area = width * flow_depth
    if closed and flow_depth >= full_depth:
        return area, 2 * (width + full_depth)
    return area, width + 2 * flow_depth


def discharge(width: float, flow_depth: float, slope: float, n: float,
              closed: bool, full_depth: float) -> float:
    """Manning discharge, m³/s, at one depth."""
    area, perimeter = wetted(width, flow_depth, closed, full_depth)
    if area <= 0 or perimeter <= 0:
        return 0.0
    return area * (area / perimeter) ** (2 / 3) * math.sqrt(slope) / n


def normal_depth(flow_m3s: float, width: float, full_depth: float, slope: float,
                 n: float, closed: bool) -> float | None:
    """Depth the flow settles at, or None when it does not fit in the drain.

    Manning cannot be solved for depth in closed form, so it is bisected; discharge
    rises monotonically with depth, which is all bisection needs.
    """
    if flow_m3s <= 0 or width <= 0 or full_depth <= 0:
        return 0.0
    if flow_m3s > discharge(width, full_depth, slope, n, closed, full_depth):
        return None
    low, high = 0.0, full_depth
    for _ in range(40):
        mid = (low + high) / 2
        if discharge(width, mid, slope, n, closed, full_depth) < flow_m3s:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def rational_inflow(catchment_m2: float, rain_mm_h: float, coeff: float = RUNOFF_COEFF) -> float:
    """Peak runoff from a catchment, m³/s (Rational method, Q = C i A)."""
    return coeff * rain_mm_h * catchment_m2 / 3.6e6


def snap_nodes(polylines: list[list[list[float]]], tolerance_deg: float = SNAP_DEG,
               exact: list[Any] | None = None) -> tuple[list[list[int]], list[list[float]]]:
    """Give every vertex of every drain a shared junction id.

    A vertex joins the nearest existing node within the tolerance, so two drains that
    meet get the same id even when their surveyed points are a few metres apart.
    Rounding to a grid cannot do this: two points a metre apart still fall either
    side of a cell boundary, which is what left most of the network unconnected.

    Every vertex is indexed, not only the two ends, because a side drain usually
    joins a trunk part-way along it rather than at the trunk's own start or end.

    `exact` gives each line a group whose junctions are already exact - one ward
    sheet's network, which shares identical junction points - or None. Two lines of
    the same group join only within EXACT_SNAP_DEG, so the drains either side of one
    street stay apart; lines of different groups (neighbouring sheets drawing one
    boundary drain, or a sheet and the survey CSV) join within the full tolerance.

    Returns the node id of every vertex, drain by drain, and where each node is: a
    junction is a manhole, and that is where a surcharged drain puts water on the
    street.
    """
    exact = exact or [None] * len(polylines)
    node_group: list[Any] = []
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    nodes: list[list[float]] = []
    ids: list[list[int]] = []

    for line, line_exact in zip(polylines, exact):
        line_ids = []
        for lon, lat in line:
            cell = (int(lon // tolerance_deg), int(lat // tolerance_deg))
            best, best_distance = None, tolerance_deg ** 2
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for candidate in cells[(cell[0] + dx, cell[1] + dy)]:
                        node = nodes[candidate]
                        distance = (node[0] - lon) ** 2 + (node[1] - lat) ** 2
                        same = line_exact is not None and node_group[candidate] == line_exact
                        reach = EXACT_SNAP_DEG ** 2 if same else best_distance
                        if distance < min(best_distance, reach):
                            best, best_distance = candidate, distance
            if best is None:
                best = len(nodes)
                nodes.append([lon, lat])
                node_group.append(line_exact)
                cells[cell].append(best)
            line_ids.append(best)
        ids.append(line_ids)
    return ids, nodes


def _upstream_nodes(edge: dict[str, Any]) -> list[int]:
    """The junctions a drain can carry water away from: all of its own but its outlet."""
    outlet = edge["nodes"][-1]
    return list(dict.fromkeys(n for n in edge["nodes"] if n != outlet))


def network(edges: list[dict[str, Any]]) -> dict[str, Any]:
    """The drain network as a directed graph: who carries water on from each junction.

    `edges` carry `nodes` (junction ids along the drain, already ordered the way the
    water runs), `head` and `tail` (the invert levels at those two ends, or None).

    Nodes are manholes and junctions, edges are drains. Water delivered to a junction
    leaves it down exactly one drain, its carrier: the one with the lowest outlet,
    which is where water actually goes. Drains come back in topological order, so
    everything upstream of a drain is settled before it is.

    Water only runs downhill: drain i hands its water to drain j only when j's outlet
    is lower than i's, where both are surveyed. The drains either side of one street
    (LHS and RHS) run within the snapping distance of each other, so each passes
    through the other's junctions; without this rule each "carried" the other's water,
    one of the two uphill, and 128 pairs traded water in a loop for ever. A drain with
    no lower drain to hand to ends there: an outfall.

    What loops remain can only run through drains with no usable inverts. Each is cut
    at its drain with the lowest known head, so the graph is a true tree of flow.
    """
    # Which drain carries the water away from each junction. A drain cannot carry
    # away the flow arriving at its own outlet, so only its upstream vertices count.
    carrier: dict[int, int] = {}
    for i, edge in enumerate(edges):
        # Its own outlet excluded even where it repeats: snapping merges vertices
        # closer than SNAP_M, so a drain's last two points can share its outlet's id,
        # and without this 2,692 drains carried their own water away, dead-ending the
        # network there.
        for node in _upstream_nodes(edge):
            current = carrier.get(node)
            if current is None or _lower_outlet(edge, edges[current]):
                carrier[node] = i

    # Drain i feeds drain j when the water leaving i's outlet is carried on by j, and j
    # takes it lower.
    downstream: list[int | None] = []
    for i, edge in enumerate(edges):
        j = carrier.get(edge["nodes"][-1])
        downstream.append(j if j is not None and j != i and _downhill(edge, edges[j]) else None)

    while True:
        order = _topological(downstream)
        if len(order) == len(edges):
            break
        # A loop through unsurveyed inverts: walk it and cut it.
        done = set(order)
        i = next(k for k in range(len(edges)) if k not in done)
        seen: dict[int, int] = {}
        while i not in seen:
            seen[i] = len(seen)
            i = downstream[i]
        loop = [k for k, at in seen.items() if at >= seen[i]]
        cut = min(loop, key=lambda k: edges[k]["head"] if _known(edges[k]["head"]) else math.inf)
        downstream[cut] = None
    return {"carrier": carrier, "downstream": downstream, "order": order}


def _known(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _downhill(upper: dict[str, Any], lower: dict[str, Any]) -> bool:
    """Can water leaving `upper` go on down `lower`? Not uphill, where both are surveyed."""
    if _known(upper["tail"]) and _known(lower["tail"]):
        return lower["tail"] < upper["tail"]
    return True


def _topological(downstream: list[int | None]) -> list[int]:
    """Kahn's order: every drain after all the drains that feed it."""
    waiting = [0] * len(downstream)
    for j in downstream:
        if j is not None:
            waiting[j] += 1
    order = [i for i, count in enumerate(waiting) if count == 0]
    settled = 0
    while settled < len(order):
        j = downstream[order[settled]]
        settled += 1
        if j is not None:
            waiting[j] -= 1
            if waiting[j] == 0:
                order.append(j)
    return order


def accumulate(edges: list[dict[str, Any]], graph: dict[str, Any] | None = None) -> list[float]:
    """Catchment area reaching each drain, its own (`local`, m²) plus everything upstream."""
    graph = graph or network(edges)
    accumulated = [0.0] * len(edges)
    for i in graph["order"]:
        accumulated[i] += edges[i]["local"]
        j = graph["downstream"][i]
        if j is not None:
            accumulated[j] += accumulated[i]
    return accumulated


def _lower_outlet(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    """Does `candidate` take water further down than `current`?"""
    # A survey invert can arrive as "", "NA" or a stray string; an outlet that cannot
    # be read is treated as the highest possible, so a known one always wins.
    a = candidate["tail"] if isinstance(candidate["tail"], (int, float)) else math.inf
    b = current["tail"] if isinstance(current["tail"], (int, float)) else math.inf
    if a != b:
        return a < b
    # No usable inverts either way: the longer drain is the more likely trunk.
    return candidate["local"] > current["local"]


def load_band(load_ratio: float) -> str:
    """Plain words for how full a drain is."""
    for limit, label in LOAD_BANDS:
        if load_ratio < limit:
            return label
    return "overflowing"


def drain_capacity(props: dict[str, Any], length_m: float) -> dict[str, Any]:
    """Everything about one drain that does not depend on the weather."""
    width = props.get("DRAIN_WID")
    depth = props.get("DRAIN_DEP")
    width = float(width) if isinstance(width, (int, float)) else 0.0
    depth = float(depth) if isinstance(depth, (int, float)) else 0.0
    closed = "closed" in str(props.get("DRAIN_DETL", "")).lower() \
        or str(props.get("COVER", "")).strip().lower() in {"yes", "closed"}

    slope, slope_note = longitudinal_slope(
        props.get("INVERT_SP"), props.get("INVERT_EP"), length_m)
    n, material = manning_n(props.get("SWD_MAT") or props.get("TYP_MAT") or "",
                            props.get("PUCA_KACHA", ""))
    factor, condition_notes = condition_factor(props.get("STATUS", ""),
                                               props.get("OBSTACLES", ""))

    area, perimeter = wetted(width, depth, closed, depth)
    built = discharge(width, depth, slope, n, closed, depth)
    return {
        "width_m": round(width, 3),
        "depth_m": round(depth, 3),
        "closed": closed,
        "area_m2": round(area, 4),
        "wetted_perimeter_m": round(perimeter, 4),
        "hydraulic_radius_m": round(area / perimeter, 4) if perimeter else 0.0,
        "slope": round(slope, 6),
        "slope_note": slope_note,
        "manning_n": n,
        "material": material,
        "condition_factor": round(factor, 3),
        "condition_notes": condition_notes,
        # As built, and what the surveyed condition leaves of it.
        "capacity_m3s": round(built, 4),
        "effective_capacity_m3s": round(built * factor, 4),
        "full_velocity_ms": round(built / area, 3) if area else 0.0,
        "catchment_strip_m2": round(length_m * STRIP_WIDTH_M, 1),
    }


def drain_state(capacity: dict[str, Any], catchment_m2: float, rain_mm_h: float,
                length_m: float, coeff: float = RUNOFF_COEFF) -> dict[str, Any]:
    """What the drain is doing, for the rain falling on its catchment right now."""
    inflow = rational_inflow(catchment_m2, rain_mm_h, coeff)
    effective = capacity["effective_capacity_m3s"]
    ratio = inflow / effective if effective > 0 else (math.inf if inflow > 0 else 0.0)

    # The condition penalty is carried as extra roughness, so the depth, the velocity
    # and the load all describe the same drain: one that silts up runs deeper and
    # slower, and fills at the effective capacity rather than the as-built one.
    rough = capacity["manning_n"] / capacity["condition_factor"] if capacity["condition_factor"] else capacity["manning_n"]
    depth = normal_depth(inflow, capacity["width_m"], capacity["depth_m"],
                         capacity["slope"], rough, capacity["closed"])
    surcharged = depth is None
    if surcharged:
        velocity = capacity["full_velocity_ms"]
        freeboard = 0.0
        froude = velocity / math.sqrt(GRAVITY * capacity["depth_m"]) if capacity["depth_m"] else 0.0
    else:
        area = capacity["width_m"] * depth
        velocity = inflow / area if area > 0 else 0.0
        freeboard = capacity["depth_m"] - depth
        froude = velocity / math.sqrt(GRAVITY * depth) if depth > 0 else 0.0

    return {
        "rain_mm_h": round(rain_mm_h, 2),
        "catchment_m2": round(catchment_m2, 1),
        "runoff_coefficient": coeff,
        "inflow_m3s": round(inflow, 4),
        "inflow_ls": round(inflow * 1000, 1),
        "load_ratio": None if math.isinf(ratio) else round(ratio, 3),
        "load_pct": None if math.isinf(ratio) else round(ratio * 100, 1),
        "band": load_band(ratio),
        "flow_depth_m": None if surcharged else round(depth, 3),
        "freeboard_m": None if surcharged else round(freeboard, 3),
        "velocity_ms": round(velocity, 3),
        # Supercritical flow (Froude > 1) scours the invert and jumps at bends.
        "froude": round(froude, 2),
        "regime": "surcharged" if surcharged else ("supercritical" if froude > 1 else "subcritical"),
        "travel_time_s": round(length_m / velocity, 1) if velocity > 0 else None,
        "spare_m3s": round(effective - inflow, 4),
    }


if __name__ == "__main__":
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
    print("hydraulics self-check passed")
