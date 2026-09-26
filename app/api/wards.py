"""Ward endpoints: every ward with its drain figures, and each ward's base-map sheet."""

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.errors import NotFound
from app.services import wards

router = APIRouter(tags=["Wards"])


@router.get("/data-collection/wards", summary="Every ward, with its base map and drain figures")
async def get_wards() -> dict[str, Any]:
    return await asyncio.to_thread(wards.ward_index)


@router.get("/resources/ward-map/{number}", summary="Base-map sheet for one ward (PDF)")
async def get_ward_map(number: int) -> FileResponse:
    # The path comes from the directory listing, never from the request.
    path = wards.sheet_files().get(number)
    if path is None:
        raise NotFound(f"No base map for ward {number}")
    return FileResponse(path, media_type="application/pdf", filename=f"Ward {number} base map.pdf",
                        content_disposition_type="inline")
