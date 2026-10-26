"""Run the flood engine once and save its heaviest answers as files (app/snapshot.py),
for GitHub Pages. Run by .github/workflows/snapshot.yml every half hour.

    python scripts/snapshot.py --out site
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import snapshot  # noqa: E402
from app.main import app  # noqa: E402


def write_route_depths(out: Path) -> None:
    """The road depths the route search reads, for every offered storm, drain condition
    and departure (app/snapshot.route_file)."""
    import asyncio

    import numpy as np

    from app import config
    from app.services import forecast, hydraulics, navigation, rainfall

    lat, lon = config.CHENNAI_CENTRE
    for rate in (None, *snapshot.PRESET_RATES_MM_H):
        rain = asyncio.run(rainfall.report_steps(round(lat, 4), round(lon, 4), rate))
        for drain in snapshot.DRAIN_LEVELS:
            for leave in snapshot.LEAVE_MIN:
                first, last = navigation.trip_steps(leave, rain)
                depth = forecast.road_depths_cm(rain, hydraulics.RUNOFF_COEFF, drain, first, last)
                target = out / snapshot.route_file(rate, drain, leave)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(np.clip(np.rint(depth), 0, 255).astype(np.uint8).tobytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    out = parser.parse_args().out

    jobs = {name: path for path, name in snapshot.FILES.items()}
    for rate in (None, *snapshot.PRESET_RATES_MM_H):
        for drain in snapshot.DRAIN_LEVELS:
            query = f"?drain_condition={drain}" + (f"&rain_mm_h={rate}" if rate else "")
            jobs[snapshot.forecast_file(rate, drain)] = snapshot.FORECAST_PATH + query

    # The ward base maps (215 MB of PDFs): too big for Vercel, so they are served here.
    import shutil

    from app.services import wards
    for number, path in wards.sheet_files().items():
        target = out / snapshot.ward_pdf(number)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    began = time.perf_counter()
    write_route_depths(out)
    print(f"route depths in {time.perf_counter() - began:.0f} s", flush=True)

    client = TestClient(app)          # no lifespan: nothing is computed ahead
    failed = []
    for name, url in jobs.items():
        began = time.perf_counter()
        response = client.get(url)
        if response.status_code != 200:
            print(f"FAILED {url}: {response.status_code} {response.text[:200]}", flush=True)
            failed.append(url)
            continue
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        print(f"{name}: {len(response.content) / 2**20:.1f} MB in {time.perf_counter() - began:.0f} s", flush=True)
    # Someone opening the data address directly gets pointed at the website.
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Flood forecast data</title>'
        '<p>Data files for the Chennai street-level flood forecast, updated every 30 minutes. '
        'The website: <a href="https://street-level-flood-forecasting.vercel.app">'
        'street-level-flood-forecasting.vercel.app</a></p>', encoding="utf-8")
    # A failed run publishes nothing, so the site keeps the last good answers.
    if failed:
        sys.exit(f"{len(failed)} of {len(jobs)} answers failed")


if __name__ == "__main__":
    main()
