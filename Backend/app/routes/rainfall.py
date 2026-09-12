import math
from datetime import datetime, timezone

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


def _filter_future(times: list[str], precip: list[float], tz_offset_sec: int) -> tuple[list[str], list[float]]:
    """Keep only future timestamps (relative to now) and slice to NOWCAST_STEPS (3 hrs).
       Falls back to the last NOWCAST_STEPS entries if no future data is found."""
    from datetime import timedelta
    now_utc = datetime.now(timezone.utc)
    future_times = []
    future_precip = []
    for t, p in zip(times, precip):
        # Open-Meteo returns local times; convert to UTC for comparison
        local_dt = datetime.fromisoformat(t)
        utc_dt = local_dt.replace(tzinfo=timezone.utc) - timedelta(seconds=tz_offset_sec)
        if utc_dt >= now_utc:
            future_times.append(t)
            future_precip.append(p)
        if len(future_times) >= NOWCAST_STEPS:
            break
    
    # Fallback: if no future timestamps found, return the last available chunk
    if not future_times and times:
        return times[-NOWCAST_STEPS:], precip[-NOWCAST_STEPS:]
    return future_times, future_precip


def _filter_historical_peak(times: list[str], precip: list[float]) -> tuple[list[str], list[float]]:
    """Scan the 24-hour array and find the 3-hour (12 step) continuous window with the most precipitation."""
    if len(times) <= NOWCAST_STEPS:
        return times, precip
    
    max_sum = -1.0
    best_idx = 0
    for i in range(len(times) - NOWCAST_STEPS + 1):
        window_sum = sum(precip[i:i+NOWCAST_STEPS])
        if window_sum > max_sum:
            max_sum = window_sum
            best_idx = i
            
    return times[best_idx:best_idx+NOWCAST_STEPS], precip[best_idx:best_idx+NOWCAST_STEPS]


def _filter_by_hour(times: list[str], precip: list[float], start_hour: int) -> tuple[list[str], list[float]]:
    """Extract the 3-hour window starting at the given hour (0-21).
       Each hour = 4 intervals of 15 min, so start_hour=6 → index 24."""
    start_idx = start_hour * 4  # 4 intervals per hour
    end_idx = start_idx + NOWCAST_STEPS
    if start_idx >= len(times):
        return times[-NOWCAST_STEPS:], precip[-NOWCAST_STEPS:]
    return times[start_idx:end_idx], precip[start_idx:end_idx]


@router.get("/rainfall/latest")
async def get_rainfall_grid(
    min_lat: float = Query(...),
    max_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lon: float = Query(...),
    date: str = Query(None, description="YYYY-MM-DD for historical data"),
    start_hour: int = Query(None, description="0-21, start hour of the 3hr window (omit for auto-peak)"),
):
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=400, detail="min_lat must be < max_lat and min_lon must be < max_lon")

    step = fit_step(min_lat, max_lat, min_lon, max_lon)
    points = build_grid(min_lat, max_lat, min_lon, max_lon, step)
    if not points:
        raise HTTPException(status_code=400, detail="Bounding box produced no grid points")

    lats = ",".join(str(p[0]) for p in points)
    lons = ",".join(str(p[1]) for p in points)

    # Determine if this is a historical query or a live/today query
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    is_historical = date and date < today_str  # strictly in the past

    if is_historical:
        url = "https://archive-api.open-meteo.com/v1/archive"
        api_params = {
            "latitude": lats,
            "longitude": lons,
            "start_date": date,
            "end_date": date,
            "minutely_15": "precipitation,rain",
            "precipitation_unit": "mm",
            "timeformat": "iso8601",
            "timezone": "auto",
        }
    else:
        url = OPEN_METEO_URL
        api_params = {
            "latitude": lats,
            "longitude": lons,
            "minutely_15": "precipitation,rain",
            "precipitation_unit": "mm",
            "forecast_days": 1,
            "timeformat": "iso8601",
            "timezone": "auto",
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(url, params=api_params)
    if res.status_code != 200:
        try:
            detail = res.json()
        except ValueError:
            detail = res.text
        raise HTTPException(status_code=res.status_code, detail=detail)

    data = res.json()
    if not isinstance(data, list):
        data = [data]

    grid = []
    for point, entry in zip(points, data):
        m15 = entry.get("minutely_15", {})
        all_times = m15.get("time", [])
        all_precip = [p or 0.0 for p in m15.get("precipitation", [])]
        tz_offset = entry.get("utc_offset_seconds", 0)

        # If historical past date: use specific hour window or auto-detect peak.
        # If today or no date: filter to future-only timestamps.
        if is_historical:
            if start_hour is not None:
                times, precip = _filter_by_hour(all_times, all_precip, start_hour)
            else:
                times, precip = _filter_historical_peak(all_times, all_precip)
        else:
            times, precip = _filter_future(all_times, all_precip, tz_offset)

        area = cell_area_m2(point[0], step)
        volumes = [round((p / 1000.0) * area * RUNOFF_COEFFICIENT, 2) for p in precip]

        grid.append({
            "lat": point[0],
            "lon": point[1],
            "timestamps": times,
            "precipitation_mm": precip,
            "runoff_volume_m3": volumes
        })

    return {"grid": grid, "step_deg": step}
