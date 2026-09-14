import datetime
import os
from typing import Any, Dict

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, Response, status

router = APIRouter(prefix="/data-collection", tags=["Weather"])

GEOCODE_URL = "https://api.openweathermap.org/geo/1.0/direct"
WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
TILE_URL = "https://tile.openweathermap.org/map/{layer}/{z}/{x}/{y}.png"

# Open-Meteo needs no key. OpenWeather's free tier has no multi-day forecast.
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Only these reach OpenWeather: the layer name goes into an upstream URL, so it is
# matched against a fixed set rather than passed through.
TILE_LAYERS = {
    "rain": "precipitation_new",
    "temp": "temp_new",
    "wind": "wind_new",
    "clouds": "clouds_new",
}


# A district's coordinates never change, so geocoding is looked up once per name
# instead of spending one of the 60 calls/min on every search.
_geocoded: Dict[Any, Dict[str, Any]] = {}


def rain_mm(data: Dict[str, Any]) -> float:
    """Rainfall in mm/h. Stations report either a 1h or a 3h total, never guaranteed
    both, so a missing 1h is converted rather than read as zero rain."""
    rain = data.get("rain") or {}
    if "1h" in rain:
        return rain["1h"]
    if "3h" in rain:
        return round(rain["3h"] / 3, 2)
    return 0


async def _get(client: httpx.AsyncClient, url: str, params: Dict[str, Any]) -> Any:
    try:
        response = await client.get(url, params=params)
    except httpx.RequestError as exc:
        host = httpx.URL(url).host
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not reach {host}: {exc}",
        )
    if response.status_code == 401:
        # Upstream 401 means our key is bad, which is a server misconfiguration,
        # not the caller sending bad credentials.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenWeather rejected the API key.",
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


def _require_key() -> str:
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OPENWEATHER_API_KEY is not set on the server.",
        )
    return api_key


async def resolve_district(
    client: httpx.AsyncClient, district: str, country: str, api_key: str
) -> Dict[str, Any]:
    """District name to a place with lat/lon. Cached, since names do not move."""
    cache_key = (district.strip().casefold(), country.upper())
    place = _geocoded.get(cache_key)
    if place is not None:
        return place

    # Geocode rather than pass the name to the weather endpoint: its own name lookup
    # misses many districts that are not major cities, while this pins a state too.
    places = await _get(client, GEOCODE_URL, {
        "q": f"{district},{country}", "limit": 1, "appid": api_key,
    })
    if not places:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No district named '{district}' found in {country}.",
        )
    if len(_geocoded) > 500:
        _geocoded.clear()
    _geocoded[cache_key] = places[0]
    return places[0]


@router.get("/weather", summary="Current weather for a district")
async def get_weather(
    district: str = Query(..., min_length=2, max_length=80, description="District name"),
    country: str = Query("IN", min_length=2, max_length=2, description="ISO country code"),
) -> Dict[str, Any]:
    api_key = _require_key()

    async with httpx.AsyncClient(timeout=10.0) as client:
        place = await resolve_district(client, district, country, api_key)
        data = await _get(client, WEATHER_URL, {
            "lat": place["lat"], "lon": place["lon"], "units": "metric", "appid": api_key,
        })

    return {
        "district": place.get("name", district),
        "state": place.get("state"),
        "country": place.get("country"),
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
    district: str = Query(None, min_length=2, max_length=80, description="District name"),
    lat: float = Query(None, ge=-90, le=90, description="Latitude, instead of district"),
    lon: float = Query(None, ge=-180, le=180, description="Longitude, instead of district"),
    country: str = Query("IN", min_length=2, max_length=2, description="ISO country code"),
    days: int = Query(7, ge=1, le=16, description="Forecast days"),
    hours: int = Query(3, ge=1, le=48, description="Forecast hours"),
) -> Dict[str, Any]:
    if district is None and (lat is None or lon is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either 'district' or both 'lat' and 'lon'.",
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        if district is not None:
            place = await resolve_district(client, district, country, _require_key())
        else:
            # Coordinates go straight to Open-Meteo: naming the spot would cost a
            # reverse-geocode call for a label nobody needs here.
            place = {"lat": lat, "lon": lon, "name": None, "state": None}

        data = await _get(client, FORECAST_URL, {
            "latitude": place["lat"],
            "longitude": place["lon"],
            "daily": "precipitation_sum,precipitation_probability_max,temperature_2m_max",
            "hourly": "precipitation,precipitation_probability,temperature_2m",
            "forecast_days": days,
            # A few extra hours of slack: the series starts at the top of the current
            # hour, so some of it is already in the past by the time we filter.
            "forecast_hours": hours + 3,
            "timezone": "auto",
        })

    daily = data["daily"]
    hourly = data["hourly"]

    # "Next N hours" measured at the forecast location, not on this server: the two
    # can sit in different timezones and the series is in the location's local time.
    local_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=data.get("utc_offset_seconds", 0)
    )
    current_hour = local_now.replace(tzinfo=None, minute=0, second=0, microsecond=0)

    upcoming = [
        {
            "time": time,
            "rain_mm": rain if rain is not None else 0,
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
                "rain_mm": rain if rain is not None else 0,
                "rain_chance_pct": chance,
                "temp_max_c": tmax,
            }
            for date, rain, chance, tmax in zip(
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
    z: int = Path(..., ge=0, le=12),
    x: int = Path(..., ge=0),
    y: int = Path(..., ge=0),
) -> Response:
    """Proxy OpenWeather tiles so the API key stays on the server."""
    upstream = TILE_LAYERS.get(layer)
    if upstream is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown layer.")

    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OPENWEATHER_API_KEY is not set on the server.",
        )

    url = TILE_URL.format(layer=upstream, z=z, x=x, y=y)
    # ponytail: new client per tile. Browser caching keeps the volume low; switch to a
    # shared AsyncClient if tile traffic ever becomes the bottleneck.
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url, params={"appid": api_key})
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not reach OpenWeather: {exc}",
            )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Tile unavailable.")

    # A map view pulls ~20 tiles per layer, against a 60 calls/min free tier, so
    # letting the browser cache them is what keeps this inside the quota.
    return Response(
        content=response.content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )
