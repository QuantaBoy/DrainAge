"""Every resource the model is built on, checked on this server: the file is there, its
size, and how many records it holds - counted from the file, not written down.

The Technology page lists these so anyone can see the data is real and in use.
"""

import csv
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app import config

csv.field_size_limit(1 << 30)


def _csv_rows(*paths: Path) -> int:
    total = 0
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            total += sum(1 for _ in csv.DictReader(handle))
    return total


def _features(path: Path) -> int:
    return len(json.loads(path.read_text(encoding="utf-8"))["features"])


def _ward_points(path: Path) -> str:
    sheets = json.loads(path.read_text(encoding="utf-8"))["sheets"]
    manholes = sum(len(s.get("points", [])) for s in sheets)
    drains = sum(len(s.get("network", {}).get("edges", [])) for s in sheets)
    return f"{len(sheets)} sheets · {manholes:,} manholes · {drains:,} drains"


def _grid_cells(path: Path) -> str:
    with np.load(path) as data:
        grid = data[data.files[0]]
    return f"{grid.shape[1]} x {grid.shape[0]} cells"


D, S, H = config.DATA_DIR, config.SURVEY_DIR, config.HISTORY_DIR
CHANNELS = ("macro_drains.csv", "micro_drains.csv", "buckingham_canal.csv",
            "krishna_water_canal.csv", "rivers_streams.csv")

# (name, provider, link, used for, files, what to count, unit)
RESOURCES: list[tuple[str, str, str | None, str, Callable[[], list[Path]], Callable | None, str]] = [
    ("Storm water drain survey", "Greater Chennai Corporation",
     "https://data.opencity.in/dataset/chennai-stormwater-drain-swd-maps",
     "Every drain's section, invert levels, material and condition",
     lambda: [config.DRAIN_SURVEY_CSV], lambda p: _csv_rows(*p), "drains"),
    ("Ward base-map sheets", "GCC / SECON-JBA survey, Dec 2020", None,
     "Manhole-by-manhole drawings of 92 wards, read by OCR",
     lambda: sorted(config.WARD_MAP_DIR.glob("*.pdf")), len, "PDF sheets"),
    ("Digitised ward network", "Read from the ward sheets (scripts/digitise_wards.py)", None,
     "Drains and manholes with measured sections and levels",
     lambda: [D / "ward_points.json"], lambda p: _ward_points(p[0]), ""),
    ("Ward and zone index", "Title blocks of the ward sheets", None,
     "Which zone each ward belongs to",
     lambda: [D / "ward_sheets.json"], lambda p: len(json.loads(p[0].read_text(encoding="utf-8"))["sheets"]), "wards"),
    ("Terrain model (GEDTM30)", "OpenGeoHub via OpenTopography", "https://portal.opentopography.org/datasets",
     "Ground height, 30 m bare earth: where water runs and stands",
     lambda: [D / "dem" / "GEDTM30.tif"], len, "raster"),
    ("Terrain cross-check (COP30 + tiles)", "ESA Copernicus via OpenTopography", "https://portal.opentopography.org/datasets",
     "Second terrain model the first is checked against",
     lambda: [D / "dem" / "COP30.tif", *sorted(D.glob("Copernicus_DSM_COG_10_*_DEM.tif"))], len, "rasters"),
    ("Elevation check points", "Supplied survey points", None,
     "Confirms the terrain's height datum (mean sea level)",
     lambda: [S / "dem_points (1).csv"], lambda p: _csv_rows(*p), "points"),
    ("Basin channel layer", "Chennai Basin Drainage Maps (OpenCity)",
     "https://data.opencity.in/dataset/chennai-basin-drainage-maps",
     "Named canals, rivers and drains; river basin of each flooded street",
     lambda: [S / name for name in CHANNELS], lambda p: _csv_rows(*p), "channel lines"),
    ("Streets", "OpenStreetMap (Overpass API)", "https://www.openstreetmap.org/",
     "The roads depth is reported on and routes are found over",
     lambda: [D / "chennai_streets.geojson"], lambda p: _features(p[0]), "roads"),
    ("Waterways", "OpenStreetMap (Overpass API)", "https://www.openstreetmap.org/",
     "Rivers and canals cut into the terrain so embankments are not dams",
     lambda: [D / "chennai_waterways.geojson"], lambda p: _features(p[0]), "waterways"),
    ("Building cover", "OpenStreetMap footprints", "https://www.openstreetmap.org/",
     "Built share of each cell: water stands only between buildings",
     lambda: [D / "building_fraction.npz"], lambda p: _grid_cells(p[0]), ""),
    ("Water bodies", "OpenStreetMap", "https://www.openstreetmap.org/",
     "Lakes and tanks kept as storage, never filled as noise",
     lambda: [D / "water_bodies.npz"], lambda p: _grid_cells(p[0]), ""),
    ("Flood hazard zones", "Chennai flood study (supplied)", None,
     "Flood history of every street; the model's accuracy check",
     lambda: [H / "Chennai Flood Hazard Zones Map.csv"], lambda p: _csv_rows(*p), "zones"),
    ("Flood extents, 5 to 200 year", "Chennai flood study (supplied)", None,
     "How often each street floods by the record",
     lambda: sorted(H.glob("Chennai Flows *.csv")), lambda p: _csv_rows(*p), "extent polygons"),
    ("December 2015 flooding points", "Chennai flood study (supplied)", None,
     "The places the model is checked against",
     lambda: [H / "Chennai Flooding Points in 2015.csv"], lambda p: _csv_rows(*p), "places"),
    ("Inundation depth points", "Chennai flood study (supplied)", None,
     "Recorded depths, compared by rank with the model",
     lambda: [H / "Chennai Inundation Points with Depth of Inundation.csv"], lambda p: _csv_rows(*p), "points"),
]

SERVICES = [
    ("Open-Meteo", "https://open-meteo.com/", "15-minute rainfall for the next 3 hours; hourly forecast"),
    ("OpenWeather", "https://openweathermap.org/", "Current weather and the weather map tiles"),
    ("OpenStreetMap Overpass", "https://overpass-api.de/", "Roads and waterways"),
    ("OpenStreetMap Nominatim", "https://nominatim.openstreetmap.org/", "Place search for routes"),
    ("OpenTopography", "https://opentopography.org/", "Terrain model download"),
]

_cache: dict[str, Any] = {}
_lock = threading.Lock()


def check() -> dict[str, Any]:
    """Every resource as found on this server, counted once and kept."""
    with _lock:
        if not _cache:
            began = time.perf_counter()
            items = []
            for name, provider, link, used_for, files, count, unit in RESOURCES:
                paths = files()
                present = [p for p in paths if p.exists()]
                item = {"name": name, "provider": provider, "link": link, "used_for": used_for,
                        "files": len(paths), "present": len(present) == len(paths) and bool(paths),
                        "size_mb": round(sum(p.stat().st_size for p in present) / 2**20, 1),
                        "size_bytes": sum(p.stat().st_size for p in present)}
                if present and count is not None:
                    try:
                        value = count(present)
                        item["records"] = f"{value:,} {unit}".strip() if isinstance(value, int) else value
                    except (OSError, ValueError, KeyError) as exc:
                        item["records"] = f"unreadable: {exc}"
                items.append(item)
            _cache.update({
                "resources": items,
                "services": [{"name": n, "link": u, "used_for": w} for n, u, w in SERVICES],
                "present": sum(1 for i in items if i["present"]),
                "total": len(items),
                "total_mb": round(sum(i["size_mb"] for i in items), 1),
                "checked_in_s": round(time.perf_counter() - began, 1),
            })
    return _cache
