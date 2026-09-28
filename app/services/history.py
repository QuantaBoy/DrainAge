"""Chennai's flood record, on the flood model's own grid.

Four layers from the historical study (the "Historical Flood" folder):

  * flood hazard zones, Very Low to Very High, 7,453 polygons;
  * flood extents for the 5, 10, 25, 50, 100 and 200 year return periods;
  * the 753 places that flooded in December 2015;
  * 192 inundation points with a recorded depth.

They serve two purposes. They are the yardstick the physics model is checked against
(scripts/validate_history.py), and they give every street its flood history - how
often it floods by the record and whether it went under in 2015 - which the forecast
reports beside the water it predicts, so the two can be read together.
"""

import csv
from typing import Any

import numpy as np
from scipy.spatial import cKDTree
from skimage.draw import polygon as fill_polygon

from app import config
from app.services import diskcache, terrain

HAZARD_CLASSES = ("Very Low", "Low", "Moderate", "High", "Very High")
RETURN_PERIODS = {5: "Chennai Flows 5 Year Return Period.csv",
                  10: "Chennai Flows 10 Years Return Period.csv",
                  25: "Chennai Flows 25 Year Return Period.csv",
                  50: "Chennai Flows 50 Years Return Period.csv",
                  100: "Chennai Flows 100 Years Return Period.csv",
                  200: "Chennai Flows 200 Years Return Period.csv"}
HAZARD_FILE = "Chennai Flood Hazard Zones Map.csv"
POINTS_2015_FILE = "Chennai Flooding Points in 2015.csv"
DEPTH_FILE = "Chennai Inundation Points with Depth of Inundation.csv"

# A street counts as "flooded in 2015" if a recorded 2015 point is this close: the
# points mark a street or a locality, not a surveyed spot on it.
NEAR_2015_M = 250.0

csv.field_size_limit(1 << 30)


def available() -> bool:
    return (config.HISTORY_DIR / HAZARD_FILE).exists()


