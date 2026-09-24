"""Street-level flood nowcast: rainfall, terrain and drains coupled end to end.

The chain this route closes, in one place, every minute of the next three hours:

  rainfall nowcast (15 minute feed, spread over 5 minute steps)
      -> runoff on every DEM storage zone and every drain's strip
      -> caught by the drains' inlets (HEC-22 gratings, open drain edges), routed
         through the drain network as a directed graph, each drain carrying up to its
         surveyed Manning capacity (app/services/coupled.py)
      -> what the drains cannot take surcharges out of the manholes
      -> the surface spreads it, zone to zone over the terrain, towards the sea
      -> a depth in centimetres, on a street, at a time.

One model answers every question the page asks: the street table, the map, the
surcharging manholes and the flood-safe route all read the same run.
"""

import asyncio
import math
import re
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.routes.drains import _load_csv, drain_graph
from app.routes.streets import CHENNAI_BBOX
from app.routes.weather import local_current_step
from app.services import channels, coupled, hydraulics, rain_ponding, terrain

router = APIRouter(prefix="/data-collection", tags=["Flood"])

NOWCAST_URL = "https://api.open-meteo.com/v1/forecast"

# The nowcast window the problem asks for, at the resolution the feed gives.
NOWCAST_HOURS = 3
STEP_MINUTES = 15
STEPS = NOWCAST_HOURS * 60 // STEP_MINUTES

# Depths below this are wet tarmac; above it, a car is in trouble.
REPORT_DEPTH_M = 0.05
IMPASSABLE_DEPTH_M = 0.30

# Where the rain is read when the place asked about is outside the drained city.
CHENNAI_CENTRE = (13.0827, 80.2707)


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


# The surface zones and the drain network coupled onto them, built once.
_system: dict[str, Any] = {}
_system_lock = threading.Lock()


def _coupled_system() -> dict[str, Any]:
    with _system_lock:
        if not _system:
            model = _ponding_model()
            surface = coupled.build_surface(model)
            network = coupled.build_network(model, surface, _load_csv(), drain_graph())
            _system.update({"model": model, "surface": surface, "network": network,
                            "summary": coupled.coupled_summary(network, surface)})
    return _system


def warm_ponding() -> None:
    """Build the zones, the coupled network, the street points and the ward index."""
    system = _coupled_system()
    rain_ponding._street_cells(system["model"])
    _place_index()


# The flood nowcast reports in 5 minute steps across the 3 hour window.
PONDING_STEP_MINUTES = 5
PONDING_STEPS = NOWCAST_HOURS * 60 // PONDING_STEP_MINUTES


def _in_chennai(lat: float, lon: float) -> bool:
    south, west, north, east = CHENNAI_BBOX
    return south <= lat <= north and west <= lon <= east


async def _rain_steps(lat: float, lon: float, rain_mm_h: float | None) -> dict[str, Any]:
    """Rain in each 5 minute step of the next 3 hours, and the clock time each step ends.

    The feed's finest step is 15 minutes, reported as the rain of the 15 minutes before
    each stamp; each is spread evenly over its three 5 minute steps. That is the honest
    limit of the input: the model steps every minute, the rain inside a quarter hour
    is taken as steady. A radar nowcast at 5 minutes would drop straight in here.
    """
    per = STEP_MINUTES // PONDING_STEP_MINUTES
    if rain_mm_h is not None:
        rates = [rain_mm_h] * PONDING_STEPS
        ends = [None] * PONDING_STEPS
        source = (f"Scenario: rain held at {rain_mm_h} mm/h for {NOWCAST_HOURS} h, "
                  f"hypothetical, not a forecast")
    else:
        # The drains are Chennai's: rain anywhere else is not what they will carry.
        moved = not _in_chennai(lat, lon)
        if moved:
            lat, lon = CHENNAI_CENTRE
        nowcast = await rainfall_nowcast(lat, lon)
        rates = [rate for rate in nowcast["rain_mm_h"] for _ in range(per)][:PONDING_STEPS]
        rates += [0.0] * (PONDING_STEPS - len(rates))
        # The nowcast's own first slot is where its window starts "now"; each 5 minute
        # step then ends that many minutes further on.
        start = datetime.fromisoformat(nowcast["starts_at"])
        ends = [(start + timedelta(minutes=PONDING_STEP_MINUTES * (i + 1))).isoformat(timespec="minutes")
                for i in range(PONDING_STEPS)]
        source = (f"{nowcast['source']} at {lat:.3f}, {lon:.3f}"
                  f"{' (city centre: the place asked about is outside Chennai)' if moved else ''}, "
                  f"next {NOWCAST_HOURS} h")
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


