"""Precomputed answers to the heaviest requests, for a host that cannot run the flood
engine (Vercel: too small for the model and its data).

GitHub Actions runs the engine every half hour (scripts/snapshot.py), saves these
answers as files and publishes them on GitHub Pages; main.py, the Vercel entry point,
sends the matching requests there. Everything else still runs on Vercel.
"""

# The what-if choices the dashboard offers (static/js/flood.js, static/js/ponding.js).
PRESET_RATES_MM_H = (15, 30, 60, 100)
DRAIN_LEVELS = (1.0, 0.5, 0.25, 0.0)

FILES = {
    "/data-collection/elevation": "elevation.json",
    "/data-collection/elevation.png": "elevation.png",
    "/data-collection/streets": "streets.json",
    "/data-collection/waterways": "waterways.json",
    "/data-collection/inventory": "inventory.json",
}
FORECAST_PATH = "/data-collection/rain-ponding/streets"


def forecast_file(rate: int | None, drain: float) -> str:
    return f"forecast/{'live' if rate is None else rate}-{drain}.json"


def file_for(path: str, query: dict[str, str]) -> str | None:
    """The saved answer for a request, or None when there is none. A forecast is the
    city-wide one at the nearest offered storm and drain condition."""
    if path in FILES:
        return FILES[path]
    if path != FORECAST_PATH:
        return None
    try:
        rate = float(query["rain_mm_h"]) if query.get("rain_mm_h") else None
        drain = float(query.get("drain_condition") or 1)
    except ValueError:
        return None
    if rate is not None:
        rate = min(PRESET_RATES_MM_H, key=lambda preset: abs(preset - rate))
    return forecast_file(rate, min(DRAIN_LEVELS, key=lambda level: abs(level - drain)))


if __name__ == "__main__":
    assert file_for("/data-collection/elevation", {}) == "elevation.json"
    assert file_for(FORECAST_PATH, {"drain_condition": "1"}) == "forecast/live-1.0.json"
    assert file_for(FORECAST_PATH, {"rain_mm_h": "30", "drain_condition": "0.25"}) == "forecast/30-0.25.json"
    assert file_for(FORECAST_PATH, {"rain_mm_h": "", "zone": "N07"}) == "forecast/live-1.0.json"
    assert file_for("/data-collection/nowcast", {}) is None
    print("ok")
