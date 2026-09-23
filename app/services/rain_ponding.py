"""Rain that stands where it falls, minute by minute: every hollow in the DEM, filled
from its own catchment as the storm goes on, and emptied by the drains after it.

The drain model (app/routes/flood.py) puts water on a street only where a drain
overflows. Rain also ponds with no drain involved: every dip in the ground collects
what falls on the land that drains into it. This module finds those dips once, then
runs a rainfall series through them in 5 minute steps, and answers for every street
when the water arrives, how deep it gets, and when it goes.

  1. Fill. Every depression is filled to its spill point by morphological
     reconstruction, which gives the same surface as a priority-flood fill.
     filled - ground is how deep water can stand at a cell before it runs out.
  2. Catchments. A watershed on the DEM, seeded from the depressions and from the
     outlets (the sea and the edge of the box), gives each cell the hollow its rain
     runs into.
  3. Step. Each step, a hollow gains runoff (rain x runoff coefficient x catchment)
     and loses what the drains carry off its catchment. The water it holds stands at
     the level the hypsometric fill gives (lowest cells first, as terrain.fill_depth),
     never above the spill point.

ponytail: water over the spill point leaves the model. It does not cascade into the
next hollow downstream; a fill-spill-merge pass or a 2D solver would add that. Drains
are a flat rate in mm/h over every catchment, not the network flood.py sizes.
"""

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage
from skimage.morphology import reconstruction
from skimage.segmentation import watershed

from app.services import channels, terrain

# A hollow shallower than this at its deepest is DEM noise, not somewhere water stands.
MIN_HOLLOW_M = terrain.MIN_DEPTH_M

# Median filter over the DEM before filling, in cells. At 30 m, GEDTM30 in a dense
# block like T. Nagar jumps 3-5 m cell to cell where buildings bleed through, and every
# such pit fills to its rim: 4 m of "water" on Venkatanarayana Road. A 3 x 3 median
# (90 m) removes those pits and keeps Pallikaranai, Porur and Puzhal; 5 x 5 starts to
# erase Porur lake. A calibration knob: set it against surveyed flood marks.
SMOOTH_CELLS = 3

# Stream burning: DEM cells under a mapped river, canal or nullah are lowered by this
# much before filling. Every road embankment and culvert across a canal reads as a dam
# at 30 m, and upstream of each one the canal becomes a closed "lake" several metres
# deep that the fill then puts on every street beside it. Burning the channel in lets
# it drain the way the real one does. Waterways come from /data-collection/waterways.
BURN_M = 2.0
WATERWAYS_PATH = Path(__file__).resolve().parent.parent / "data" / "chennai_waterways.geojson"
# Two channel layers, because neither is complete: OpenStreetMap's waterways, and the
# basin model's own macro and micro drains, canals and surplus channels
# (app/services/channels.py), 78 km of which inside the city are on no other layer.

# How fast runoff crosses a catchment to its hollow, m/s: sheet flow and gutters on
# flat paved ground. A catchment of side L takes L / speed to deliver its rain, so a
# 1 km² one keeps filling for half an hour after the rain stops. ponytail: one speed
# for the city; slope and surface per catchment would refine it. Calibrate against
# the time lag between rain and water on known streets.
FLOW_SPEED_M_S = 0.5
_EIGHT = np.ones((3, 3), dtype=bool)

_model: dict[str, Any] | None = None


