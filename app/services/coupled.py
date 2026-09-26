"""Dual drainage: the street surface and the drain network run as one system.

Rain falls on the ground. Part of it runs off (the runoff coefficient), and where it
runs is decided by the terrain and the drains together, every minute of the storm:

  surface (2D)   the city is cut into storage zones, one per DEM depression and the
                 ground that drains into it (surface.prepare). A zone holds water
                 at one level, from its own fill curve. Neighbouring zones exchange
                 water over the cell faces they share, as flow over a broad-crested
                 weir with the Villemonte submergence reduction, so a full hollow spills
                 into the next one and on to the sea, and water finds its way along
                 the terrain rather than stopping where it fell. This is the Rapid
                 Flood Spreading Method (UK Environment Agency, RFSM) with an explicit
                 weir exchange in place of instant spreading.

  inlets         rain on the strip beside a drain reaches its gully gratings (closed
                 drains) or its open edge (open drains) and is caught up to the
                 inlet's capacity at gutter depth; the rest runs on over the surface.
                 Standing water over an inlet drains into it at the depth it stands,
                 by the HEC-22 grate equations (weir when shallow, orifice when deep).

  network (1D)   what the inlets catch is routed through the drain graph (nodes are
                 junctions, edges are drains, app/services/hydraulics.network), each
                 drain taking in at most its surveyed Manning capacity, and delaying
                 what it carries as a linear reservoir with the drain's own travel
                 time, length / full-bore velocity.

  backflow       what a drain cannot take comes back up at the junction it was trying
                 to enter: water already in the pipes surcharges out of the manhole
                 onto the zone around it, and is spread by the surface from there.

Every volume is accounted for; `simulate` returns the mass balance and the self-check
holds it to round-off.

ponytail: the drain water level is not solved (no pressure heads, no SWMM). A drain
carries min(what reaches it, its capacity) and the rest surcharges at the junction
above it; backwater from a full drain below into the one above is not modelled, and
nor is the tide or river stage at an outfall. A 1D dynamic-wave solver is the upgrade.
"""

import math
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from app.services import hydraulics, surface, terrain
from app.services.street_index import densify

GRAVITY = 9.81

# Broad-crested weir, free flow: the flow passes critical depth over the crest, so
# q = sqrt(g) (2H/3)^1.5 per metre of crest, 1.705 m^0.5/s.
WEIR_C = math.sqrt(GRAVITY) * (2.0 / 3.0) ** 1.5
# Villemonte (1947): a weir drowned from downstream passes Q (1 - (Hd/Hu)^n)^0.385.
SUBMERGENCE_N = 1.5
SUBMERGENCE_EXP = 0.385

# Gully grating in a closed drain's cover, FHWA HEC-22 (3rd ed.) sag-inlet equations
# in SI: weir Q = 1.66 P d^1.5 while shallow, orifice Q = 0.67 A sqrt(2 g d) once deep,
# whichever is smaller. HEC-22 designs for half the grate clogged with debris, which is
# the everyday state of a Chennai gully in the monsoon.
GRATE_WEIR_C = 1.66
GRATE_ORIFICE_C = 0.67
GRATE_PERIMETER_M = 1.8          # 450 x 450 mm grating, water on all four sides
GRATE_OPEN_M2 = 0.10             # about half of its 0.2 m² is bars
GRATE_CLOGGING = 0.5
# One grating this often along a closed drain. ponytail: the survey does not record
# gully positions; a calibration knob until it does.
INLET_SPACING_M = 30.0
# An open drain takes water over its edge along its whole length, both sides: a side
# weir with the same coefficient as the grate's weir mode.
SIDE_WEIR_C = 1.66
# Depth of the kerb-side flow that on-grade inlets see while the rain is falling:
# 5 cm is the gutter flow Indian road drainage is designed to (IRC:SP:50).
GUTTER_DEPTH_M = 0.05

# How fast runoff crosses a zone to its low point, m/s: sheet flow and gutters on flat
# paved ground. A zone of side L takes L / speed to deliver its rain. ponytail: one
# speed for the city; calibrate against the lag between rain and water on known streets.
FLOW_SPEED_M_S = 0.5

# Where the survey reaches no drain, the ground is given drains built for a design
# storm of this intensity: by the Rational method such a drain carries C x i x A, so
# it takes the runoff of up to this much rain off its zone. ponytail: a design figure
# standing in for drains nobody surveyed, not a measurement; the page says so, and a
# survey of those areas replaces it.
UNSURVEYED_DESIGN_MM_H = 50.0