def _rows(name: str) -> list[dict[str, str]]:
    with (config.HISTORY_DIR / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _rings(wkt: str) -> list[np.ndarray]:
    """Outer rings of a POLYGON or MULTIPOLYGON as (lon, lat) arrays. Holes are rare in
    these layers and small beside a 30 m cell, so a ring is filled whole."""
    # Walk the brackets: a ring is text at the deepest level (2 in a POLYGON, 3 in a
    # MULTIPOLYGON), and the first ring of each polygon is its outer boundary.
    deepest = 3 if wkt.lstrip().upper().startswith("MULTI") else 2
    rings, depth, start, ring_no = [], 0, 0, 0
    for i, ch in enumerate(wkt):
        if ch == "(":
            depth += 1
            if depth == deepest - 1:
                ring_no = 0
            elif depth == deepest:
                start = i + 1
        elif ch == ")":
            if depth == deepest:
                if ring_no == 0:
                    xy = np.array([[float(v) for v in pair.split()[:2]]
                                   for pair in wkt[start:i].split(",") if len(pair.split()) >= 2])
                    if len(xy) >= 3:
                        rings.append(xy)
                ring_no += 1
            depth -= 1
    return rings


def _paint(grid: np.ndarray, model: dict[str, Any], wkt: str, value: int) -> None:
    for ring in _rings(wkt):
        rows = (model["north"] - ring[:, 1]) / model["lat_step"]
        cols = (ring[:, 0] - model["west"]) / model["lon_step"]
        rr, cc = fill_polygon(rows, cols, shape=grid.shape)
        grid[rr, cc] = value


def _build_grids(model: dict[str, Any]) -> dict[str, np.ndarray]:
    shape = model["surface"].shape
    hazard = np.zeros(shape, dtype=np.int8)           # 0 none, 1 Very Low .. 5 Very High
    rows = _rows(HAZARD_FILE)
    rank = {name.lower(): i + 1 for i, name in enumerate(HAZARD_CLASSES)}
    # Painted low to high, so where zones overlap the higher hazard wins.
    for row in sorted(rows, key=lambda r: rank.get(r["CATEGORY"].strip().lower(), 0)):
        level = rank.get(row["CATEGORY"].strip().lower())
        if level:
            _paint(hazard, model, row["Geometry_WKT"], level)

    period = np.zeros(shape, dtype=np.int16)          # smallest return period covering
    for years in sorted(RETURN_PERIODS, reverse=True):  # 200 first, 5 last: smallest wins
        path = config.HISTORY_DIR / RETURN_PERIODS[years]
        if path.exists():
            for row in _rows(RETURN_PERIODS[years]):
                _paint(period, model, row["Geometry_WKT"], years)
    return {"hazard": hazard, "period": period}


def grids(model: dict[str, Any]) -> dict[str, np.ndarray] | None:
    """The hazard class and the most frequent return period covering each grid cell,
    built once and kept on disk; None without the historical files."""
    if not available():
        return None
    inputs = [config.HISTORY_DIR / HAZARD_FILE,
              *(config.HISTORY_DIR / name for name in RETURN_PERIODS.values()),
              *diskcache.sources(terrain)]
    from app.services import surface
    inputs += surface.model_inputs()
    import pathlib
    inputs.append(pathlib.Path(__file__))
    return diskcache.cached("history", inputs, lambda: _build_grids(model))


def points_2015() -> dict[str, np.ndarray] | None:
    """The places recorded flooded in December 2015: lat, lon and name."""
    if not (config.HISTORY_DIR / POINTS_2015_FILE).exists():
        return None
    rows = _rows(POINTS_2015_FILE)
    return {"lat": np.array([float(r["Latitude"]) for r in rows]),
            "lon": np.array([float(r["Longitude"]) for r in rows]),
            "name": [r["Name"].strip() for r in rows],
            "zone": [r["ZONE"].strip() for r in rows]}


def depth_points() -> dict[str, np.ndarray] | None:
    """Inundation points with a recorded depth. The source does not state the unit;
    the values read as centimetres (road-side water 5-6, subways 35-60) but are not
    consistent with every remark, so they are compared by rank, not by value."""
    if not (config.HISTORY_DIR / DEPTH_FILE).exists():
        return None
    rows = [r for r in _rows(DEPTH_FILE) if r["DEPTH"].strip()]
    return {"lat": np.array([float(r["Latitude"]) for r in rows]),
            "lon": np.array([float(r["Longitude"]) for r in rows]),
            "depth": np.array([float(r["DEPTH"]) for r in rows]),
            "remark": [r["F_REMARKS"].strip() for r in rows]}


def metres_xy(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Points in local metres, for distance queries over the city."""
    return np.c_[lon * terrain.M_PER_DEG * np.cos(np.radians(13.05)), lat * terrain.M_PER_DEG]


_signature: dict[str, Any] = {}


def street_signature(model: dict[str, Any]) -> dict[str, np.ndarray] | None:
    """For every street vertex (surface.street_cells order): its hazard class, the most
    frequent return period that floods it, and whether it went under in 2015."""
    if "hazard" in _signature:
        return _signature
    layers = grids(model)
    if layers is None:
        return None
    from app.services import surface

    _, coords, _, cell, _ = surface.street_cells(model)
    inside = cell >= 0
    hazard = np.zeros(cell.size, dtype=np.int8)
    period = np.zeros(cell.size, dtype=np.int16)
    hazard[inside] = layers["hazard"].ravel()[cell[inside]]
    period[inside] = layers["period"].ravel()[cell[inside]]
    flooded_2015 = np.zeros(cell.size, dtype=bool)
    pts = points_2015()
    if pts is not None:
        tree = cKDTree(metres_xy(pts["lat"], pts["lon"]))
        dist, _ = tree.query(metres_xy(coords[:, 1], coords[:, 0]), distance_upper_bound=NEAR_2015_M)
        flooded_2015 = np.isfinite(dist)
    _signature.update({"hazard": hazard, "period": period, "flooded_2015": flooded_2015})
    return _signature


# A flooded street is a "recorded hotspot" when the record agrees: it went under in
# 2015, lies in a flood extent that returns at least every 25 years, or sits in a High
# or Very High hazard zone. It is context for the reader, not a sharper forecast:
# checked on the record (30 mm/h), 40% of modelled wet streets are within 250 m of a
# 2015 flood site against 18% of dry ones, and the extent or hazard tag lifts that
# only to 41-44%. The model's own agreement with the record is the stronger signal.
FREQUENT_YEARS = 25
HIGH_HAZARD = 4


def recorded(hazard: int, period: int, flooded_2015: bool) -> bool:
    return bool(flooded_2015 or 0 < period <= FREQUENT_YEARS or hazard >= HIGH_HAZARD)


_points_tree: dict[str, Any] = {}


def label_stretches(model: dict[str, Any], stretches: list[dict[str, Any]]) -> None:
    """Put each flooded stretch's history on it: the highest hazard class and the most
    frequent return period along it, whether it went under in 2015, and whether the
    record backs the forecast ("recorded") or the model alone sees it."""
    layers = grids(model)
    if layers is None or not stretches:
        return
    if "tree" not in _points_tree:
        pts = points_2015()
        _points_tree["tree"] = cKDTree(metres_xy(pts["lat"], pts["lon"])) if pts else None
    coords = [f["geometry"]["coordinates"] for f in stretches]
    owner = np.repeat(np.arange(len(coords)), [len(c) for c in coords])
    xy = np.asarray([p for c in coords for p in c], dtype=np.float64).reshape(-1, 2)
    rows_n, cols_n = layers["hazard"].shape
    rows = np.floor((model["north"] - xy[:, 1]) / model["lat_step"]).astype(np.int64)
    cols = np.floor((xy[:, 0] - model["west"]) / model["lon_step"]).astype(np.int64)
    inside = (rows >= 0) & (rows < rows_n) & (cols >= 0) & (cols < cols_n)
    hazard = np.zeros(len(xy), dtype=np.int64)
    period = np.zeros(len(xy), dtype=np.int64)
    hazard[inside] = layers["hazard"][rows[inside], cols[inside]]
    period[inside] = layers["period"][rows[inside], cols[inside]]
    near = np.zeros(len(xy), dtype=bool)
    if _points_tree["tree"] is not None:
        dist, _ = _points_tree["tree"].query(metres_xy(xy[:, 1], xy[:, 0]), distance_upper_bound=NEAR_2015_M)
        near = np.isfinite(dist)

    n = len(coords)
    top_hazard = np.zeros(n, dtype=np.int64)
    np.maximum.at(top_hazard, owner, hazard)
    most_often = np.full(n, 10_000, dtype=np.int64)
    np.minimum.at(most_often, owner, np.where(period > 0, period, 10_000))
    most_often[most_often == 10_000] = 0
    in_2015 = np.zeros(n, dtype=bool)
    np.logical_or.at(in_2015, owner, near)
    for feature, h, t, f in zip(stretches, top_hazard.tolist(), most_often.tolist(), in_2015.tolist()):
        feature["properties"]["history"] = describe(h, t, f)
        feature["properties"]["recorded"] = recorded(h, t, f)


def validation_summary() -> dict[str, Any] | None:
    """The model's checked accuracy against the record, from the last validation run
    (scripts/validate_history.py), at the settings in use."""
    path = config.DATA_DIR / "validation.json"
    if not path.exists():
        return None
    import json

    report = json.loads(path.read_text(encoding="utf-8"))
    in_use = report["settings_in_use"]
    runs = {r["rain_mm_h"]: r for r in report["runs"]
            if r["runoff_coeff"] == in_use["runoff_coeff"] and r["drain_condition"] == in_use["drain_condition"]}
    return {"storms": {str(int(rate)): {key: run[key] for key in (
                "hit_rate_2015", "chance_rate", "skill_holdout", "depth_rank_correlation",
                "hazard_enrichment")} for rate, run in sorted(runs.items())},
            "points_2015": 753, "depth_points": 192}


def describe(hazard: int, period: int, flooded_2015: bool) -> dict[str, Any]:
    """A street's history in words the page can show as it is."""
    return {
        "hazard": HAZARD_CLASSES[hazard - 1] if hazard else None,
        "return_period_years": int(period) or None,
        "flooded_2015": bool(flooded_2015),
    }
