# Cross-Checked Data

The datasets we cross-checked our sources against, kept here as they were supplied.

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

## Basin channel files — extend the drainage network

`macro_drains.csv`, `micro_drains.csv`, `buckingham_canal.csv`,
`krishna_water_canal.csv`, `rivers_streams.csv` — from
[Chennai Basin Drainage Maps, OpenCity](https://data.opencity.in/dataset/chennai-basin-drainage-maps),
the basin model's channel layer:
934 rows, 1,030 lines once multi-part channels are split (27 macro drain, 39 micro
drain, 946 supply / surplus channel and 18 river lines), each with an official name, its river
subbasin (Kosasthalayar, Cooum, Adyar, Kovalam, Nandhiyar, Nagariyar) and whether the
official model routes it hydraulically.

We cross-checked them against the two drainage layers the site already had: the
**OpenStreetMap waterways** and the **GCC storm water drain survey**.

| File | km inside Chennai | Already on OSM | Already in GCC survey | **New** |
|---|---|---|---|---|
| macro_drains | 39.3 | 29.1 | 0.1 | **10.1** |
| micro_drains | 78.8 | 66.9 | 4.1 | **7.8** |
| buckingham_canal | 48.2 | 47.7 | 0.0 | **0.5** |
| krishna_water_canal | 0.0 | — | — | — |
| rivers_streams | 215.7 | 152.5 | 3.5 | **59.6** |

**Result:** 78 km of channel inside the city was on neither layer — Okkium Madavu,
Meenambakkam and airport drains, the Ambattur Surplus – Otteri Nullah link, Adyar
Creek. The drainage network is extended with them: they are burned into the DEM with
the OSM waterways, so the terrain no longer dams them.

Also checked:

- **Stated length vs drawn geometry** agree to 0.3 % (median): the geometry is sound.
- **Our DEM catchments vs the official basins:** 96.0 % of sampled catchment cells lie
  in the same official basin as the hollow they drain to. Most of the rest sit on the
  Adyar / Kovalam divide, where the files themselves list channels as shared.

The files carry no cross-sections, so they place and name the network but cannot size
it. Every flooded street is now reported with its river basin and nearest named channel.

---

Full source list and results: [`../DATA_SOURCES.md`](../DATA_SOURCES.md)
