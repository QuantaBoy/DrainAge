"""The ground the water ends up on: a DEM, read where it is needed.

A surcharged drain does not stop at "over capacity". The water it cannot take comes
back up at the manhole and runs downhill until the street holds it, so turning a spill
rate into a street-level depth needs the terrain around that manhole.

The DEM is 1 arc-second (about 30 m at Chennai's latitude) in plain WGS 84: the
OpenTopography download fetch_dem.py saves (GEDTM30 bare earth by preference), or the
Copernicus surface-model tiles. Either way a lon/lat maps onto a pixel with the tie
point, pixel scale and raster type from the GeoTIFF itself - no reprojection, no GDAL.

ponytail: 30 m says nothing about the camber of one street, so a depth here is the
depth of the depression a street sits in, not of the kerb line. Swapping in a LiDAR
or drone DSM changes only the files this module reads.
"""

import math
from pathlib import Path
from typing import Any

import numpy as np
import tifffile

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TILE_PATTERN = "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif"

# Metres per degree of latitude; longitude is scaled by cos(latitude) where it matters.
M_PER_DEG = 111_320.0

# How far around a manhole the water is allowed to look for somewhere to sit. One
# block: beyond that it is a different street's problem, and on ground as flat as
# Chennai's a wider window just smears the water into millimetres.
PONDING_RADIUS_M = 120.0

# Water coming up a manhole stands on the road before it goes anywhere else, and the
# road holds it until it is over the kerb. These two say how much that is.
# ponytail: one width and one kerb for the whole city; a road-width attribute on the
# street layer would replace both.
ROAD_WIDTH_M = 12.0
KERB_M = 0.15

# Water shallower than this is wet tarmac, not a flood, and is not reported.
MIN_DEPTH_M = 0.02

# Which DEM the model reads. fetch_dem.py saves OpenTopography downloads to
# app/data/dem/<DEMTYPE>.tif; the Copernicus tiles in app/data are the fallback.
# DEM_SOURCE in .env picks one ("GEDTM30", "COP30", "tiles"); unset, a bare-earth
# model wins over a surface one, and any download over the tiles.
DEM_DIR = DATA_DIR / "dem"
DEM_PREFERENCE = ("GEDTM30", "COP30", "NASADEM", "AW3D30", "SRTMGL1", "COP90")
# A terrain model (DTM) has buildings and trees taken out; a surface model (DSM) reads
# roofs and canopy as ground and needs the road-level trick in ground_level().
TERRAIN_MODELS = {"GEDTM30"}

# GeoTIFF raster type key: whether the tie point is the corner of the first pixel
# (PixelIsArea) or its centre (PixelIsPoint).
_RASTER_TYPE_KEY, _PIXEL_IS_POINT = 1025, 2

_sources: list[dict[str, Any]] | None = None


def _read_raster(path: Path, name: str) -> dict[str, Any]:
    """One GeoTIFF, with its extent worked out from the tags it carries."""
    import logging

    logging.getLogger("tifffile").setLevel(logging.ERROR)
    with tifffile.TiffFile(str(path)) as handle:
        page = handle.pages[0]
        tags = page.tags
        scale = tags["ModelPixelScaleTag"].value
        tie = tags["ModelTiepointTag"].value
        keys = tags["GeoKeyDirectoryTag"].value
        geokeys = {keys[i]: keys[i + 3] for i in range(4, len(keys), 4)}
        grid = page.asarray().astype(np.float32)
        nodata = tags["GDAL_NODATA"].value if "GDAL_NODATA" in tags else None

    lon_step, lat_step = float(scale[0]), float(scale[1])
    west, north = float(tie[3]), float(tie[4])
    # Copernicus files are PixelIsPoint: the tie point is the centre of the first
    # pixel, so its outer corner is half a cell further out. Reading it as the corner
    # puts every height half a cell (about 15 m) off.
    if geokeys.get(_RASTER_TYPE_KEY) == _PIXEL_IS_POINT:
        west -= lon_step / 2
        north += lat_step / 2

    if nodata is not None:
        try:
            grid[grid == np.float32(float(str(nodata).strip("\x00 ")))] = np.nan
        except (ValueError, OverflowError):
            pass
    # Float32 maximum is the usual no-data marker even when the tag cannot be parsed.
    grid[~np.isfinite(grid) | (np.abs(grid) > 1e30)] = np.nan

    kind = "terrain" if name in TERRAIN_MODELS else "surface"
    return {
        "name": name, "path": path, "kind": kind, "grid": grid,
        "west": west, "north": north, "lon_step": lon_step, "lat_step": lat_step,
        "east": west + grid.shape[1] * lon_step, "south": north - grid.shape[0] * lat_step,
        # Copernicus flattens open water to exactly 0 m; a model with no-data at sea
        # does not, and there 0 m is simply low ground.
        "sea_is_zero": name.startswith(("COP", "Copernicus")),
    }


