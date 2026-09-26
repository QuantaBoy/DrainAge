"""The storm water drain network: the GCC survey and the ward maps, merged into one graph.

Two surveys describe Chennai's drains. The GCC storm water drain survey (a CSV of
10,240 drains) covers the whole city but records almost every section as square; the
SECON-JBA ward maps (Ward/*.pdf, read by scripts/digitise_wards.py) draw 92 wards
manhole by manhole with measured sections and levels. Where a ward map draws a drain,
its drain replaces the CSV's and borrows the CSV's condition, material and name.

Each drain then gets its hydraulic capacity (app/services/hydraulics.py) and the
catchment that reaches it through the network, and the whole network becomes the
directed graph the flood model routes water through. Building it takes a minute, so
the result is kept on disk (app/services/diskcache.py) until an input changes.
"""

import csv
import json
import math
import re
import statistics
import threading
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from app import config
from app.services import diskcache, hydraulics
from app.services.terrain import M_PER_DEG

# Fields forwarded into each GeoJSON feature's properties.
PROPERTY_FIELDS = [
    "feature_no", "DRAIN_DETL", "DRAIN_TYPE", "DRAIN_SIZE", "DRAIN_DEP",
    "DRAIN_WID", "DRAIN_LEN", "STATUS", "ST_NAME", "LOCATION", "WARD",
    "ZONE", "WATER_FLOW", "SWD_MAT", "TYP_MAT", "COVER", "OBSTACLES",
    "PUCA_KACHA", "computed_length_m", "INLET_SHP", "MH_SHAPE", "MH_SIZE",
    "INVERT_SP", "INVERT_EP",
]

# Invert levels outside this span (m above MSL) are data-entry errors, e.g. 104860.
INVERT_RANGE = (-5.0, 100.0)

# What each figure on the page rests on. The survey is measured ground truth; the
# hydraulics are textbook formulae applied to it; the two catchment numbers are
# assumptions, and the page says so rather than dressing them up as measurements.
PROVENANCE = {
    "network, size, inverts": "the GCC / SECON-JBA ward base-map survey (Ward/*.pdf, Dec 2020, "
                              "levels above MSL): each drain as drawn from manhole to manhole, "
                              "with the section and invert measured at its manholes, or carried "
                              "or interpolated along the drain where a manhole was not measured; "
                              "outside the sheets, the GCC survey CSV, whose square sections are "
                              "marked unverified",
    "material, condition, obstacles": "GCC storm water drain survey (gcc_storm_water_drains.csv), "
                                      "from the survey drain each sheet drain lies along",
    "slope": "surveyed invert levels over surveyed length, floored at "
             f"{hydraulics.MIN_SLOPE} and capped at {hydraulics.MAX_SLOPE}",
    "capacity": "Manning's equation at full section, roughness from the surveyed "
                "material",
    "effective capacity": "capacity reduced for surveyed condition and obstructions",
    "catchment": f"assumed strip of {hydraulics.STRIP_WIDTH_M} m along the drain, "
                 "accumulated down the network - not a surveyed sub-catchment",
    "inflow": f"Rational method Q=CiA with C={hydraulics.RUNOFF_COEFF}, i from the "
              "live Open-Meteo rainfall forecast for this point",
    "depth, velocity, Froude": "Manning solved for normal depth at that inflow",
}

# Cached ward sheet index, read from config.WARD_SHEETS_JSON on first use.
_sheets: dict[str, dict[str, Any]] | None = None


def ward_sheets() -> dict[str, dict[str, Any]]:
    """The base-map sheet for each ward, keyed the way the CSV keys wards."""
    global _sheets
    if _sheets is None:
        try:
            _sheets = json.loads(config.WARD_SHEETS_JSON.read_text(encoding="utf-8"))["sheets"]
        except (OSError, ValueError, KeyError):
            _sheets = {}
    return _sheets


