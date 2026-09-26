"""Chennai's streets as points every 20 m: the places the flood model reports depth at.

Streets are OpenStreetMap ways (app/data/chennai_streets.geojson). Their vertices can
be hundreds of metres apart on a straight road, so they are densified first. Bridges
are left out: the DEM under a deck is the river below it.
"""

import json
import math
from typing import Any

from app import config
from app.services import terrain

# Vertices are laid at least this close along every street, so a depth is resolved
# to within this distance.
SPACING_M = 20.0
_index: dict[str, Any] | None = None


def _metres(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Ground distance; equirectangular is within a millimetre over a few hundred metres."""
    x = (lon2 - lon1) * terrain.M_PER_DEG * math.cos(math.radians((lat1 + lat2) / 2))
    y = (lat2 - lat1) * terrain.M_PER_DEG
    return math.hypot(x, y)


def densify(coords: list[list[float]], spacing_m: float = SPACING_M) -> list[list[float]]:
    """A polyline with extra vertices so no two are further apart than the spacing."""
    out = [list(coords[0])]
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        pieces = max(1, math.ceil(_metres(lon1, lat1, lon2, lat2) / spacing_m))
        for k in range(1, pieces + 1):
            f = k / pieces
            out.append([lon1 + (lon2 - lon1) * f, lat1 + (lat2 - lat1) * f])
    return out


def load() -> dict[str, Any]:
    """Every street, densified."""
    global _index
    if _index is not None:
        return _index

    streets: list[dict[str, Any]] = []
    if config.STREETS_GEOJSON.exists():
        collection = json.loads(config.STREETS_GEOJSON.read_text(encoding="utf-8"))
        for feature in collection["features"]:
            # A bridge deck is metres above the DEM, which reads the river or canal under
            # it: every flood model would put the channel's water on the deck.
            if feature["properties"].get("bridge"):
                continue
            coords = densify(feature["geometry"]["coordinates"])
            streets.append({"coords": coords, "name": feature["properties"].get("name"),
                            "highway": feature["properties"].get("highway"),
                            "tunnel": bool(feature["properties"].get("tunnel"))})

    _index = {"streets": streets}
    return _index
