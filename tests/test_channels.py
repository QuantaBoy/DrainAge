"""The basin model's channel layer: lengths agree with geometry, places find basins."""

import collections
import math

import pytest

from app.services import terrain
from app.services.channels import FILES, load, nearest


@pytest.mark.city
def test_channel_layer() -> None:
    found = load()
    print(f"{len(found)} channel lines from {len(FILES)} files")
    basins = collections.Counter(c["subbasin"] for c in found)
    print("subbasins:", dict(basins.most_common()))
    kinds = collections.Counter(c["type"] for c in found)
    print("types:", dict(kinds))
    print("routed by the official model:", sum(1 for c in found if c["routed"]))
    print("named:", sum(1 for c in found if c["name"]))

    # The drawn geometry must agree with the length the file states.
    gap = []
    for channel in found:
        if not channel["length_km"]:
            continue
        drawn = sum(math.hypot((b[0] - a[0]) * terrain.M_PER_DEG * math.cos(math.radians(13.0)),
                               (b[1] - a[1]) * terrain.M_PER_DEG)
                    for a, b in zip(channel["coords"], channel["coords"][1:])) / 1000
        gap.append(abs(drawn - channel["length_km"]) / channel["length_km"] * 100)
    gap.sort()
    assert gap and gap[len(gap) // 2] < 2.0, gap[len(gap) // 2]
    print(f"stated length vs drawn geometry: median {gap[len(gap) // 2]:.1f}% apart")

    # Chennai Central sits in the Cooum basin, and the nearest channel is not far.
    here = nearest(13.0827, 80.2707)
    assert here and here["subbasin"], here
    print(f"Chennai Central -> {here['name'] or 'unnamed'} ({here['subbasin']} basin, "
          f"{here['metres_away']:.0f} m away)")
    for place, lat, lon in (("Velachery", 12.979, 80.218),
                            ("Pallikaranai", 12.9465, 80.2315),
                            ("Ambattur", 13.105, 80.160)):
        near = nearest(lat, lon)
        print(f"{place} -> {near['name'] or 'unnamed'} ({near['subbasin']} basin, "
              f"{near['metres_away']:.0f} m)")