def sheet_note(ward: str, zone: str) -> dict[str, Any]:
    """What the base map says about this ward, and whether the CSV agrees with it."""
    sheet = ward_sheets().get(ward)
    if not sheet:
        return {
            "sheet": None,
            "agrees": None,
            "note": "No base-map sheet for this ward in Ward/; the survey row is the "
                    "only source.",
        }
    agrees = sheet["zone"] == zone
    return {
        "sheet": f"Ward {sheet['ward']} base map, Zone {sheet['zone_roman']} "
                 f"{sheet['zone_name']}",
        "agrees": agrees,
        "note": "Ward and zone match the base-map sheet." if agrees else
                f"The survey row puts this ward in zone {zone}, the base-map sheet in "
                f"{sheet['zone']} ({sheet['zone_name']}). Shown as surveyed, flagged "
                f"rather than corrected.",
    }


# In-memory cache: one dict per drain, with its survey row, geometry, capacity and
# the catchment area that reaches it through the network.
_drains: list[dict[str, Any]] | None = None


def _parse_wkt_linestring(wkt: str) -> list[list[float]] | None:
    """Extract [[lon, lat], …] from a WKT LINESTRING string."""
    match = re.match(r"LINESTRING\s*\((.+)\)", wkt, re.IGNORECASE)
    if not match:
        return None
    coords = []
    for pair in match.group(1).split(","):
        parts = pair.strip().split()
        if len(parts) >= 2:
            coords.append([float(parts[0]), float(parts[1])])
    return coords if len(coords) >= 2 else None


def _valid_invert(value: Any) -> bool:
    return isinstance(value, float) and INVERT_RANGE[0] <= value <= INVERT_RANGE[1]


def _length_m(props: dict[str, Any]) -> float:
    """Surveyed length, falling back to the length of the drawn geometry."""
    for field in ("computed_length_m", "DRAIN_LEN"):
        value = props.get(field)
        if isinstance(value, float) and value > 0:
            return value
    return 0.0


_build_lock = threading.Lock()


def network_inputs() -> list[Path]:
    """Every file the merged drain network is built from, code included."""
    return [config.DRAIN_SURVEY_CSV, config.WARD_POINTS_JSON, config.WARD_SHEETS_JSON,
            Path(__file__), *diskcache.sources(hydraulics)]


def load_drains() -> list[dict[str, Any]]:
    """The drain network, built once: from disk if nothing it depends on has changed."""
    global _drains
    if _drains is not None:
        return _drains
    with _build_lock:
        if _drains is None:
            drains, graph = diskcache.cached("drains", network_inputs(), _build)
            _graph.clear()
            _graph.update(graph)
            _drains = drains
    return _drains