def _load_sources() -> list[dict[str, Any]]:
    """The rasters the model reads, in the order they are searched."""
    global _sources
    if _sources is not None:
        return _sources

    import os

    wanted = os.environ.get("DEM_SOURCE", "").strip()
    downloads = {name: DEM_DIR / f"{name}.tif" for name in DEM_PREFERENCE}
    if wanted and wanted.lower() != "tiles":
        order = [wanted]
    elif wanted.lower() == "tiles":
        order = []
    else:
        order = [name for name in DEM_PREFERENCE if downloads[name].exists()][:1]

    sources = [_read_raster(downloads[name], name) for name in order if downloads.get(name, Path()).exists()]
    if not sources:
        sources = [_read_raster(path, "Copernicus GLO-30 tile")
                   for path in sorted(DATA_DIR.glob("Copernicus_DSM_COG_10_*_DEM.tif"))]
    _sources = sources
    return _sources


def source_info() -> dict[str, Any]:
    """Which DEM the model is reading, for the page to say so."""
    sources = _load_sources()
    if not sources:
        return {"name": None, "kind": None, "files": []}
    return {
        "name": sources[0]["name"] if len(sources) == 1 else "Copernicus GLO-30 tiles",
        "kind": sources[0]["kind"],
        "files": [str(r["path"].relative_to(DATA_DIR.parent.parent)) for r in sources],
    }


def _tile_for(lat: float, lon: float) -> dict[str, Any] | None:
    """The raster covering a point, or None off every one of them."""
    for raster in _load_sources():
        if raster["south"] <= lat <= raster["north"] and raster["west"] <= lon <= raster["east"]:
            return raster
    return None


def _cell(tile: dict[str, Any], lat: float, lon: float) -> tuple[int, int] | None:
    """Row and column of a point in a raster, or None if it is not on it.

    A point exactly on a raster's southern or eastern edge lands one row or column past
    the end, so the last cell is used rather than the point being dropped: tiles share
    those lines.
    """
    grid = tile["grid"]
    row = int((tile["north"] - lat) / tile["lat_step"])
    col = int((lon - tile["west"]) / tile["lon_step"])
    if not (0 <= row <= grid.shape[0] and 0 <= col <= grid.shape[1]):
        return None
    return min(row, grid.shape[0] - 1), min(col, grid.shape[1] - 1)


def _value(value: float) -> float | None:
    return None if not np.isfinite(value) else float(value)


def elevation(lat: float, lon: float) -> float | None:
    """Ground level at a point, metres above the geoid (about mean sea level), or None
    off the DEM or over water."""
    tile = _tile_for(lat, lon)
    cell = None if tile is None else _cell(tile, lat, lon)
    return None if cell is None else _value(tile["grid"][cell])


