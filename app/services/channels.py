"""Chennai's macro and micro drains, canals, rivers and surplus channels.

The basin model's own channel layer: 934 lines carrying an official name, the river
subbasin each belongs to (Kosasthalayar, Cooum, Adyar, Kovalam, Nandhiyar, Nagariyar),
what kind of channel it is, and whether the official model routes it hydraulically.

Two things the rest of the site takes from it:

* **Where the water goes.** The channel nearest a place names the drain it runs to and
  the basin it belongs to, so a street's flooding can be reported as "Adyar basin,
  towards Okkium Madavu Drain" rather than as an anonymous dip in the terrain.
* **Where water can flow.** app/services/surface.py burns these channels into the
  DEM, because a channel a 30 m DEM cannot see reads as a dam across the hollow above it.

The files hold no cross-section, so nothing here sizes a channel: they place and name
the network, they do not say what it can carry.
"""

import csv
import math
import re
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from app import config
from app.services import terrain
from app.services.street_index import densify

FILES = ("macro_drains.csv", "micro_drains.csv", "buckingham_canal.csv",
         "krishna_water_canal.csv", "rivers_streams.csv")

# Channel points are laid this far apart before indexing, so "the nearest channel" is
# the nearest point on a line, not the nearest of its drawn vertices.
SPACING_M = 30.0
# Past this, a place belongs to no channel worth naming: the basins are big, but a
# channel 3 km away does not tell a street where its water goes.
NEAR_LIMIT_M = 3000.0

_channels: list[dict[str, Any]] | None = None
_index: dict[str, Any] | None = None


def load() -> list[dict[str, Any]]:
    """Every channel, with its geometry and what the basin model records about it."""
    global _channels
    if _channels is not None:
        return _channels

    out: list[dict[str, Any]] = []
    for name in FILES:
        path = config.SURVEY_DIR / name
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                # LINESTRING and MULTILINESTRING alike: every bracketed run of points.
                for chunk in re.findall(r"\(([^()]+)\)", row.get("geometry") or ""):
                    points = [[float(v) for v in pair.split()[:2]]
                              for pair in chunk.split(",") if len(pair.split()) >= 2]
                    if len(points) < 2:
                        continue
                    out.append({
                        "name": (row.get("name") or "").strip() or None,
                        "subbasin": (row.get("subbasin") or "").strip() or None,
                        "type": (row.get("type") or "").strip() or None,
                        # "Nil" where the official model does not route this channel.
                        "routed": (row.get("modelling") or "").strip().lower().endswith("channel routing"),
                        "length_km": _float(row.get("length_km")),
                        "coords": points,
                        "source": name,
                    })
    _channels = out
    return _channels


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def lines() -> list[list[list[float]]]:
    """Just the geometry, for burning into the DEM."""
    return [channel["coords"] for channel in load()]


def _points() -> dict[str, Any]:
    """Every channel resampled to an even spacing, in a tree, with its channel."""
    global _index
    if _index is None:
        xy, owner = [], []
        for i, channel in enumerate(load()):
            dense = densify(channel["coords"], SPACING_M)
            xy.extend(dense)
            owner.extend([i] * len(dense))
        points = np.asarray(xy, dtype=np.float64)
        scale = terrain.M_PER_DEG * math.cos(math.radians(13.0))
        _index = {
            "tree": cKDTree(np.c_[points[:, 0] * scale, points[:, 1] * terrain.M_PER_DEG]),
            "owner": np.asarray(owner),
            "scale": scale,
        }
    return _index


def nearest(lat: float, lon: float) -> dict[str, Any] | None:
    """The channel nearest one place, and how far off it is; None past NEAR_LIMIT_M."""
    found = nearest_many([(lat, lon)])
    return found[0]


def nearest_many(places: list[tuple[float, float]]) -> list[dict[str, Any] | None]:
    """The nearest channel to each of many places, in one pass over the tree."""
    if not places:
        return []
    index = _points()
    query = np.array([[lon * index["scale"], lat * terrain.M_PER_DEG] for lat, lon in places])
    distance, hit = index["tree"].query(query)
    channels = load()
    out: list[dict[str, Any] | None] = []
    for metres, point in zip(distance, hit):
        if metres > NEAR_LIMIT_M:
            out.append(None)
            continue
        channel = channels[index["owner"][point]]
        out.append({
            "name": channel["name"],
            "subbasin": channel["subbasin"],
            "type": channel["type"],
            "routed": channel["routed"],
            "metres_away": round(float(metres), 0),
        })
    return out
