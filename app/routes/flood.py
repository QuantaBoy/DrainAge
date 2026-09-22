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
import math
import re
from datetime import datetime, timedelta
from typing import Any

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.routes.drains import _catchment, _load_csv, _matches
from app.routes.streets import CHENNAI_BBOX
from app.routes.weather import local_current_step
from app.services import hydraulics, rain_ponding, street_flood, terrain

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
            # A few steps of slack: the feed's own first step can sit a little behind
            # the clock (its data cycle, not this server's), and the window is trimmed
            # to "now" below rather than trusted to start there.
            "forecast_minutely_15": STEPS + 4,
            "timezone": "auto",
        })
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Rainfall nowcast unavailable")

    body = response.json()
    block = body.get("minutely_15", {})
    all_times = block.get("time", [])
    all_rates = [(value or 0.0) * (60 / STEP_MINUTES) for value in block.get("precipitation", [])]

    # The feed's first slot is not always "now": drop whatever in it has already
    # passed, so +5 min on the page is 5 minutes from the clock, not from a stale slot.
    now = local_current_step(body.get("utc_offset_seconds", 0), STEP_MINUTES)
    first = next((i for i, time in enumerate(all_times)
                 if datetime.fromisoformat(time) >= now), 0)
    times = all_times[first:first + STEPS]
    rates = all_rates[first:first + STEPS]
    return {
        "source": "Open-Meteo 15-minute precipitation",
        "lat": body.get("latitude", lat),
        "lon": body.get("longitude", lon),
        "step_minutes": STEP_MINUTES,
        # The clock this window starts on, i.e. times[0]; kept explicit so a caller
        # never has to reverse-engineer "now" from a feed timestamp again.
        "starts_at": times[0] if times else now.isoformat(timespec="minutes"),
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


# The relief image is the same for every visitor and slow to draw, so it is drawn once.
_relief: dict[str, Any] = {}


def _relief_layer() -> dict[str, Any]:
    if not _relief:
        south, west, north, east = CHENNAI_BBOX
        dem = terrain.grid(south, north, west, east)
        if dem is None:
            raise HTTPException(status_code=503, detail="No DEM tiles in app/data")
        land = dem["grid"][(dem["grid"] == dem["grid"]) & (dem["grid"] != 0)]
        _relief.update({
            "png": terrain.relief_image(dem),
            "bounds": [[south, west], [north, east]],
            "min_m": round(float(land.min()), 1),
            "max_m": round(float(land.max()), 1),
            "median_m": round(float(sorted(land.ravel())[land.size // 2]), 1),
        })
    return _relief


@router.get("/elevation", summary="Where the elevation layer goes, and how to read it")
async def get_elevation() -> dict[str, Any]:
    layer = await asyncio.to_thread(_relief_layer)
    return {
        "image": "/data-collection/elevation.png",
        "bounds": layer["bounds"],
        "range_m": [layer["min_m"], layer["max_m"]],
        "median_m": layer["median_m"],
        "stops": [{"m": m, "color": "#%02x%02x%02x" % rgb} for m, rgb in terrain.ELEVATION_STOPS],
        "source": terrain.source_info(),
    }


@router.get("/elevation.png", summary="The DEM as a shaded-relief image")
async def get_elevation_png() -> Response:
    layer = await asyncio.to_thread(_relief_layer)
    return Response(layer["png"], media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/nowcast", summary="Rainfall nowcast for the next three hours")
async def get_nowcast(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    return await rainfall_nowcast(lat, lon)


def _ponding_model() -> dict[str, Any]:
    south, west, north, east = CHENNAI_BBOX
    model = rain_ponding.load(south, north, west, east)
    if model is None:
        raise HTTPException(status_code=503, detail="No DEM tiles in app/data")
    return model


_hollow_capacity: dict[str, Any] = {}


def drain_capacity_by_hollow(model: dict[str, Any]) -> np.ndarray:
    """What the surveyed drains carry out of each DEM hollow's catchment, m³/s.

    The rain model fills a hollow from the land that drains into it; this is what
    empties it. Every drain sits in one of those catchments, and the ones that carry
    water out of it are its outfalls - the drains nothing else runs out of. Their
    surveyed conveyance is the rate the hollow can be emptied at, so a street with a
    large clear drain below it clears while one with a small or silted drain stays wet.

    The survey does not reach every hollow. Rather than assume those have no drainage
    (which floods them for ever) or invent a rate for them, they are given the rate
    the surveyed part of the city has: its total outfall conveyance over the total
    catchment those outfalls serve. Measured, not guessed, and one aggregate rather
    than a median of ratios that tiny catchments with a large outfall would skew.

    ponytail: outfall conveyance, not an inlet capacity. A drain can only take water
    the gully gratings actually reach, and the survey does not record those; where a
    hollow's water cannot get into the drain beside it, this is optimistic.
    """
    if not _hollow_capacity:
        basins = model["basins"]
        rows, cols = basins.shape
        capacity = np.zeros(model["sea"] + 1)
        for drain in _load_csv():
            if not drain["is_outfall"]:
                continue
            rate = drain["capacity"]["effective_capacity_m3s"]
            if rate <= 0:
                continue
            lon, lat = drain["outlet"]
            row = math.floor((model["north"] - lat) / model["lat_step"])
            col = math.floor((lon - model["west"]) / model["lon_step"])
            if 0 <= row < rows and 0 <= col < cols:
                capacity[basins[row, col]] += rate

        catchment = model["catchment_m2"]
        surveyed = (capacity > 0) & (catchment > 0)
        typical = (float(capacity[surveyed].sum() / catchment[surveyed].sum())
                   if surveyed.any() else 0.0)
        filled = np.where(surveyed, capacity, catchment * typical)
        # The sea and the land between hollows hold nothing to drain.
        filled[0] = filled[model["sea"]] = 0.0
        _hollow_capacity.update({
            "m3s": filled,
            "surveyed": int(surveyed.sum()),
            "assumed": int(((filled > 0) & ~surveyed).sum()),
            # The surveyed rate, as mm/h over the catchment it drains.
            "typical_mm_h": round(typical * 3.6e6, 1),
        })
    return _hollow_capacity["m3s"]


def warm_ponding() -> None:
    """Build the rain model, the street sample points and the ward index ahead of use."""
    model = _ponding_model()
    rain_ponding._street_cells(model)
    _place_index()
    drain_capacity_by_hollow(model)


# The rain-on-streets forecast runs in 5 minute steps across the 3 hour window.
PONDING_STEP_MINUTES = 5
PONDING_STEPS = NOWCAST_HOURS * 60 // PONDING_STEP_MINUTES


async def _rain_steps(lat: float, lon: float, rain_mm_h: float | None) -> dict[str, Any]:
    """Rain in each 5 minute step of the next 3 hours, and the clock time each step ends.

    The feed's finest step is 15 minutes, reported as the rain of the 15 minutes before
    each stamp; each is spread evenly over its three 5 minute steps. That is the honest
    limit of the input: the model steps every 5 minutes, the rain inside a quarter hour
    is taken as steady. A radar nowcast at 5 minutes would drop straight in here.
    """
    per = STEP_MINUTES // PONDING_STEP_MINUTES
    if rain_mm_h is not None:
        rates = [rain_mm_h] * PONDING_STEPS
        ends = [None] * PONDING_STEPS
        source = (f"Scenario: rain held at {rain_mm_h} mm/h for {NOWCAST_HOURS} h, "
                  f"hypothetical, not a forecast")
    else:
        nowcast = await rainfall_nowcast(lat, lon)
        rates = [rate for rate in nowcast["rain_mm_h"] for _ in range(per)][:PONDING_STEPS]
        rates += [0.0] * (PONDING_STEPS - len(rates))
        # The nowcast's own first slot is where its window starts "now"; each 5 minute
        # step then ends that many minutes further on.
        start = datetime.fromisoformat(nowcast["starts_at"])
        ends = [(start + timedelta(minutes=PONDING_STEP_MINUTES * (i + 1))).isoformat(timespec="minutes")
                for i in range(PONDING_STEPS)]
        source = f"{nowcast['source']}, next {NOWCAST_HOURS} h"
    return {
        # A scenario is never passed off as a forecast: every response says which it is.
        "mode": "scenario" if rain_mm_h is not None else "live",
        "source": source,
        "step_minutes": PONDING_STEP_MINUTES,
        "rain_mm_h": [round(r, 2) for r in rates],
        "rain_mm": [r * PONDING_STEP_MINUTES / 60 for r in rates],
        "ends": ends,
        "total_mm": round(sum(rates) * PONDING_STEP_MINUTES / 60, 1),
    }


# A street is put in the ward of the nearest surveyed drain, if one is this close. The
# survey is the only ward layer the site has; further out the guess is not worth making.
PLACE_RADIUS_M = 400.0
_places: dict[str, Any] = {}


def _place_index() -> dict[str, Any]:
    """Drain outlets in a KD-tree, in metres, carrying the ward, zone and locality."""
    if not _places:
        from scipy.spatial import cKDTree

        drains = _load_csv()
        xy = np.array([d["outlet"] for d in drains], dtype=np.float64)
        scale = terrain.M_PER_DEG * math.cos(math.radians(13.0))
        _places.update({
            "tree": cKDTree(np.c_[xy[:, 0] * scale, xy[:, 1] * terrain.M_PER_DEG]),
            "scale": scale,
            "props": [{
                "ward": d["props"].get("WARD"),
                "zone": d["props"].get("ZONE"),
                # "Menambedu,Chennai." is Menambedu: the city is every row's suffix.
                "locality": re.sub(r"[\s,.]*chennai[\s.]*$", "", d["props"].get("LOCATION") or "",
                                   flags=re.I).strip(" ,.") or None,
            } for d in drains],
        })
    return _places


def _label_places(stretches: list[dict[str, Any]]) -> None:
    """Ward, zone and locality on every stretch, from the drain nearest its deepest point."""
    if not stretches:
        return
    index = _place_index()
    points = np.array([f["properties"]["deepest"] for f in stretches])       # lat, lon
    distance, nearest = index["tree"].query(
        np.c_[points[:, 1] * index["scale"], points[:, 0] * terrain.M_PER_DEG])
    for feature, metres, i in zip(stretches, distance, nearest):
        place = index["props"][i] if metres <= PLACE_RADIUS_M else {"ward": None, "zone": None, "locality": None}
        feature["properties"].update(place)


def _street_table(stretches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per street per ward: when it goes under, how deep, and when it clears.

    Grouping is by name and ward, not name alone: Chennai has a "5th Street" in dozens
    of neighbourhoods, and merging them would put one depth on streets kilometres apart.
    A street floods when its first stretch does and clears when its last one does.
    """
    def earliest(a: int | None, b: int | None) -> int | None:
        return b if a is None else a if b is None else min(a, b)

    rows: dict[tuple, dict[str, Any]] = {}
    for feature in stretches:
        p = feature["properties"]
        key = (p["name"], p["ward"] or f"{p['deepest'][0]:.2f},{p['deepest'][1]:.2f}")
        row = rows.get(key)
        if row is None:
            row = rows[key] = {
                "street": p["name"], "highway": p["highway"],
                "ward": p["ward"], "zone": p["zone"], "locality": p["locality"],
                "max_depth_cm": 0.0, "wet_m": 0.0, "stretches": 0, "_depth_x_m": 0.0,
                "floods_at_min": None, "impassable_at_min": None,
                "clears_at_min": 0, "series_cm": p["series_cm"],
            }
        row["stretches"] += 1
        row["wet_m"] += p["wet_m"]
        row["_depth_x_m"] += p["mean_depth_cm"] * p["wet_m"]
        row["floods_at_min"] = earliest(row["floods_at_min"], p["floods_at_min"])
        row["impassable_at_min"] = earliest(row["impassable_at_min"], p["impassable_at_min"])
        # Still wet at the end of the window anywhere means still wet, full stop.
        row["clears_at_min"] = (None if row["clears_at_min"] is None or p["clears_at_min"] is None
                                else max(row["clears_at_min"], p["clears_at_min"]))
        row["series_cm"] = [max(a, b) for a, b in zip(row["series_cm"], p["series_cm"])]
        if p["max_depth_cm"] > row["max_depth_cm"]:
            row["max_depth_cm"] = p["max_depth_cm"]
            row["deepest"] = p["deepest"]
            row["peak_at_min"] = p["peak_at_min"]
    table = []
    for row in rows.values():
        row["mean_depth_cm"] = round(row.pop("_depth_x_m") / row["wet_m"], 1) if row["wet_m"] else 0.0
        row["wet_m"] = round(row["wet_m"], 0)
        table.append(row)
    # Soonest under first: the order a control room works through them.
    table.sort(key=lambda r: (r["floods_at_min"] if r["floods_at_min"] is not None else 10**6,
                              -r["max_depth_cm"]))
    return table


_RAIN_MM_H = Query(None, ge=0, le=500, description="Hold the rain at this rate for 3 h "
                                                     "instead of the nowcast, for what-if runs")
# What the drains take off every catchment. 20 mm/h is a working figure for drains that
# are open and clear, not a surveyed one; 0 is a network that is full or blocked.
# How much of each drain's surveyed conveyance is working: 1 is the survey as built,
# 0.5 half silted, 0 a network that is full or blocked.
_DRAIN_CONDITION = Query(1.0, ge=0, le=1, description="Share of the surveyed drain "
                                                      "capacity that is working (1 = as surveyed)")
# Only where the survey reaches no drain out of a hollow.
_DRAIN_MM_H = Query(0.0, ge=0, le=200, description="Override the drainage everywhere with "
                                                   "a flat rate, mm/h; 0 uses the survey")


def _run_ponding(model: dict[str, Any], rain: dict[str, Any], runoff_coeff: float,
                 drain_mm_h: float, drain_condition: float) -> Any:
    capacity = drain_capacity_by_hollow(model) * drain_condition
    return rain_ponding.simulate(model, rain["rain_mm"], runoff_coeff, drain_mm_h,
                                 rain["step_minutes"], capacity)


@router.get("/rain-ponding", summary="Depth at a coordinate every 5 minutes for 3 hours, from the DEM")
async def get_rain_ponding(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    rain_mm_h: float | None = _RAIN_MM_H,
    runoff_coeff: float = Query(hydraulics.RUNOFF_COEFF, gt=0, le=1),
    drain_mm_h: float = _DRAIN_MM_H,
    drain_condition: float = _DRAIN_CONDITION,
) -> dict[str, Any]:
    model = await asyncio.to_thread(_ponding_model)
    rain = await _rain_steps(lat, lon, rain_mm_h)
    levels = await asyncio.to_thread(_run_ponding, model, rain, runoff_coeff,
                                     drain_mm_h, drain_condition)
    result = rain_ponding.at(model, lat, lon, levels, rain["step_minutes"],
                             REPORT_DEPTH_M, IMPASSABLE_DEPTH_M)
    if result is None:
        raise HTTPException(status_code=404, detail="Outside the DEM, or over the sea")
    rain.pop("rain_mm")
    return {**result, "rain": rain, "runoff_coeff": runoff_coeff,
            "drain_condition": drain_condition, "drain_mm_h": drain_mm_h}


@router.get("/rain-ponding/streets",
            summary="When each street goes under, how deep, and when it clears: 5 minute steps, 3 hours")
async def get_rain_ponding_streets(
    lat: float = Query(13.0827, ge=-90, le=90, description="Where to take the rainfall nowcast"),
    lon: float = Query(80.2707, ge=-180, le=180),
    rain_mm_h: float | None = _RAIN_MM_H,
    runoff_coeff: float = Query(hydraulics.RUNOFF_COEFF, gt=0, le=1),
    drain_mm_h: float = _DRAIN_MM_H,
    drain_condition: float = _DRAIN_CONDITION,
    min_depth_cm: float = Query(REPORT_DEPTH_M * 100, ge=1, le=500),
    zone: str | None = Query(None, description="Only streets in this zone, e.g. N07"),
    ward: str | None = Query(None, description="Only streets in this ward, e.g. N082"),
) -> dict[str, Any]:
    model = await asyncio.to_thread(_ponding_model)
    rain = await _rain_steps(lat, lon, rain_mm_h)
    impassable = IMPASSABLE_DEPTH_M * 100

    def run() -> tuple[list[dict[str, Any]], dict[str, list[float]], list[dict[str, Any]]]:
        levels = _run_ponding(model, rain, runoff_coeff, drain_mm_h, drain_condition)
        stretches, water = rain_ponding.flooded_streets(
            model, levels, rain["step_minutes"], min_depth_cm / 100, IMPASSABLE_DEPTH_M)
        _label_places(stretches)
        if zone or ward:
            stretches = [f for f in stretches
                         if (not zone or f["properties"]["zone"] == zone)
                         and (not ward or f["properties"]["ward"] == ward)]
            used = {str(h) for f in stretches for h in f["properties"]["hollow"] if h}
            water = {h: v for h, v in water.items() if h in used}
        return stretches, water, _street_table(stretches)

    stretches, water, table = await asyncio.to_thread(run)
    named = [r for r in table if r["street"]]
    # Per step: how many named streets are under, and how many are impassable.
    per_step = [
        {"wet": sum(1 for r in named if r["series_cm"][i] >= min_depth_cm),
         "impassable": sum(1 for r in named if r["series_cm"][i] >= impassable)}
        for i in range(len(rain["rain_mm_h"]))
    ]
    rain.pop("rain_mm")
    return {
        "type": "FeatureCollection",
        "features": stretches,
        "streets": table,
        # Water level in every hollow a stretch touches, one per step: depth at a
        # vertex at step i is water[hollow][i] - ground_m.
        "water_level_m": water,
        "rain": rain,
        "runoff_coeff": runoff_coeff,
        "drain_condition": drain_condition, "drain_mm_h": drain_mm_h,
        "drainage": {
            "hollows_from_the_survey": _hollow_capacity["surveyed"],
            "hollows_at_the_surveyed_rate": _hollow_capacity["assumed"],
            "surveyed_rate_mm_h": _hollow_capacity["typical_mm_h"],
        },
        "stretches": len(stretches),
        "named_streets": len(named),
        "impassable_streets": sum(1 for r in named if r["max_depth_cm"] >= impassable),
        "wet_km": round(sum(r["wet_m"] for r in table) / 1000, 2),
        "per_step": per_step,
        "thresholds": {"reported_cm": min_depth_cm, "impassable_cm": impassable},
    }


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
    streets: bool = Query(False, description="Also return the street stretches under water"),
) -> dict[str, Any]:
    all_drains = await asyncio.to_thread(_load_csv)
    selected = [d for d in all_drains if _matches(d["props"], {"ZONE": zone, "WARD": ward})]
    if not selected:
        raise HTTPException(status_code=404, detail="No drains match those filters")

    if rain_mm_h is None:
        nowcast = {**await rainfall_nowcast(lat, lon), "mode": "live"}
    else:
        nowcast = {
            "mode": "scenario",
            "source": f"Scenario: rain held at {rain_mm_h} mm/h, hypothetical, not a forecast",
            "lat": lat, "lon": lon, "step_minutes": STEP_MINUTES,
            "times": [f"+{i * STEP_MINUTES} min" for i in range(STEPS)],
            "rain_mm_h": [rain_mm_h] * STEPS,
        }

    step_seconds = STEP_MINUTES * 60
    ponded = await asyncio.to_thread(
        simulate, selected, nowcast["rain_mm_h"], strip_m, runoff_coeff, step_seconds)

    features = []
    wet_points = []
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
        wet = {
            "node": node, "lon": drain["outlet"][0], "lat": drain["outlet"][1],
            # Road level, not the DEM cell: a manhole cell with a roof in it would put
            # the water level metres too high and flood every street around it.
            "ground_m": terrain.ground_level(drain["outlet"][1], drain["outlet"][0]),
            # Shallower than reportable counts as dry, so a street is never drawn wet
            # at a step its manhole is not.
            "depth_m": [d if d >= REPORT_DEPTH_M else 0.0 for d in depths],
            "volume_m3": entry["volume"],
        }
        wet_points.append(wet)
        reaches, levels = street_flood.water_profile(wet)
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
                # How far along the road and how high the water stands, per step: what
                # the street stretches around this manhole are cut against.
                "reach_m": [round(r, 1) for r in reaches],
                "level_m": [None if level is None else round(level, 2) for level in levels],
            },
        })

    features.sort(key=lambda f: f["properties"]["peak_depth_cm"], reverse=True)
    extra: dict[str, Any] = {}
    if streets:
        stretches = await asyncio.to_thread(street_flood.flooded_streets, wet_points)
        extra["streets"] = {"type": "FeatureCollection", "features": stretches}
        extra["street_names"] = len({f["properties"]["name"] for f in stretches
                                     if f["properties"]["name"]})
    return {
        **extra,
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
