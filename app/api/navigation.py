"""Navigation endpoints: place search, and the flood-safe route between two places.
The routing is in app/services/navigation.py."""

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from app.api.flood import DRAIN_CONDITION, RAIN_MM_H, RUNOFF_COEFF
from app.services import navigation, rainfall

router = APIRouter(prefix="/data-collection", tags=["Navigation"])


@router.get("/geocode", summary="Places in Chennai matching a name")
async def geocode(q: str = Query(..., min_length=2, max_length=120)) -> dict[str, Any]:
    return {"query": q, "results": await navigation.geocode(q)}


@router.get("/route", summary="The flood-safe route between two places, 0-3 h ahead")
async def get_route(
    from_lat: float = Query(..., ge=12.0, le=14.0),
    from_lon: float = Query(..., ge=79.5, le=81.0),
    to_lat: float = Query(..., ge=12.0, le=14.0),
    to_lon: float = Query(..., ge=79.5, le=81.0),
    leave_in_min: int = Query(0, ge=0, le=180, description="When the trip starts, minutes from now"),
    rain_mm_h: float | None = RAIN_MM_H,
    drain_condition: float = DRAIN_CONDITION,
    runoff_coeff: float = RUNOFF_COEFF,
    algorithm: str = Query("astar", pattern="^(astar|dijkstra)$"),
) -> dict[str, Any]:
    rain = await rainfall.report_steps(from_lat, from_lon, rain_mm_h)
    return await asyncio.to_thread(navigation.plan_route, (from_lat, from_lon), (to_lat, to_lon),
                                   leave_in_min, rain, runoff_coeff, drain_condition, algorithm)