def prepare(surface: np.ndarray, outlet: np.ndarray, cell_area_m2: float) -> dict[str, Any]:
    """Hollows, catchments and fill curves for one ground grid.

    `surface` is finite everywhere; `outlet` marks the cells where water leaves the
    model (the sea, the edge of the box).
    """
    seed = np.where(outlet, surface, surface.max())
    filled = reconstruction(seed, surface, method="erosion")
    capacity_depth = filled - surface

    labels, count = ndimage.label((capacity_depth > 1e-6) & ~outlet, structure=_EIGHT)
    if count:
        deepest = ndimage.maximum(capacity_depth, labels, index=np.arange(1, count + 1))
        keep = np.concatenate([[False], np.asarray(deepest) >= MIN_HOLLOW_M])
        labels = np.where(keep[labels], labels, 0)
    sea = count + 1

    markers = labels.copy()
    markers[outlet] = sea
    basins = watershed(surface, markers, connectivity=2)
    catchment_m2 = np.bincount(basins.ravel(), minlength=sea + 1) * cell_area_m2
    # The sea and the ground between hollows collect nothing that stands anywhere.
    catchment_m2[0] = catchment_m2[sea] = 0.0

    # Each hollow's cells sorted lowest first, with the volume it takes to bring the
    # water up to each one: the fill curve, cut into groups by hollow.
    cells = np.flatnonzero(labels)
    order = np.lexsort((surface.flat[cells], labels.flat[cells]))
    cells = cells[order]
    hollow = labels.flat[cells]
    ground = surface.flat[cells]
    start = np.full(sea + 1, -1)
    firsts = np.flatnonzero(np.r_[True, np.diff(hollow) != 0]) if cells.size else np.array([], int)
    start[hollow[firsts]] = firsts
    rank = np.arange(cells.size) - start[hollow]
    running = np.cumsum(ground)
    below = running - ground - (running - ground)[start[hollow]]   # sum of lower cells
    spill = np.full(sea + 1, -np.inf)
    np.maximum.at(spill, hollow, filled.flat[cells])
    capacity = np.zeros(sea + 1)
    np.add.at(capacity, hollow, (spill[hollow] - ground) * cell_area_m2)

    return {
        "surface": surface, "labels": labels, "basins": basins, "sea": sea,
        "cell_area_m2": cell_area_m2, "catchment_m2": catchment_m2, "spill_m": spill,
        "capacity_m3": capacity,
        "cells": cells, "hollow": hollow, "ground": ground, "start": start,
        "size": np.bincount(hollow, minlength=sea + 1),
        "curve_m3": (rank * ground - below) * cell_area_m2,
        "upto": below + ground,                                         # sum up to and including
    }


def water_levels(model: dict[str, Any], volume_m3: np.ndarray) -> np.ndarray:
    """The level water stands at in every hollow, given the volume in each.

    -inf where there is no hollow, so a depth taken from it is never positive.
    """
    v = volume_m3[model["hollow"]]
    wet = model["curve_m3"] <= v
    covered = np.bincount(model["hollow"][wet], minlength=model["sea"] + 1)
    level = np.full(model["sea"] + 1, -np.inf)
    has = covered > 0
    last = model["start"][has] + covered[has] - 1
    level[has] = (volume_m3[has] / model["cell_area_m2"] + model["upto"][last]) / covered[has]
    return np.minimum(level, model["spill_m"])


def simulate(model: dict[str, Any], rain_mm: list[float], runoff_coeff: float,
             drain_mm_h: float, step_minutes: float,
             drain_m3s: np.ndarray | None = None) -> np.ndarray:
    """Water level in every hollow at the end of every step, shape (steps, hollows).

    `rain_mm` is the rain that falls in each step, taken as falling evenly over the
    city. Water builds while the runoff arriving is more than the drains take and goes
    down once it is less, so a street floods after the rain starts, peaks after it
    peaks, and clears after it stops.

    `drain_m3s` is what the surveyed drains carry out of each hollow, one rate per
    hollow, from app/routes/flood.py. Where it is None, or zero for a hollow the
    survey does not reach, `drain_mm_h` stands in as a flat rate over the catchment.
    """
    step_seconds = step_minutes * 60.0
    drained_m = drain_mm_h / 1000.0 * step_minutes / 60.0
    catchment = model["catchment_m2"]
    # Per step, in m³: what the drains below each hollow can take away.
    drained_m3 = np.full_like(catchment, np.nan) if drain_m3s is None else drain_m3s * step_seconds
    if drain_m3s is not None:
        # A hollow with no surveyed drain falls back to the flat allowance rather than
        # being treated as having no drainage at all.
        drained_m3 = np.where(drain_m3s > 0, drained_m3, catchment * drained_m)
    # Rain on the far edge of a catchment takes time to run to the hollow. Each hollow
    # takes its runoff spread evenly over its time of concentration: the rain of the
    # last n steps, averaged, where n is how long water takes to cross the catchment.
    travel_s = np.sqrt(catchment) / FLOW_SPEED_M_S
    lag = np.maximum(1, np.ceil(travel_s / (step_minutes * 60.0))).astype(int)
    fallen = np.r_[0.0, np.cumsum(rain_mm)]                 # rain fallen by the end of each step
    volume = np.zeros_like(catchment)
    levels = []
    for step in range(len(rain_mm)):
        arriving_mm = (fallen[step + 1] - fallen[np.maximum(0, step + 1 - lag)]) / lag
        arriving_m3 = catchment * (arriving_mm / 1000.0 * runoff_coeff)
        taken = catchment * drained_m if drain_m3s is None else drained_m3
        gain = arriving_m3 - taken
        # Past the spill point the water runs on out of the model; it is not held
        # back to delay the hollow emptying later.
        volume = np.clip(volume + gain, 0.0, model["capacity_m3"])
        levels.append(water_levels(model, volume))
    return np.array(levels)


