"""Weather for the dashboard: current conditions, the rainfall forecast, the rain grid and
the map tiles.

Current weather and tiles come from OpenWeather (key required, kept server-side); the
forecast and the rain grid come from Open-Meteo, which needs no key.
"""

import datetime
from typing import Any

import httpx

from app import config
from app.errors import BadRequest, NotFound, ServiceError, Unavailable, UpstreamError

GEOCODE_URL = "https://api.openweathermap.org/geo/1.0/direct"
WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
TILE_URL = "https://tile.openweathermap.org/map/{layer}/{z}/{x}/{y}.png"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# The layer name ends up in an upstream URL, so it is matched against a fixed set
# rather than passed through.
TILE_LAYERS = {
    "rain": "precipitation_new",
    "temp": "temp_new",
    "wind": "wind_new",
    "clouds": "clouds_new",
}

# Hourly rainfall at or above this counts as rain; below it is drizzle noise.
RAIN_THRESHOLD_MM = 0.2
# Dry hours allowed inside one rain event. Showery rain dips below the threshold for
# an hour or two, and splitting there would report only the first fragment.
MAX_DRY_GAP_HOURS = 2

# The rain grid is GRID_SIZE x GRID_SIZE points, GRID_STEP_DEG apart (~27 km), about
# the resolution of the underlying forecast models; a finer grid would only
# interpolate, suggesting precision the data does not have.
GRID_SIZE = 7
GRID_STEP_DEG = 0.25

# A district's coordinates never change, so each name is geocoded once instead of
# spending one of the 60 calls/min free-tier quota on every search.
_geocoded: dict[tuple[str, str], dict[str, Any]] = {}
_GEOCODE_CACHE_LIMIT = 500


# ─── Pure helpers ────────────────────────────────────────────────────────────────

def rain_mm(data: dict[str, Any]) -> float:
    """Rainfall in mm/h from an OpenWeather response.

    Stations report a 1h total, a 3h total, or neither, so a missing 1h value is
    derived from the 3h one rather than read as zero rain.
    """
    rain = data.get("rain") or {}
    if "1h" in rain:
        return float(rain["1h"])
    if "3h" in rain:
        return round(rain["3h"] / 3, 2)
    return 0.0


def local_current_step(utc_offset_seconds: int, step_minutes: int = 60) -> datetime.datetime:
    """Now at a location, floored to the start of its current step, as a naive local
    datetime: the top of the hour by default, or the top of any shorter step a feed is
    sampled at (e.g. 15 minutes for Open-Meteo's minutely_15 product).

    Forecast series are expressed in the location's local time, which may differ from
    this server's timezone.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    local = now + datetime.timedelta(seconds=utc_offset_seconds)
    local = local.replace(tzinfo=None, second=0, microsecond=0)
    floored = local.minute - local.minute % step_minutes
    return local.replace(minute=0) + datetime.timedelta(minutes=floored)


def local_current_hour(utc_offset_seconds: int) -> datetime.datetime:
    """The current hour at a location, as a naive datetime in its local time."""
    return local_current_step(utc_offset_seconds, 60)


def next_rain_spell(hours: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The next rain event in an hourly forecast, if any.

    An event runs from the first rainy hour to the last rainy hour before a dry break
    longer than MAX_DRY_GAP_HOURS. `hours` must start at the current hour, so the index
    of the first rainy hour is also how many hours away it is.
    """
    for start, hour in enumerate(hours):
        if hour["rain_mm"] < RAIN_THRESHOLD_MM:
            continue
        end, dry_run = start, 0
        for index in range(start + 1, len(hours)):
            if hours[index]["rain_mm"] >= RAIN_THRESHOLD_MM:
                end, dry_run = index, 0
            else:
                dry_run += 1
                if dry_run > MAX_DRY_GAP_HOURS:
                    break
        spell = hours[start:end + 1]
        peak = max(spell, key=lambda h: h["rain_mm"])
        # The spell ends when its last rainy hour does.
        ends = datetime.datetime.fromisoformat(hours[end]["time"]) + datetime.timedelta(hours=1)
        return {
            "start": hour["time"],
            "end": ends.isoformat(timespec="minutes"),
            "starts_in_hours": start,
            "duration_hours": len(spell),
            "total_mm": round(sum(h["rain_mm"] for h in spell), 1),
            "peak_mm": peak["rain_mm"],
            "peak_time": peak["time"],
            "chance_pct": max((h["rain_chance_pct"] or 0) for h in spell),
        }
    return None


