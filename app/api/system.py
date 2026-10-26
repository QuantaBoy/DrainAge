"""Engine and resource endpoints for the Technology page: what the forecast engine is
doing on this device, and every resource checked on disk."""

import asyncio
import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app import config
from app.services import compute, history, inventory

router = APIRouter(prefix="/data-collection", tags=["System"])


@router.get("/engine", summary="The forecast engine: device and cache")
async def get_engine() -> dict[str, Any]:
    # Reading the CPU load takes half a second of sampling: off the event loop.
    return await asyncio.to_thread(compute.engine_status)


@router.get("/inventory", summary="Every data resource, checked on this server")
async def get_inventory() -> dict[str, Any]:
    found = await asyncio.to_thread(inventory.check)
    return {**found, "validation": history.validation_summary()}


@router.get("/dem-check", summary="The supplied elevation points against the terrain model")
async def get_dem_check() -> dict[str, Any]:
    # Made by scripts/make_dem_check.py, which needs the terrain files.
    return json.loads((config.DATA_DIR / "dem_check.json").read_text(encoding="utf-8"))


@router.get("/dem-points.csv", summary="The 1,644 supplied elevation points (CSV)")
async def get_dem_points() -> FileResponse:
    return FileResponse(config.SURVEY_DIR / "dem_points (1).csv", media_type="text/csv",
                        filename="chennai_dem_points.csv")