def _build() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse the CSV and the ward sheets, then work out capacities and catchments."""
    drains: list[dict[str, Any]] = []
    with open(config.DRAIN_SURVEY_CSV, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            wkt = row.get("wkt_geometry", "")
            coords = _parse_wkt_linestring(wkt)
            if coords is None:
                continue

            props: dict[str, Any] = {}
            for field in PROPERTY_FIELDS:
                val = row.get(field, "")
                # Try to convert numeric-looking values.
                if val and field in (
                    "feature_no", "DRAIN_DEP", "DRAIN_WID", "DRAIN_LEN",
                    "computed_length_m", "INVERT_SP", "INVERT_EP",
                ):
                    try:
                        props[field] = float(val)
                    except ValueError:
                        props[field] = val
                else:
                    props[field] = val
            # The survey writes some wards as N69, some as N069, some bare "69".
            if re.fullmatch(r"N?\d{1,3}", props["WARD"]):
                props["WARD"] = f"N{int(props['WARD'].lstrip('N')):03d}"
            square = isinstance(props.get("DRAIN_WID"), float) and props["DRAIN_WID"] == props.get("DRAIN_DEP")
            props.update({"SOURCE": "survey CSV",
                          "SIZE_SOURCE": "survey CSV" + (" (width = depth: unverified)" if square else ""),
                          "SIZE_UNVERIFIED": square})
            drains.append({"props": props, "coords": coords})

    # Where the ward sheets draw the network, theirs replaces the CSV's.
    sheet, replaced = _sheet_drains(drains)
    drains = [d for j, d in enumerate(drains) if j not in replaced] + sheet
    _typical_section_for_unsized(drains)
    for drain in drains:
        props = drain["props"]
        length = _length_m(props)
        drain.update({
            "flow": props.get("FLOW_FIXED") or flow_direction(props.get("INVERT_SP"), props.get("INVERT_EP")),
            "length_m": length,
            "capacity": hydraulics.drain_capacity(props, length),
        })

    _accumulate_network(drains)
    return drains, dict(_graph)


# A CSV drain is the same drain as the sheets' where this share of it lies within
# SAME_DRAIN_M of a sheet drain; the survey is walked at SAMPLE_M.
SAME_DRAIN_M = 20.0
SAME_SHARE = 0.6
SAMPLE_M = 15.0
# Two sheets' drains are one where they run within this of each other (the grid-line
# georeference is exact to 0.1 m, the drawing to a few metres).
DUPLICATE_M = 10.0
# The CSV attributes a sheet drain takes from the survey drain beside it: the sheets
# record sections and levels, the CSV condition, material, obstructions and names.
FROM_SURVEY = ("STATUS", "SWD_MAT", "TYP_MAT", "OBSTACLES", "PUCA_KACHA", "ST_NAME",
               "LOCATION", "WATER_FLOW", "DRAIN_TYPE", "INLET_SHP", "MH_SHAPE", "MH_SIZE")
SHEET_FEATURE_BASE = 900_000


def _metres_xy(coords: list[list[float]]) -> np.ndarray:
    xy = np.asarray(coords, dtype=float)
    return np.c_[xy[:, 0] * M_PER_DEG * math.cos(math.radians(13.0)), xy[:, 1] * M_PER_DEG]


def _samples(xy: np.ndarray) -> np.ndarray:
    """Points every SAMPLE_M along a line, both ends included."""
    out = [xy[0]]
    for p, q in zip(xy[:-1], xy[1:]):
        steps = max(1, int(np.ceil(np.hypot(*(q - p)) / SAMPLE_M)))
        out.extend(p + (q - p) * (k / steps) for k in range(1, steps + 1))
    return np.array(out)


def _sheet_drains(survey: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[int]]:
    """The GCC / SECON-JBA ward sheets' own drain network, and the CSV drains it replaces.

    digitise_wards.py reads each sheet's drawn drains from manhole to manhole with the
    surveyed invert and section at every manhole (app/data/ward_points.json). Only
    sheets georeferenced from their own grid lines are used, exact to 0.1 m. Each sheet
    drain carries its measured section, or one carried along the drain from the nearest
    measured manhole, and inverts surveyed or interpolated along it; it takes condition,
    material and name from the survey drain it overlaps. A CSV drain lying along sheet
    drains for SAME_SHARE of its length is the same drain, measured better, and goes.
    """
    if not config.WARD_POINTS_JSON.exists():
        return [], set()
    sheets = json.loads(config.WARD_POINTS_JSON.read_text(encoding="utf-8"))["sheets"]
    zones = {f"N{int(s['ward']):03d}": s["zone"] for s in ward_sheets().values()}

    kept: list[dict[str, Any]] = []
    kept_tree_pts: list[np.ndarray] = []
    for sheet in sheets:
        if (sheet.get("georeference") or {}).get("from") != ["grid lines"]:
            continue
        nodes = sheet["network"]["nodes"]
        others = cKDTree(np.vstack(kept_tree_pts)) if kept_tree_pts else None
        for edge in sheet["network"]["edges"]:
            xy = _metres_xy(edge["coords"])
            if others is not None:
                dist, _ = others.query(_samples(xy))
                if (dist < DUPLICATE_M).mean() >= SAME_SHARE:
                    continue                           # drawn on the neighbouring sheet too
            a, b = nodes[edge["a"]], nodes[edge["b"]]
            kept.append({"sheet": sheet["ward"], "edge": edge, "a": a, "b": b, "xy": xy})
        if kept:
            kept_tree_pts = [np.vstack([_samples(k["xy"]) for k in kept])]

    if not kept:
        return [], set()
    sheet_pts = np.vstack([_samples(k["xy"]) for k in kept])
    owner = np.concatenate([[i] * len(_samples(k["xy"])) for i, k in enumerate(kept)])
    tree = cKDTree(sheet_pts)

    replaced: set[int] = set()
    nearest_survey: dict[int, tuple[float, int]] = {}
    for j, drain in enumerate(survey):
        pts = _samples(_metres_xy(drain["coords"]))
        dist, idx = tree.query(pts)
        close = dist < SAME_DRAIN_M
        if close.mean() >= SAME_SHARE:
            replaced.add(j)
        # Each sheet drain takes its attributes from the survey drain it lies along.
        for d, i in zip(dist[close], idx[close]):
            k = int(owner[i])
            if k not in nearest_survey or d < nearest_survey[k][0]:
                nearest_survey[k] = (float(d), j)

    out = []
    for i, k in enumerate(kept):
        edge, a, b = k["edge"], k["a"], k["b"]
        coords = edge["coords"]
        # Laid out the way the water runs: upstream end first.
        if edge.get("to") == "a":
            a, b, coords = b, a, coords[::-1]
        source = survey[nearest_survey[i][1]]["props"] if i in nearest_survey else {}
        props: dict[str, Any] = {field: source.get(field, "") for field in PROPERTY_FIELDS}
        props.update({field: source.get(field, "") for field in FROM_SURVEY})
        width, depth = edge["width_m"], edge["depth_m"]
        sized = bool(width and depth)
        ward = f"N{int(k['sheet']):03d}"
        props.update({
            "feature_no": float(SHEET_FEATURE_BASE + i),
            "WARD": ward, "ZONE": zones.get(ward, source.get("ZONE", "")),
            "DRAIN_WID": float(width) if sized else "", "DRAIN_DEP": float(depth) if sized else "",
            "DRAIN_SIZE": f"{width:.2f} x {depth:.2f}" if sized else "",
            "DRAIN_DETL": "Closed" if edge.get("closed") else "Open" if edge.get("closed") is False
            else source.get("DRAIN_DETL", ""),
            "COVER": "Yes" if edge.get("closed") else "No" if edge.get("closed") is False else source.get("COVER", ""),
            "INVERT_SP": a["invert_m"] if a["invert_m"] is not None else "",
            "INVERT_EP": b["invert_m"] if b["invert_m"] is not None else "",
            "computed_length_m": float(edge["length_m"]),
            "DRAIN_LEN": float(edge["length_m"]),
            "SOURCE": f"ward sheet {k['sheet']}",
            "SIZE_SOURCE": (f"ward sheet {k['sheet']}: " + ("measured at the manhole" if edge["section_from"] == "survey"
                            else edge["section_from"])) if sized else "not on the sheet",
            "SIZE_UNVERIFIED": not sized,
            "INVERT_SOURCE": f"ward sheet {k['sheet']}: " + ", ".join(
                f"{n['id'] or 'node'} {n['invert_from'] or 'no level'}" for n in (a, b)),
            "MANHOLES": " to ".join(n["id"] or "junction" for n in (a, b)),
            "REVERSE_SURVEYED": bool(edge["pink"]),
            "FLOW_FIXED": "forward" if edge.get("to") else None,
            "FLOW_FROM": edge.get("to_from") or "not known: drawn direction kept",
        })
        if not source:
            props["ST_NAME"] = ""
        out.append({"props": props, "coords": coords})
    return out, replaced


# A drain that ends this close to another one hands its water on to it: the CSV was
# digitised in pieces, and a drain stopping 20 m short of the next is a gap in the
# drawing, not a dead end. About two road widths; beyond it the end is a blind end.
GAP_M = 30.0


def _close_gaps(drains: list[dict[str, Any]], node_ids: list[list[int]],
                node_points: list[list[float]]) -> None:
    """Join every drain end nothing carries on from to the nearest other drain within
    GAP_M, in place, by giving the end that drain's junction."""
    carried = {n for nodes in node_ids for n in hydraulics.upstream_nodes({"nodes": nodes})}
    if not carried:
        return
    candidates = np.array(sorted(carried))
    scale = M_PER_DEG * math.cos(math.radians(13.0))
    xy = np.asarray(node_points, dtype=float)
    tree = cKDTree(np.c_[xy[candidates, 0] * scale, xy[candidates, 1] * M_PER_DEG])
    for drain, nodes in zip(drains, node_ids):
        end = nodes[-1]
        drain["gap_joined_m"] = None
        if end in carried:
            continue
        own = set(nodes)
        dist, idx = tree.query([xy[end, 0] * scale, xy[end, 1] * M_PER_DEG], k=6,
                               distance_upper_bound=GAP_M)
        for d, i in zip(np.atleast_1d(dist), np.atleast_1d(idx)):
            if np.isfinite(d) and int(candidates[i]) not in own:
                nodes[-1] = int(candidates[i])
                drain["gap_joined_m"] = round(float(d), 1)
                break


