"""The ground the flood runs over: the DEM cut into storage zones, and the streets on it.

The coupled model (app/services/coupled.py) moves water between these zones and the
drains. This module prepares the terrain it runs on, and reads the answer back out
street by street.

  1. Condition. The DEM is smoothed, and every mapped river, canal and nullah is burned
     into it, so a 30 m grid does not read road embankments and culverts as dams.
  2. Zones. Every regional minimum at least MIN_HOLLOW_M deep seeds a zone, and a
     watershed gives each cell the minimum its rain runs to. Nested hollows stay
     separate zones - two dips either side of a low ridge fill on their own and join
     only once water tops the ridge, which the surface model's weirs decide. The sea
     and the edge of the box form one more zone, where water leaves the model.
  3. Streets. Every 20 m point of every OpenStreetMap road sits in a zone; its depth at
     a step is the zone's water level less its ground.
"""

import json
import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage
from skimage.morphology import h_minima
from skimage.segmentation import watershed

from app.services import terrain

# A dip shallower than this is DEM noise, not somewhere water stands on its own.
MIN_HOLLOW_M = terrain.MIN_DEPTH_M

# Median filter over the DEM before anything else, in cells. At 30 m, GEDTM30 in a
# dense block like T. Nagar jumps 3-5 m cell to cell where buildings bleed through, and
# every such pit fills to its rim: 4 m of "water" on Venkatanarayana Road. A 3 x 3
# median (90 m) removes those pits and keeps Pallikaranai, Porur and Puzhal; 5 x 5
# starts to erase Porur lake. A calibration knob: set it against surveyed flood marks.
SMOOTH_CELLS = 3

# Stream burning: DEM cells under a mapped river, canal or nullah are lowered by this
# much. Every road embankment and culvert across a canal reads as a dam at 30 m, and
# upstream of each one the canal becomes a closed "lake" several metres deep. Burning
# the channel in lets it drain the way the real one does. Two channel layers, because
# neither is complete: OpenStreetMap's waterways, and the basin model's own macro and
# micro drains, canals and surplus channels (app/services/channels.py).
BURN_M = 2.0
WATERWAYS_PATH = Path(__file__).resolve().parent.parent / "data" / "chennai_waterways.geojson"

_EIGHT = np.ones((3, 3), dtype=bool)
_model: dict[str, Any] | None = None
_load_lock = threading.Lock()


def prepare(surface: np.ndarray, outlet: np.ndarray, cell_area_m2: float) -> dict[str, Any]:
    """Storage zones for one ground grid.

    `surface` is finite everywhere; `outlet` marks the cells where water leaves the
    model (the sea, the edge of the box), which all become the last zone, `sea`.
    """
    # Outlets set below everything, so the ground falling towards the sea is not taken
    # for a hollow at the shoreline.
    ground = np.where(outlet, surface.min() - 1.0, surface)
    minima = h_minima(ground, MIN_HOLLOW_M).astype(bool) & ~outlet
    labels, count = ndimage.label(minima, structure=_EIGHT)
    sea = count + 1
    markers = labels.copy()
    markers[outlet] = sea
    zones = watershed(surface, markers, connectivity=2)
    return {"surface": surface, "labels": labels, "basins": zones, "sea": sea,
            "cell_area_m2": cell_area_m2}


def model_inputs() -> list[Path]:
    """Every file the conditioned terrain is built from, code included."""
    from app.services import channels, diskcache, street_flood

    dems = [Path(terrain.DATA_DIR.parent.parent / f) for f in terrain.source_info()["files"]]
    channel_files = [channels.DATA_DIR / name for name in channels.FILES]
    return [*dems, BUILDINGS_PATH, WATER_PATH, WARD_POINTS_PATH, WATERWAYS_PATH, *channel_files,
            Path(__file__), *diskcache.sources(terrain, channels, street_flood)]


def load(south: float, north: float, west: float, east: float) -> dict[str, Any] | None:
    """The zones over a box, built once: from disk if nothing it depends on has changed."""
    global _model
    if _model is not None:
        return _model
    from app.services import diskcache

    with _load_lock:
        if _model is None and terrain.grid(south, north, west, east) is not None:
            _model = diskcache.cached(f"terrain-{south}-{north}-{west}-{east}", model_inputs(),
                                      lambda: _build(south, north, west, east))
    return _model


