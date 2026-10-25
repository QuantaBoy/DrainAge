"""Engine and resource endpoints for the Technology page: what the forecast engine is
doing on this device, and every resource checked on disk."""

import asyncio
from typing import Any

from fastapi import APIRouter

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
