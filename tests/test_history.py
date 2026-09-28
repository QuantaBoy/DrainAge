"""The flood record: reading its polygons, the "recorded hotspot" rule, and the model's
skill against the places that flooded in December 2015."""

import numpy as np
import pytest
from scipy.spatial import cKDTree

from app.services import coupled, forecast, history, surface


def test_polygon_rings() -> None:
    rings = history._rings("MULTIPOLYGON (((80.1 13.0, 80.2 13.0, 80.2 13.1, 80.1 13.0)), "
                           "((80.3 13.0, 80.4 13.0, 80.4 13.1, 80.3 13.0)))")
    assert len(rings) == 2 and rings[1][0].tolist() == [80.3, 13.0]
    assert len(history._rings("POLYGON ((1 1, 2 1, 2 2, 1 1))")) == 1


def test_recorded_rule() -> None:
    assert history.recorded(0, 0, True)                      # went under in 2015
    assert history.recorded(0, 25, False)                    # frequent flood extent
    assert not history.recorded(0, 50, False)                # a rarer extent alone is not
    assert history.recorded(4, 0, False) and not history.recorded(3, 0, False)
    assert history.describe(5, 10, False) == {"hazard": "Very High", "return_period_years": 10,
                                              "flooded_2015": False}


@pytest.mark.city
def test_model_knows_where_chennai_flooded() -> None:
    if not history.available():
        pytest.skip("no historical data")
    system = forecast.coupled_system()
    model = system["model"]
    run = coupled.simulate(system["surface"], system["network"], [30 * 5 / 60] * 36, 5, 0.75, 1.0)
    _, coords, _, cell, _ = surface.street_cells(model)
    inside = cell >= 0
    xy = history.metres_xy(coords[inside, 1], coords[inside, 0])
    wet = surface.depth_grid(model, run["levels"].max(axis=0)).ravel()[cell[inside]] >= 0.05
    tree = cKDTree(xy[wet])
    pts = history.points_2015()
    hits = np.isfinite(tree.query(history.metres_xy(pts["lat"], pts["lon"]), distance_upper_bound=250)[0])
    sample = xy[np.random.default_rng(1).choice(len(xy), 3000, replace=False)]
    chance = np.isfinite(tree.query(sample, distance_upper_bound=250)[0])
    # The model must know far more than "a lot of the city is wet".
    assert hits.mean() > chance.mean() + 0.25, (hits.mean(), chance.mean())