def depth_grid(model: dict[str, Any], level: np.ndarray) -> np.ndarray:
    """Standing water depth over the whole grid at one step, metres, above the land."""
    return np.clip(level[model["labels"]] - model.get("land", model["surface"]), 0.0, None)


def timeline(series_m: np.ndarray, step_minutes: float, wet_m: float,
             impassable_m: float) -> dict[str, Any]:
    """When a depth series crosses the thresholds, in minutes from the start.

    Step i is the state at the end of minute (i + 1) x step_minutes. None where the
    series never gets there.
    """
    def first(mask: np.ndarray, after: int = 0) -> int | None:
        hits = np.flatnonzero(mask[after:])
        return None if hits.size == 0 else int(hits[0]) + after

    minutes = lambda i: None if i is None else int(round((i + 1) * step_minutes))
    wet = series_m >= wet_m
    floods = first(wet)
    peak = int(series_m.argmax())
    clears = None if floods is None else first(~wet, peak)
    return {
        "floods_at_min": minutes(floods),
        "impassable_at_min": minutes(first(series_m >= impassable_m)),
        "peak_at_min": minutes(peak) if floods is not None else None,
        "clears_at_min": minutes(clears),
    }


def load(south: float, north: float, west: float, east: float) -> dict[str, Any] | None:
    """The model over a box, built once from whichever DEM terrain.py is reading."""
    global _model
    if _model is not None:
        return _model
    dem = terrain.grid(south, north, west, east)
    if dem is None:
        return None

    ground = dem["grid"].astype(np.float64)
    outlet = ~np.isfinite(ground)
    if dem["sea_is_zero"]:
        outlet |= ground == 0.0
    if terrain.source_info()["kind"] == "surface":
        # A surface model reads roofs as ground; the road is the lowest thing around.
        ground = ndimage.minimum_filter(np.where(outlet, np.inf, ground), size=3, mode="nearest")
    if SMOOTH_CELLS > 1:
        ground = ndimage.median_filter(np.where(outlet, np.nanmin(ground[~outlet]), ground),
                                       size=SMOOTH_CELLS, mode="nearest")
    ground[outlet] = np.nan
    # The land as it is, for depths: burning only decides where water can run.
    land = np.where(np.isfinite(ground), ground, np.nanmin(ground) - 1.0)
    burned = _burn_waterways(ground, north, west, dem["lat_step"], dem["lon_step"])
    # Water leaves the box at its edges as well as at the sea.
    outlet[[0, -1], :] = True
    outlet[:, [0, -1]] = True
    surface = np.where(np.isfinite(ground), ground, np.nanmin(ground) - 1.0)

    mid = math.radians((south + north) / 2)
    cell_area = (dem["lat_step"] * terrain.M_PER_DEG) * (dem["lon_step"] * terrain.M_PER_DEG * math.cos(mid))
    model = prepare(surface, outlet, cell_area)
    model.update({"north": north, "west": west, "lat_step": dem["lat_step"],
                  "lon_step": dem["lon_step"], "outlet": outlet, "burned_cells": burned,
                  "land": land})
    _model = model
    return _model