def _typical_section_for_unsized(drains: list[dict[str, Any]]) -> None:
    """A drain with no recorded size gets the typical measured one, marked assumed.

    Left unsized it would carry without limit - an infinite pipe that drains a whole
    pond at once and dumps it on the next drain down, 265 m³/s out of a manhole whose
    own catchment gives 0.5. The typical section is the median of the ward sheets'
    measured ones, the best-grounded size a drain nobody measured can be given.
    """
    def size(props: dict[str, Any], key: str) -> float | None:
        value = props.get(key)
        return value if isinstance(value, float) and value > 0 else None

    measured = [(size(d["props"], "DRAIN_WID"), size(d["props"], "DRAIN_DEP")) for d in drains
                if d["props"].get("SOURCE", "").startswith("ward sheet")
                and "measured" in str(d["props"].get("SIZE_SOURCE", ""))]
    measured = [(w, h) for w, h in measured if w and h]
    if not measured:
        return
    width = statistics.median(w for w, _ in measured)
    depth = statistics.median(h for _, h in measured)
    for drain in drains:
        props = drain["props"]
        if size(props, "DRAIN_WID") and size(props, "DRAIN_DEP"):
            continue
        props["DRAIN_WID"], props["DRAIN_DEP"] = width, depth
        props["DRAIN_SIZE"] = f"{width:.2f} x {depth:.2f}"
        props["SIZE_SOURCE"] = (f"no size recorded: the typical measured section ({width:.2f} x "
                                f"{depth:.2f} m) assumed")
        props["SIZE_UNVERIFIED"] = True


