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
    "/data-collection/wards": "wards.json",
}
FORECAST_PATH = "/data-collection/rain-ponding/streets"
# Departure choices in the route panel (static/js/route.js).
LEAVE_MIN = (0, 30, 60, 90, 120, 150)


def forecast_file(rate: int | None, drain: float) -> str:
    return f"forecast/{'live' if rate is None else rate}-{drain}.json"


def route_file(rate: int | None, drain: float, leave_min: int) -> str:
    """Deepest water on every road of the street graph for one trip window, one byte
    per road in cm (255 = 255 cm or more), in routing.load()'s order."""
    return f"route/{'live' if rate is None else rate}-{drain}-{leave_min}.u8"


def use_published_routing(base_url: str) -> None:
    """Route on a host without the engine: the street graph and the road depths come
    from the published files, and only the search runs here (navigation.plan_route)."""
    import tempfile
    import urllib.request
    from pathlib import Path

    import numpy as np

    from app import config
    from app.services import compute, forecast, routing

    def fetch(name: str) -> bytes:
        with urllib.request.urlopen(f"{base_url}/{name}", timeout=60) as response:
            return response.read()

    streets = Path(tempfile.gettempdir()) / "streets.geojson"
    config.STREETS_GEOJSON = streets
    build_graph = routing.load

    def load() -> dict:
        if not streets.exists():
            streets.write_bytes(fetch("streets.json"))
        return build_graph()

    depths: dict[str, np.ndarray] = {}

    def road_depths_cm(rain: dict, runoff_coeff: float, drain_condition: float,
                       first_step: int, last_step: int) -> np.ndarray:
        rate = None
        if rain["mode"] == "scenario":
            rate = min(PRESET_RATES_MM_H, key=lambda preset: abs(preset - rain["rain_mm_h"][0]))
        drain = min(DRAIN_LEVELS, key=lambda level: abs(level - drain_condition))
        leave = min(LEAVE_MIN, key=lambda m: abs(m - (first_step + 1) * rain["step_minutes"]))
        name = route_file(rate, drain, leave)
        if name not in depths:
            depths[name] = np.frombuffer(fetch(name), dtype=np.uint8).astype(float)
        if depths[name].size != len(load()["edges"]):
            raise RuntimeError(f"{name} does not match the street graph")
        return depths[name]

    async def no_storm(*args: object) -> bytes:
        return b""                    # the storm was run when the files were made

    routing.load = load
    forecast.road_depths_cm = road_depths_cm
    compute.street_forecast_gz = no_storm


def ward_pdf(number: int) -> str:
    return f"ward/{number}.pdf"


def file_for(path: str, query: dict[str, str]) -> str | None:
    """The saved answer for a request, or None when there is none. A forecast is the
    city-wide one at the nearest offered storm and drain condition."""
    if path in FILES:
        return FILES[path]
    if path.startswith("/resources/ward-map/") and path.rsplit("/", 1)[1].isdigit():
        return ward_pdf(int(path.rsplit("/", 1)[1]))
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
    assert file_for("/resources/ward-map/103", {}) == "ward/103.pdf"
    assert file_for("/resources/ward-map/x", {}) is None
    print("ok")