def ground_level(lat: float, lon: float) -> float | None:
    """Road level at a point.

    On a terrain model (GEDTM30) that is simply the DEM: buildings and trees are
    already out of it. On a surface model a 30 m cell holding a building reads the
    roof, metres above the street beside it; in a built-up block the road is the lowest
    thing around, so the minimum over the 3 x 3 cells centred here stands in for it -
    the morphological trick for pulling bare ground out of a surface model.
    """
    tile = _tile_for(lat, lon)
    cell = None if tile is None else _cell(tile, lat, lon)
    if cell is None:
        return None
    if tile["kind"] == "terrain":
        return _value(tile["grid"][cell])
    if "road" not in tile:
        from scipy.ndimage import minimum_filter

        # No-data must not win the minimum, so it is lifted out of reach and put back.
        lifted = np.where(np.isfinite(tile["grid"]), tile["grid"], np.inf)
        road = minimum_filter(lifted, size=3, mode="nearest")
        road[~np.isfinite(tile["grid"])] = np.nan
        tile["road"] = road
    return _value(tile["road"][cell])


def _window(lat: float, lon: float, radius_m: float) -> tuple[np.ndarray, float, tuple[int, int]] | None:
    """The DEM square around a point, its cell area, and where the point sits in it."""
    tile = _tile_for(lat, lon)
    if tile is None:
        return None
    grid = tile["grid"]

    cell_lat_m = tile["lat_step"] * M_PER_DEG
    cell_lon_m = tile["lon_step"] * M_PER_DEG * math.cos(math.radians(lat))
    rows = max(1, int(radius_m / cell_lat_m))
    cols = max(1, int(radius_m / cell_lon_m))

    cell = _cell(tile, lat, lon)
    if cell is None:
        return None
    row, col = cell
    top, bottom = max(0, row - rows), min(grid.shape[0], row + rows + 1)
    left, right = max(0, col - cols), min(grid.shape[1], col + cols + 1)
    if top >= bottom or left >= right:
        return None

    return grid[top:bottom, left:right], cell_lat_m * cell_lon_m, (row - top, col - left)


def grid(south: float, north: float, west: float, east: float) -> dict[str, Any] | None:
    """The DEM over a box, assembled from however many rasters it crosses.

    With the Copernicus tiles Chennai sits across the 13th parallel and is split
    between two of them; a download covers it in one. Either way the box comes back as
    one grid. Cells no raster covers are NaN rather than zero, because zero is a real
    height on this coast.
    """
    sources = [r for r in _load_sources()
               if r["south"] < north and r["north"] > south and r["west"] < east and r["east"] > west]
    if not sources:
        return None
    lat_step, lon_step = sources[0]["lat_step"], sources[0]["lon_step"]
    rows = int(round((north - south) / lat_step))
    cols = int(round((east - west) / lon_step))
    out = np.full((rows, cols), np.nan, dtype=np.float32)

    for raster in sources:
        s, n = max(south, raster["south"]), min(north, raster["north"])
        w, e = max(west, raster["west"]), min(east, raster["east"])
        if s >= n or w >= e:
            continue
        # The pixel holding each edge, by the same floor rule as _cell(), so the grid
        # and a point lookup agree on which pixel a place falls in.
        first_row = math.floor((raster["north"] - n) / lat_step + 1e-9)
        first_col = math.floor((w - raster["west"]) / lon_step + 1e-9)
        block = raster["grid"][
            first_row:first_row + int(round((n - s) / lat_step)),
            first_col:first_col + int(round((e - w) / lon_step)),
        ]
        top = int(round((north - n) / lat_step))
        left = int(round((w - west) / lon_step))
        block = block[:rows - top, :cols - left]
        out[top:top + block.shape[0], left:left + block.shape[1]] = block

    return {"grid": out, "south": south, "north": north, "west": west, "east": east,
            "lat_step": lat_step, "lon_step": lon_step,
            "sea_is_zero": sources[0]["sea_is_zero"]}