def _build(south: float, north: float, west: float, east: float) -> dict[str, Any] | None:
    """Condition the DEM and cut it into storage zones."""
    dem = terrain.grid(south, north, west, east)
    if dem is None:
        return None

    ground = dem["grid"].astype(np.float64)
    # The sea: no-data, or exactly 0 m. Copernicus flattens open water to 0, and GEDTM30
    # stores stretches of the Bay of Bengal as 0 too; land never reads exactly 0.0.
    outlet = ~np.isfinite(ground) | (ground == 0.0)
    if terrain.source_info()["kind"] == "surface":
        # A surface model reads roofs as ground; the road is the lowest thing around.
        ground = ndimage.minimum_filter(np.where(outlet, np.inf, ground), size=3, mode="nearest")
    if SMOOTH_CELLS > 1:
        ground = ndimage.median_filter(np.where(outlet, np.nanmin(ground[~outlet]), ground),
                                       size=SMOOTH_CELLS, mode="nearest")
    grid_at = dict(north=north, west=west, step=dem["lat_step"], shape=ground.shape)
    # Down to the surveyed road levels where the ward sheets measured them.
    correction = survey_correction(dem["grid"], **grid_at)
    # Taking off buildings and trees cannot dig below the bare ground around them: the
    # lowest reading within FLOOR_RADIUS_M is a floor. Without it the beach, a few
    # hundred metres from built-up blocks, went below the sea.
    floor_size = max(3, int(round(2 * FLOOR_RADIUS_M / (dem["lat_step"] * terrain.M_PER_DEG))) | 1)
    floor = ndimage.minimum_filter(np.where(np.isfinite(ground), ground, np.inf), size=floor_size)
    lowered = np.maximum(ground - correction, floor)
    correction = np.where(np.isfinite(ground), ground - lowered, 0.0)     # as applied
    ground = lowered
    # Pits too small to be anything but noise are filled; mapped water bodies are kept.
    water = _grid_layer(WATER_PATH, **grid_at)
    ground, pits = _fill_pits(ground, outlet, water)
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
    buildings = _grid_layer(BUILDINGS_PATH, **grid_at)
    model.update({"north": north, "west": west, "lat_step": dem["lat_step"],
                  "lon_step": dem["lon_step"], "outlet": outlet, "burned_cells": burned,
                  "land": land, "water": water,
                  # What share of each cell water can stand on: all of it but the buildings.
                  "open": np.ones(ground.shape) if buildings is None else 1.0 - buildings,
                  "conditioning": {
                      "survey_correction_m": {
                          **getattr(survey_correction, "last", {}),
                          "median_over_land": round(float(np.median(correction[~outlet])), 2),
                          "max": round(float(correction[~outlet].max()), 2)},
                      "pits_filled_cells": pits,
                      "buildings": buildings is not None,
                      "water_bodies": water is not None,
                  }})
    return model


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BUILDINGS_PATH = DATA_DIR / "building_fraction.npz"
WATER_PATH = DATA_DIR / "water_bodies.npz"
WARD_POINTS_PATH = DATA_DIR / "ward_points.json"

# A closed hollow covering fewer cells than this (about 1.5 ha at 30 m) is taken for DEM
# noise - a building footprint bleeding through a bare-earth model - and filled to its
# rim, unless a mapped water body is there. A calibration knob: raise it and fewer small
# low spots survive; temple tanks and ponds are kept by the water layer either way.
PIT_MAX_CELLS = 16
# A deeper hollow is noise over a larger area: on ground as flat as Chennai's nothing
# but a tank, a lake or a quarry is more than DEEP_PIT_M deep and under DEEP_PIT_CELLS
# (about 6 ha), and the tanks and lakes are mapped. Checked on 13th Street, Ashok
# Nagar: a 30 m hollow read 2 m deep five minutes into a storm.
DEEP_PIT_CELLS = 64
DEEP_PIT_M = 1.0
# A cell at least this much water body keeps its real ground.
WATER_SHARE = 0.3


def _grid_layer(path: Path, north: float, west: float, step: float, shape: tuple) -> np.ndarray | None:
    """A 0-1 share per cell saved by fetch_buildings.py, if it is on this grid."""
    if not path.exists():
        return None
    data = np.load(path)
    same = (abs(float(data["north"]) - north) < 1e-7 and abs(float(data["west"]) - west) < 1e-7
            and abs(float(data["step"]) - step) < 1e-9 and data["fraction"].shape == shape)
    return data["fraction"].astype(np.float64) / 250.0 if same else None


