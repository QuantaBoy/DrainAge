"""Building footprints from OpenStreetMap, as the share of each DEM cell they cover.

    python fetch_buildings.py        # -> app/data/building_fraction.npz
    python fetch_buildings.py water  # -> app/data/water_bodies.npz

Water bodies (lakes, tanks, ponds, reservoirs) are fetched the same way: the DEM
cleaning in app/services/rain_ponding.py fills small deep pits as noise, and these are
the small deep hollows that are real and must be kept.

Water cannot stand inside a building, so the flood model gives each 30 m cell only the
area between buildings to hold water in (app/services/coupled.py): a street between two
rows of houses fills faster and deeper than open ground. This is the building-porosity
treatment of urban flood models, fed by the footprints OSM has.

The box is fetched from Overpass in tiles, cached raw under .osm_buildings/, drawn onto
a canvas SUPERSAMPLE times finer than the 1 arc-second grid and averaged back down.
ponytail: OSM misses buildings in places, so coverage is a lower bound there; relations
(multipolygon buildings) are left out, a few percent of footprints.
"""

import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".osm_buildings"
OUT = ROOT / "app" / "data" / "building_fraction.npz"
WATER_OUT = ROOT / "app" / "data" / "water_bodies.npz"
QUERIES = {
    "building": 'way["building"]',
    # Standing water in all the ways OSM tags it.
    "water": 'way["natural"="water"];way["landuse"~"^(reservoir|basin)$"];way["water"]',
}
# The main Overpass server is often saturated; the public mirrors serve the same data.
OVERPASS = ("https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
USER_AGENT = "SIH26085-flood-dashboard/1.0"

# The flood model's box and grid (app/routes/streets.CHENNAI_BBOX, 1 arc-second DEM).
SOUTH, WEST, NORTH, EAST = 12.85, 80.10, 13.25, 80.35
STEP = 1 / 3600
TILE_DEG = 0.05
SUPERSAMPLE = 4


def fetch_tile(south: float, west: float, kind: str = "building") -> list:
    target = CACHE / f"{kind}_{south:.2f}_{west:.2f}.json"
    legacy = CACHE / f"{south:.2f}_{west:.2f}.json"
    if kind == "building" and legacy.exists():
        return json.loads(legacy.read_text(encoding="utf-8"))
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    box = f"({south},{west},{south + TILE_DEG},{west + TILE_DEG})"
    parts = "".join(f"{q}{box};" for q in QUERIES[kind].split(";"))
    query = f"[out:json][timeout:180];({parts});out geom;"
    for attempt in range(8):
        server = OVERPASS[attempt % len(OVERPASS)]
        try:
            response = httpx.post(server, data={"data": query}, timeout=240,
                                  headers={"User-Agent": USER_AGENT})
            if response.status_code == 200:
                ways = [[(p["lon"], p["lat"]) for p in e["geometry"]]
                        for e in response.json()["elements"] if len(e.get("geometry", [])) >= 3]
                target.write_text(json.dumps(ways), encoding="utf-8")
                return ways
            print(f"  HTTP {response.status_code}, retrying", flush=True)
        except httpx.HTTPError as exc:
            print(f"  {type(exc).__name__}, retrying", flush=True)
        time.sleep(10 * (attempt + 1))
    sys.exit(f"Overpass would not serve tile {south:.2f},{west:.2f}")


def main(kind: str = "building") -> None:
    CACHE.mkdir(exist_ok=True)
    rows = int(round((NORTH - SOUTH) / STEP))
    cols = int(round((EAST - WEST) / STEP))
    scale = SUPERSAMPLE / STEP
    canvas = Image.new("L", (cols * SUPERSAMPLE, rows * SUPERSAMPLE), 0)
    draw = ImageDraw.Draw(canvas)
    total = 0
    for south in np.arange(SOUTH, NORTH - 1e-9, TILE_DEG):
        for west in np.arange(WEST, EAST - 1e-9, TILE_DEG):
            ways = fetch_tile(round(float(south), 2), round(float(west), 2), kind)
            for ring in ways:
                draw.polygon([((lon - WEST) * scale, (NORTH - lat) * scale) for lon, lat in ring], fill=255)
            total += len(ways)
            print(f"tile {south:.2f},{west:.2f}: {len(ways)} {kind} ways", flush=True)
            time.sleep(2)
    fine = np.asarray(canvas, dtype=np.float32) / 255.0
    fraction = fine.reshape(rows, SUPERSAMPLE, cols, SUPERSAMPLE).mean(axis=(1, 3))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if kind == "water":
        np.savez_compressed(WATER_OUT, fraction=np.round(fraction * 250).astype(np.uint8),
                            north=NORTH, west=WEST, step=STEP, ways=total)
        print(f"{total} water ways; {(fraction > 0.5).sum()} cells mostly water; wrote {WATER_OUT.relative_to(ROOT)}")
        return
    np.savez_compressed(OUT, fraction=np.round(fraction * 250).astype(np.uint8),
                        north=NORTH, west=WEST, step=STEP, buildings=total)
    print(f"{total} buildings; mean cover {fraction.mean():.3f}, cells over half built "
          f"{(fraction > 0.5).mean():.3f}; wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "building")
