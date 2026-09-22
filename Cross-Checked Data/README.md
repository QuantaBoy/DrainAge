# Cross-Checked Data

The two datasets we cross-checked our sources against, kept here as they were supplied.

## `dem_points (1).csv` — checks the elevation

1,644 elevation points over Chennai. We used them to cross-check the DEM we download
from the **OpenTopography API** (GEDTM30, 30 m bare earth, fetched by `fetch_dem.py`).

**Result:** the points read a steady **92.1 m lower** than the OpenTopography DEM on
land. That gap is the geoid height at Chennai, so these points are heights above the
WGS 84 ellipsoid, while the DEM is above mean sea level. The check confirmed our DEM is
on the right datum, so every depth the site reports is above sea level. We also compared
GEDTM30 against Copernicus COP30 from the same API: they agree to 1.00 m on land.

## `gcc_storm_water_drains (1).csv` — the drainage network

The GCC storm water drain survey: 10,240 drains, 2,219 km, with section, invert levels,
ward and zone. This is the network the site maps, sizes and floods.

We cross-checked it against the **ward dataset** — the GCC / SECON-JBA ward base-map
sheets in `Ward/`, indexed in `app/data/ward_sheets.json`, which are the sheets this
survey was digitised from.

**Result:** 196 wards across both sources, 91 with both, and 90 of those agree.
Two disagreements, which the site reports rather than quietly fixing:

- **Ward 33** — the sheet says Zone III (Madhavaram), the survey says N04, over 35 drains.
- **Ward 35** — a sheet exists, but the survey has no drains for it.

---

Full source list and results: [`../DATA_SOURCES.md`](../DATA_SOURCES.md)