# Colour stops for heights, metres above the EGM2008 geoid (about mean sea level). Chennai's streets sit within a
# few metres of the sea, so the ramp spends most of its colours on the bottom 10 m,
# where the difference between two streets decides which one floods.
ELEVATION_STOPS = (
    (-2.0, (8, 48, 107)),
    (2.0, (33, 113, 181)),
    (5.0, (66, 171, 199)),
    (8.0, (120, 198, 121)),
    (12.0, (217, 214, 102)),
    (18.0, (230, 150, 70)),
    (30.0, (166, 97, 52)),
    (60.0, (120, 70, 45)),
)


def relief_image(dem: dict[str, Any], opacity: float = 0.7) -> bytes:
    """The DEM drawn as colour by height, shaded by slope, as a transparent PNG.

    Colour answers "how high is this street"; the hillshade answers "which way does
    the ground fall", which is what decides where water runs. Both are needed to read
    a flat city, where the colour alone barely changes from one street to the next.
    """
    from io import BytesIO

    from PIL import Image

    heights = dem["grid"].astype(np.float64)
    # Copernicus flattens open water to exactly 0 m. Left in, the Bay of Bengal is a
    # quarter of this box and would be painted as the lowest - most flood-prone - land
    # in the city, so it is left clear and the base map shows through.
    valid = np.isfinite(heights)
    if dem.get("sea_is_zero"):
        valid &= heights != 0.0
    filled = np.where(valid, heights, np.nanmin(heights) if valid.any() else 0.0)

    levels = np.array([stop[0] for stop in ELEVATION_STOPS])
    colours = np.array([stop[1] for stop in ELEVATION_STOPS], dtype=np.float64)
    rgb = np.stack([np.interp(filled, levels, colours[:, band]) for band in range(3)], axis=-1)

    # Hillshade, light from the north-west at 45 degrees, with the cell size in metres
    # so a 1 m rise over 30 m reads as the gentle slope it is.
    cell_y = dem["lat_step"] * M_PER_DEG
    cell_x = dem["lon_step"] * M_PER_DEG * math.cos(math.radians((dem["south"] + dem["north"]) / 2))
    dz_dy, dz_dx = np.gradient(filled, cell_y, cell_x)
    # Vertical exaggeration: Chennai is so flat that true relief shades as a blank sheet.
    exaggeration = 8.0
    slope = np.arctan(exaggeration * np.hypot(dz_dx, dz_dy))
    aspect = np.arctan2(-dz_dx, dz_dy)
    azimuth, altitude = math.radians(315), math.radians(45)
    shade = (math.sin(altitude) * np.cos(slope)
             + math.cos(altitude) * np.sin(slope) * np.cos(azimuth - aspect))
    rgb *= (0.55 + 0.45 * np.clip(shade, 0, 1))[..., None]

    alpha = np.where(valid, int(round(255 * opacity)), 0)
    rgba = np.dstack([np.clip(rgb, 0, 255), alpha]).astype(np.uint8)
    buffer = BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def fill_depth(elevations: np.ndarray, cell_area_m2: float, volume_m3: float) -> dict[str, float]:
    """Pour a volume of water into a patch of ground and see how deep it stands.

    The patch is filled from its lowest cell upwards - the hypsometric fill every
    bathtub flood model uses - so the water finds its own level instead of being
    spread evenly over an assumed area. Returned are that level, how much ground ends
    up under water, and how deep it gets at the deepest point.

    ponytail: a still-water fill, with no momentum and no route between one hollow
    and the next. It answers "how deep does this much water stand here", which is
    what a depth in centimetres means on a dashboard, and it is the piece a 2D solver
    would replace.
    """
    if volume_m3 <= 0 or elevations.size == 0 or cell_area_m2 <= 0:
        return {"level_m": float("nan"), "max_depth_m": 0.0, "area_m2": 0.0, "held_m3": 0.0}

    ground = np.sort(elevations.astype(np.float64).ravel())
    # Water covering the k lowest cells stands at a level that holds exactly
    # sum(level - ground[:k]) * cell_area. Walking k upwards, the first k whose
    # capacity at the next cell's height exceeds the volume is where it settles.
    rims = ground[1:]
    counts = np.arange(1, ground.size)
    capacity = (rims * counts - np.cumsum(ground[:-1])) * cell_area_m2

    reached = int(np.searchsorted(capacity, volume_m3))
    if reached >= counts.size:
        # More water than this patch can hold below its own rim: it stands level with
        # the rim and the rest has run off somewhere outside the window.
        level = float(ground[-1])
        held = float(capacity[-1]) if capacity.size else 0.0
        covered = ground.size
    else:
        count = int(counts[reached])
        level = float((volume_m3 / cell_area_m2 + np.sum(ground[:count])) / count)
        held = volume_m3
        covered = count

    return {
        "level_m": level,
        "max_depth_m": max(0.0, level - float(ground[0])),
        "area_m2": covered * cell_area_m2,
        "held_m3": held,
    }


