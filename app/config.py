"""Settings shared across the application: where things are, and what the city is.

Every path, the study area and the thresholds the forecast reports against are defined
here once. Modules import them from here rather than from each other, so a route never
has to reach into another route for a constant.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ─── Paths ───────────────────────────────────────────────────────────────────────

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
CACHE_DIR = DATA_DIR / "cache"

load_dotenv(ROOT_DIR / ".env")

# Supplied survey data, kept as it was received.
SURVEY_DIR = ROOT_DIR / "Cross-Checked Data"
DRAIN_SURVEY_CSV = SURVEY_DIR / "gcc_storm_water_drains (1).csv"
WARD_MAP_DIR = ROOT_DIR / "Ward"

# Derived data, produced by the tools in scripts/.
WARD_SHEETS_JSON = DATA_DIR / "ward_sheets.json"        # title blocks of the ward maps
WARD_POINTS_JSON = DATA_DIR / "ward_points.json"        # scripts/digitise_wards.py
BUILDINGS_NPZ = DATA_DIR / "building_fraction.npz"      # scripts/fetch_buildings.py
WATER_BODIES_NPZ = DATA_DIR / "water_bodies.npz"        # scripts/fetch_buildings.py water
STREETS_GEOJSON = DATA_DIR / "chennai_streets.geojson"  # cached from Overpass
WATERWAYS_GEOJSON = DATA_DIR / "chennai_waterways.geojson"
CERT_DIR = ROOT_DIR / "certs"                           # scripts/make_cert.py

# ─── Study area ──────────────────────────────────────────────────────────────────

# Greater Chennai: (south, west, north, east), degrees. The streets, the DEM grid and
# the flood model all use this one box.
CHENNAI_BBOX = (12.85, 80.10, 13.25, 80.35)
CHENNAI_CENTRE = (13.0827, 80.2707)        # Chennai Central


def in_chennai(lat: float, lon: float) -> bool:
    south, west, north, east = CHENNAI_BBOX
    return south <= lat <= north and west <= lon <= east


# ─── Forecast ────────────────────────────────────────────────────────────────────

NOWCAST_HOURS = 3             # the 0-3 h window the problem statement asks for
FEED_STEP_MINUTES = 15        # the rainfall feed's resolution
REPORT_STEP_MINUTES = 5       # the flood forecast reports every 5 minutes
REPORT_DEPTH_M = 0.05         # shallower is wet tarmac, not a flood
IMPASSABLE_DEPTH_M = 0.30     # deeper, a car is in trouble

# ─── Services ────────────────────────────────────────────────────────────────────

# Overpass and Nominatim ask callers to identify themselves.
USER_AGENT = "SIH26085-flood-dashboard/1.0"
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
# Which DEM the terrain reads: "GEDTM30", "COP30", ... or "tiles"; unset, the best one
# downloaded (app/services/terrain.py).
DEM_SOURCE = os.getenv("DEM_SOURCE", "").strip()
# Development server auto-reload; off unless asked for.
RELOAD = os.getenv("RELOAD", "").lower() in {"1", "true", "yes"}