def _channel_lines() -> list[list[list[float]]]:
    """Every channel to burn in: OpenStreetMap's waterways and the basin model's own."""
    from app.services import channels

    lines: list[list[list[float]]] = []
    if WATERWAYS_PATH.exists():
        for feature in json.loads(WATERWAYS_PATH.read_text(encoding="utf-8"))["features"]:
            lines.append(feature["geometry"]["coordinates"])
    lines.extend(channels.lines())
    return lines


def _burn_waterways(ground: np.ndarray, north: float, west: float,
                    lat_step: float, lon_step: float) -> int:
    """Lower the cells under every mapped channel by BURN_M, in place; how many."""
    if BURN_M <= 0:
        return 0
    from app.services.street_flood import densify

    # Vertices half a cell apart, so a line crossing the grid misses no cell.
    spacing = lat_step * terrain.M_PER_DEG / 2
    rows_n, cols_n = ground.shape
    hit = np.zeros(ground.shape, dtype=bool)
    for line in _channel_lines():
        xy = np.asarray(densify(line, spacing))
        rows = np.floor((north - xy[:, 1]) / lat_step).astype(int)
        cols = np.floor((xy[:, 0] - west) / lon_step).astype(int)
        inside = (rows >= 0) & (rows < rows_n) & (cols >= 0) & (cols < cols_n)
        hit[rows[inside], cols[inside]] = True
    ground[hit] -= BURN_M
    return int(hit.sum())


def _where(model: dict[str, Any], flat: int) -> list[float]:
    row, col = divmod(int(flat), model["surface"].shape[1])
    return [round(model["north"] - (row + 0.5) * model["lat_step"], 6),
            round(model["west"] + (col + 0.5) * model["lon_step"], 6)]


def _hollow(model: dict[str, Any], hollow: int, levels: np.ndarray) -> dict[str, Any]:
    """One hollow: its shape, and how full it is at its fullest."""
    first, size = model["start"][hollow], model["size"][hollow]
    ground = model["ground"][first:first + size]
    spill = model["spill_m"][hollow]
    peak = float(levels[:, hollow].max())
    held = float(np.sum(np.clip(peak - ground, 0.0, None)) * model["cell_area_m2"])
    capacity = float(model["capacity_m3"][hollow])
    return {
        "deepest_point": _where(model, model["cells"][first]),
        "spill_level_m": round(float(spill), 2),
        "max_depth_m": round(float(spill - ground[0]), 3),
        "area_m2": round(float(size * model["cell_area_m2"]), 0),
        "capacity_m3": round(capacity, 1),
        "catchment_m2": round(float(model["catchment_m2"][hollow]), 0),
        "peak_level_m": round(peak, 2),
        "peak_held_m3": round(held, 1),
        "peak_flooded_area_m2": round(float(np.sum(ground < peak) * model["cell_area_m2"]), 0),
        "full_pct": round(100.0 * held / capacity, 1) if capacity > 0 else 100.0,
        "overflows": held >= capacity * 0.999,
    }


def at(model: dict[str, Any], lat: float, lon: float, levels: np.ndarray, step_minutes: float,
       wet_m: float = 0.05, impassable_m: float = 0.30) -> dict[str, Any] | None:
    """Depth at one coordinate through the storm, and where rain falling there goes."""
    rows, cols = model["surface"].shape
    row = math.floor((model["north"] - lat) / model["lat_step"])
    col = math.floor((lon - model["west"]) / model["lon_step"])
    if not (0 <= row < rows and 0 <= col < cols) or model["outlet"][row, col]:
        return None

    ground = float(model.get("land", model["surface"])[row, col])
    here = int(model["labels"][row, col])
    into = int(model["basins"][row, col])
    series = np.clip(levels[:, here] - ground, 0.0, None) if here else np.zeros(len(levels))
    return {
        "lat": lat, "lon": lon,
        "ground_m": round(ground, 2),
        "depth_cm": np.round(series * 100, 1).tolist(),
        "peak_depth_cm": round(float(series.max()) * 100, 1),
        **timeline(series, step_minutes, wet_m, impassable_m),
        # In a hollow the rain stands here; outside one it runs off to the hollow
        # (or the sea) its catchment belongs to.
        "in_hollow": bool(here),
        "hollow": _hollow(model, here, levels) if here else None,
        "drains_to": None if here or into in (0, model["sea"]) else _hollow(model, into, levels),
        "drains_to_sea_or_edge": into == model["sea"],
        # The named channel and river basin this place belongs to, so the answer reads
        # "Cooum basin, by the Otteri Nullah" and not just as a dip in the terrain.
        "channel": channels.nearest(lat, lon),
    }


