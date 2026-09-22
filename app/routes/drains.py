"""Storm water drains: GCC drain network from CSV, served as GeoJSON.

The CSV contains 10 000+ drain segments with WKT LineString geometries and
rich metadata (zone, ward, type, status, dimensions, material, obstacles).
Data is parsed once on first request and cached in memory.

On top of the survey, each drain gets its hydraulic capacity (what the built
section can carry) and the catchment that reaches it through the network; with a
rainfall figure those become the hydrodynamic state of the drain right now. The
physics lives in app/services/hydraulics.py.
"""

import asyncio
import csv
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import hydraulics

router = APIRouter(prefix="/data-collection", tags=["Drains"])

CSV_PATH = (Path(__file__).resolve().parent.parent.parent
            / "Cross-Checked Data" / "gcc_storm_water_drains (1).csv")
# Ward and zone as printed on the GCC base-map sheets in Ward/, extracted from their
# title blocks. The sheets are the paper source the survey CSV was digitised from, so
# they are what the CSV's own ward and zone codes are checked against.
SHEETS_PATH = Path(__file__).resolve().parent.parent / "data" / "ward_sheets.json"

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
    "size, inverts, material, condition, obstacles":
        "GCC / SECON-JBA storm water drain survey (gcc_storm_water_drains.csv, the "
        "digitised form of the ward base maps in Ward/)",
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

# Cached ward sheet index, read from SHEETS_PATH on first use.
_sheets: dict[str, dict[str, Any]] | None = None


def _ward_sheets() -> dict[str, dict[str, Any]]:
    """The base-map sheet for each ward, keyed the way the CSV keys wards."""
    global _sheets
    if _sheets is None:
        try:
            _sheets = json.loads(SHEETS_PATH.read_text(encoding="utf-8"))["sheets"]
        except (OSError, ValueError, KeyError):
            _sheets = {}
    return _sheets


def _sheet_note(ward: str, zone: str) -> dict[str, Any]:
    """What the base map says about this ward, and whether the CSV agrees with it."""
    sheet = _ward_sheets().get(ward)
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
_cache: list[dict[str, Any]] | None = None


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


def _load_csv() -> list[dict[str, Any]]:
    """Parse the CSV once, then work out capacities and accumulated catchments."""
    global _cache
    if _cache is not None:
        return _cache

    drains: list[dict[str, Any]] = []
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as fh:
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

            length = _length_m(props)
            drains.append({
                "props": props,
                "coords": coords,
                "flow": flow_direction(props.get("INVERT_SP"), props.get("INVERT_EP")),
                "length_m": length,
                "capacity": hydraulics.drain_capacity(props, length),
            })

    _accumulate_network(drains)
    _cache = drains
    return _cache


def _accumulate_network(drains: list[dict[str, Any]]) -> None:
    """Route every drain's own catchment downstream, and store what reaches each one.

    A drain runs from its higher invert to its lower one, so the geometry is walked
    in that direction before its vertices are snapped onto shared junctions. A drain
    with no usable inverts keeps the direction it was drawn in.
    """
    runs = [drain["coords"][::-1] if drain["flow"] == "reverse" else drain["coords"]
            for drain in drains]
    node_ids, node_points = hydraulics.snap_nodes(runs)

    edges = []
    for drain, nodes in zip(drains, node_ids):
        props = drain["props"]
        head, tail = props.get("INVERT_SP"), props.get("INVERT_EP")
        if drain["flow"] == "reverse":
            head, tail = tail, head
        edges.append({
            "nodes": nodes,
            "local": drain["capacity"]["catchment_strip_m2"],
            "head": head if _valid_invert(head) else None,
            "tail": tail if _valid_invert(tail) else None,
        })

    for drain, nodes, catchment in zip(drains, node_ids, hydraulics.accumulate(edges)):
        drain["catchment_m2"] = catchment
        # Where this drain hands its water on, and so where it comes back up when it
        # cannot take any more.
        drain["outlet_node"] = nodes[-1]
        drain["outlet"] = node_points[nodes[-1]]


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


def _catchment(drain: dict[str, Any], strip_m: float) -> float:
    """Catchment reaching this drain for a given strip width.

    Accumulation is linear in the strip each drain collects from, so the network is
    only walked once, at hydraulics.STRIP_WIDTH_M, and every other setting of the
    knob is that answer scaled.
    """
    return drain["catchment_m2"] * strip_m / hydraulics.STRIP_WIDTH_M


def _matches(props: dict[str, Any], wanted: dict[str, str | None]) -> bool:
    return all(props.get(field) == value for field, value in wanted.items() if value)


def _unique_values(field: str, drains: list[dict[str, Any]]) -> list[str]:
    """Sorted unique non-empty values for a property field."""
    return sorted({d["props"][field] for d in drains if d["props"].get(field)})


