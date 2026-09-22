"""Cross-check the supplied and downloaded elevation data against the DEMs the app reads.

    python "Cross-Checked Data/data_cross_check.py"

Writes one CSV per check next to this file and prints the headline numbers. Needs the
DEMs fetched by fetch_dem.py (app/data/dem/). The ward cross-check against the GCC
base maps is a separate script, ward_cross_check.py.
"""


import csv
import datetime
import os
import sys
from pathlib import Path

import httpx
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.services import rain_ponding, terrain  # noqa: E402

BOX = (12.85, 13.25, 80.10, 80.35)              # south, north, west, east: the app's Chennai box


def write(name: str, header: list[str], rows: list[list]) -> None:
    with (HERE / name).open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  wrote {name} ({len(rows)} rows)")


def use_dem(name: str) -> None:
    os.environ["DEM_SOURCE"] = name
    terrain._sources = None


def check_dem_points() -> None:
    """dem_points.csv against the two DEMs the app can read."""
    print("1. dem_points.csv vs GEDTM30 and COP30")
    with (HERE / "dem_points.csv").open(encoding="utf-8") as src:
        points = [(float(r["latitude"]), float(r["longitude"]), float(r["elevation_m"]))
                  for r in csv.DictReader(src)]
    heights = {}
    for name in ("GEDTM30", "COP30"):
        use_dem(name)
        heights[name] = [terrain.elevation(lat, lon) for lat, lon, _ in points]

    rows, gaps = [], []
    for i, (lat, lon, given) in enumerate(points):
        g, c = heights["GEDTM30"][i], heights["COP30"][i]
        # Land: both DEMs cover it and COP30 is not its flat 0 m sea.
        land = g is not None and c is not None and c != 0 and g > 1
        if land:
            gaps.append(given - g)
        rows.append([lat, lon, given,
                     None if g is None else round(g, 2), None if c is None else round(c, 2),
                     None if g is None else round(given - g, 2), land])
    write("dem_points_vs_dem.csv",
          ["latitude", "longitude", "csv_elevation_m", "gedtm30_m", "cop30_m",
           "csv_minus_gedtm30_m", "on_land_in_app_box"], rows)
    gaps = np.array(gaps)
    print(f"  {len(points)} points, {len(gaps)} on land inside the DEM; CSV minus GEDTM30: "
          f"median {np.median(gaps):.1f} m, std {gaps.std():.1f} m")
    print(f"  CSV range {min(p[2] for p in points)} to {max(p[2] for p in points)} m")


def check_dem_sources() -> None:
    """GEDTM30 (bare earth) against COP30 (surface) over the same box."""
    print("2. GEDTM30 vs COP30")
    grids = {}
    for name in ("GEDTM30", "COP30"):
        use_dem(name)
        grids[name] = terrain.grid(*BOX)["grid"]
    g, c = grids["GEDTM30"], grids["COP30"]
    land = np.isfinite(g) & np.isfinite(c) & (c != 0)
    diff = (c - g)[land]
    rows = [["cells_compared", int(land.sum())],
            ["median_cop30_minus_gedtm30_m", round(float(np.median(diff)), 2)],
            ["p5_m", round(float(np.percentile(diff, 5)), 2)],
            ["p95_m", round(float(np.percentile(diff, 95)), 2)],
            ["gedtm30_cells_below_minus_2m", int((g < -2).sum())],
            ["cop30_cells_below_minus_2m", int((c < -2).sum())]]
    write("dem_sources_gedtm30_vs_cop30.csv", ["measure", "value"], rows)
    print(f"  median COP30 - GEDTM30 on land: {np.median(diff):.2f} m "
          f"(p5 {np.percentile(diff, 5):.2f}, p95 {np.percentile(diff, 95):.2f})")



if __name__ == "__main__":
    check_dem_points()
    check_dem_sources()
    use_dem("")
