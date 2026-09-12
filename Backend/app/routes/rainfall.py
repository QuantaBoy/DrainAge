import math

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/data-collection", tags=["Rainfall"])

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# Open-Meteo's model grid is ~0.07 deg here, so finer steps return duplicate data.
# Anything below this is fake resolution, not extra detail.
GRID_STEP_DEG = 0.07
MAX_CELLS = 200
NOWCAST_STEPS = 12  # 12 x 15min = 3 hour lead time
METERS_PER_DEG = 111_320
RUNOFF_COEFFICIENT = 0.9  # calibration knob: 0.9 = dense concrete, lower for green/permeable wards


def cell_area_m2(lat: float, step: float) -> float:
    lat_m = step * METERS_PER_DEG
    lon_m = step * METERS_PER_DEG * math.cos(math.radians(lat))
    return lat_m * lon_m


def build_grid(min_lat: float, max_lat: float, min_lon: float, max_lon: float, step: float):
    points = []
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            points.append((round(lat, 4), round(lon, 4)))
            lon += step
        lat += step
    return points


def fit_step(min_lat: float, max_lat: float, min_lon: float, max_lon: float) -> float:
    """Coarsen the grid until the bounding box fits within MAX_CELLS."""
    step = GRID_STEP_DEG
    while ((max_lat - min_lat) / step + 1) * ((max_lon - min_lon) / step + 1) > MAX_CELLS:
        step *= 2
    return step


@router.get("/rainfall/latest")
async def get_rainfall_grid(
    min_lat: float = Query(...),
    max_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lon: float = Query(...),
):
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=400, detail="min_lat must be < max_lat and min_lon must be < max_lon")

    step = fit_step(min_lat, max_lat, min_lon, max_lon)
    points = build_grid(min_lat, max_lat, min_lon, max_lon, step)
    if not points:
        raise HTTPException(status_code=400, detail="Bounding box produced no grid points")

    lats = ",".join(str(p[0]) for p in points)
    lons = ",".join(str(p[1]) for p in points)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                OPEN_METEO_URL,
                params={
                    "latitude": lats,
                    "longitude": lons,
                    "minutely_15": "precipitation,rain",
                    "precipitation_unit": "mm",
                    # Anchors the window to now and rolls past midnight. Requesting a whole
                    # day instead would run short late in the evening and, after 23:45,
                    # hand back the past as a forecast.
                    "forecast_minutely_15": NOWCAST_STEPS,
                    "timeformat": "iso8601",
                    "timezone": "auto",
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Failed to reach Open-Meteo: {exc}")

    if res.status_code != 200:
        try:
            detail = res.json()
        except ValueError:
            detail = res.text
        raise HTTPException(status_code=res.status_code, detail=detail)

    data = res.json()
    if not isinstance(data, list):
        data = [data]

    if len(data) != len(points):
        raise HTTPException(
            status_code=502,
            detail=f"Open-Meteo returned {len(data)} entries for {len(points)} requested points",
        )

    grid = []
    for point, entry in zip(points, data):
        m15 = entry.get("minutely_15", {})
        times = m15.get("time", [])
        precip = [p or 0.0 for p in m15.get("precipitation", [])]

        area = cell_area_m2(point[0], step)
        volumes = [round((p / 1000.0) * area * RUNOFF_COEFFICIENT, 2) for p in precip]

        grid.append({
            "lat": point[0],
            "lon": point[1],
            "timestamps": times,
            "precipitation_mm": precip,
            "runoff_volume_m3": volumes
        })

    return {
        "grid": grid,
        "step_deg": step,
        "mode": "nowcast",
        "interval_minutes": 15,
        "source": "Open-Meteo forecast (15-min)",
    }
