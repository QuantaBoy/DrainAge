"""Weather endpoints: current conditions, the rainfall forecast, the rain grid and map
tiles. The upstream calls live in app/services/weather.py."""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response

from app.services import weather

router = APIRouter(prefix="/data-collection", tags=["Weather"])

District = Annotated[str | None, Query(min_length=2, max_length=80, description="District or city")]
Latitude = Annotated[float | None, Query(ge=-90, le=90, description="Latitude, instead of district")]
Longitude = Annotated[float | None, Query(ge=-180, le=180, description="Longitude, instead of district")]
Country = Annotated[str, Query(min_length=2, max_length=2, description="ISO 3166 country code")]


@router.get("/weather", summary="Current weather by district or coordinates")
async def get_weather(district: District = None, lat: Latitude = None, lon: Longitude = None,
                      country: Country = "IN") -> dict[str, Any]:
    return await weather.current_weather(district, lat, lon, country)


@router.get("/forecast", summary="Rainfall forecast by district or coordinates")
async def get_forecast(
    district: District = None,
    lat: Latitude = None,
    lon: Longitude = None,
    country: Country = "IN",
    days: Annotated[int, Query(ge=1, le=16, description="Forecast days")] = 7,
    hours: Annotated[int, Query(ge=1, le=48, description="Forecast hours")] = 3,
) -> dict[str, Any]:
    return await weather.rainfall_forecast(district, lat, lon, country, days, hours)


@router.get("/rain-grid", summary="Hourly rainfall forecast on a grid around a point")
async def get_rain_grid(
    response: Response,
    lat: Annotated[float, Query(ge=-89, le=89, description="Grid centre latitude")],
    lon: Annotated[float, Query(ge=-180, le=180, description="Grid centre longitude")],
    hours: Annotated[int, Query(ge=1, le=72, description="Hours to include")] = 24,
) -> dict[str, Any]:
    grid = await weather.rain_grid(lat, lon, hours)
    response.headers["Cache-Control"] = "public, max-age=600"
    return grid


@router.get("/tiles/{layer}/{z}/{x}/{y}.png", summary="Weather map tile")
async def get_tile(
    layer: str,
    z: Annotated[int, Path(ge=0, le=12)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
) -> Response:
    # One map view pulls about 20 tiles per layer against a 60 calls/min free tier, so
    # browser caching is what keeps the proxy within quota.
    return Response(await weather.weather_tile(layer, z, x, y), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=600"})