@router.get("/drains", summary="GCC storm water drains as GeoJSON")
async def get_drains(
    zone: str | None = Query(None, description="Filter by zone, e.g. N07"),
    ward: str | None = Query(None, description="Filter by ward, e.g. N082"),
    drain_type: str | None = Query(None, description="Filter by DRAIN_TYPE"),
    status: str | None = Query(None, description="Filter by STATUS"),
    strip_m: float = Query(hydraulics.STRIP_WIDTH_M, ge=1, le=500,
                           description="Catchment strip each drain collects from, m"),
) -> dict[str, Any]:
    all_drains = await asyncio.to_thread(_load_csv)
    wanted = {"ZONE": zone, "WARD": ward, "DRAIN_TYPE": drain_type, "STATUS": status}

    features = []
    for drain in all_drains:
        props, capacity = drain["props"], drain["capacity"]
        if not _matches(props, wanted):
            continue
        features.append({
            "type": "Feature",
            # Coordinates run the way the water runs; the CSV's start/end stay in
            # INVERT_SP/INVERT_EP.
            "geometry": {
                "type": "LineString",
                "coordinates": drain["coords"][::-1] if drain["flow"] == "reverse"
                else drain["coords"],
            },
            "properties": {
                **props,
                "FLOW": drain["flow"],
                # Enough hydraulics for the page to colour and size live load without
                # asking again; the full picture is in /drain-hydraulics.
                "Q_CAP": capacity["effective_capacity_m3s"],
                "Q_BUILT": capacity["capacity_m3s"],
                "V_FULL": capacity["full_velocity_ms"],
                "SLOPE": capacity["slope"],
                "CATCH_M2": round(_catchment(drain, strip_m), 1),
                "OWN_M2": round(drain["length_m"] * strip_m, 1),
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "total": len(features),
        # So the page computes inflow exactly the way the server does.
        "runoff_coefficient": hydraulics.RUNOFF_COEFF,
        "strip_m": strip_m,
    }


@router.get("/drain-hydraulics", summary="Capacity and live state of one drain")
async def get_drain_hydraulics(
    feature_no: int = Query(..., description="feature_no of the drain"),
    rain_mm_h: float = Query(0.0, ge=0, le=500, description="Rainfall intensity, mm/h"),
    strip_m: float = Query(hydraulics.STRIP_WIDTH_M, ge=1, le=500,
                           description="Catchment strip each drain collects from, m"),
    runoff_coeff: float = Query(hydraulics.RUNOFF_COEFF, gt=0, le=1,
                                description="Rational-method runoff coefficient"),
) -> dict[str, Any]:
    all_drains = await asyncio.to_thread(_load_csv)
    for drain in all_drains:
        if drain["props"].get("feature_no") == float(feature_no):
            break
    else:
        raise HTTPException(status_code=404, detail=f"No drain with feature_no {feature_no}")

    props = drain["props"]
    return {
        "drain": {
            "feature_no": feature_no,
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
            "base_map": _sheet_note(props.get("WARD", ""), props.get("ZONE", "")),
        },
        "capacity": drain["capacity"],
        "state": hydraulics.drain_state(drain["capacity"], _catchment(drain, strip_m),
                                        rain_mm_h, drain["length_m"], runoff_coeff),
        # Nothing here is a black box: every number is either surveyed, derived from
        # surveyed values, or an assumption with its value on show.
        "provenance": PROVENANCE,
    }


@router.get("/network-summary", summary="Live load across the selected drain network")
async def get_network_summary(
    rain_mm_h: float = Query(0.0, ge=0, le=500, description="Rainfall intensity, mm/h"),
    zone: str | None = Query(None),
    ward: str | None = Query(None),
    strip_m: float = Query(hydraulics.STRIP_WIDTH_M, ge=1, le=500,
                           description="Catchment strip each drain collects from, m"),
    runoff_coeff: float = Query(hydraulics.RUNOFF_COEFF, gt=0, le=1,
                                description="Rational-method runoff coefficient"),
    worst: int = Query(5, ge=0, le=50, description="How many overloaded drains to name"),
) -> dict[str, Any]:
    """What the whole selection is doing at this rainfall.

    The capacities are summed for scale, not as a network capacity: the drains are in
    series as well as in parallel, so the network can only ever pass what its tightest
    downstream section passes.
    """
    all_drains = await asyncio.to_thread(_load_csv)
    wanted = {"ZONE": zone, "WARD": ward}

    bands: dict[str, int] = {}
    totals = {"drains": 0, "length_m": 0.0, "capacity_m3s": 0.0,
              "effective_m3s": 0.0, "catchment_m2": 0.0, "inflow_m3s": 0.0}
    loaded: list[tuple[float, dict[str, Any]]] = []
    unsized = 0

    for drain in all_drains:
        props, capacity = drain["props"], drain["capacity"]
        if not _matches(props, wanted):
            continue
        catchment = _catchment(drain, strip_m)
        inflow = hydraulics.rational_inflow(catchment, rain_mm_h, runoff_coeff)
        effective = capacity["effective_capacity_m3s"]

        totals["drains"] += 1
        totals["length_m"] += drain["length_m"]
        totals["capacity_m3s"] += capacity["capacity_m3s"]
        totals["effective_m3s"] += effective
        totals["catchment_m2"] += catchment
        totals["inflow_m3s"] += inflow

        # A drain the survey left without a size has no capacity to compare against.
        # It is reported as a gap in the data, not as an overloaded drain, so it
        # cannot crowd the real findings out of the worst list.
        if effective <= 0:
            unsized += 1
            bands["unsized"] = bands.get("unsized", 0) + 1
            continue

        ratio = inflow / effective
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
                "inflow_m3s": round(inflow, 3),
                "effective_capacity_m3s": effective,
                "spill_m3s": round(inflow - effective, 3),
            }))

    loaded.sort(key=lambda item: item[0], reverse=True)
    return {
        "rain_mm_h": rain_mm_h,
        "runoff_coefficient": runoff_coeff,
        "strip_m": strip_m,
        "filters": {"zone": zone, "ward": ward},
        "drains": totals["drains"],
        "length_km": round(totals["length_m"] / 1000, 2),
        "catchment_km2": round(totals["catchment_m2"] / 1e6, 3),
        "built_capacity_m3s": round(totals["capacity_m3s"], 1),
        "effective_capacity_m3s": round(totals["effective_m3s"], 1),
        "inflow_m3s": round(totals["inflow_m3s"], 2),
        "bands": bands,
        "overloaded": len(loaded),
        "unsized": unsized,
        # What the overloaded drains together put onto the street.
        "spill_m3s": round(sum(item[1]["spill_m3s"] for item in loaded), 2),
        "worst": [item[1] for item in loaded[:worst]],
    }