# A drain that ends within this of a mapped canal, river or nullah empties into it.
# One that ends anywhere else - a blind end, where the survey's drawing stops and no
# channel on our layers takes over - puts its water on the ground there, and the
# surface carries it on downhill: water that has nowhere mapped to go cannot vanish.
OUTFALL_TO_CHANNEL_M = 100.0

# The sea stands at mean sea level. Water leaves the land into it, never the reverse:
# storm surge is not modelled.
SEA_LEVEL_M = 0.0

# The surface and the network exchange water every COUPLE_S; the surface steps
# SURFACE_SUBSTEPS times inside that.
COUPLE_S = 60.0
SURFACE_SUBSTEPS = 2
# Share of the level difference between two zones one surface step may close: the
# explicit weir exchange is stable because it never overshoots equal levels.
EQUALISE = 0.5
# However built up a cell is, this share of it is open ground water can stand on:
# yards, lanes, the street itself.
MIN_OPEN = 0.1

# A face this far above the lowest crest between two zones only carries water in a
# flood deeper than any this model should be trusted on, and is dropped.
PRUNE_M = 3.0


# ─── Surface ────────────────────────────────────────────────────────────────────

def build_surface(model: dict[str, Any]) -> dict[str, Any]:
    """Zone fill curves and the weir faces between zones, built once per DEM.

    Buildings hold no water: each cell stores water only on its open share,
    model["open"] (1 - building cover, at least MIN_OPEN), and a face between two
    cells passes water only through the gaps between buildings on both sides.
    """
    zones, sea, heights = model["basins"], model["sea"], model["surface"]
    a = float(model["cell_area_m2"])
    n = sea + 1
    open_share = np.clip(model.get("open", np.ones(zones.shape)), MIN_OPEN, 1.0)

    flat_z = zones.ravel()
    flat_s = heights.ravel().astype(np.float64)
    land = np.flatnonzero(flat_z != sea)
    cells = land[np.lexsort((flat_s[land], flat_z[land]))]
    zc = flat_z[cells]
    el = flat_s[cells]
    wet = a * open_share.ravel()[cells]            # m² of each cell water can stand on
    size = np.bincount(zc, minlength=n)
    start = np.zeros(n, dtype=np.int64)
    start[1:] = np.cumsum(size)[:-1]
    cum_area = np.cumsum(wet)
    cum_elev = np.cumsum(wet * el)
    has = size > 0
    base_area = np.zeros(n)
    base_elev = np.zeros(n)
    base_area[has] = (cum_area - wet)[start[has]]
    base_elev[has] = (cum_elev - wet * el)[start[has]]
    # Volume that brings a zone's water up to each of its cells, lowest first: the
    # open area of the cells below times the rise, less what their ground takes up.
    below_area = cum_area - wet - base_area[zc]
    below_elev = cum_elev - wet * el - base_elev[zc]
    curve = el * below_area - below_elev

    # Composite keys (zone, value), globally sorted, so one searchsorted answers a
    # question for every zone at once.
    vscale = 10.0 ** math.ceil(math.log10(curve.max() + 2.0))
    emin = float(el.min())
    escale = 10.0 ** math.ceil(math.log10(float(el.max()) - emin + 2.0))

    # Faces between cells of different zones. Neighbours east-west share a face as
    # long as a cell is tall; north-south, as wide as it is wide.
    dy = model["lat_step"] * terrain.M_PER_DEG
    mid_lat = model["north"] - heights.shape[0] * model["lat_step"] / 2
    dx = model["lon_step"] * terrain.M_PER_DEG * math.cos(math.radians(mid_lat))
    fa, fb, fc, fw = [], [], [], []
    for za, zb, sa, sb, oa, ob, width in (
        (zones[:, :-1], zones[:, 1:], heights[:, :-1], heights[:, 1:], open_share[:, :-1], open_share[:, 1:], dy),
        (zones[:-1, :], zones[1:, :], heights[:-1, :], heights[1:, :], open_share[:-1, :], open_share[1:, :], dx),
    ):
        cut = za != zb
        # Lower id first; the sea has the highest id, so it is always the b side.
        fa.append(np.minimum(za[cut], zb[cut]))
        fb.append(np.maximum(za[cut], zb[cut]))
        # Water crosses a face once it stands above both cells.
        fc.append(np.maximum(sa[cut], sb[cut]).astype(np.float64))
        # Only the gaps between buildings on both sides carry water across.
        fw.append(width * np.minimum(oa[cut], ob[cut]))
    fa, fb, fc, fw = (np.concatenate(x) for x in (fa, fb, fc, fw))

    pair_key, fp = np.unique(fa.astype(np.int64) * n + fb, return_inverse=True)
    cmin = np.full(pair_key.size, np.inf)
    np.minimum.at(cmin, fp, fc)
    keep = fc <= cmin[fp] + PRUNE_M
    fa, fb, fc, fw, fp = fa[keep], fb[keep], fc[keep], fw[keep], fp[keep]
    pa, pb = pair_key // n, pair_key % n

    surface_model = {
        "n": n, "sea": sea, "cell_area_m2": a,
        "size": size, "start": start, "cum_area": cum_area, "cum_elev": cum_elev,
        "base_area": base_area, "base_elev": base_elev,
        "vkey": zc * vscale + curve, "vscale": vscale,
        "ekey": zc * escale + (el - emin), "escale": escale, "emin": emin, "emax": float(el.max()),
        "zones": np.flatnonzero(has),
        # Rain falls on the whole zone, roofs included: they drain to the street.
        "area_m2": size * a,
        "open_m2": np.bincount(zc, weights=wet, minlength=n),
        "fa": fa, "fb": fb, "fc": fc, "fw": fw, "fp": fp,
        "pa": pa, "pb": pb, "cmin": cmin,
    }
    # Water in a zone below the lowest crest to a neighbour cannot leave towards it.
    surface_model["vc_a"] = volume_below(surface_model, pa, cmin)
    surface_model["vc_b"] = np.where(pb == sea, 0.0, volume_below(surface_model, np.minimum(pb, sea - 1), cmin))
    return surface_model