def flooded_streets(model: dict[str, Any], levels: np.ndarray, step_minutes: float,
                    min_depth_m: float = 0.05, impassable_m: float = 0.30,
                    ) -> tuple[list[dict[str, Any]], dict[str, list[float]]]:
    """Street stretches that go under at any point in the storm, and when.

    Streets are the densified OpenStreetMap ways street_flood indexes. A stretch runs
    over consecutive vertices wet at their own peak, plus one either side: the water's
    edge lies between a wet vertex and a dry one, and 20 m vertex spacing is finer than
    a 30 m DEM cell.

    Each vertex carries its ground level and the hollow it sits in, and the second
    value returned is the water level of each of those hollows at every step. Depth at
    a vertex at a step is level - ground, so a map can redraw any minute without
    another request, and the per-step series stays per hollow rather than per vertex.
    """
    streets, coords, street_of, cell, step_m = _street_cells(model)
    on_grid = cell >= 0
    at_cell = np.maximum(cell, 0)
    land = np.where(on_grid, model["land"].ravel()[at_cell], np.nan)
    hollow_of = np.where(on_grid, model["labels"].ravel()[at_cell], 0)

    # Depth through time only where a vertex sits in a hollow; everywhere else it is 0.
    in_hollow = np.flatnonzero(hollow_of > 0)
    series = np.clip(levels[:, hollow_of[in_hollow]] - land[in_hollow], 0.0, None)
    column = np.full(len(coords), -1)
    column[in_hollow] = np.arange(in_hollow.size)
    peak = np.zeros(len(coords))
    peak[in_hollow] = series.max(axis=0) if in_hollow.size else 0.0

    wet = peak >= min_depth_m
    # A run starts where a wet vertex follows a dry one or begins a street, and ends
    # likewise, so a run never crosses from one street into the next.
    new_street = np.r_[True, street_of[1:] != street_of[:-1]]
    last_of_street = np.r_[new_street[1:], True]
    firsts = np.flatnonzero(wet & (new_street | ~np.r_[False, wet[:-1]]))
    lasts = np.flatnonzero(wet & (last_of_street | ~np.r_[wet[1:], False]))

    # Rounded once for the whole city, then sliced: rounding per vertex per stretch
    # was most of the time this took.
    peak_cm = np.round(peak * 100, 1).tolist()
    ground_m = [None if g != g else g for g in np.round(land, 3).tolist()]
    hollows = hollow_of.tolist()
    xy = np.round(coords, 5).tolist()
    step_total = np.r_[0.0, np.cumsum(step_m)]
    used: set[int] = set()

    features: list[dict[str, Any]] = []
    for first, last in zip(firsts.tolist(), lasts.tolist()):
        lo = first if new_street[first] else first - 1
        hi = last + 1 if last_of_street[last] else last + 2
        street = streets[street_of[first]]
        # Road under water at the peak: every gap between two wet vertices, plus half
        # the gap out to the dry vertex at each end, where the water's edge lies.
        wet_m = step_total[last] - step_total[first]
        wet_m += 0.5 * step_m[first - 1] if lo < first else 0.0
        wet_m += 0.5 * step_m[last] if hi > last + 1 else 0.0
        deepest = first + int(np.argmax(peak[first:last + 1]))
        # The deepest water anywhere on the stretch at each step.
        worst = series[:, column[first:last + 1]].max(axis=1)
        used.update(h for h in hollows[lo:hi] if h)
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": xy[lo:hi]},
            "properties": {
                "name": street["name"],
                "highway": street["highway"],
                "tunnel": street.get("tunnel", False),
                "max_depth_cm": peak_cm[deepest],
                "mean_depth_cm": round(float(peak[first:last + 1].mean()) * 100, 1),
                "wet_m": round(max(float(wet_m), 1.0), 1),
                "deepest": [xy[deepest][1], xy[deepest][0]],
                **timeline(worst, step_minutes, min_depth_m, impassable_m),
                "series_cm": np.round(worst * 100, 1).tolist(),
                "depth_cm": peak_cm[lo:hi],
                "ground_m": ground_m[lo:hi],
                "hollow": hollows[lo:hi],
            },
        })
    features.sort(key=lambda f: f["properties"]["max_depth_cm"], reverse=True)
    water = {str(h): np.round(levels[:, h], 3).tolist() for h in sorted(used)}
    return features, water


