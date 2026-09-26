"""Street and waterway geometry from OpenStreetMap, served from the files on disk."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.services import osm

router = APIRouter(prefix="/data-collection", tags=["Streets"])


# The files are served as they are: parsing 80,000 roads only to serialise them again
# would hold the event loop for seconds.
@router.get("/streets", summary="Chennai road network as GeoJSON")
async def get_streets() -> FileResponse:
    return FileResponse(await osm.streets_file(), media_type="application/geo+json")


@router.get("/waterways", summary="Chennai rivers, canals and drains as GeoJSON")
async def get_waterways() -> FileResponse:
    return FileResponse(await osm.waterways_file(), media_type="application/geo+json")