def basin(lat: float, lon: float, radius_m: float = PONDING_RADIUS_M) -> dict[str, Any] | None:
    """The ground around a point, prepared once so it can be filled many times.

    A nowcast fills the same hollow at every step of the forecast, and the ground does
    not change between them, so the window is read, sorted and cumulated here and the
    filling itself becomes a search.
    """
    found = _window(lat, lon, radius_m)
    if found is None:
        return None
    patch, cell_area, (row, col) = found
    if not np.isfinite(patch[row, col]):
        return None

    values = patch.astype(np.float64).ravel()
    ground = np.sort(values[np.isfinite(values)])
    counts = np.arange(1, ground.size)
    return {
        "ground": ground,
        "counts": counts,
        "prefix": np.cumsum(ground[:-1]),
        # Water covering the k lowest cells, level with cell k+1, holds this much.
        "capacity": (ground[1:] * counts - np.cumsum(ground[:-1])) * cell_area,
        "cell_area_m2": cell_area,
        "ground_here_m": float(patch[row, col]),
    }


def fill_basin(prepared: dict[str, Any], volume_m3: float) -> dict[str, Any]:
    """Pour a volume into a prepared basin: the same fill, without re-reading ground."""
    ground, capacity = prepared["ground"], prepared["capacity"]
    if volume_m3 <= 0 or capacity.size == 0:
        level = float(ground[0]) if ground.size else float("nan")
        return {"level_m": level, "max_depth_m": 0.0, "area_m2": 0.0, "held_m3": 0.0}

    reached = int(np.searchsorted(capacity, volume_m3))
    if reached >= prepared["counts"].size:
        level = float(ground[-1])
        held = float(capacity[-1])
        covered = ground.size
    else:
        count = int(prepared["counts"][reached])
        # prefix[count - 1] is the sum of the count lowest cells; the water sits
        # level above all of them.
        level = float((volume_m3 / prepared["cell_area_m2"] + prepared["prefix"][count - 1]) / count)
        held = volume_m3
        covered = count

    return {
        "level_m": level,
        "max_depth_m": max(0.0, level - float(ground[0])),
        "area_m2": covered * prepared["cell_area_m2"],
        "held_m3": held,
    }


def pond(lat: float, lon: float, volume_m3: float,
         radius_m: float = PONDING_RADIUS_M) -> dict[str, Any] | None:
    """Where water surcharging at one point ends up, and how deep it gets.

    Depth is reported both at the point itself - the manhole, which may sit above the
    hollow the water runs into - and at the deepest point of that hollow, which is
    where a vehicle actually gets stuck.
    """
    prepared = basin(lat, lon, radius_m)
    if prepared is None:
        return None
    fill = fill_basin(prepared, volume_m3)
    if math.isnan(fill["level_m"]):
        return None

    here = prepared["ground_here_m"]
    return {
        "ground_m": round(here, 2),
        "water_level_m": round(fill["level_m"], 2),
        "depth_here_m": round(max(0.0, fill["level_m"] - here), 3),
        "max_depth_m": round(fill["max_depth_m"], 3),
        "flooded_area_m2": round(fill["area_m2"], 0),
        "volume_held_m3": round(fill["held_m3"], 1),
        "ran_off_m3": round(max(0.0, volume_m3 - fill["held_m3"]), 1),
    }


