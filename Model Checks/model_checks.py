"""Re-run the model-tuning checks behind the rain-on-streets model.

    python "Model Checks/model_checks.py"

These are not comparisons against official data (those live in Cross-Checked Data/);
they are the runs that set the model's knobs: how much the DEM is smoothed, how deep
waterways are burned in, and whether the two Open-Meteo rain feeds agree. Writes one
CSV per check next to this file. Needs the DEMs from fetch_dem.py and the street and
waterway caches in app/data/. The rain feed check calls Open-Meteo, so its numbers are
a snapshot of when it ran.
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


# Places the rain model has to get right: two real water bodies it must keep, and a
# T. Nagar road where DEM noise once put 4 m of "water".
PLACES = [("Venkatanarayana Road, T. Nagar", 13.03431, 80.23778),
          ("Pallikaranai marsh", 12.9465, 80.2315),
          ("Porur lake", 13.0326, 80.1526),
          ("5th Street, Kilpauk (Otteri Nullah)", 13.08786, 80.22918)]


def _depths(rain_mm: float, drain_mm_h: float = 0.0) -> tuple[dict, dict]:
    """Model built fresh with the current knobs; peak depth at each place, and totals."""
    rain_ponding._model = None
    rain_ponding._streets = None
    model = rain_ponding.load(BOX[0], BOX[1], BOX[2], BOX[3])
    # All the rain in one 5 minute step and time for it to arrive: the fill a storm
    # of that total gives, with the drain rate taken over the 3 hour window.
    steps = [rain_mm] + [0.0] * 35
    levels = rain_ponding.simulate(model, steps, 0.75, drain_mm_h, 5.0)
    depth = rain_ponding.depth_grid(model, levels.max(axis=0))
    at = {}
    for name, lat, lon in PLACES:
        point = rain_ponding.at(model, lat, lon, levels, 5.0)
        at[name] = None if point is None else point["peak_depth_cm"]
    totals = {"hollows": int((model["size"][1:model["sea"]] > 0).sum()),
              "km2_over_5cm": round(float((depth >= 0.05).sum() * model["cell_area_m2"] / 1e6), 1)}
    return at, totals


def check_smoothing() -> None:
    """DEM median filter size: removes building pits, must keep the real lakes."""
    print("3. DEM smoothing (median filter), 100 mm, drains full")
    use_dem("GEDTM30")
    saved = rain_ponding.SMOOTH_CELLS
    rows = []
    for size in (1, 3, 5, 7):
        rain_ponding.SMOOTH_CELLS = size
        at, totals = _depths(100.0)
        rows.append([size, totals["hollows"], totals["km2_over_5cm"], *[at[name] for name, _, _ in PLACES]])
        print(f"  {size} x {size}: {totals['hollows']} hollows, " +
              ", ".join(f"{n.split(',')[0]} {at[n]} cm" for n, _, _ in PLACES))
    rain_ponding.SMOOTH_CELLS = saved
    write("dem_smoothing_check.csv",
          ["median_cells", "hollows", "km2_over_5cm", *[f"{n} (cm)" for n, _, _ in PLACES]], rows)


def check_burning() -> None:
    """Stream burning depth: canals must drain, not become lakes beside the street."""
    print("4. Waterway burning, 60 mm, drains 20 mm/h")
    use_dem("GEDTM30")
    saved = rain_ponding.BURN_M
    rows = []
    for burn in (0.0, 2.0, 4.0):
        rain_ponding.BURN_M = burn
        at, totals = _depths(60.0, 20.0)
        rows.append([burn, totals["hollows"], totals["km2_over_5cm"], *[at[name] for name, _, _ in PLACES]])
        print(f"  burn {burn} m: {totals['hollows']} hollows, Kilpauk 5th Street "
              f"{at[PLACES[3][0]]} cm")
    rain_ponding.BURN_M = saved
    write("waterway_burning_check.csv",
          ["burn_m", "hollows", "km2_over_5cm", *[f"{n} (cm)" for n, _, _ in PLACES]], rows)


def check_rain_feeds() -> None:
    """The 15 minute nowcast and the hourly rain-movement feed, side by side."""
    print("5. Rain feeds: Open-Meteo 15-minute vs hourly (live snapshot)")
    lat, lon = 13.0827, 80.2707
    url = "https://api.open-meteo.com/v1/forecast"
    quarter = httpx.get(url, params=dict(latitude=lat, longitude=lon, minutely_15="precipitation",
                                         forecast_minutely_15=16, timezone="auto"), timeout=30).json()
    hourly = httpx.get(url, params=dict(latitude=lat, longitude=lon, hourly="precipitation",
                                        forecast_days=2, timezone="auto"), timeout=30).json()
    by_hour: dict[str, float] = {}
    for time, mm in zip(quarter["minutely_15"]["time"], quarter["minutely_15"]["precipitation"]):
        by_hour[time[:13]] = by_hour.get(time[:13], 0.0) + (mm or 0.0)
    hours = dict(zip((t[:13] for t in hourly["hourly"]["time"]), hourly["hourly"]["precipitation"]))
    taken = datetime.datetime.now().isoformat(timespec="minutes")
    rows = [[taken, hour + ":00", round(total, 2), hours.get(hour)] for hour, total in by_hour.items()]
    write("rain_feeds_15min_vs_hourly.csv",
          ["checked_at", "hour", "sum_of_15min_mm", "hourly_mm"], rows)
    for row in rows:
        print(f"  {row[1]}: 15-min sum {row[2]} mm, hourly {row[3]} mm")


if __name__ == "__main__":
    check_smoothing()
    check_burning()
    check_rain_feeds()
    use_dem("")
