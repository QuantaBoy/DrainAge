"""Hourly rainfall over Chennai and the sea to its east, for training the rain nowcast
models (ml/train.py).

    python ml/fetch_rain.py

ERA5 reanalysis through the Open-Meteo archive (free, no key): a 7 x 7 grid at 0.25
degrees, September to December (the north-east monsoon, when Chennai floods),
2005-2025. Writes ml/rain_era5.npz: rain[time, row, col] in mm/h, rows north to south.
"""

import json
import time
import urllib.request
from pathlib import Path

import numpy as np

OUT = Path(__file__).with_name("rain_era5.npz")
LATS = np.round(np.arange(13.75, 12.24, -0.25), 2)      # north to south
LONS = np.round(np.arange(79.75, 81.26, 0.25), 2)       # west to east
YEARS = range(2005, 2026)


MONTHS = (("09-01", "09-30"), ("10-01", "10-31"), ("11-01", "11-30"), ("12-01", "12-31"))


def fetch_span(start: str, end: str) -> tuple[list[str], np.ndarray]:
    lat, lon = np.meshgrid(LATS, LONS, indexing="ij")
    url = ("https://archive-api.open-meteo.com/v1/archive?"
           f"latitude={','.join(map(str, lat.ravel()))}&longitude={','.join(map(str, lon.ravel()))}"
           f"&start_date={start}&end_date={end}"
           "&hourly=precipitation&models=era5&timezone=Asia%2FKolkata")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=300) as response:
                points = json.load(response)
            break
        except Exception as exc:                 # rate limit or a dropped connection
            print(f"  {start}: {exc}, retrying", flush=True)
            time.sleep(30 * (attempt + 1))
    else:
        raise SystemExit(f"could not fetch {start}")
    times = points[0]["hourly"]["time"]
    rain = np.array([[v if v is not None else np.nan for v in p["hourly"]["precipitation"]] for p in points])
    return times, rain.T.reshape(len(times), len(LATS), len(LONS))


def fetch(year: int) -> tuple[list[str], np.ndarray]:
    """One season, a month per request: a whole season for 49 points is too slow for one."""
    parts = [fetch_span(f"{year}-{a}", f"{year}-{b}") for a, b in MONTHS]
    return [t for times, _ in parts for t in times], np.concatenate([r for _, r in parts])


def main() -> None:
    all_times, all_rain = [], []
    for year in YEARS:
        times, rain = fetch(year)
        all_times += times
        all_rain.append(rain)
        print(f"{year}: {len(times)} hours, season total at Chennai {np.nansum(rain[:, 3, 2]):.0f} mm", flush=True)
        time.sleep(3)
    np.savez_compressed(OUT, time=np.array(all_times), rain=np.concatenate(all_rain).astype(np.float32),
                        lat=LATS, lon=LONS)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