def heaviest_hour(hours: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The wettest hour in a forecast, or None when no hour reaches rain."""
    if not hours:
        return None
    index = max(range(len(hours)), key=lambda i: hours[i]["rain_mm"])
    hour = hours[index]
    if hour["rain_mm"] < RAIN_THRESHOLD_MM:
        return None
    return {
        "time": hour["time"],
        "rain_mm": hour["rain_mm"],
        "chance_pct": hour["rain_chance_pct"],
        "in_hours": index,
    }


# ─── Upstream calls ──────────────────────────────────────────────────────────────

def _api_key() -> str:
    if not config.OPENWEATHER_API_KEY:
        raise ServiceError(500, "OPENWEATHER_API_KEY is not set on the server.")
    return config.OPENWEATHER_API_KEY


async def _fetch(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> httpx.Response:
    """GET an upstream URL, translating its failures into meaningful errors."""
    host = httpx.URL(url).host
    try:
        response = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise Unavailable(f"Could not reach {host}.") from exc
    if response.status_code == 200:
        return response
    if response.status_code == 401:
        # A rejected key is a server misconfiguration, not a bad client request.
        raise UpstreamError(f"{host} rejected the API key.")
    if response.status_code == 429:
        raise UpstreamError(f"{host} rate limit reached. Try again shortly.", status_code=429)
    raise UpstreamError(f"{host} returned HTTP {response.status_code}.")


async def _resolve_district(client: httpx.AsyncClient, district: str, country: str) -> dict[str, Any]:
    """A district or city name as a place with coordinates and state."""
    cache_key = (district.strip().casefold(), country.upper())
    if cache_key in _geocoded:
        return _geocoded[cache_key]
    # Geocoded explicitly because the weather endpoint's own name lookup misses many
    # districts that are not major cities.
    response = await _fetch(client, GEOCODE_URL,
                            {"q": f"{district},{country}", "limit": 1, "appid": _api_key()})
    places = response.json()
    if not places:
        raise NotFound(f"No district named '{district}' found in {country}.")
    if len(_geocoded) >= _GEOCODE_CACHE_LIMIT:
        _geocoded.clear()
    _geocoded[cache_key] = places[0]
    return places[0]


async def _place(client: httpx.AsyncClient, district: str | None, lat: float | None,
                 lon: float | None, country: str) -> dict[str, Any]:
    """The place asked about: a district by name, or a coordinate pair."""
    if district is not None:
        return await _resolve_district(client, district, country)
    if lat is None or lon is None:
        raise BadRequest("Provide either 'district' or both 'lat' and 'lon'.")
    return {"lat": lat, "lon": lon}


# ─── Service functions ───────────────────────────────────────────────────────────

async def current_weather(district: str | None, lat: float | None, lon: float | None,
                          country: str) -> dict[str, Any]:
    """Current conditions at a district or coordinate."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        place = await _place(client, district, lat, lon, country)
        response = await _fetch(client, WEATHER_URL, {
            "lat": place["lat"], "lon": place["lon"], "units": "metric", "appid": _api_key(),
        })
    data = response.json()
    return {
        # For coordinates, the weather response already names the nearest locality, so
        # no reverse-geocode call is spent on a label.
        "district": place.get("name") or data.get("name") or district,
        "state": place.get("state"),
        "country": place.get("country") or data.get("sys", {}).get("country"),
        "lat": place["lat"],
        "lon": place["lon"],
        "temp_c": data["main"]["temp"],
        "feels_like_c": data["main"]["feels_like"],
        "humidity_pct": data["main"]["humidity"],
        "condition": data["weather"][0]["description"],
        # OpenWeather's own icon code (e.g. 10d): condition plus day/night.
        "icon": data["weather"][0]["icon"],
        "wind_ms": data["wind"]["speed"],
        "rain_1h_mm": rain_mm(data),
    }


async def rainfall_forecast(district: str | None, lat: float | None, lon: float | None,
                            country: str, days: int, hours: int) -> dict[str, Any]:
    """Daily and hourly rainfall ahead, with the next rain event called out."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        place = await _place(client, district, lat, lon, country)
        response = await _fetch(client, FORECAST_URL, {
            "latitude": place["lat"],
            "longitude": place["lon"],
            "daily": "weather_code,precipitation_sum,precipitation_probability_max,temperature_2m_max",
            # Hourly data covers every forecast day, so the next rain can be found
            # however far ahead it is, not just within `hours`.
            "hourly": "precipitation,precipitation_probability,temperature_2m",
            "forecast_days": days,
            "timezone": "auto",
        })
    data = response.json()
    daily, hourly = data["daily"], data["hourly"]
    current_hour = local_current_hour(data.get("utc_offset_seconds", 0))
    upcoming = [
        {"time": time, "rain_mm": rain or 0.0, "rain_chance_pct": chance, "temp_c": temp}
        for time, rain, chance, temp in zip(hourly["time"], hourly["precipitation"],
                                            hourly["precipitation_probability"], hourly["temperature_2m"])
        if datetime.datetime.fromisoformat(time) >= current_hour
    ]
    return {
        "district": place.get("name") or district,
        "state": place.get("state"),
        "lat": place["lat"],
        "lon": place["lon"],
        "next_rain": next_rain_spell(upcoming),
        # Reported separately: a stronger downpour can come after the next event.
        "heaviest_24h": heaviest_hour(upcoming[:24]),
        "hours": upcoming[:hours],
        "days": [
            # WMO weather code, the basis for the day's icon.
            {"date": date, "code": code, "rain_mm": rain or 0.0, "rain_chance_pct": chance,
             "temp_max_c": temp_max}
            for date, code, rain, chance, temp_max in zip(
                daily["time"], daily["weather_code"], daily["precipitation_sum"],
                daily["precipitation_probability_max"], daily["temperature_2m_max"])
        ],
    }


async def rain_grid(lat: float, lon: float, hours: int) -> dict[str, Any]:
    """Hourly rain on a grid around a point, for animating rain moving over the map.

    `rain_mm[hour][row * GRID_SIZE + col]`, rows running south to north and columns
    west to east.
    """
    half = GRID_SIZE // 2
    lats = [round(lat + (i - half) * GRID_STEP_DEG, 4) for i in range(GRID_SIZE)]
    # Wrapped so a grid centred near the antimeridian still sends valid longitudes.
    lons = [round((lon + (i - half) * GRID_STEP_DEG + 180) % 360 - 180, 4) for i in range(GRID_SIZE)]
    points = [(point_lat, point_lon) for point_lat in lats for point_lon in lons]

    async with httpx.AsyncClient(timeout=20.0) as client:
        upstream = await _fetch(client, FORECAST_URL, {
            # Every grid point goes in one request rather than one call each.
            "latitude": ",".join(str(point[0]) for point in points),
            "longitude": ",".join(str(point[1]) for point in points),
            "hourly": "precipitation",
            # Covers `hours` from any time of day, plus today's elapsed hours.
            "forecast_days": hours // 24 + 2,
            "timezone": "auto",
        })
    series = upstream.json()
    times = series[0]["hourly"]["time"]
    current_hour = local_current_hour(series[0].get("utc_offset_seconds", 0))
    first = next((i for i, time in enumerate(times)
                  if datetime.datetime.fromisoformat(time) >= current_hour), 0)
    window = range(first, min(first + hours, len(times)))
    return {
        "lats": lats,
        "lons": lons,
        "step_deg": GRID_STEP_DEG,
        # How the series was trimmed, so a client can verify it starts at "now".
        "from_hour": current_hour.isoformat(timespec="minutes"),
        "skipped_past_hours": first,
        "rain_threshold_mm": RAIN_THRESHOLD_MM,
        "times": [times[i] for i in window],
        "rain_mm": [[point["hourly"]["precipitation"][i] or 0.0 for point in series] for i in window],
    }


async def weather_tile(layer: str, z: int, x: int, y: int) -> bytes:
    """One OpenWeather map tile, fetched here so the API key never reaches the browser."""
    upstream = TILE_LAYERS.get(layer)
    if upstream is None:
        raise NotFound("Unknown layer.")
    # ponytail: one client per tile; move to a shared AsyncClient if tile traffic ever
    # becomes a bottleneck. Browser caching keeps the volume low for now.
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await _fetch(client, TILE_URL.format(layer=upstream, z=z, x=x, y=y),
                                {"appid": _api_key()})
    return response.content
