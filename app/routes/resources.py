"""Resources: the wards behind the model, with their base maps.

The GCC / SECON-JBA base-map sheets in Ward/ are the paper source the drain survey was
digitised from. This lists every ward the site knows about - from the survey, from the
sheets, or both - with the figures the model derives for it, and serves each sheet.
"""

import asyncio
import re
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.routes.drains import _load_csv, _ward_sheets

router = APIRouter(tags=["Resources"])

WARD_DIR = Path(__file__).resolve().parent.parent.parent / "Ward"

# GCC's 15 corporation zones, in code order (N01 = Zone I, ...).
ZONE_NAMES = [
    "Thiruvottiyur", "Manali", "Madhavaram", "Tondiarpet", "Royapuram",
    "Thiru Vi Ka Nagar", "Ambattur", "Anna Nagar", "Teynampet", "Kodambakkam",
    "Valasaravakkam", "Alandur", "Adyar", "Perungudi", "Sholinganallur",
]
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV"]


def _zone_label(code: str) -> str:
    match = re.fullmatch(r"N(\d{2})", code or "")
    number = int(match.group(1)) if match else 0
    if 1 <= number <= len(ZONE_NAMES):
        return f"Zone {ROMAN[number - 1]} – {ZONE_NAMES[number - 1]}"
    return code or "Zone not recorded"


def _sheet_files() -> dict[int, Path]:
    """Ward number -> its base-map PDF.

    Files are named "100.pdf" or "Ward 103.pdf"; anything without a number in its name
    (the drainage-places note) is not a ward sheet.
    """
    files = {}
    for path in WARD_DIR.glob("*.pdf"):
        match = re.search(r"\d+", path.stem)
        if match:
            files[int(match.group())] = path
    return files


@router.get("/data-collection/wards", summary="Every ward, with its base map and drain figures")
async def get_wards() -> dict[str, Any]:
    drains = await asyncio.to_thread(_load_csv)
    sheets = _ward_sheets()
    files = _sheet_files()

    stats: dict[str, dict[str, Any]] = {}
    for drain in drains:
        props = drain["props"]
        ward = props.get("WARD") or ""
        if not re.fullmatch(r"N\d{3}", ward):
            continue
        entry = stats.setdefault(ward, {"zones": Counter(), "drains": 0, "length_m": 0.0,
                                        "capacity": 0.0, "poor": 0})
        if props.get("ZONE"):
            entry["zones"][props["ZONE"]] += 1
        entry["drains"] += 1
        entry["length_m"] += drain["length_m"]
        entry["capacity"] += drain["capacity"]["effective_capacity_m3s"]
        entry["poor"] += (props.get("STATUS") or "").strip().lower() == "bad"

    wards = []
    for code in sorted(set(stats) | set(sheets)):
        entry, sheet = stats.get(code), sheets.get(code)
        number = int(code[1:])
        # The zone the survey records; only a ward with no surveyed drains falls back to
        # its sheet. Where the two disagree the survey is shown and the sheet flagged.
        zone = entry["zones"].most_common(1)[0][0] if entry and entry["zones"] else (
            sheet["zone"] if sheet else "")
        wards.append({
            "ward": code,
            "number": number,
            "zone": zone,
            "zone_label": _zone_label(zone),
            "map_url": f"/resources/ward-map/{number}" if number in files else None,
            "map_zone_label": _zone_label(sheet["zone"]) if sheet and sheet["zone"] != zone else None,
            "drains": entry["drains"] if entry else 0,
            "length_km": round(entry["length_m"] / 1000, 2) if entry else 0.0,
            "capacity_m3s": round(entry["capacity"], 2) if entry else 0.0,
            "poor": entry["poor"] if entry else 0,
        })

    zones = sorted({w["zone"] for w in wards if w["zone"]})
    return {"wards": wards, "zones": [{"code": z, "label": _zone_label(z)} for z in zones]}


@router.get("/resources/ward-map/{number}", summary="Base-map sheet for one ward (PDF)")
async def get_ward_map(number: int) -> FileResponse:
    # The path comes from the directory listing, never from the request.
    path = _sheet_files().get(number)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No base map for ward {number}")
    return FileResponse(path, media_type="application/pdf", filename=f"Ward {number} base map.pdf",
                        content_disposition_type="inline")


if __name__ == "__main__":
    assert _zone_label("N07") == "Zone VII – Ambattur"
    assert _zone_label("") == "Zone not recorded" and _zone_label("N99") == "N99"
    # The page links every sheet by ward number, so the files and the index must agree.
    assert set(_sheet_files()) == {s["ward"] for s in _ward_sheets().values()}
    print("resources self-check passed")