def _accumulate_network(drains: list[dict[str, Any]]) -> None:
    """Route every drain's own catchment downstream, and store what reaches each one.

    A drain runs from its higher invert to its lower one, so the geometry is walked
    in that direction before its vertices are snapped onto shared junctions. A drain
    with no usable inverts keeps the direction it was drawn in.
    """
    runs = [drain["coords"][::-1] if drain["flow"] == "reverse" else drain["coords"]
            for drain in drains]
    node_ids, node_points = hydraulics.snap_nodes(
        runs, exact=[drain["props"]["SOURCE"] if drain["props"].get("SOURCE", "").startswith("ward sheet")
                     else None for drain in drains])
    _close_gaps(drains, node_ids, node_points)

    edges = []
    for drain, nodes in zip(drains, node_ids):
        props = drain["props"]
        head, tail = props.get("INVERT_SP"), props.get("INVERT_EP")
        if drain["flow"] == "reverse":
            head, tail = tail, head
        # A sheet drain runs the way the survey drew it, reverse gradient or not: its
        # levels give its fall, not whether the drain below takes its water.
        by_drawing = props.get("FLOW_FIXED") is not None
        edges.append({
            "nodes": nodes,
            "local": drain["capacity"]["catchment_strip_m2"],
            "head": None if by_drawing or not _valid_invert(head) else head,
            "tail": None if by_drawing or not _valid_invert(tail) else tail,
        })

    graph = hydraulics.network(edges)
    for drain, nodes, catchment in zip(drains, node_ids, hydraulics.accumulate(edges, graph)):
        drain["catchment_m2"] = catchment
        # Where this drain hands its water on.
        drain["outlet_node"] = nodes[-1]
        drain["outlet"] = node_points[nodes[-1]]
        drain["start_node"] = nodes[0]
    # Where the network lets go of its water: nothing carries on from the junction a
    # drain ends at. A side drain usually joins a trunk part-way along it, which the
    # graph's carriers already account for.
    for drain, below in zip(drains, graph["downstream"]):
        drain["is_outfall"] = below is None

    # Kept for routing rain through the network step by step (app/services/forecast.py): the same
    # graph, so the flood nowcast and the catchments can never disagree about it.
    _graph.clear()
    _graph.update({"edges": edges, "graph": graph, "points": node_points})


# The drain network as a graph: edges (one per drain, in the survey's order), the
# carrier of every junction and the topological order, and where each junction is.
_graph: dict[str, Any] = {}


def drain_graph() -> dict[str, Any]:
    """The network the drains are loaded into, built on first use."""
    load_drains()
    return _graph