_streets: tuple | None = None


def _street_cells(model: dict[str, Any]) -> tuple:
    """Every street vertex, flattened, with the grid cell it falls in (-1 off the grid)
    and the distance on to the next vertex of the same street (0 at its end)."""
    global _streets
    if _streets is None:
        from app.services import street_flood

        streets = street_flood._load()["streets"]
        coords = np.array([xy for s in streets for xy in s["coords"]], dtype=np.float64).reshape(-1, 2)
        street_of = np.repeat(np.arange(len(streets)), [len(s["coords"]) for s in streets])
        rows_n, cols_n = model["surface"].shape
        rows = np.floor((model["north"] - coords[:, 1]) / model["lat_step"]).astype(np.int64)
        cols = np.floor((coords[:, 0] - model["west"]) / model["lon_step"]).astype(np.int64)
        inside = (rows >= 0) & (rows < rows_n) & (cols >= 0) & (cols < cols_n)
        cell = np.where(inside, rows * cols_n + cols, -1)
        dx = np.diff(coords[:, 0]) * terrain.M_PER_DEG * np.cos(np.radians(coords[:-1, 1]))
        dy = np.diff(coords[:, 1]) * terrain.M_PER_DEG
        step_m = np.r_[np.where(street_of[1:] == street_of[:-1], np.hypot(dx, dy), 0.0), 0.0]
        _streets = (streets, coords, street_of, cell, step_m)
    return _streets