def street_pond(prepared: dict[str, Any] | None, volume_m3: float,
                corridor_m2: float) -> dict[str, Any]:
    """How deep water from a manhole stands, on the road first and the ground after.

    Water surcharging at a manhole does not immediately spread over every low cell in
    the neighbourhood: the road it comes up in is a channel with kerbs, and it fills
    that first. Only what will not fit below the kerb spills into the surrounding
    ground, and that part is spread by the terrain.

    Doing it the other way round - pouring straight into a 30 m DEM - answers a
    different question, because one DEM cell is wider than the street and the water
    is spread over the gardens either side of it before it is ankle deep.
    """
    corridor_m2 = max(corridor_m2, 0.0)
    road_capacity = corridor_m2 * KERB_M

    if corridor_m2 > 0 and volume_m3 <= road_capacity:
        return {
            "depth_m": volume_m3 / corridor_m2,
            "area_m2": corridor_m2,
            "over_kerb": False,
        }

    spilled = volume_m3 - road_capacity
    if prepared is None:
        # No terrain here: keep the water on the road rather than inventing ground.
        return {
            "depth_m": volume_m3 / corridor_m2 if corridor_m2 > 0 else 0.0,
            "area_m2": corridor_m2,
            "over_kerb": corridor_m2 > 0,
        }

    fill = fill_basin(prepared, spilled)
    above_kerb = max(0.0, fill["level_m"] - prepared["ground_here_m"])
    return {
        "depth_m": KERB_M + above_kerb,
        "area_m2": max(corridor_m2, fill["area_m2"]),
        "over_kerb": True,
    }