def levels(s: dict[str, Any], volume: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Water level in every zone and the plan area it covers there.

    An empty zone stands at its lowest cell. The sea is held at SEA_LEVEL_M with an
    unbounded area.
    """
    z = s["zones"]
    v = volume[z]
    idx = np.searchsorted(s["vkey"], z * s["vscale"] + v, side="right")
    # Past its highest cell a zone is covered wall to wall and keeps rising evenly.
    k = np.clip(idx - s["start"][z], 1, s["size"][z])
    idx = s["start"][z] + k
    covered = s["cum_area"][idx - 1] - s["base_area"][z]
    level = np.full(s["n"], -np.inf)
    level[z] = (v + s["cum_elev"][idx - 1] - s["base_elev"][z]) / covered
    area = np.zeros(s["n"])
    area[z] = covered
    level[s["sea"]] = SEA_LEVEL_M
    area[s["sea"]] = np.inf
    return level, area


def volume_below(s: dict[str, Any], zone: np.ndarray, level: np.ndarray) -> np.ndarray:
    """Volume a zone holds when its water stands at `level`, m³."""
    zone = np.asarray(zone, dtype=np.int64)
    level = np.clip(np.asarray(level, dtype=np.float64), s["emin"] - 1.0, s["emin"] + s["escale"] - 1.0)
    idx = np.searchsorted(s["ekey"], zone * s["escale"] + (level - s["emin"]), side="left")
    k = idx - s["start"][zone]
    last = np.maximum(idx - 1, 0)
    area = np.where(k > 0, s["cum_area"][last] - s["base_area"][zone], 0.0)
    elev = np.where(k > 0, s["cum_elev"][last] - s["base_elev"][zone], 0.0)
    return np.clip(level * area - elev, 0.0, None)


def weir_flow(width: np.ndarray, head_up: np.ndarray, head_down: np.ndarray) -> np.ndarray:
    """Broad-crested weir discharge, m³/s, drowned by the Villemonte reduction."""
    hu = np.maximum(head_up, 0.0)
    hd = np.clip(head_down, 0.0, None)
    ratio = np.divide(hd, hu, out=np.zeros_like(hu), where=hu > 0)
    drowned = np.clip(1.0 - ratio ** SUBMERGENCE_N, 0.0, 1.0) ** SUBMERGENCE_EXP
    return WEIR_C * width * hu ** 1.5 * drowned


def exchange(s: dict[str, Any], volume: np.ndarray, dt: float) -> float:
    """One explicit surface step between zones, in place; returns m³ lost to the sea."""
    level, area = levels(s, volume)
    fa, fb, fc = s["fa"], s["fb"], s["fc"]
    la, lb = level[fa], level[fb]
    high, low = np.maximum(la, lb), np.minimum(la, lb)
    live = high > fc
    if not live.any():
        return 0.0
    fp = s["fp"][live]
    q = weir_flow(s["fw"][live], high[live] - fc[live], low[live] - fc[live])
    # Nothing flows in from the sea.
    q[(fb[live] == s["sea"]) & (lb[live] > la[live])] = 0.0
    signed = np.where(la[live] >= lb[live], q, -q)
    flow = np.bincount(fp, weights=signed, minlength=s["pa"].size)

    pa, pb, cmin = s["pa"], s["pb"], s["cmin"]
    forward = flow >= 0
    src = np.where(forward, pa, pb)
    dst = np.where(forward, pb, pa)
    moved = np.abs(flow) * dt
    active = moved > 0
    # A zone can only give what stands above the lowest crest towards its neighbour...
    above_crest = volume[src] - np.where(forward, s["vc_a"], s["vc_b"])
    moved = np.minimum(moved, np.maximum(above_crest, 0.0))
    # ...and never so much that the two levels cross. Shared between every neighbour
    # a zone is draining into at once.
    a_src, a_dst = np.maximum(area[src], s["cell_area_m2"]), np.maximum(area[dst], s["cell_area_m2"])
    finite = np.where(np.isinf(a_dst), 1.0, a_dst)
    shared = np.where(np.isinf(a_dst), a_src, a_src * finite / (a_src + finite))
    fanout = np.bincount(src[active], minlength=s["n"])
    drowned = level[dst] > cmin
    cap = EQUALISE * (level[src] - level[dst]) * shared / np.maximum(fanout[src], 1)
    moved = np.where(drowned, np.minimum(moved, np.maximum(cap, 0.0)), moved)
    # Finally no zone gives more than it holds.
    out = np.bincount(src, weights=moved, minlength=s["n"])
    held = np.maximum(volume, 0.0)
    scale = np.divide(held, out, out=np.ones_like(volume), where=(out > held) & (out > 0))
    moved *= scale[src]

    volume -= np.bincount(src, weights=moved, minlength=s["n"])
    volume += np.bincount(dst, weights=moved, minlength=s["n"])
    np.clip(volume, 0.0, None, out=volume)
    lost = float(volume[s["sea"]])
    volume[s["sea"]] = 0.0
    return lost


# ─── Inlets and the network ─────────────────────────────────────────────────────

def grate_capacity(grates: np.ndarray, open_edge_m: np.ndarray, depth: np.ndarray) -> np.ndarray:
    """What the inlets at a junction take at a water depth, m³/s (HEC-22 sag inlet)."""
    d = np.clip(depth, 0.0, None)
    keep = 1.0 - GRATE_CLOGGING
    weir = GRATE_WEIR_C * GRATE_PERIMETER_M * keep * d ** 1.5
    orifice = GRATE_ORIFICE_C * GRATE_OPEN_M2 * keep * np.sqrt(2 * GRAVITY * d)
    return grates * np.minimum(weir, orifice) + SIDE_WEIR_C * 2.0 * open_edge_m * d ** 1.5


def build_network(model: dict[str, Any], s: dict[str, Any], drains: list[dict[str, Any]],
                  network: dict[str, Any]) -> dict[str, Any]:
    """Where each junction sits on the surface, and each drain's inlets and routing."""
    edges, graph, points = network["edges"], network["graph"], network["points"]
    carrier, order = graph["carrier"], graph["order"]
    sea = s["sea"]

    xy = np.asarray(points, dtype=np.float64)
    rows_n, cols_n = model["basins"].shape
    rows = np.floor((model["north"] - xy[:, 1]) / model["lat_step"]).astype(np.int64)
    cols = np.floor((xy[:, 0] - model["west"]) / model["lon_step"]).astype(np.int64)
    inside = (rows >= 0) & (rows < rows_n) & (cols >= 0) & (cols < cols_n)
    r, c = np.where(inside, rows, 0), np.where(inside, cols, 0)
    node_zone = np.where(inside, model["basins"][r, c], sea)
    ground = np.where(inside, model["land"][r, c], np.inf)
    ground = np.where(np.isfinite(ground), ground, np.inf)

    nodes_n = len(points)
    grates = np.zeros(nodes_n)
    open_edge = np.zeros(nodes_n)
    strip = np.zeros(nodes_n)
    capacity, travel, entries, outlet, outfall = [], [], [], [], []
    for i, (edge, drain) in enumerate(zip(edges, drains)):
        cap = drain["capacity"]
        q = cap["effective_capacity_m3s"]
        sized = q > 0
        capacity.append(q if sized else None)
        v = cap["full_velocity_ms"]
        travel.append(drain["length_m"] / v if sized and v > 0 else 0.0)
        entries.append([n for n in hydraulics.upstream_nodes(edge) if carrier.get(n) == i])
        outlet.append(edge["nodes"][-1])
        # Where the graph hands this drain's water on: nowhere lower means an outfall.
        outfall.append(graph["downstream"][i] is None)
        # Inlets sit all along the drain, and so does the strip that drains to them:
        # both are shared evenly between the junctions along it, each of which hands
        # what it catches to the drain that carries water on from there.
        along = [n for n in hydraulics.upstream_nodes(edge) if carrier.get(n) is not None]
        if not along:
            continue
        length = drain["length_m"]
        share = 1.0 / len(along)
        for n in along:
            strip[n] += length * hydraulics.STRIP_WIDTH_M * share
            if cap["closed"]:
                grates[n] += max(1.0, length / INLET_SPACING_M) * share
            else:
                open_edge[n] += length * share

    # Strips cannot claim more ground than their zone has; the rest of the zone runs
    # to its low point over the surface.
    area = s["area_m2"].astype(np.float64)
    claimed = np.bincount(node_zone, weights=strip, minlength=s["n"])
    fit = np.divide(area, claimed, out=np.ones_like(area), where=claimed > area)
    strip *= fit[node_zone]
    strip[node_zone == sea] = 0.0
    claimed = np.bincount(node_zone, weights=strip, minlength=s["n"])
    remainder = np.clip(area - claimed, 0.0, None)
    remainder[sea] = 0.0

    inlet = np.flatnonzero((grates > 0) | (open_edge > 0))
    drained = np.zeros(s["n"], dtype=bool)
    drained[node_zone[inlet]] = True
    drained[sea] = False

    # Generations: a drain's generation is one more than that of any drain feeding it,
    # so every drain in a generation can be routed at once, after all of its feeders.
    downstream = graph["downstream"]
    generation = [0] * len(edges)
    for i in order:
        j = downstream[i]
        if j is not None and generation[j] < generation[i] + 1:
            generation[j] = generation[i] + 1
    generations = []
    for g in range(max(generation, default=-1) + 1):
        members = np.array([i for i in order if generation[i] == g and entries[i]], dtype=np.int64)
        if not members.size:
            continue
        nodes = [n for i in members for n in entries[i]]
        local = [k for k, i in enumerate(members) for _ in entries[i]]
        generations.append({"edges": members, "nodes": np.array(nodes, dtype=np.int64),
                            "local": np.array(local, dtype=np.int64)})
    return {
        "node_zone": node_zone, "ground": ground,
        "grates": grates, "open_edge": open_edge, "strip": strip,
        "remainder": remainder, "inlet": inlet,
        # An unsized drain passes on whatever reaches it rather than inventing a flood.
        "capacity": np.array([np.inf if q is None else q for q in capacity]),
        "travel": np.asarray(travel), "outlet": np.asarray(outlet, dtype=np.int64),
        "outfall": np.asarray(outfall, dtype=bool), "generations": generations,
        "blind_end": (blind := _blind_ends(np.asarray(outlet, dtype=np.int64), np.asarray(outfall, dtype=bool),
                                           xy, node_zone, sea)),
        # Whether ponded water over each junction's inlets is taken anywhere by them.
        "drains_zone": _drains_its_zone(carrier, graph["downstream"], order, np.asarray(outlet, dtype=np.int64),
                                        blind, node_zone, len(points)),
        "undrained": ~drained,
        "points": points, "edges": len(edges),
    }


def _drains_its_zone(carrier: dict[int, int], downstream: list, order: list, outlet: np.ndarray,
                     blind: np.ndarray, node_zone: np.ndarray, nodes_n: int) -> np.ndarray:
    """For every junction, whether the drains below it take water out of its zone.

    A drain whose route ends at a blind end back inside the same zone only moves
    standing water round in a circle: it would capture the pond, carry it and put it
    back, over and over. Its inlets still take the rain falling on its strip, but not
    the standing water.
    """
    # The zone each drain's water finally lands in: -1 where it leaves by an outfall.
    final = np.full(len(downstream), -1, dtype=np.int64)
    for i in reversed(order):
        j = downstream[i]
        if j is None:
            final[i] = node_zone[outlet[i]] if blind[i] else -1
        else:
            final[i] = final[j]
    ok = np.ones(nodes_n, dtype=bool)
    for node, edge in carrier.items():
        ok[node] = final[edge] != node_zone[node]
    return ok


def _blind_ends(outlet: np.ndarray, outfall: np.ndarray, xy: np.ndarray, node_zone: np.ndarray,
                sea: int) -> np.ndarray:
    """Which outfall drains end away from any mapped channel and from the sea."""
    blind = np.zeros(outfall.size, dtype=bool)
    lines = surface.channel_lines()
    if not outfall.any() or not lines:
        return blind
    scale = terrain.M_PER_DEG * math.cos(math.radians(13.0))
    pts = np.vstack([np.asarray(densify(line, 10.0)) for line in lines])
    tree = cKDTree(np.c_[pts[:, 0] * scale, pts[:, 1] * terrain.M_PER_DEG])
    ends = xy[outlet[outfall]]
    dist, _ = tree.query(np.c_[ends[:, 0] * scale, ends[:, 1] * terrain.M_PER_DEG])
    at_sea = node_zone[outlet[outfall]] == sea
    blind[outfall] = (dist > OUTFALL_TO_CHANNEL_M) & ~at_sea
    return blind


def _route(net: dict[str, Any], arrive: np.ndarray, stored: np.ndarray, intercept: np.ndarray,
           pond: np.ndarray, room: np.ndarray, decay: np.ndarray, gain: np.ndarray) -> tuple:
    """One pass of the network, a generation at a time, upstream first.

    Each drain takes in what waits at the junctions it carries water on from - water
    already in the pipes, inlet catch, and ponded water over its inlets - up to `room`,
    its capacity over the step. Short of room, ponded water is simply not taken, inlet
    catch runs past the inlet, and pipe water surcharges out of the manhole. What it
    takes is delayed by a linear reservoir with the drain's travel time K, exact for a
    steady inflow over the step: S' = S e^(-dt/K) + I K (1 - e^(-dt/K)), and what
    leaves is S + I dt - S'. `arrive` and `stored` carry on to the next step.
    """
    nodes_n = arrive.size
    captured, bypass, surcharge = np.zeros(nodes_n), np.zeros(nodes_n), np.zeros(nodes_n)
    released = np.zeros(nodes_n)            # out of a blind end, onto the ground
    outfall = 0.0
    for g in net["generations"]:
        e, en, el = g["edges"], g["nodes"], g["local"]
        m = e.size
        w_arr = np.bincount(el, weights=arrive[en], minlength=m)
        w_icp = np.bincount(el, weights=intercept[en], minlength=m)
        w_pnd = np.bincount(el, weights=pond[en], minlength=m)
        waiting = w_arr + w_icp + w_pnd
        taken = np.minimum(waiting, room[e])
        short = waiting - taken
        cut_pnd = np.minimum(short, w_pnd)
        cut_icp = np.minimum(short - cut_pnd, w_icp)
        cut_arr = np.clip(short - cut_pnd - cut_icp, 0.0, None)
        f_pnd = np.divide(cut_pnd, w_pnd, out=np.zeros(m), where=w_pnd > 0)
        f_icp = np.divide(cut_icp, w_icp, out=np.zeros(m), where=w_icp > 0)
        f_arr = np.divide(cut_arr, w_arr, out=np.zeros(m), where=w_arr > 0)
        captured[en] += pond[en] * (1.0 - f_pnd[el])
        bypass[en] += intercept[en] * f_icp[el]
        surcharge[en] += arrive[en] * f_arr[el]
        arrive[en] = 0.0

        before = stored[e]
        stored[e] = before * decay[e] + taken * gain[e]
        out = before + taken - stored[e]
        leaves = net["outfall"][e]
        blind = net["blind_end"][e]
        outfall += float(out[leaves & ~blind].sum())
        np.add.at(released, net["outlet"][e][blind], out[blind])
        np.add.at(arrive, net["outlet"][e][~leaves], out[~leaves])
    return captured, bypass, surcharge, outfall, released


# ─── The storm ──────────────────────────────────────────────────────────────────

def simulate(s: dict[str, Any], net: dict[str, Any], rain_mm: Any, step_minutes: float,
             runoff_coeff: float, drain_condition: float) -> dict[str, Any]:
    """Run a storm through the surface and the network together.

    `rain_mm` is the rain that falls in each output step, either one value per step
    (even over the city) or an array (steps, zones) for rain that varies by place.
    Returns the water level of every zone at the end of every step (-inf where dry),
    what surcharges at every junction, and the mass balance.
    """
    n, sea, dt = s["n"], s["sea"], COUPLE_S
    per = max(1, int(round(step_minutes * 60 / dt)))
    rain = np.asarray(rain_mm, dtype=np.float64)
    if rain.ndim == 1:
        rain = np.repeat(rain[:, None], n, axis=1)
    out_steps = rain.shape[0]
    steps = out_steps * per
    rain = np.repeat(rain / per, per, axis=0)              # mm in each coupling step
    rain[:, sea] = 0.0

    # The zone's own ground away from the drains delivers its runoff over its time of
    # concentration: the mean of the rain over the last `lag` steps.
    remainder = net["remainder"]
    lag = np.maximum(1, np.ceil(np.sqrt(remainder) / FLOW_SPEED_M_S / dt)).astype(np.int64)
    fallen = np.vstack([np.zeros(n), np.cumsum(rain, axis=0)])
    cols = np.arange(n)

    inlet = net["inlet"]
    node_zone, ground = net["node_zone"], net["ground"]
    in_zone, in_ground = node_zone[inlet], ground[inlet]
    drains_zone = net["drains_zone"][inlet]
    g_in, o_in, strip_in = net["grates"][inlet], net["open_edge"][inlet], net["strip"][inlet]
    gutter = grate_capacity(g_in, o_in, np.full(inlet.size, GUTTER_DEPTH_M)) * dt
    undrained = net["undrained"]
    # Zones with no surveyed drain lose runoff at the design rate, scaled like every
    # other drain by its condition.
    virtual_rate = (runoff_coeff * UNSURVEYED_DESIGN_MM_H / 3.6e6 * drain_condition
                    * s["area_m2"] * undrained)

    volume = np.zeros(n)
    arrive = np.zeros(len(net["points"]))
    stored = np.zeros(net["edges"])
    travel = net["travel"]
    room = np.where(np.isinf(net["capacity"]), np.inf, net["capacity"] * drain_condition * dt)
    decay = np.where(travel > 0, np.exp(-dt / np.where(travel > 0, travel, 1.0)), 0.0)
    gain = np.where(travel > 0, travel / dt * (1.0 - decay), 0.0)
    nodes_n = len(net["points"])
    record_levels = np.full((out_steps, n), -np.inf)
    surcharge_m3 = np.zeros((out_steps, nodes_n))
    node_depth = np.zeros((out_steps, nodes_n))
    ledger = {"rain": 0.0, "runoff": 0.0, "to_sea": 0.0, "outfall": 0.0,
              "assumed_drainage": 0.0, "surcharged": 0.0, "captured": 0.0,
              "blind_ends_to_ground": 0.0}
    sub = dt / SURFACE_SUBSTEPS

    for t in range(steps):
        mm = rain[t]
        ledger["rain"] += float(np.dot(mm, s["area_m2"])) / 1000.0
        # Rain on the ground away from the drains, arriving over the zone's lag.
        arriving = (fallen[t + 1] - fallen[np.maximum(0, t + 1 - lag), cols]) / lag
        volume += runoff_coeff * arriving / 1000.0 * remainder

        # Rain on each drain's strip, caught at gutter depth; the rest runs on.
        strip_runoff = runoff_coeff * mm[in_zone] / 1000.0 * strip_in
        caught = np.minimum(strip_runoff, gutter)
        missed = strip_runoff - caught
        ledger["runoff"] += float(strip_runoff.sum())

        # Standing water over an inlet drains into it at the depth it stands.
        level, _ = levels(s, volume)
        depth = level[in_zone] - in_ground
        wet = (depth > 0) & (volume[in_zone] > 0) & drains_zone
        demand = np.where(wet, grate_capacity(g_in, o_in, np.where(wet, depth, 0.0)) * dt, 0.0)
        if wet.any():
            available = np.where(wet, volume[in_zone] - volume_below(s, in_zone, np.where(wet, in_ground, -np.inf)), 0.0)
            demand = np.minimum(demand, np.maximum(available, 0.0))
            most = np.zeros(n)
            np.maximum.at(most, in_zone, available)
            asked = np.bincount(in_zone, weights=demand, minlength=n)
            demand *= np.divide(most, asked, out=np.ones(n), where=asked > most)[in_zone]

        intercept = np.zeros(nodes_n)
        pond = np.zeros(nodes_n)
        intercept[inlet] = caught
        pond[inlet] = demand
        captured, bypass, surcharge, outfall, released = _route(net, arrive, stored, intercept, pond,
                                                                room, decay, gain)
        ledger["outfall"] += outfall
        ledger["blind_ends_to_ground"] += float(released.sum())
        ledger["surcharged"] += float(surcharge.sum())
        ledger["captured"] += float(captured.sum())

        # Back onto the surface: what missed the inlets, what the pipes could not take.
        onto = np.bincount(in_zone, weights=missed, minlength=n)
        onto += np.bincount(node_zone, weights=bypass + surcharge + released - captured, minlength=n)
        volume += onto
        # Capture never takes more than stands there; this only clears round-off.
        np.clip(volume, 0.0, None, out=volume)
        ledger["to_sea"] += float(volume[sea])
        volume[sea] = 0.0

        for _ in range(SURFACE_SUBSTEPS):
            ledger["to_sea"] += exchange(s, volume, sub)
            if virtual_rate.any():
                gone = np.minimum(volume, virtual_rate * sub)
                volume -= gone
                ledger["assumed_drainage"] += float(gone.sum())

        out = t // per
        surcharge_m3[out] += surcharge
        if (t + 1) % per == 0:
            level, _ = levels(s, volume)
            record_levels[out] = np.where(volume > 0, level, -np.inf)
            record_levels[out, sea] = -np.inf
            node_depth[out] = np.clip(level[node_zone] - ground, 0.0, None) * (volume[node_zone] > 0)

    # Runoff generated off the strips so far, less what has not yet reached its zone.
    generated_rest = runoff_coeff * float(np.dot(fallen[-1], remainder)) / 1000.0
    arrived_rest = runoff_coeff * float(np.dot(
        sum((fallen[t + 1] - fallen[np.maximum(0, t + 1 - lag), cols]) / lag for t in range(steps)),
        remainder)) / 1000.0 if steps else 0.0
    ledger["runoff"] += generated_rest
    ledger["on_slopes"] = generated_rest - arrived_rest
    ledger["on_surface"] = float(volume.sum())
    ledger["in_drains"] = float(stored.sum() + arrive.sum())
    held = (ledger["on_surface"] + ledger["in_drains"] + ledger["on_slopes"] + ledger["outfall"]
            + ledger["to_sea"] + ledger["assumed_drainage"])
    ledger["error_m3"] = ledger["runoff"] - held
    return {"levels": record_levels, "surcharge_m3": surcharge_m3, "node_depth_m": node_depth,
            "balance": {k: round(v, 1) if k != "error_m3" else v for k, v in ledger.items()}}


def coupled_summary(net: dict[str, Any], s: dict[str, Any]) -> dict[str, Any]:
    """What the coupling rests on, for the page to say so."""
    outfalls = int(net["outfall"].sum())
    undrained = net["undrained"].copy()
    undrained[s["sea"]] = False
    return {
        "inlet_junctions": int(net["inlet"].size),
        "gratings": int(net["grates"].sum()),
        "open_drain_edge_km": round(float(net["open_edge"].sum()) / 1000, 1),
        "zones": int(s["zones"].size),
        "zones_without_surveyed_drains": int((undrained & (s["area_m2"] > 0)).sum()),
        "unsurveyed_km2": round(float(s["area_m2"][undrained].sum()) / 1e6, 1),
        "unsurveyed_design_mm_h": UNSURVEYED_DESIGN_MM_H,
        "outfalls_to_channels": outfalls - int(net["blind_end"].sum()),
        "blind_ends_to_ground": int(net["blind_end"].sum()),
    }