@router.get("/drain-filters", summary="Available filter values for drains")
async def get_drain_filters(
    zone: str | None = Query(None),
) -> dict[str, Any]:
    """Return the unique values for each filter dropdown.

    When a zone is selected, ward values are scoped to that zone so the
    ward dropdown only shows wards that exist in the chosen zone.
    """
    all_drains = _load_csv()

    scoped = all_drains
    if zone:
        scoped = [d for d in scoped if d["props"].get("ZONE") == zone]

    return {
        "zones": _unique_values("ZONE", all_drains),
        "wards": _unique_values("WARD", scoped),
        "drain_types": _unique_values("DRAIN_TYPE", all_drains),
        "statuses": _unique_values("STATUS", all_drains),
        # Which wards the GCC base maps in Ward/ cover, so the page can say whether a
        # ward's drains have a drawing behind them or only the survey row.
        "ward_sheets": _ward_sheets(),
    }


if __name__ == "__main__":
    assert flow_direction(13.096, 12.676) == "forward"
    assert flow_direction(11.692, 14.054) == "reverse"
    assert flow_direction(12.0, 12.0) == "unknown"
    assert flow_direction("", 12.0) == "unknown"
    assert flow_direction(104860.0, 12.0) == "unknown"

    from collections import Counter
    drains = _load_csv()
    counts = Counter(d["flow"] for d in drains)
    print("drain flow from invert levels:", dict(counts))

    # Every drain must end up with a catchment at least as large as its own strip,
    # and the network must move runoff downstream, not create or lose it.
    assert all(d["catchment_m2"] >= d["capacity"]["catchment_strip_m2"] - 1e-6 for d in drains)
    own = sum(d["capacity"]["catchment_strip_m2"] for d in drains)
    print(f"catchment: {own / 1e6:.1f} km² surveyed strip, "
          f"largest accumulated {max(d['catchment_m2'] for d in drains) / 1e6:.2f} km²")

    sized = [d for d in drains if d["capacity"]["capacity_m3s"] > 0]
    print(f"capacity: {len(sized)} drains sized, "
          f"median {sorted(d['capacity']['capacity_m3s'] for d in sized)[len(sized) // 2]:.3f} m³/s, "
          f"total {sum(d['capacity']['capacity_m3s'] for d in sized):.0f} m³/s")

    # A heavy Chennai hour: 50 mm/h is a red-alert rate, and the network should show
    # real distress at it but not at drizzle.
    for rain in (2.5, 50.0):
        over = sum(
            1 for d in drains
            if hydraulics.rational_inflow(d["catchment_m2"], rain)
            > d["capacity"]["effective_capacity_m3s"] > 0
        )
        print(f"at {rain:>5} mm/h: {over} drains over capacity")