if __name__ == "__main__":
    # A flat-bottomed bowl: 9 cells at 10 m, the rest at 11 m, 100 m² each.
    bowl = np.array([[11, 11, 11, 11, 11],
                     [11, 10, 10, 10, 11],
                     [11, 10, 10, 10, 11],
                     [11, 10, 10, 10, 11],
                     [11, 11, 11, 11, 11]], dtype=np.float32)
    # 90 m³ over the 9 flat cells is 0.1 m deep, well below the 1 m rim.
    fill = fill_depth(bowl, 100.0, 90.0)
    assert abs(fill["max_depth_m"] - 0.1) < 1e-6, fill
    assert abs(fill["area_m2"] - 900.0) < 1e-6, fill
    assert abs(fill["held_m3"] - 90.0) < 1e-6, fill

    # Fill the bowl to its rim: 9 cells x 1 m x 100 m² = 900 m³.
    assert abs(fill_depth(bowl, 100.0, 900.0)["max_depth_m"] - 1.0) < 1e-3
    # More than that runs off; the level cannot exceed the rim.
    over = fill_depth(bowl, 100.0, 5000.0)
    assert abs(over["level_m"] - 11.0) < 1e-6 and over["held_m3"] < 5000.0

    # A slope holds almost nothing: 1 m³ on ground rising 1 m per cell.
    slope = np.arange(25, dtype=np.float32).reshape(5, 5)
    assert fill_depth(slope, 100.0, 1.0)["max_depth_m"] < 0.02

    assert fill_depth(bowl, 100.0, 0.0)["max_depth_m"] == 0.0

    # The prepared basin must agree with the one-shot fill, that being the point of it.
    sorted_bowl = np.sort(bowl.astype(np.float64).ravel())
    prepared = {
        "ground": sorted_bowl,
        "counts": np.arange(1, bowl.size),
        "prefix": np.cumsum(sorted_bowl[:-1]),
        "capacity": (sorted_bowl[1:] * np.arange(1, bowl.size)
                     - np.cumsum(sorted_bowl[:-1])) * 100.0,
        "cell_area_m2": 100.0,
        "ground_here_m": 10.0,
    }
    for volume in (0.0, 45.0, 90.0, 900.0, 5000.0):
        once = fill_depth(bowl, 100.0, volume)
        again = fill_basin(prepared, volume)
        assert abs(once["max_depth_m"] - again["max_depth_m"]) < 1e-6, (volume, once, again)
        assert abs(once["area_m2"] - again["area_m2"]) < 1e-6, (volume, once, again)

    # On the road: 100 m of street 12 m wide holds 180 m³ before the kerb is over.
    corridor = 100 * ROAD_WIDTH_M
    shallow = street_pond(None, 60.0, corridor)
    assert abs(shallow["depth_m"] - 0.05) < 1e-9 and not shallow["over_kerb"]
    assert abs(street_pond(None, corridor * KERB_M, corridor)["depth_m"] - KERB_M) < 1e-9
    # Over the kerb, the ground takes the rest and the depth is at least the kerb.
    deep = street_pond(prepared, corridor * KERB_M + 900.0, corridor)
    assert deep["over_kerb"] and deep["depth_m"] > KERB_M, deep
    # A manhole with no street around it still reports what the road would hold.
    assert street_pond(None, 100.0, 0.0)["depth_m"] == 0.0

    # And against the real tiles, if they are present.
    city = grid(12.85, 13.25, 80.10, 80.35)
    if city is not None:
        # Whatever the source, the city is covered: no-data only where the sea is,
        # and no seam along 13 degrees N where the tiles meet.
        land = city["grid"][:, :int(0.6 * city["grid"].shape[1])]
        assert np.isfinite(land).mean() > 0.97, "holes in the DEM over land"
        seam = int(round((13.25 - 13.0) / city["lat_step"]))
        assert abs(float(np.nanmean(city["grid"][seam - 1])) - float(np.nanmean(city["grid"][seam]))) < 3
        # The grid and the point lookup must agree: the box edges need not fall on
        # pixel edges, so the point's pixel is this cell or a neighbour of it.
        row = int((13.25 - 13.0827) / city["lat_step"])
        col = int((80.2707 - 80.10) / city["lon_step"])
        near = city["grid"][row - 1:row + 2, col - 1:col + 2]
        assert np.any(np.isclose(near, elevation(13.0827, 80.2707))), (near, elevation(13.0827, 80.2707))
        png = relief_image(city)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000
        # Road level never sits above the surface it is read from.
        for lat_, lon_ in ((13.0827, 80.2707), (13.04, 80.23), (12.98, 80.20)):
            assert ground_level(lat_, lon_) <= elevation(lat_, lon_) + 1e-6
        print(f"DEM source: {source_info()}")
        print(f"Chennai DEM {city['grid'].shape[1]} x {city['grid'].shape[0]}, "
              f"{np.nanmin(city['grid']):.1f} to {np.nanmax(city['grid']):.1f} m, "
              f"relief image {len(png) / 1e6:.1f} MB")

    here = elevation(13.0827, 80.2707)          # Chennai Central
    if here is None:
        print("DEM tiles not found; formula checks passed")
    else:
        assert -5 < here < 60, here             # Chennai is flat and near sea level
        assert elevation(13.0, 80.2) is not None and elevation(12.9, 80.2) is not None
        wet = pond(13.0827, 80.2707, 2000.0)
        assert wet and 0 <= wet["max_depth_m"] < 5, wet
        print(f"Chennai Central ground {here:.1f} m; 2000 m³ there stands "
              f"{wet['max_depth_m'] * 100:.0f} cm deep over {wet['flooded_area_m2']:.0f} m²")
    print("terrain self-check passed")
