"""Street-level flood nowcast: rainfall, drains and terrain coupled end to end.

The chain this route closes, in one place:

  rainfall nowcast (15 minute steps, 0-3 h ahead)
      -> runoff onto each drain's accumulated catchment
      -> what the drain can carry, from its surveyed section (app/services/hydraulics.py)
      -> what it cannot carry surcharges at its outlet manhole
      -> that volume is poured onto the ground there (app/terrain.py)
      -> a depth in centimetres, at a place, at a time.

Water is carried between steps rather than recomputed each time: once a street is
under water it stays under water until the drain below it has spare capacity again,
which is the difference between a rainfall map and a flood forecast.
"""

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.services import hydraulics, terrain
from app.routes.drains import _catchment, _load_csv, _matches

router = APIRouter(prefix="/data-collection", tags=["Flood"])

NOWCAST_URL = "https://api.open-meteo.com/v1/forecast"

# The nowcast window the problem asks for, at the resolution the feed gives.
NOWCAST_HOURS = 3
STEP_MINUTES = 15
STEPS = NOWCAST_HOURS * 60 // STEP_MINUTES

# Depths below this are wet tarmac; above it, a car is in trouble.
REPORT_DEPTH_M = 0.05
IMPASSABLE_DEPTH_M = 0.30

# A basin is read from the DEM once per manhole and filled at every step.
_basins: dict[int, dict[str, Any] | None] = {}


async def rainfall_nowcast(lat: float, lon: float) -> dict[str, Any]:
    """Rainfall for the next three hours, in 15 minute steps.

    Open-Meteo's 15 minute product is the stand-in for a Doppler radar nowcast: it is
    free, global and updates hourly. A DWR feed would replace this function and
    nothing else, since everything downstream takes millimetres per hour on a clock.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(NOWCAST_URL, params={
            "latitude": lat,
            "longitude": lon,
            "minutely_15": "precipitation",
            "forecast_minutely_15": STEPS,
            "timezone": "auto",
        })
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Rainfall nowcast unavailable")

    body = response.json()
    block = body.get("minutely_15", {})
    times = block.get("time", [])[:STEPS]
    # The feed reports millimetres fallen in each 15 minute step; the hydraulics work
    # in millimetres per hour.
    rates = [(value or 0.0) * (60 / STEP_MINUTES) for value in block.get("precipitation", [])[:STEPS]]
    return {
        "source": "Open-Meteo 15-minute precipitation",
        "lat": body.get("latitude", lat),
        "lon": body.get("longitude", lon),
        "step_minutes": STEP_MINUTES,
        "times": times,
        "rain_mm_h": [round(rate, 2) for rate in rates],
    }


def _basin_for(drain: dict[str, Any]) -> dict[str, Any] | None:
    """The ground around this drain's outlet manhole, read once and kept."""
    node = drain["outlet_node"]
    if node not in _basins:
        lon, lat = drain["outlet"]
        _basins[node] = terrain.basin(lat, lon)
    return _basins[node]


def simulate(drains: list[dict[str, Any]], rain_series: list[float], strip_m: float,
             runoff_coeff: float, step_seconds: float) -> dict[int, dict[str, Any]]:
    """Run the rainfall series through the network and pond what it cannot take.

    Returns one entry per manhole that floods, carrying the volume standing there at
    each step. Volume builds while the drain below is over capacity and drains away
    at its spare capacity once the rain eases, so the peak lands after the rain does,
    which is how urban flooding actually behaves.
    """
    ponded: dict[int, dict[str, Any]] = {}

    for drain in drains:
        capacity = drain["capacity"]["effective_capacity_m3s"]
        if capacity <= 0:
            continue
        catchment = _catchment(drain, strip_m)
        if catchment <= 0:
            continue

        standing = 0.0
        series: list[float] | None = None
        for step, rain in enumerate(rain_series):
            inflow = hydraulics.rational_inflow(catchment, rain, runoff_coeff)
            # Over capacity the excess comes up at the manhole; under it, the drain
            # takes back what is standing there, as fast as it has room for.
            standing = max(0.0, standing + (inflow - capacity) * step_seconds)
            if standing <= 0 and series is None:
                continue
            if series is None:
                series = [0.0] * len(rain_series)
            series[step] = standing

        if series is None:
            continue

        node = drain["outlet_node"]
        entry = ponded.get(node)
        if entry is None:
            entry = {"drain": drain, "volume": [0.0] * len(rain_series),
                     "drains": 0, "street_m": 0.0}
            ponded[node] = entry
        # The road the water comes up in is as long as the drains that surcharge into
        # it: that is the channel it fills before it goes over the kerb.
        entry["street_m"] += drain["length_m"]
        # Several drains can surcharge at the same manhole; the street takes all of it.
        for step, volume in enumerate(series):
            entry["volume"][step] += volume
        entry["drains"] += 1
        # The report names the worst-hit drain at that manhole.
        if series[-1] >= max(entry["drain"].get("_peak", 0.0), 0.0):
            entry["drain"] = drain
            entry["_peak"] = series[-1]

    return ponded