# A run is six seconds of CPU; the map, the street table and the router ask for the
# same storm, so each run is kept, and only one is computed at a time.
_runs: "OrderedDict[tuple, dict[str, Any]]" = OrderedDict()
_RUNS_KEPT = 12
_run_lock = threading.Lock()


def _run_coupled(rain: dict[str, Any], runoff_coeff: float, drain_condition: float) -> dict[str, Any]:
    """The coupled model for one storm, cached by its rain and settings."""
    key = (tuple(round(r, 4) for r in rain["rain_mm"]), round(runoff_coeff, 4), round(drain_condition, 4))
    with _run_lock:
        if key in _runs:
            _runs.move_to_end(key)
            return _runs[key]
        system = _coupled_system()
        run = coupled.simulate(system["surface"], system["network"], rain["rain_mm"],
                               rain["step_minutes"], runoff_coeff, drain_condition)
        _runs[key] = run
        if len(_runs) > _RUNS_KEPT:
            _runs.popitem(last=False)
        return run


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


def _label_basins(stretches: list[dict[str, Any]]) -> None:
    """The river basin and named channel each stretch's water belongs to.

    Chennai's drainage is reported by basin - Kosasthalayar, Cooum, Adyar, Kovalam -
    and the basin model names every channel, so a flooded street can be placed in the
    system that is supposed to carry its water away.
    """
    if not stretches:
        return
    found = channels.nearest_many([tuple(f["properties"]["deepest"]) for f in stretches])
    for feature, near in zip(stretches, found):
        feature["properties"]["basin"] = near["subbasin"] if near else None
        feature["properties"]["channel"] = near["name"] if near else None
        feature["properties"]["channel_m"] = near["metres_away"] if near else None


def _basin_table(stretches: list[dict[str, Any]], impassable_cm: float) -> list[dict[str, Any]]:
    """One row per river basin: how much of it goes under, and how deep."""
    rows: dict[str, dict[str, Any]] = {}
    for feature in stretches:
        p = feature["properties"]
        name = p.get("basin") or "outside the mapped basins"
        row = rows.setdefault(name, {"basin": name, "stretches": 0, "wet_m": 0.0,
                                     "max_depth_cm": 0.0, "impassable": 0, "streets": set()})
        row["stretches"] += 1
        row["wet_m"] += p["wet_m"]
        row["max_depth_cm"] = max(row["max_depth_cm"], p["max_depth_cm"])
        row["impassable"] += p["max_depth_cm"] >= impassable_cm
        if p["name"]:
            row["streets"].add((p["name"], p.get("ward")))
    table = []
    for row in rows.values():
        row["named_streets"] = len(row.pop("streets"))
        row["wet_km"] = round(row.pop("wet_m") / 1000, 2)
        table.append(row)
    table.sort(key=lambda r: r["wet_km"], reverse=True)
    return table


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


# A manhole is reported once this much water has come out of it over the window.
SURCHARGE_REPORT_M3 = 1.0


def _manholes(run: dict[str, Any], step_minutes: float, zone: str | None,
              ward: str | None) -> list[dict[str, Any]]:
    """The junctions the drains surcharge out of: where, how much, when, how deep."""
    system = _coupled_system()
    network, drains = drain_graph(), _load_csv()
    carrier = network["graph"]["carrier"]
    points = network["points"]
    total = run["surcharge_m3"].sum(axis=0)
    step_s = step_minutes * 60
    out = []
    for node in np.flatnonzero(total >= SURCHARGE_REPORT_M3).tolist():
        props = drains[carrier[node]]["props"] if node in carrier else {}
        if (zone and props.get("ZONE") != zone) or (ward and props.get("WARD") != ward):
            continue
        series = run["surcharge_m3"][:, node]
        depth = run["node_depth_m"][:, node]
        first = int(np.flatnonzero(series > 0)[0])
        out.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(points[node][0], 6), round(points[node][1], 6)]},
            "properties": {
                "node": node,
                # The drain the water is trying to get into: the one that is full.
                "street": props.get("ST_NAME"), "location": props.get("LOCATION"),
                "ward": props.get("WARD"), "zone": props.get("ZONE"),
                "ground_m": None if not math.isfinite(system["network"]["ground"][node])
                else round(float(system["network"]["ground"][node]), 2),
                "surcharge_m3": round(float(total[node]), 1),
                "peak_l_s": round(float(series.max()) / step_s * 1000, 1),
                "surcharge_l_s": np.round(series / step_s * 1000, 1).tolist(),
                "depth_cm": np.round(depth * 100, 1).tolist(),
                "starts_at_min": int(round((first + 1) * step_minutes)),
            },
        })
    out.sort(key=lambda f: f["properties"]["surcharge_m3"], reverse=True)
    return out