def _fill_pits(ground: np.ndarray, outlet: np.ndarray, water: np.ndarray | None) -> tuple[np.ndarray, int]:
    """Fill every closed hollow smaller than PIT_MAX_CELLS, and every one deeper than
    DEEP_PIT_M smaller than DEEP_PIT_CELLS, nested ones included; mapped water stays."""
    from skimage.morphology import area_closing

    if PIT_MAX_CELLS <= 1:
        return ground, 0
    low = np.nanmin(ground[~outlet]) - 1.0
    work = np.where(outlet, low, ground)
    closed = area_closing(work, area_threshold=PIT_MAX_CELLS, connectivity=2)
    wider = area_closing(work, area_threshold=DEEP_PIT_CELLS, connectivity=2)
    closed = np.where(wider - work > DEEP_PIT_M, wider, closed)
    if water is not None:
        closed = np.where(water >= WATER_SHARE, work, closed)
    closed = np.where(outlet, ground, closed)
    return closed, int((((closed - work) > 1e-6) & ~outlet).sum())


# The survey correction. Tested by predicting each surveyed ward from all the others
# (75 wards, 4,513 manholes): no correction leaves 4.55 m RMS error, one city-wide
# offset 2.05 m, a background rising with building cover plus the local readings
# 1.77 m. What remains is the 1.5 m scatter inside a single ward.
CORRECTION_SIGMA_M = 800.0          # how far one manhole's reading reaches
CORRECTION_PRIOR_POINTS = 3.0       # readings needed nearby to outweigh the background
# Building cover is averaged over this radius for the background...
COVER_RADIUS_M = 75.0
# The corrected ground never goes below the lowest ground within this radius.
FLOOR_RADIUS_M = 300.0


def _survey_readings(raw: np.ndarray, north: float, west: float, step: float,
                     shape: tuple) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DEM - surveyed road edge at every manhole placed exactly on its sheet:
    rows, columns and values, with each sheet's misreads dropped."""
    if not WARD_POINTS_PATH.exists():
        return np.array([], int), np.array([], int), np.array([])
    rows, cols, values = [], [], []
    for sheet in json.loads(WARD_POINTS_PATH.read_text(encoding="utf-8"))["sheets"]:
        if (sheet.get("georeference") or {}).get("from") != ["grid lines"]:
            continue
        pts = [p for p in sheet["points"] if p.get("road_edge_m") is not None and "lat" in p
               and p.get("position_from") == "manhole symbol"]
        if len(pts) < 5:
            continue
        r = np.floor((north - np.array([p["lat"] for p in pts])) / step).astype(int)
        c = np.floor((np.array([p["lon"] for p in pts]) - west) / step).astype(int)
        inside = (r >= 0) & (r < shape[0]) & (c >= 0) & (c < shape[1])
        r, c = r[inside], c[inside]
        diff = raw[r, c] - np.array([p["road_edge_m"] for p, ok in zip(pts, inside) if ok])
        good = np.isfinite(diff)
        if good.sum() < 5:
            continue
        med = np.median(diff[good])
        spread = 1.4826 * np.median(np.abs(diff[good] - med)) or 0.5
        good &= np.abs(diff - med) <= 3 * spread
        rows.extend(r[good]); cols.extend(c[good]); values.extend(diff[good])
    return np.array(rows, int), np.array(cols, int), np.array(values)


