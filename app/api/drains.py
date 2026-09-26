"""Storm water drain endpoints: the network as GeoJSON, one drain's hydraulics, the load
on a selection, and the filter values. The network is built in
app/services/drain_network.py."""

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from app.errors import NotFound
from app.services import drain_network, hydraulics

router = APIRouter(prefix="/data-collection", tags=["Drains"])

_STRIP_M = Query(hydraulics.STRIP_WIDTH_M, ge=1, le=500, description="Catchment strip each drain collects from, m")
_RAIN_MM_H = Query(0.0, ge=0, le=500, description="Rainfall intensity, mm/h")
_RUNOFF_COEFF = Query(hydraulics.RUNOFF_COEFF, gt=0, le=1, description="Rational-method runoff coefficient")


@router.get("/drains", summary="GCC storm water drains as GeoJSON")
async def get_drains(
    zone: str | None = Query(None, description="Filter by zone, e.g. N07"),
    ward: str | None = Query(None, description="Filter by ward, e.g. N082"),
    drain_type: str | None = Query(None, description="Filter by DRAIN_TYPE"),
    status: str | None = Query(None, description="Filter by STATUS"),
    strip_m: float = _STRIP_M,
) -> dict[str, Any]:
    filters = {"ZONE": zone, "WARD": ward, "DRAIN_TYPE": drain_type, "STATUS": status}
    features = await asyncio.to_thread(drain_network.drain_features, filters, strip_m)
    return {
        "type": "FeatureCollection",
        "features": features,
        "total": len(features),
        # So the page computes inflow exactly the way the server does.
        "runoff_coefficient": hydraulics.RUNOFF_COEFF,
        "strip_m": strip_m,
    }


@router.get("/drain-hydraulics", summary="Capacity and live state of one drain")
async def get_drain_hydraulics(
    feature_no: int = Query(..., description="feature_no of the drain"),
    rain_mm_h: float = _RAIN_MM_H,
    strip_m: float = _STRIP_M,
    runoff_coeff: float = _RUNOFF_COEFF,
) -> dict[str, Any]:
    drain = await asyncio.to_thread(drain_network.drain_by_feature, feature_no)
    if drain is None:
        raise NotFound(f"No drain with feature_no {feature_no}")
    return drain_network.drain_report(drain, rain_mm_h, strip_m, runoff_coeff)


@router.get("/network-summary", summary="Live load across the selected drain network")
async def get_network_summary(
    rain_mm_h: float = _RAIN_MM_H,
    zone: str | None = Query(None),
    ward: str | None = Query(None),
    strip_m: float = _STRIP_M,
    runoff_coeff: float = _RUNOFF_COEFF,
    worst: int = Query(5, ge=0, le=50, description="How many overloaded drains to name"),
) -> dict[str, Any]:
    return await asyncio.to_thread(drain_network.network_summary, {"ZONE": zone, "WARD": ward},
                                   rain_mm_h, strip_m, runoff_coeff, worst)


@router.get("/drain-filters", summary="Available filter values for drains")
async def get_drain_filters(zone: str | None = Query(None)) -> dict[str, Any]:
    # Off the event loop: on a cold start the network takes a minute to build, and the
    # page asks for these the moment it opens.
    return await asyncio.to_thread(drain_network.filter_options, zone)