def flow_direction(invert_start: Any, invert_end: Any) -> str:
    """Which way water runs in one drain, from its own surveyed invert levels.

    "forward": start invert higher, water runs start to end (the drawn direction).
    "reverse": end invert higher, a reverse-gradient drain; water runs back to the start.
    "unknown": an invert is missing, out of range, or both are equal.
    """
    if not all(_valid_invert(v) for v in (invert_start, invert_end)):
        return "unknown"
    if invert_start == invert_end:
        return "unknown"
    return "forward" if invert_start > invert_end else "reverse"


def catchment_for(drain: dict[str, Any], strip_m: float) -> float:
    """Catchment reaching this drain for a given strip width.

    Accumulation is linear in the strip each drain collects from, so the network is
    only walked once, at hydraulics.STRIP_WIDTH_M, and every other setting of the
    knob is that answer scaled.
    """
    return drain["catchment_m2"] * strip_m / hydraulics.STRIP_WIDTH_M


def matches(props: dict[str, Any], wanted: dict[str, str | None]) -> bool:
    return all(props.get(field) == value for field, value in wanted.items() if value)


def unique_values(field: str, drains: list[dict[str, Any]]) -> list[str]:
    """Sorted unique non-empty values for a property field."""
    return sorted({d["props"][field] for d in drains if d["props"].get(field)})


# ─── Queries ─────────────────────────────────────────────────────────────────────

_by_feature: dict[int, dict[str, Any]] = {}


def drain_by_feature(feature_no: int) -> dict[str, Any] | None:
    """One drain by its feature number, from an index built on first use."""
    if not _by_feature:
        _by_feature.update({int(d["props"]["feature_no"]): d for d in load_drains()
                            if isinstance(d["props"].get("feature_no"), float)})
    return _by_feature.get(int(feature_no))


def drain_features(filters: dict[str, str | None], strip_m: float) -> list[dict[str, Any]]:
    """The drains matching `filters`, as GeoJSON features with their hydraulics."""
    features = []
    for drain in load_drains():
        props, capacity = drain["props"], drain["capacity"]
        if not matches(props, filters):
            continue
        features.append({
            "type": "Feature",
            # Coordinates run the way the water runs; the survey's own start and end
            # stay in INVERT_SP / INVERT_EP.
            "geometry": {
                "type": "LineString",
                "coordinates": drain["coords"][::-1] if drain["flow"] == "reverse" else drain["coords"],
            },
            "properties": {
                **props,
                "FLOW": drain["flow"],
                # Enough hydraulics for the page to colour live load without asking again.
                "Q_CAP": capacity["effective_capacity_m3s"],
                "Q_BUILT": capacity["capacity_m3s"],
                "V_FULL": capacity["full_velocity_ms"],
                "SLOPE": capacity["slope"],
                "CATCH_M2": round(catchment_for(drain, strip_m), 1),
                "OWN_M2": round(drain["length_m"] * strip_m, 1),
            },
        })
    return features


def drain_report(drain: dict[str, Any], rain_mm_h: float, strip_m: float,
                 runoff_coeff: float) -> dict[str, Any]:
    """Everything about one drain: the survey, its capacity, and its state in this rain."""
    props = drain["props"]
    return {
        "drain": {
            "feature_no": int(props["feature_no"]),
            "street": props.get("ST_NAME"),
            "location": props.get("LOCATION"),
            "ward": props.get("WARD"),
            "zone": props.get("ZONE"),
            "type": props.get("DRAIN_TYPE"),
            "detail": props.get("DRAIN_DETL"),
            "status": props.get("STATUS"),
            "size": props.get("DRAIN_SIZE"),
            "length_m": round(drain["length_m"], 1),
            "invert_start_m": props.get("INVERT_SP"),
            "invert_end_m": props.get("INVERT_EP"),
            "flow": drain["flow"],
            "base_map": sheet_note(props.get("WARD", ""), props.get("ZONE", "")),
        },
        "capacity": drain["capacity"],
        "state": hydraulics.drain_state(drain["capacity"], catchment_for(drain, strip_m),
                                        rain_mm_h, drain["length_m"], runoff_coeff),
        # Nothing here is a black box: every number is surveyed, derived from surveyed
        # values, or an assumption with its value on show.
        "provenance": PROVENANCE,
    }


