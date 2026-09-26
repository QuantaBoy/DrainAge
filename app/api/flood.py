"""Flood endpoints: the elevation layer, the rainfall nowcast, and the street-level flood
forecast. The model is in app/services/forecast.py."""

import asyncio
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response

from app import config
from app.errors import Unavailable
from app.services import forecast, hydraulics, rainfall, terrain

router = APIRouter(prefix="/data-collection", tags=["Flood"])

# Shared with the router (app/api/navigation.py), which runs the same storm.
RAIN_MM_H = Query(None, ge=0, le=500, description="Hold the rain at this rate for 3 h "
                                                    "instead of the nowcast, for what-if runs")
# How much of each drain's surveyed conveyance is working: 1 is the survey as built,
# 0.5 half silted, 0 a network that is full or blocked.
DRAIN_CONDITION = Query(1.0, ge=0, le=1, description="Share of the surveyed drain "
                                                     "capacity that is working (1 = as surveyed)")
RUNOFF_COEFF = Query(hydraulics.RUNOFF_COEFF, gt=0, le=1)


async def _relief() -> dict[str, Any]:
    layer = await asyncio.to_thread(terrain.relief_layer)
    if layer is None:
        raise Unavailable("No DEM tiles in app/data")
    return layer


@router.get("/elevation", summary="Where the elevation layer goes, and how to read it")
async def get_elevation() -> dict[str, Any]:
    layer = await _relief()
    return {"image": "/data-collection/elevation.png",
            **{key: value for key, value in layer.items() if key != "png"}}


@router.get("/elevation.png", summary="The DEM as a shaded-relief image")
async def get_elevation_png() -> Response:
    layer = await _relief()
    return Response(layer["png"], media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/nowcast", summary="Rainfall nowcast for the next three hours")
async def get_nowcast(lat: float = Query(..., ge=-90, le=90),
                      lon: float = Query(..., ge=-180, le=180)) -> dict[str, Any]:
    return await rainfall.nowcast(lat, lon)


@router.get("/rain-ponding/streets",
            summary="Coupled flood nowcast: when each street goes under, how deep, when it clears, "
                    "and which manholes surcharge; 5 minute steps, 3 hours")
async def get_street_forecast(
    lat: float = Query(config.CHENNAI_CENTRE[0], ge=-90, le=90, description="Where to take the rainfall nowcast"),
    lon: float = Query(config.CHENNAI_CENTRE[1], ge=-180, le=180),
    rain_mm_h: float | None = RAIN_MM_H,
    runoff_coeff: float = RUNOFF_COEFF,
    drain_condition: float = DRAIN_CONDITION,
    min_depth_cm: float = Query(config.REPORT_DEPTH_M * 100, ge=1, le=500),
    zone: str | None = Query(None, description="Only streets in this zone, e.g. N07"),
    ward: str | None = Query(None, description="Only streets in this ward, e.g. N082"),
) -> dict[str, Any]:
    rain = await rainfall.report_steps(lat, lon, rain_mm_h)
    return await asyncio.to_thread(forecast.street_forecast, rain, runoff_coeff, drain_condition,
                                   min_depth_cm, zone, ward)
