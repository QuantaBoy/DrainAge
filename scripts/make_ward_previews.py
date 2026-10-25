"""Preview images of the ward base-map sheets, for the ward catalogue on the Resources page.

    python scripts/make_ward_previews.py      # -> app/static/img/wards/<ward number>.webp

Each sheet is rendered and cropped to its surveyed manholes, placed on the sheet with
the grid-line georeference scripts/digitise_wards.py found for it (app/data/
ward_points.json), so a card shows the ward's drains, not the survey tables beside
them. Needs PyMuPDF (pip install pymupdf), like scripts/digitise_wards.py; the website
only reads the images it writes.
"""

import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
WARD_DIR = ROOT / "Ward"
SHEETS_JSON = ROOT / "app" / "data" / "ward_points.json"
OUT_DIR = ROOT / "app" / "static" / "img" / "wards"
RENDER_PX = 2400        # long side of the rendered sheet
SIZE_PX = 640           # the square preview


def manholes_box(sheet: dict, zoom: float, w: int, h: int) -> tuple[int, int, int, int] | None:
    """The square around the sheet's surveyed manholes, in rendered pixels.

    The georeference maps a sheet point to UTM as east = e0 + ex * x, north = n0 + ny * y;
    inverted here to put each manhole's surveyed easting and northing on the sheet.
    """
    geo = sheet.get("georeference") or {}
    pts = [(p["easting"], p["northing"]) for p in sheet.get("points", [])
           if p.get("easting") and p.get("northing")]
    if "east" not in geo or len(pts) < 5:
        return None
    (e0, ex), (n0, ny) = geo["east"], geo["north"]
    xy = np.array([((e - e0) / ex, (n - n0) / ny) for e, n in pts]) * zoom
    inside = (xy[:, 0] >= 0) & (xy[:, 0] < w) & (xy[:, 1] >= 0) & (xy[:, 1] < h)
    if inside.sum() < 5:
        return None
    xy = xy[inside]
    left, right = np.percentile(xy[:, 0], [2, 98])
    top, bottom = np.percentile(xy[:, 1], [2, 98])
    side = int(max(right - left, bottom - top) * 1.15) + 40
    cx, cy = (left + right) / 2, (top + bottom) / 2
    x0 = int(min(max(0, cx - side / 2), max(0, w - side)))
    y0 = int(min(max(0, cy - side / 2), max(0, h - side)))
    return x0, y0, min(w, x0 + side), min(h, y0 + side)


def main() -> None:
    try:
        import pymupdf
    except ImportError:
        sys.exit("Needs PyMuPDF: pip install pymupdf")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sheets = {}
    for sheet in json.loads(SHEETS_JSON.read_text(encoding="utf-8"))["sheets"]:
        match = re.search(r"\d+", sheet["sheet"])
        if match:
            sheets[int(match.group())] = sheet
    done = fallback = 0
    for pdf in sorted(WARD_DIR.glob("*.pdf")):
        match = re.search(r"\d+", pdf.stem)
        if not match:
            continue                      # not a ward sheet (the drainage-places note)
        number = int(match.group())
        page = pymupdf.open(pdf)[0]
        zoom = RENDER_PX / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        box = manholes_box(sheets[number], zoom, pix.width, pix.height) if number in sheets else None
        if box is None:                   # no georeference: the sheet's middle
            fallback += 1
            side = min(pix.width, pix.height)
            box = ((pix.width - side) // 2, (pix.height - side) // 2,
                   (pix.width + side) // 2, (pix.height + side) // 2)
        crop = Image.fromarray(rgb).crop(box).resize((SIZE_PX, SIZE_PX), Image.LANCZOS)
        crop.save(OUT_DIR / f"{number}.webp", "WEBP", quality=72, method=6)
        done += 1
    print(f"{done} previews in {OUT_DIR} ({fallback} without a georeference, centred)")


if __name__ == "__main__":
    main()
