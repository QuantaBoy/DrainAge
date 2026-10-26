"""Compare the supplied elevation check points with the terrain model the forecast
runs on, point by point, for the Resources page.

    python scripts/make_dem_check.py

Writes app/data/dem_check.json: every check point that falls on the DEM, with both
heights and their difference. The supplied points read about 92 m below the DEM
everywhere on land (Cross-Checked Data/README.md): a steady offset between two height
references, not noise, which is what confirms the DEM's datum.
"""

import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.services import terrain  # noqa: E402


def main() -> None:
    rows = []
    with open(config.SURVEY_DIR / "dem_points (1).csv", newline="", encoding="utf-8") as f:
        for point in csv.DictReader(f):
            lat, lon = float(point["latitude"]), float(point["longitude"])
            dem = terrain.elevation(lat, lon)
            if dem is None:               # off the DEM, or over water
                continue
            given = float(point["elevation_m"])
            rows.append([round(lat, 5), round(lon, 5), given, round(dem, 1), round(dem - given, 1)])

    gaps = [r[4] for r in rows]
    out = {
        "source": terrain.source_info().get("name", "DEM"),
        "points_supplied": sum(1 for _ in open(config.SURVEY_DIR / "dem_points (1).csv")) - 1,
        "points_on_dem": len(rows),
        "median_gap_m": round(statistics.median(gaps), 1),
        # The middle 80 %: a few points on the shoreline compare sea-floor depth with
        # the DEM's flat sea and would swamp a standard deviation.
        "middle_80_m": [round(q, 1) for q in statistics.quantiles(gaps, n=10)[::8]],
        "columns": ["latitude", "longitude", "check_point_m", "dem_m", "gap_m"],
        "rows": rows,
    }
    (config.DATA_DIR / "dem_check.json").write_text(json.dumps(out), encoding="utf-8")
    print(f"{len(rows)} points on the DEM; gap median {out['median_gap_m']} m, middle 80 % {out['middle_80_m']}")


if __name__ == "__main__":
    main()
