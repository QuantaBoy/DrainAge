"""The ground the water ends up on: a DEM, read where it is needed.

Where rain runs, where it stands and how deep it gets on a street are all read off the
ground (app/services/surface.py cuts it into storage zones for the flood model).

The DEM is 1 arc-second (about 30 m at Chennai's latitude) in plain WGS 84: the
OpenTopography download scripts/fetch_dem.py saves (GEDTM30 bare earth by preference), or the
Copernicus surface-model tiles. Either way a lon/lat maps onto a pixel with the tie
point, pixel scale and raster type from the GeoTIFF itself - no reprojection, no GDAL.

ponytail: 30 m says nothing about the camber of one street, so a depth here is the
depth of the depression a street sits in, not of the kerb line. Swapping in a LiDAR
or drone DSM changes only the files this module reads.
"""

import logging
import math
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from PIL import Image
from scipy.ndimage import minimum_filter

from app import config

TILE_PATTERN = "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif"

# Metres per degree of latitude; longitude is scaled by cos(latitude) where it matters.
M_PER_DEG = 111_320.0

# Water shallower than this is wet tarmac, not a flood, and is not reported.
MIN_DEPTH_M = 0.02

# Which DEM the model reads. scripts/fetch_dem.py saves OpenTopography downloads to
# app/data/dem/<DEMTYPE>.tif; the Copernicus tiles in app/data are the fallback.
# DEM_SOURCE in .env picks one ("GEDTM30", "COP30", "tiles"); unset, a bare-earth
# model wins over a surface one, and any download over the tiles.
DEM_DIR = config.DATA_DIR / "dem"
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

    wanted = config.DEM_SOURCE
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
                   for path in sorted(config.DATA_DIR.glob("Copernicus_DSM_COG_10_*_DEM.tif"))]
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
        "files": [str(r["path"].relative_to(config.ROOT_DIR)) for r in sources],
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
        # No-data must not win the minimum, so it is lifted out of reach and put back.
        lifted = np.where(np.isfinite(tile["grid"]), tile["grid"], np.inf)
        road = minimum_filter(lifted, size=3, mode="nearest")
        road[~np.isfinite(tile["grid"])] = np.nan
        tile["road"] = road
    return _value(tile["road"][cell])


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


# The relief image is the same for every visitor and slow to draw, so it is drawn once.
_relief: dict[str, Any] = {}


def relief_layer() -> dict[str, Any] | None:
    """The study area's shaded relief as a PNG, with where it goes and how to read it."""
    if not _relief:
        south, west, north, east = config.CHENNAI_BBOX
        dem = grid(south, north, west, east)
        if dem is None:
            return None
        land = dem["grid"][(dem["grid"] == dem["grid"]) & (dem["grid"] != 0)]
        _relief.update({
            "png": relief_image(dem),
            "bounds": [[south, west], [north, east]],
            "range_m": [round(float(land.min()), 1), round(float(land.max()), 1)],
            "median_m": round(float(np.median(land)), 1),
            "stops": [{"m": m, "color": "#%02x%02x%02x" % rgb} for m, rgb in ELEVATION_STOPS],
            "source": source_info(),
        })
    return _relief
