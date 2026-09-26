"""Download a DEM for Chennai from OpenTopography's Global DEM API.

    python scripts/fetch_dem.py                       # COP30 over Chennai -> app/data/dem/COP30.tif
    python scripts/fetch_dem.py --demtype GEDTM30     # a bare-earth terrain model instead
    python scripts/fetch_dem.py --demtype NASADEM --bbox 12.85 13.25 80.10 80.35

The key is read from OPENTOPOGRAPHY_API_KEY in .env (see .env.example). It is never
printed: the API takes it as a query parameter, so a URL in an error message would
leak it, and this script reports errors without the URL.

The area is one request, not tiles, so the file is exactly the box asked for.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import NoReturn

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "app" / "data" / "dem"
API = "https://portal.opentopography.org/API/globaldem"

# Datasets worth trying for Chennai, from the API's own list. COP30 is what the app
# already reads (a surface model: roofs and canopy count as ground); GEDTM30 is a
# terrain model; the others are for comparison.
DEMTYPES = ("COP30", "GEDTM30", "NASADEM", "AW3D30", "SRTMGL1", "COP90")

# Same box as the street layer (app/config.py), south, north, west, east, plus
# a margin so the ponding window around a manhole at the edge is not cut off.
CHENNAI = (12.85, 13.25, 80.10, 80.35)
MARGIN_DEG = 0.02

# The API answers errors with XML and success with a GeoTIFF.
TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--demtype", default="COP30", choices=DEMTYPES)
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("SOUTH", "NORTH", "WEST", "EAST"),
                        help="area to fetch; default is Chennai plus a margin")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("OPENTOPOGRAPHY_API_KEY", "").strip()
    if not key or key == "your_API_key_here":
        fail("OPENTOPOGRAPHY_API_KEY is not set. Add it to .env (see .env.example).")

    south, north, west, east = (round(v, 4) for v in args.bbox or (
        CHENNAI[0] - MARGIN_DEG, CHENNAI[1] + MARGIN_DEG,
        CHENNAI[2] - MARGIN_DEG, CHENNAI[3] + MARGIN_DEG))
    if not (south < north and west < east and -90 <= south and north <= 90):
        fail("bbox must be SOUTH < NORTH and WEST < EAST, in degrees.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"{args.demtype}.tif"
    partial = target.with_suffix(".part")

    params = {"demtype": args.demtype, "south": south, "north": north,
              "west": west, "east": east, "outputFormat": "GTiff", "API_Key": key}
    print(f"Fetching {args.demtype} for S{south} N{north} W{west} E{east} ...")
    try:
        with httpx.stream("GET", API, params=params, timeout=180.0, follow_redirects=True) as response:
            if response.status_code == 401:
                fail("OpenTopography rejected the key (401). Check OPENTOPOGRAPHY_API_KEY.")
            if response.status_code == 204:
                fail(f"{args.demtype} has no data for that area (204).")
            if response.status_code != 200:
                body = response.read().decode("utf-8", "replace").strip()
                fail(f"HTTP {response.status_code}: {body[:300]}")
            with partial.open("wb") as out:
                for chunk in response.iter_bytes():
                    out.write(chunk)
    except httpx.HTTPError as err:
        # The message of an httpx error carries the request URL, and so the key.
        partial.unlink(missing_ok=True)
        fail(f"request failed ({type(err).__name__}); check the network and try again.")

    with partial.open("rb") as check:
        if check.read(4) not in TIFF_MAGIC:
            text = partial.read_text(encoding="utf-8", errors="replace").strip()[:300]
            partial.unlink(missing_ok=True)
            fail(f"the reply was not a GeoTIFF: {text}")
    partial.replace(target)
    print(f"Saved {target.relative_to(ROOT)} ({target.stat().st_size / 1e6:.1f} MB)")

    describe(target)


def describe(path: Path) -> None:
    """Say what came back, so a bad download shows up here and not as odd flood depths."""
    import tifffile

    with tifffile.TiffFile(str(path)) as tif:
        page = tif.pages[0]
        grid = page.asarray().astype("float64")
        tags = {tag.name: tag.value for tag in page.tags.values()}
    print(f"  grid      {grid.shape[1]} x {grid.shape[0]} px, {page.dtype}")
    for name in ("ModelPixelScaleTag", "ModelTiepointTag", "ModelTransformationTag"):
        if name in tags:
            print(f"  {name:22} {tags[name]}")
    nodata = tags.get("GDAL_NODATA")
    valid = grid[grid != float(nodata)] if nodata is not None else grid
    print(f"  nodata    {nodata!r}, {grid.size - valid.size} cells")
    print(f"  elevation {valid.min():.1f} to {valid.max():.1f} m, mean {valid.mean():.1f} m")


if __name__ == "__main__":
    main()