def network_summary(filters: dict[str, str | None], rain_mm_h: float, strip_m: float,
                    runoff_coeff: float, worst: int) -> dict[str, Any]:
    """What the selected drains are doing at this rainfall.

    Load is each drain's demand - the runoff of everything upstream of it - against
    its capacity, which is what the map colours. Spill is routed: a drain passes on
    only what it can carry, so water a drain upstream has already put on the street is
    not counted again at every drain below it.
    """
    drains = load_drains()
    graph = drain_graph()["graph"]
    local = [hydraulics.rational_inflow(d["length_m"] * strip_m, rain_mm_h, runoff_coeff) for d in drains]
    capacity = [d["capacity"]["effective_capacity_m3s"] for d in drains]
    _, spill = hydraulics.steady_route(local, capacity, graph)

    bands: dict[str, int] = {}
    totals = {"drains": 0, "length_m": 0.0, "capacity_m3s": 0.0, "effective_m3s": 0.0,
              "catchment_m2": 0.0, "spill_m3s": 0.0}
    loaded: list[tuple[float, dict[str, Any]]] = []
    unsized = 0
    for i, drain in enumerate(drains):
        props, cap = drain["props"], drain["capacity"]
        if not matches(props, filters):
            continue
        catchment = catchment_for(drain, strip_m)
        demand = hydraulics.rational_inflow(catchment, rain_mm_h, runoff_coeff)
        effective = cap["effective_capacity_m3s"]
        totals["drains"] += 1
        totals["length_m"] += drain["length_m"]
        totals["capacity_m3s"] += cap["capacity_m3s"]
        totals["effective_m3s"] += effective
        totals["catchment_m2"] += catchment
        totals["spill_m3s"] += spill[i]
        # A drain with no size has nothing to compare against: a gap in the data, not
        # an overloaded drain, so it cannot crowd the real findings out of the list.
        if effective <= 0:
            unsized += 1
            bands["unsized"] = bands.get("unsized", 0) + 1
            continue
        ratio = demand / effective
        band = hydraulics.load_band(ratio)
        bands[band] = bands.get(band, 0) + 1
        if ratio >= 1.0:
            loaded.append((ratio, {
                "feature_no": props.get("feature_no"),
                "street": props.get("ST_NAME"),
                "ward": props.get("WARD"),
                "zone": props.get("ZONE"),
                "status": props.get("STATUS"),
                "load_pct": round(ratio * 100, 1),
                "inflow_m3s": round(demand, 3),
                "effective_capacity_m3s": effective,
                "spill_m3s": round(spill[i], 3),
            }))
    loaded.sort(key=lambda item: item[0], reverse=True)
    return {
        "rain_mm_h": rain_mm_h,
        "runoff_coefficient": runoff_coeff,
        "strip_m": strip_m,
        "filters": {field.lower(): value for field, value in filters.items()},
        "drains": totals["drains"],
        "length_km": round(totals["length_m"] / 1000, 2),
        "catchment_km2": round(totals["catchment_m2"] / 1e6, 3),
        "built_capacity_m3s": round(totals["capacity_m3s"], 1),
        "effective_capacity_m3s": round(totals["effective_m3s"], 1),
        "inflow_m3s": round(sum(hydraulics.rational_inflow(d["length_m"] * strip_m, rain_mm_h, runoff_coeff)
                                for d in drains if matches(d["props"], filters)), 2),
        "bands": bands,
        "overloaded": len(loaded),
        "unsized": unsized,
        "spill_m3s": round(totals["spill_m3s"], 2),
        "worst": [item[1] for item in loaded[:worst]],
    }


def filter_options(zone: str | None) -> dict[str, Any]:
    """The values each drain filter can take; wards scoped to `zone` when one is given."""
    drains = load_drains()
    scoped = [d for d in drains if d["props"].get("ZONE") == zone] if zone else drains
    return {
        "zones": unique_values("ZONE", drains),
        "wards": unique_values("WARD", scoped),
        "drain_types": unique_values("DRAIN_TYPE", drains),
        "statuses": unique_values("STATUS", drains),
        # Which wards have a ward map, so the page can say whether a ward's drains have
        # a drawing behind them or only the survey row.
        "ward_sheets": ward_sheets(),
    }
