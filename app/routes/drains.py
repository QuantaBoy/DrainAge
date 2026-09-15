"""Storm water drains: GCC drain network from CSV, served as GeoJSON.

The CSV contains 10 000+ drain segments with WKT LineString geometries and
rich metadata (zone, ward, type, status, dimensions, material, obstacles).
Data is parsed once on first request and cached in memory.
"""

import asyncio
import csv
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(prefix="/data-collection", tags=["Drains"])

CSV_PATH = Path(__file__).resolve().parent.parent.parent / "gcc_storm_water_drains (1).csv"

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

# In-memory cache: list of (properties-dict, coordinates-list) tuples.
_cache: list[tuple[dict[str, Any], list[list[float]]]] | None = None


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


def _load_csv() -> list[tuple[dict[str, Any], list[list[float]]]]:
    """Parse the CSV once and return a list of (properties, coordinates)."""
    global _cache
    if _cache is not None:
        return _cache

    rows: list[tuple[dict[str, Any], list[list[float]]]] = []
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
            # The survey writes some wards as N69 and others as N069.
            if re.fullmatch(r"N\d{1,2}", props["WARD"]):
                props["WARD"] = f"N{int(props['WARD'][1:]):03d}"
            rows.append((props, coords))

    _cache = rows
    return _cache


def flow_direction(invert_start: Any, invert_end: Any) -> str:
    """Which way water runs in one drain, from its own surveyed invert levels.

    "forward": start invert higher, water runs start to end (the drawn direction).
    "reverse": end invert higher, a reverse-gradient drain; water runs back to the start.
    "unknown": an invert is missing, out of range, or both are equal.
    """
    valid = all(isinstance(v, float) and INVERT_RANGE[0] <= v <= INVERT_RANGE[1]
                for v in (invert_start, invert_end))
    if not valid or invert_start == invert_end:
        return "unknown"
    return "forward" if invert_start > invert_end else "reverse"


def _unique_values(field: str, rows: list[tuple[dict, list]]) -> list[str]:
    """Sorted unique non-empty values for a property field."""
    return sorted({props[field] for props, _ in rows if props.get(field)})


@router.get("/drains", summary="GCC storm water drains as GeoJSON")
async def get_drains(
    zone: str | None = Query(None, description="Filter by zone, e.g. N07"),
    ward: str | None = Query(None, description="Filter by ward, e.g. N082"),
    drain_type: str | None = Query(None, description="Filter by DRAIN_TYPE"),
    status: str | None = Query(None, description="Filter by STATUS"),
) -> dict[str, Any]:
    all_rows = await asyncio.to_thread(_load_csv)
    wanted = {"ZONE": zone, "WARD": ward, "DRAIN_TYPE": drain_type, "STATUS": status}

    features = []
    for props, coords in all_rows:
        if not all(props.get(field) == value for field, value in wanted.items() if value):
            continue
        flow = flow_direction(props.get("INVERT_SP"), props.get("INVERT_EP"))
        features.append({
            "type": "Feature",
            # Coordinates run the way the water runs; the CSV's start/end stay in
            # INVERT_SP/INVERT_EP.
            "geometry": {
                "type": "LineString",
                "coordinates": coords[::-1] if flow == "reverse" else coords,
            },
            "properties": {**props, "FLOW": flow},
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "total": len(features),
    }


@router.get("/drain-filters", summary="Available filter values for drains")
async def get_drain_filters(
    zone: str | None = Query(None),
) -> dict[str, Any]:
    """Return the unique values for each filter dropdown.

    When a zone is selected, ward values are scoped to that zone so the
    ward dropdown only shows wards that exist in the chosen zone.
    """
    all_rows = _load_csv()

    scoped = all_rows
    if zone:
        scoped = [(p, c) for p, c in scoped if p.get("ZONE") == zone]

    return {
        "zones": _unique_values("ZONE", all_rows),
        "wards": _unique_values("WARD", scoped),
        "drain_types": _unique_values("DRAIN_TYPE", all_rows),
        "statuses": _unique_values("STATUS", all_rows),
    }


if __name__ == "__main__":
    assert flow_direction(13.096, 12.676) == "forward"
    assert flow_direction(11.692, 14.054) == "reverse"
    assert flow_direction(12.0, 12.0) == "unknown"
    assert flow_direction("", 12.0) == "unknown"
    assert flow_direction(104860.0, 12.0) == "unknown"
    from collections import Counter
    counts = Counter(flow_direction(p.get("INVERT_SP"), p.get("INVERT_EP")) for p, _ in _load_csv())
    print("drain flow from invert levels:", dict(counts))
