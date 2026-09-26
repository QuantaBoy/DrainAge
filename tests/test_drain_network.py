"""The drain network: flow direction from invert levels, and the built network's
catchments and capacities."""

import pytest

from app.services import hydraulics
from app.services.drain_network import flow_direction, load_drains, network_summary


def test_flow_direction_follows_the_fall() -> None:
    assert flow_direction(13.096, 12.676) == "forward"
    assert flow_direction(11.692, 14.054) == "reverse"
    assert flow_direction(12.0, 12.0) == "unknown"
    assert flow_direction("", 12.0) == "unknown"
    assert flow_direction(104860.0, 12.0) == "unknown"      # a data-entry error, not a level


@pytest.mark.city
def test_network_moves_runoff_downstream() -> None:
    drains = load_drains()
    # Every drain collects at least its own strip; the network adds, never loses.
    assert all(d["catchment_m2"] >= d["capacity"]["catchment_strip_m2"] - 1e-6 for d in drains)
    # A 50 mm/h red-alert hour shows real distress; drizzle does not.
    over = {rain: sum(1 for d in drains
                      if hydraulics.rational_inflow(d["catchment_m2"], rain)
                      > d["capacity"]["effective_capacity_m3s"] > 0)
            for rain in (2.5, 50.0)}
    assert over[50.0] > over[2.5]


@pytest.mark.city
def test_spill_is_not_counted_twice() -> None:
    # Routed spill can never exceed the rain that fell on the network.
    summary = network_summary({"ZONE": None, "WARD": None}, 50.0, hydraulics.STRIP_WIDTH_M,
                              hydraulics.RUNOFF_COEFF, 5)
    assert 0 < summary["spill_m3s"] <= summary["inflow_m3s"] + 1e-6