def survey_correction(raw: np.ndarray, north: float, west: float, step: float,
                      shape: tuple) -> np.ndarray:
    """How much to lower the DEM at every cell to meet the surveyed road levels, m.

    The GCC / SECON-JBA ward sheets (Ward/*.pdf, read by digitise_wards.py) give the
    road-edge level above MSL beside every manhole, on the same datum as the DEM (their
    ground-level control points agree with it to 0.3 m). Against them GEDTM30 stands a
    median 4 m high in the built-up wards - a 30 m bare-earth model keeps part of the
    buildings and trees - and higher the denser the building.

    The gap is fitted as a + b * cover, with cover the OSM building share around the
    manhole: b, about 3 m from open to fully built, is the building and tree clutter a
    30 m bare-earth model keeps, and it is taken off. The constant a, about 2.9 m, is
    not: surveyed streets with no mapped building near them still show it, while
    Chennai Egmore (8 m), Mambalam (13 m) and Guindy (12 m) stations, rail level from
    Indian Railways, stand at or above the raw DEM, and Marina beach is only 2 m above
    the sea on it. A uniform 2.9 m that open ground, the beach and the stations do not
    share is more likely the sheets' levelling datum than the DEM, and a constant does
    not change which way water runs; it is reported, not applied, until GCC's benchmark
    records settle it.

    Near the manholes their own departures from the fit are added, spread with
    CORRECTION_SIGMA_M and weighted against CORRECTION_PRIOR_POINTS readings' worth of
    nothing, so surveyed areas get their own shape and there is no step at the survey's
    edge.
    """
    zero = np.zeros(shape)
    rows, cols, values = _survey_readings(raw, north, west, step, shape)
    buildings = _grid_layer(BUILDINGS_PATH, north=north, west=west, step=step, shape=shape)
    if values.size < 50:
        return zero
    cell_m = step * terrain.M_PER_DEG
    if buildings is None:
        background = np.zeros(shape)
        fit = (float(np.median(values)), 0.0)
    else:
        size = max(1, int(round(2 * COVER_RADIUS_M / cell_m)) | 1)
        cover = ndimage.uniform_filter(buildings, size)
        slope, intercept = np.polyfit(cover[rows, cols], values, 1)
        background = slope * cover
        fit = (float(intercept), float(slope))
    # Only what varies from place to place is corrected: the building clutter, and each
    # surveyed area's own departure from the fit. The fit's constant is not - see above.
    departure = values - fit[0] - background[rows, cols]
    total, count = np.zeros(shape), np.zeros(shape)
    np.add.at(total, (rows, cols), departure)
    np.add.at(count, (rows, cols), 1.0)
    sigma = CORRECTION_SIGMA_M / cell_m
    spread_total = ndimage.gaussian_filter(total, sigma, mode="constant")
    spread_count = ndimage.gaussian_filter(count, sigma, mode="constant")
    one = 1.0 / (2 * math.pi * sigma ** 2)          # one reading's weight at its own cell
    correction = background + spread_total / (spread_count + CORRECTION_PRIOR_POINTS * one)
    survey_correction.last = {"readings": int(values.size), "fit_m": [round(v, 2) for v in fit],
                              "constant_not_applied_m": round(fit[0], 2)}
    return correction


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


def depth_grid(model: dict[str, Any], level: np.ndarray) -> np.ndarray:
    """Standing water depth over the whole grid at one step, metres, above the land.

    `level` is one water level per zone, -inf where the zone is dry.
    """
    return np.clip(level[model["basins"]] - model["land"], 0.0, None)


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


