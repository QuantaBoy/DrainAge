"""Street densification, and the cached street layer."""

import math

import pytest

from app.services import terrain
from app.services.street_index import SPACING_M, _metres, densify, load


def test_densify_keeps_every_gap_within_spacing() -> None:
    # A 90 m straight line needs five 18 m pieces to keep every gap within 20 m.
    line = densify([[80.2, 13.0], [80.2 + 90 / (terrain.M_PER_DEG * math.cos(math.radians(13))), 13.0]])
    assert len(line) == 6, len(line)
    assert all(_metres(*a, *b) <= SPACING_M + 1e-6 for a, b in zip(line, line[1:]))


@pytest.mark.city
def test_street_layer_loads() -> None:
    streets = load()["streets"]
    assert streets, "no streets cached"
    print(f"{len(streets)} streets indexed")
