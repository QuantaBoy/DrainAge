"""Weather data routes: current conditions, rainfall forecast and map tiles.

Current weather and tiles come from OpenWeather (key required, kept server-side).
The forecast comes from Open-Meteo, which needs no key.
"""

import datetime
import os
from typing import Annotated, Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, Response, status

router = APIRouter(prefix="/data-collection", tags=["Weather"])

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

District = Annotated[
    Optional[str], Query(min_length=2, max_length=80, description="District or city")
]
Latitude = Annotated[
    Optional[float], Query(ge=-90, le=90, description="Latitude, instead of district")
]
Longitude = Annotated[
    Optional[float],
    Query(ge=-180, le=180, description="Longitude, instead of district"),
]
Country = Annotated[
    str, Query(min_length=2, max_length=2, description="ISO 3166 country code")
]

# A district's coordinates never change, so each name is geocoded once instead of
# spending one of the 60 calls/min free-tier quota on every search.
_geocoded: dict[tuple[str, str], dict[str, Any]] = {}
_GEOCODE_CACHE_LIMIT = 500

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


def local_current_hour(utc_offset_seconds: int) -> datetime.datetime:
    """The current hour at a location, as a naive datetime in its local time.

    Forecast series are expressed in the location's local time, which may differ
    from this server's timezone.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    local = now + datetime.timedelta(seconds=utc_offset_seconds)
    return local.replace(tzinfo=None, minute=0, second=0, microsecond=0)


def next_rain_spell(hours: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The next rain event in an hourly forecast, if any.

    An event runs from the first rainy hour to the last rainy hour before a dry
    break longer than MAX_DRY_GAP_HOURS. `hours` must start at the current hour,
    so the index of the first rainy hour is also how many hours away it is.
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
        spell = hours[start : end + 1]
        peak = max(spell, key=lambda h: h["rain_mm"])
        # The spell ends when its last rainy hour does.
        ends = datetime.datetime.fromisoformat(hours[end]["time"]) + datetime.timedelta(
            hours=1
        )
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


def heaviest_hour(hours: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
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


def _require_key() -> str:
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OPENWEATHER_API_KEY is not set on the server.",
        )
    return api_key


def _require_place(
    district: Optional[str], lat: Optional[float], lon: Optional[float]
) -> None:
    if district is None and (lat is None or lon is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either 'district' or both 'lat' and 'lon'.",
        )


async def _fetch(
    client: httpx.AsyncClient, url: str, params: dict[str, Any]
) -> httpx.Response:
    """GET an upstream URL, translating its failures into meaningful API errors."""
    host = httpx.URL(url).host
    try:
        response = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not reach {host}.",
        ) from exc

    if response.status_code == status.HTTP_200_OK:
        return response
    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        # A rejected key is a server misconfiguration, not a bad client request.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{host} rejected the API key.",
        )
    if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{host} rate limit reached. Try again shortly.",
        )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"{host} returned HTTP {response.status_code}.",
    )


async def resolve_district(
    client: httpx.AsyncClient, district: str, country: str, api_key: str
) -> dict[str, Any]:
    """Resolve a district or city name to a place with coordinates and state."""
    cache_key = (district.strip().casefold(), country.upper())
    if cache_key in _geocoded:
        return _geocoded[cache_key]

    # Geocoded explicitly because the weather endpoint's own name lookup misses many
    # districts that are not major cities.
    response = await _fetch(
        client,
        GEOCODE_URL,
        {"q": f"{district},{country}", "limit": 1, "appid": api_key},
    )
    places = response.json()
    if not places:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No district named '{district}' found in {country}.",
        )

    if len(_geocoded) >= _GEOCODE_CACHE_LIMIT:
        _geocoded.clear()
    _geocoded[cache_key] = places[0]
    return places[0]


@router.get("/weather", summary="Current weather by district or coordinates")
async def get_weather(
    district: District = None,
    lat: Latitude = None,
    lon: Longitude = None,
    country: Country = "IN",
) -> dict[str, Any]:
    _require_place(district, lat, lon)
    api_key = _require_key()

    async with httpx.AsyncClient(timeout=10.0) as client:
        if district is not None:
            place = await resolve_district(client, district, country, api_key)
        else:
            place = {"lat": lat, "lon": lon}
        response = await _fetch(
            client,
            WEATHER_URL,
            {
                "lat": place["lat"],
                "lon": place["lon"],
                "units": "metric",
                "appid": api_key,
            },
        )
    data = response.json()

    return {
        # For coordinates, the weather response already names the nearest locality,
        # so no reverse-geocode call is spent on a label.
        "district": place.get("name") or data.get("name") or district,
        "state": place.get("state"),
        "country": place.get("country") or data.get("sys", {}).get("country"),
        "lat": place["lat"],
        "lon": place["lon"],
        "temp_c": data["main"]["temp"],
        "feels_like_c": data["main"]["feels_like"],
        "humidity_pct": data["main"]["humidity"],
        "condition": data["weather"][0]["description"],
        "wind_ms": data["wind"]["speed"],
        "rain_1h_mm": rain_mm(data),
    }


@router.get("/forecast", summary="Rainfall forecast by district or coordinates")
async def get_forecast(
    district: District = None,
    lat: Latitude = None,
    lon: Longitude = None,
    country: Country = "IN",
    days: Annotated[int, Query(ge=1, le=16, description="Forecast days")] = 7,
    hours: Annotated[int, Query(ge=1, le=48, description="Forecast hours")] = 3,
) -> dict[str, Any]:
    _require_place(district, lat, lon)

    async with httpx.AsyncClient(timeout=15.0) as client:
        if district is not None:
            place = await resolve_district(client, district, country, _require_key())
        else:
            place = {"lat": lat, "lon": lon}
        response = await _fetch(
            client,
            FORECAST_URL,
            {
                "latitude": place["lat"],
                "longitude": place["lon"],
                "daily": "precipitation_sum,precipitation_probability_max,"
                "temperature_2m_max",
                # Hourly data covers every forecast day, so the next rain can be
                # found however far ahead it is, not just within `hours`.
                "hourly": "precipitation,precipitation_probability,temperature_2m",
                "forecast_days": days,
                "timezone": "auto",
            },
        )
    data = response.json()
    daily, hourly = data["daily"], data["hourly"]
    current_hour = local_current_hour(data.get("utc_offset_seconds", 0))

    upcoming = [
        {
            "time": time,
            "rain_mm": rain or 0.0,
            "rain_chance_pct": chance,
            "temp_c": temp,
        }
        for time, rain, chance, temp in zip(
            hourly["time"],
            hourly["precipitation"],
            hourly["precipitation_probability"],
            hourly["temperature_2m"],
        )
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
            {
                "date": date,
                "rain_mm": rain or 0.0,
                "rain_chance_pct": chance,
                "temp_max_c": temp_max,
            }
            for date, rain, chance, temp_max in zip(
                daily["time"],
                daily["precipitation_sum"],
                daily["precipitation_probability_max"],
                daily["temperature_2m_max"],
            )
        ],
    }


@router.get("/rain-grid", summary="Hourly rainfall forecast on a grid around a point")
async def get_rain_grid(
    response: Response,
    # Kept off the poles so the whole grid stays within valid latitudes.
    lat: Annotated[float, Query(ge=-89, le=89, description="Grid centre latitude")],
    lon: Annotated[float, Query(ge=-180, le=180, description="Grid centre longitude")],
    hours: Annotated[int, Query(ge=1, le=72, description="Hours to include")] = 24,
) -> dict[str, Any]:
    """Rain for each grid point and hour, used to animate rain moving over the map.

    `rain_mm[hour][row * GRID_SIZE + col]`, rows running south to north and
    columns west to east.
    """
    half = GRID_SIZE // 2
    lats = [round(lat + (i - half) * GRID_STEP_DEG, 4) for i in range(GRID_SIZE)]
    # Wrapped so a grid centred near the antimeridian still sends valid longitudes.
    lons = [
        round((lon + (i - half) * GRID_STEP_DEG + 180) % 360 - 180, 4)
        for i in range(GRID_SIZE)
    ]
    points = [(point_lat, point_lon) for point_lat in lats for point_lon in lons]

    async with httpx.AsyncClient(timeout=20.0) as client:
        upstream = await _fetch(
            client,
            FORECAST_URL,
            {
                # Every grid point goes in one request rather than one call each.
                "latitude": ",".join(str(point[0]) for point in points),
                "longitude": ",".join(str(point[1]) for point in points),
                "hourly": "precipitation",
                # Covers `hours` from any time of day, plus today's elapsed hours.
                "forecast_days": hours // 24 + 2,
                "timezone": "auto",
            },
        )
    series = upstream.json()

    times = series[0]["hourly"]["time"]
    current_hour = local_current_hour(series[0].get("utc_offset_seconds", 0))
    first = next(
        (
            i
            for i, time in enumerate(times)
            if datetime.datetime.fromisoformat(time) >= current_hour
        ),
        0,
    )
    window = range(first, min(first + hours, len(times)))

    # The grid changes only when the forecast model runs, so a short browser cache
    # spares repeated upstream calls while someone replays the animation.
    response.headers["Cache-Control"] = "public, max-age=600"
    return {
        "lats": lats,
        "lons": lons,
        "step_deg": GRID_STEP_DEG,
        # How the series was trimmed, so a client can verify it starts at "now".
        "from_hour": current_hour.isoformat(timespec="minutes"),
        "skipped_past_hours": first,
        "rain_threshold_mm": RAIN_THRESHOLD_MM,
        "times": [times[i] for i in window],
        "rain_mm": [
            [point["hourly"]["precipitation"][i] or 0.0 for point in series]
            for i in window
        ],
    }


@router.get("/tiles/{layer}/{z}/{x}/{y}.png", summary="Weather map tile")
async def get_tile(
    layer: str,
    z: Annotated[int, Path(ge=0, le=12)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
) -> Response:
    """Proxy an OpenWeather map tile so the API key never reaches the browser."""
    upstream = TILE_LAYERS.get(layer)
    if upstream is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown layer."
        )

    url = TILE_URL.format(layer=upstream, z=z, x=x, y=y)
    # ponytail: one client per tile; move to a shared AsyncClient if tile traffic
    # ever becomes a bottleneck. Browser caching keeps the volume low for now.
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await _fetch(client, url, {"appid": _require_key()})

    # One map view pulls ~20 tiles per layer against a 60 calls/min free tier, so
    # browser caching is what keeps the proxy within quota.
    return Response(
        content=response.content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )
