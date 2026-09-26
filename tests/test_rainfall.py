"""The rain the flood model runs on, in scenario mode (no network call)."""

import asyncio

from app import config
from app.services.rainfall import REPORT_STEPS, report_steps


def test_scenario_holds_the_rate_for_three_hours() -> None:
    rain = asyncio.run(report_steps(*config.CHENNAI_CENTRE, 30.0))
    assert rain["mode"] == "scenario" and "not a forecast" in rain["source"]
    assert rain["rain_mm_h"] == [30.0] * REPORT_STEPS == [30.0] * 36
    assert rain["total_mm"] == 90.0
    assert abs(sum(rain["rain_mm"]) - 90.0) < 1e-9
