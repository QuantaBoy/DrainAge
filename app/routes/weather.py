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
                "hourly": "precipitation,precipitation_probability,temperature_2m",
                "forecast_days": days,
                # The hourly series starts at the top of the current hour, part of
                # which has already passed, so a few extra hours are requested.
                "forecast_hours": hours + 3,
                "timezone": "auto",
            },
        )
    data = response.json()
    daily, hourly = data["daily"], data["hourly"]

    # "Next N hours" is measured in the forecast location's local time, which is what
    # the series is expressed in, not in this server's timezone.
    local_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=data.get("utc_offset_seconds", 0)
    )
    current_hour = local_now.replace(tzinfo=None, minute=0, second=0, microsecond=0)

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
    ][:hours]

    return {
        "district": place.get("name") or district,
        "state": place.get("state"),
        "lat": place["lat"],
        "lon": place["lon"],
        "hours": upcoming,
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
