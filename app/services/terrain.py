"""The ground the water ends up on: Copernicus DEM tiles, read where they are needed.

A surcharged drain does not stop at "over capacity". The water it cannot take comes
back up at the manhole and runs downhill until the street holds it, so turning a spill
rate into a street-level depth needs the terrain around that manhole.

The tiles are 1 arc-second Copernicus DSM (about 30 m at Chennai's latitude) in plain
WGS 84, so a lon/lat maps onto a pixel with the tie point and pixel scale from the
GeoTIFF itself - no reprojection, and no GDAL.

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

_tiles: dict[tuple[int, int], dict[str, Any]] = {}


def _load_tile(lat_deg: int, lon_deg: int) -> dict[str, Any] | None:
    """Read one DEM tile, with the georeferencing it carries."""
    key = (lat_deg, lon_deg)
    if key in _tiles:
        return _tiles[key]

    path = DATA_DIR / TILE_PATTERN.format(lat=lat_deg, lon=lon_deg)
    if not path.exists():
        _tiles[key] = None
        return None

    with tifffile.TiffFile(str(path)) as handle:
        page = handle.pages[0]
        scale = page.tags["ModelPixelScaleTag"].value
        tie = page.tags["ModelTiepointTag"].value
        _tiles[key] = {
            "grid": page.asarray(),
            # Tie point is the outer corner of the first pixel: (lon, lat) of the
            # top-left, with the grid running east and south from there.
            "west": float(tie[3]),
            "north": float(tie[4]),
            "lon_step": float(scale[0]),
            "lat_step": float(scale[1]),
        }
    return _tiles[key]


def _tile_for(lat: float, lon: float) -> dict[str, Any] | None:
    return _load_tile(math.floor(lat), math.floor(lon))


def _cell(tile: dict[str, Any], lat: float, lon: float) -> tuple[int, int] | None:
    """Row and column of a point in a tile, or None if it is not on this tile.

    A point exactly on a tile's southern or eastern edge lands one row or column past
    the end, so the last cell is used rather than the point being dropped: the tiles
    share those lines.
    """
    grid = tile["grid"]
    row = int((tile["north"] - lat) / tile["lat_step"])
    col = int((lon - tile["west"]) / tile["lon_step"])
    if not (0 <= row <= grid.shape[0] and 0 <= col <= grid.shape[1]):
        return None
    return min(row, grid.shape[0] - 1), min(col, grid.shape[1] - 1)


def elevation(lat: float, lon: float) -> float | None:
    """Ground level at a point, metres above the ellipsoid, or None off the tiles."""
    tile = _tile_for(lat, lon)
    if tile is None:
        return None
    cell = _cell(tile, lat, lon)
    if cell is None:
        return None
    return float(tile["grid"][cell])


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

    ground = np.sort(patch.astype(np.float64).ravel())
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