if __name__ == "__main__":
    # A 5 x 5 bowl, 9 floor cells at 10 m, rim at 11 m, 100 m² cells, the outer ring
    # the outlet. Floor holds 900 m³ to the rim.
    bowl = np.full((7, 7), 12.0)
    bowl[1:6, 1:6] = 11.0
    bowl[2:5, 2:5] = 10.0
    edge = np.zeros_like(bowl, dtype=bool)
    edge[[0, -1], :] = edge[:, [0, -1]] = True
    bowl[edge] = 0.0                                 # the sea, below everything
    m = prepare(bowl, edge, 100.0)
    assert m["labels"][3, 3] > 0 and m["labels"][1, 1] == 0
    h = int(m["labels"][3, 3])
    assert abs(m["spill_m"][h] - 11.0) < 1e-9 and abs(m["capacity_m3"][h] - 900.0) < 1e-9
    # The floor and the slope inside the rim drain into the bowl; rim cells sit on the
    # divide, so the watershed may give them either way.
    catch = m["catchment_m2"][h]
    assert 900.0 <= catch <= 2500.0, catch

    vol = np.zeros(m["sea"] + 1)
    for poured, depth in ((90.0, 0.1), (450.0, 0.5), (900.0, 1.0), (5000.0, 1.0)):
        vol[h] = poured
        assert abs(water_levels(m, vol)[h] - (10.0 + depth)) < 1e-9, (poured, water_levels(m, vol)[h])
    # Matches terrain.fill_depth on the same bowl: one fill rule, two callers.
    vol[h] = 90.0
    assert abs(water_levels(m, vol)[h] - 10.0 - terrain.fill_depth(bowl[1:6, 1:6], 100.0, 90.0)["max_depth_m"]) < 1e-9

    # Rain sized to put 90 m³ in the bowl over three 5 minute steps, then none: the
    # water builds to 10 cm, and with no drains it stays.
    per_step = 90.0 / (0.9 * catch) * 1000.0 / 3
    held = simulate(m, [per_step] * 3 + [0.0] * 3, 0.9, 0.0, 5.0)
    floor = held[:, h] - 10.0
    assert np.all(np.diff(floor[:3]) > 0) and abs(floor[2] - 0.1) < 1e-9 and abs(floor[-1] - 0.1) < 1e-9
    # With drains, the same storm peaks lower and drains away once the rain stops.
    drain = per_step * 0.9 * 12 / 2                  # half the runoff rate, in mm/h
    fed = simulate(m, [per_step] * 3 + [0.0] * 3, 0.9, drain, 5.0)[:, h] - 10.0
    assert abs(fed[2] - 0.05) < 1e-9 and fed[-1] < 1e-9, fed
    # A slow catchment (3 steps to cross) fills later but, with no drains, to the same
    # level in the end: the lag moves water in time, it does not lose any.
    FLOW_SPEED_M_S, fast = math.sqrt(catch) / (2.5 * 300.0), FLOW_SPEED_M_S
    slow = simulate(m, [per_step] * 3 + [0.0] * 3, 0.9, 0.0, 5.0)[:, h] - 10.0
    FLOW_SPEED_M_S = fast
    assert slow[0] < floor[0] and slow[2] < floor[2] and abs(slow[-1] - floor[-1]) < 1e-9, (slow, floor)
    t = timeline(np.r_[0.0, 0.06, 0.4, 0.2, 0.01, 0.0], 5.0, 0.05, 0.30)
    assert t == {"floods_at_min": 10, "impassable_at_min": 15, "peak_at_min": 15, "clears_at_min": 25}, t
    assert timeline(np.zeros(4), 5.0, 0.05, 0.3)["floods_at_min"] is None

    m.update({"north": 7.0, "west": 0.0, "lat_step": 1.0, "lon_step": 1.0, "outlet": edge})
    assert abs(depth_grid(m, held[2])[3, 3] - 0.1) < 1e-9 and depth_grid(m, held[2])[1, 3] == 0.0
    rim = at(m, 5.5, 3.5, held, 5.0)                 # row 1, col 3: on the rim
    assert rim and not rim["in_hollow"] and rim["peak_depth_cm"] == 0.0, rim
    centre = at(m, 3.5, 3.5, held, 5.0)
    assert abs(centre["peak_depth_cm"] - 10.0) < 1e-6 and centre["floods_at_min"] == 10, centre

    # Against the real DEM, if there is one.
    import time
    _model = None
    began = time.perf_counter()
    city = load(12.85, 13.25, 80.10, 80.35)
    if city is not None:
        built = time.perf_counter() - began
        hollows = int((city["size"][1:city["sea"]] > 0).sum())
        # 36 five-minute steps: an hour at 60 mm/h, then two dry hours, drains 20 mm/h.
        rain = [5.0] * 12 + [0.0] * 24
        began = time.perf_counter()
        levels = simulate(city, rain, 0.75, 20.0, 5.0)
        ran = time.perf_counter() - began
        began = time.perf_counter()
        streets, water = flooded_streets(city, levels, 5.0)
        drawn = time.perf_counter() - began
        assert streets and all(f["properties"]["max_depth_cm"] >= 5.0 for f in streets)
        # Every stretch floods while it rains or after, never before the first step.
        assert all(f["properties"]["floods_at_min"] >= 5 for f in streets)
        # The client's depth - level minus ground - reproduces the server's peak.
        f = streets[len(streets) // 2]["properties"]
        client = max(max((water[str(h)][s] - g) * 100 for s in range(len(rain)))
                     for h, g in zip(f["hollow"], f["ground_m"]) if h)
        assert abs(client - f["max_depth_cm"]) < 0.2, (client, f["max_depth_cm"])
        soon = sorted(streets, key=lambda f: f["properties"]["floods_at_min"])[0]["properties"]
        print(f"{hollows} hollows, built in {built:.1f} s; 36 steps simulated in {ran:.2f} s, "
              f"{len(streets)} stretches in {drawn:.1f} s")
        print(f"first under: {soon['name'] or 'unnamed'} at +{soon['floods_at_min']} min; "
              f"clearing by +{max(f['properties']['clears_at_min'] or 999 for f in streets)} min at the latest "
              f"(999 = still wet at 3 h)")
    print("rain_ponding self-check passed")