_RAIN_MM_H = Query(None, ge=0, le=500, description="Hold the rain at this rate for 3 h "
                                                     "instead of the nowcast, for what-if runs")
# How much of each drain's surveyed conveyance is working: 1 is the survey as built,
# 0.5 half silted, 0 a network that is full or blocked.
_DRAIN_CONDITION = Query(1.0, ge=0, le=1, description="Share of the surveyed drain "
                                                      "capacity that is working (1 = as surveyed)")


@router.get("/rain-ponding/streets",
            summary="Coupled flood nowcast: when each street goes under, how deep, when it clears, "
                    "and which manholes surcharge; 5 minute steps, 3 hours")
async def get_rain_ponding_streets(
    lat: float = Query(13.0827, ge=-90, le=90, description="Where to take the rainfall nowcast"),
    lon: float = Query(80.2707, ge=-180, le=180),
    rain_mm_h: float | None = _RAIN_MM_H,
    runoff_coeff: float = Query(hydraulics.RUNOFF_COEFF, gt=0, le=1),
    drain_condition: float = _DRAIN_CONDITION,
    min_depth_cm: float = Query(REPORT_DEPTH_M * 100, ge=1, le=500),
    zone: str | None = Query(None, description="Only streets in this zone, e.g. N07"),
    ward: str | None = Query(None, description="Only streets in this ward, e.g. N082"),
) -> dict[str, Any]:
    rain = await _rain_steps(lat, lon, rain_mm_h)
    impassable = IMPASSABLE_DEPTH_M * 100

    def run() -> dict[str, Any]:
        result = _run_coupled(rain, runoff_coeff, drain_condition)
        model = _coupled_system()["model"]
        stretches, water = rain_ponding.flooded_streets(
            model, result["levels"], rain["step_minutes"], min_depth_cm / 100, IMPASSABLE_DEPTH_M)
        _label_places(stretches)
        _label_basins(stretches)
        if zone or ward:
            stretches = [f for f in stretches
                         if (not zone or f["properties"]["zone"] == zone)
                         and (not ward or f["properties"]["ward"] == ward)]
            used = {str(z) for f in stretches for z in f["properties"]["storage"] if z}
            water = {z: v for z, v in water.items() if z in used}
        manholes = _manholes(result, rain["step_minutes"], zone, ward)
        return {"stretches": stretches, "water": water, "table": _street_table(stretches),
                "manholes": manholes, "balance": result["balance"]}

    out = await asyncio.to_thread(run)
    stretches, table = out["stretches"], out["table"]
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
        "manholes": {"type": "FeatureCollection", "features": out["manholes"]},
        # Chennai's drainage is organised by river basin; this says which of them the
        # flooding is in, from the basin model's own channel layer.
        "basins": _basin_table(stretches, impassable),
        # Water level in every zone a stretch touches, one per step, null while dry:
        # depth at a vertex at step i is water[zone][i] - ground_m.
        "water_level_m": out["water"],
        "rain": rain,
        "runoff_coeff": runoff_coeff,
        "drain_condition": drain_condition,
        "model": {
            **_coupled_system()["summary"],
            "coupling_step_s": coupled.COUPLE_S,
            "surface_step_s": coupled.COUPLE_S / coupled.SURFACE_SUBSTEPS,
        },
        # Where every cubic metre of runoff went, and how well the books close.
        "balance_m3": out["balance"],
        "stretches": len(stretches),
        "named_streets": len(named),
        "impassable_streets": sum(1 for r in named if r["max_depth_cm"] >= impassable),
        "wet_km": round(sum(r["wet_m"] for r in table) / 1000, 2),
        "surcharging_manholes": len(out["manholes"]),
        "per_step": per_step,
        "thresholds": {"reported_cm": min_depth_cm, "impassable_cm": impassable},
    }
