"""The street forecast end to end, on the real city: a what-if storm through the
coupled model, read back street by street, and the router's view of the same run."""

import asyncio

import pytest

from app import config
from app.services import forecast, navigation, rainfall


@pytest.fixture(scope="module")
def storm() -> dict:
    return asyncio.run(rainfall.report_steps(*config.CHENNAI_CENTRE, 60.0))


@pytest.mark.city
def test_street_forecast(storm: dict) -> None:
    out = forecast.street_forecast(storm, 0.75, 1.0, config.REPORT_DEPTH_M * 100, None, None)
    assert out["rain"]["mode"] == "scenario" and "rain_mm" not in out["rain"]
    assert out["stretches"] == len(out["features"]) > 0
    assert len(out["per_step"]) == len(storm["rain_mm_h"])
    balance = out["balance_m3"]
    assert abs(balance["error_m3"]) < 1e-5 * balance["runoff"], balance
    # The same storm is not run twice.
    assert forecast.run_storm(storm, 0.75, 1.0) is forecast.run_storm(storm, 0.75, 1.0)


@pytest.mark.city
def test_route_round_the_water(storm: dict) -> None:
    trip = navigation.plan_route((13.0827, 80.2707), (13.0067, 80.2206), 30, storm, 0.75, 1.0, "astar")
    search = trip["search"]
    assert search["astar"]["cost_km"] == search["dijkstra"]["cost_km"]
    assert search["astar"]["settled_junctions"] < search["dijkstra"]["settled_junctions"]
    assert trip["shortest_route"] is not None