def flooded_streets(model: dict[str, Any], levels: np.ndarray, step_minutes: float,
                    min_depth_m: float = 0.05, impassable_m: float = 0.30,
                    ) -> tuple[list[dict[str, Any]], dict[str, list[float]]]:
    """Street stretches that go under at any point in the storm, and when.

    Streets are the densified OpenStreetMap ways street_flood indexes. A stretch runs
    over consecutive vertices wet at their own peak, plus one either side: the water's
    edge lies between a wet vertex and a dry one, and 20 m vertex spacing is finer than
    a 30 m DEM cell.

    Each vertex carries its ground level and the zone it sits in, and the second value
    returned is the water level of each of those zones at every step. Depth at a vertex
    at a step is level - ground, so a map can redraw any minute without another request.
    """
    streets, coords, street_of, cell, step_m = _street_cells(model)
    on_grid = cell >= 0
    at_cell = np.maximum(cell, 0)
    land = np.where(on_grid, model["land"].ravel()[at_cell], np.nan)
    zone_of = np.where(on_grid, model["basins"].ravel()[at_cell], model["sea"])

    # Depth through time where a vertex sits on land; the sea zone is always dry.
    # A street point in a mapped lake or tank reads the water surface, not a road.
    in_water = np.zeros(len(coords), dtype=bool)
    if model.get("water") is not None:
        in_water = np.where(on_grid, model["water"].ravel()[at_cell] >= WATER_SHARE, False)
    on_land = np.flatnonzero((zone_of != model["sea"]) & ~in_water)
    series = np.clip(levels[:, zone_of[on_land]] - land[on_land], 0.0, None)
    column = np.full(len(coords), -1)
    column[on_land] = np.arange(on_land.size)
    peak = np.zeros(len(coords))
    peak[on_land] = series.max(axis=0) if on_land.size else 0.0

    wet = peak >= min_depth_m
    # A run starts where a wet vertex follows a dry one or begins a street, and ends
    # likewise, so a run never crosses from one street into the next.
    new_street = np.r_[True, street_of[1:] != street_of[:-1]]
    last_of_street = np.r_[new_street[1:], True]
    firsts = np.flatnonzero(wet & (new_street | ~np.r_[False, wet[:-1]]))
    lasts = np.flatnonzero(wet & (last_of_street | ~np.r_[wet[1:], False]))

    # Rounded once for the whole city, then sliced.
    peak_cm = np.round(peak * 100, 1).tolist()
    ground_m = [None if g != g else g for g in np.round(land, 3).tolist()]
    zones = np.where(zone_of == model["sea"], 0, zone_of).tolist()
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
        used.update(z for z in zones[lo:hi] if z)
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
                "storage": zones[lo:hi],
            },
        })
    features.sort(key=lambda f: f["properties"]["max_depth_cm"], reverse=True)
    # Dry steps are sent as null: JSON has no -inf.
    water = {str(z): [None if not math.isfinite(v) else round(v, 3) for v in levels[:, z].tolist()]
             for z in sorted(used)}
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
    # Two dips either side of a low ridge, inside a rim, the sea around the edge: two
    # zones, not one, so each fills on its own until water tops the ridge.
    ground = np.full((9, 15), 11.0)
    ground[2:7, 2:7] = 10.0
    ground[2:7, 8:13] = 9.0
    ground[2:7, 7] = 10.5
    ground[[0, -1], :] = ground[:, [0, -1]] = -1.0
    m = prepare(ground, ground < 0, 100.0)
    west, east = int(m["basins"][4, 4]), int(m["basins"][4, 10])
    assert west != east and m["sea"] not in (west, east), m["basins"]
    assert m["basins"][0, 0] == m["sea"]
    # A 1 cm dip is noise and seeds nothing.
    flat = np.full((5, 5), 5.0)
    flat[2, 2] = 4.99
    flat[[0, -1], :] = flat[:, [0, -1]] = 0.0
    assert prepare(flat, flat == 0.0, 100.0)["sea"] == 1

    # Pit filling: a one-cell pit 2 m deep is noise and goes; a 25-cell hollow stays; the
    # same pit under a mapped water body stays.
    field = np.full((12, 12), 10.0)
    field[2, 2] = 8.0
    field[5:10, 5:10] = 9.0
    edge12 = np.zeros_like(field, dtype=bool)
    edge12[[0, -1], :] = edge12[:, [0, -1]] = True
    filled, n = _fill_pits(field.copy(), edge12, None)
    assert filled[2, 2] == 10.0 and filled[7, 7] == 9.0 and n == 1, (filled[2, 2], filled[7, 7], n)
    pond = np.zeros_like(field)
    pond[2, 2] = 1.0
    assert _fill_pits(field.copy(), edge12, pond)[0][2, 2] == 8.0
    # The same 25-cell hollow 2 m deep is too deep for its size: filled.
    deep = field.copy()
    deep[5:10, 5:10] = 8.0
    assert _fill_pits(deep, edge12, None)[0][7, 7] == 10.0

    m.update({"land": ground})
    level = np.full(m["sea"] + 1, -np.inf)
    level[west] = 10.1
    depth = depth_grid(m, level)
    assert abs(depth[4, 4] - 0.1) < 1e-9 and depth[4, 10] == 0.0 and depth[0, 0] == 0.0

    t = timeline(np.r_[0.0, 0.06, 0.4, 0.2, 0.01, 0.0], 5.0, 0.05, 0.30)
    assert t == {"floods_at_min": 10, "impassable_at_min": 15, "peak_at_min": 15, "clears_at_min": 25}, t
    assert timeline(np.zeros(4), 5.0, 0.05, 0.3)["floods_at_min"] is None
    print("rain_ponding self-check passed")