@router.get("/nowcast", summary="Rainfall nowcast for the next three hours")
async def get_nowcast(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    return await rainfall_nowcast(lat, lon)


@router.get("/flood-nowcast", summary="Street-level flood depth, 0-3 hours ahead")
async def get_flood_nowcast(
    lat: float = Query(13.0827, ge=-90, le=90, description="Where to take the rainfall nowcast"),
    lon: float = Query(80.2707, ge=-180, le=180),
    zone: str | None = Query(None, description="Limit to one zone, e.g. N07"),
    ward: str | None = Query(None, description="Limit to one ward, e.g. N082"),
    strip_m: float = Query(hydraulics.STRIP_WIDTH_M, ge=1, le=500),
    runoff_coeff: float = Query(hydraulics.RUNOFF_COEFF, gt=0, le=1),
    rain_mm_h: float | None = Query(None, ge=0, le=500,
                                    description="Hold the rain at this rate instead of "
                                                "using the nowcast, for what-if runs"),
) -> dict[str, Any]:
    all_drains = await asyncio.to_thread(_load_csv)
    selected = [d for d in all_drains if _matches(d["props"], {"ZONE": zone, "WARD": ward})]
    if not selected:
        raise HTTPException(status_code=404, detail="No drains match those filters")

    if rain_mm_h is None:
        nowcast = await rainfall_nowcast(lat, lon)
    else:
        nowcast = {
            "source": f"held at {rain_mm_h} mm/h (what-if)",
            "lat": lat, "lon": lon, "step_minutes": STEP_MINUTES,
            "times": [f"+{i * STEP_MINUTES} min" for i in range(STEPS)],
            "rain_mm_h": [rain_mm_h] * STEPS,
        }

    step_seconds = STEP_MINUTES * 60
    ponded = await asyncio.to_thread(
        simulate, selected, nowcast["rain_mm_h"], strip_m, runoff_coeff, step_seconds)

    features = []
    worst_depth = 0.0
    for node, entry in ponded.items():
        drain = entry["drain"]
        basin = _basin_for(drain)

        corridor = entry["street_m"] * terrain.ROAD_WIDTH_M
        depths, over_kerb = [], False
        for volume in entry["volume"]:
            stood = terrain.street_pond(basin, volume, corridor)
            depths.append(round(stood["depth_m"], 3))
            over_kerb = over_kerb or stood["over_kerb"]
        peak = max(depths)
        if peak < REPORT_DEPTH_M:
            continue
        worst_depth = max(worst_depth, peak)

        first = next((i for i, d in enumerate(depths) if d >= REPORT_DEPTH_M), None)
        props = drain["props"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": drain["outlet"]},
            "properties": {
                "node": node,
                "street": props.get("ST_NAME"),
                "location": props.get("LOCATION"),
                "ward": props.get("WARD"),
                "zone": props.get("ZONE"),
                "drains_surcharging": entry["drains"],
                "peak_depth_cm": round(peak * 100, 1),
                "depth_cm": [round(d * 100, 1) for d in depths],
                "floods_in_minutes": None if first is None else first * STEP_MINUTES,
                "impassable": peak >= IMPASSABLE_DEPTH_M,
                "over_kerb": over_kerb,
                "street_m": round(entry["street_m"], 1),
                "ground_m": None if basin is None else round(basin["ground_here_m"], 2),
                "peak_volume_m3": round(max(entry["volume"]), 1),
            },
        })

    features.sort(key=lambda f: f["properties"]["peak_depth_cm"], reverse=True)
    return {
        "type": "FeatureCollection",
        "features": features,
        "nowcast": nowcast,
        "window_minutes": STEPS * STEP_MINUTES,
        "drains_considered": len(selected),
        "flood_points": len(features),
        "impassable_points": sum(1 for f in features if f["properties"]["impassable"]),
        "worst_depth_cm": round(worst_depth * 100, 1),
        "thresholds": {
            "reported_cm": REPORT_DEPTH_M * 100,
            "impassable_cm": IMPASSABLE_DEPTH_M * 100,
        },
    }
