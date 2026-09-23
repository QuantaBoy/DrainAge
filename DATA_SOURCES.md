# Data sources and cross-checks

Every layer the site computes on, where it comes from, and what it was checked
against. Nothing here is generated or assumed: each row is real data from a named
source, and each was cross-checked against a second, independent one before use.

`Cross-Checked Data/` holds the datasets that were supplied and checked, including
`gcc_storm_water_drains (1).csv` (the drain survey the site runs on) and
`dem_points (1).csv` (the elevation points the DEM was checked against).

| Layer | Source used | Cross-checked against | Result |
|---|---|---|---|
| **Drainage network** | GCC storm water drain survey, `Cross-Checked Data/gcc_storm_water_drains (1).csv`: 10,240 drains, 2,219 km, with section, invert levels, ward and zone | GCC / SECON-JBA ward base maps (`Ward/*.pdf`), the sheets it was digitised from | 91 wards carry both; 90 agree. Ward 33's zone disagrees and ward 35 is missing from the survey: both reported, neither patched |
| **Ward and zone** | Title blocks of the 92 GCC / SECON-JBA base-map sheets, indexed in `app/data/ward_sheets.json` | The zone recorded on each surveyed drain | 196 wards across the two sources; the one disagreement is flagged in the drain popup |
| **Terrain (DEM)** | GEDTM30, 1 arc-second (about 30 m) bare-earth terrain, from OpenTopography | 1. Copernicus COP30 over the same box. 2. The supplied `Cross-Checked Data/dem_points (1).csv` | COP30 sits 1.00 m above GEDTM30 (median on land), the expected gap between a surface and a bare-earth model. The point file proved to be ellipsoidal, 92.1 m below sea-level heights, so it is **not** used as a source: it is what confirmed the DEM's datum |
| **Streets** | OpenStreetMap ways, 80,206 roads, cached in `app/data/chennai_streets.geojson` | OSM `bridge` tags, fetched separately | 1,011 bridges excluded from flooding: the DEM under a bridge is the river below it |
| **Waterways** | OpenStreetMap rivers, canals, nullahs and drains: 574 ways including the Adyar, Buckingham Canal, Otteri Nullah, Mambalam Canal | Depths measured on the DEM with and without them burned in | Burning them 2 m removes false canal "lakes": 5th Street, Kilpauk drops from 212 cm to 51 cm |
| **Basin channels** | Macro / micro drains, canals, rivers and surplus channels (`Cross-Checked Data/macro_drains.csv` and four more): 934 channels with names and river subbasins | OSM waterways and the GCC drain survey; stated length vs drawn geometry; our DEM catchments vs the official basins | 78 km inside the city on neither layer, now burned into the DEM. Lengths agree to 0.3 %. 96.0 % of catchment cells fall in the same official basin as the hollow they drain to |
| **Rainfall** | Open-Meteo: 15-minute precipitation for the flood forecast, hourly for the rain-movement map | The two feeds summed to the same hours | They agree hour for hour, as they should from one provider. The check also caught a clock bug, where the 15-minute feed's first slot can start behind the real time |
| **Weather tiles** | OpenWeather raster tiles (rain, temperature, wind, cloud) | Not a model input | Pictures only; a different provider, so they can differ from the Open-Meteo forecast |

## What the cross-checking buys

- **The elevation is on the right datum.** A 92 m datum error is the difference
  between a street 10 m above the sea and one 80 m below it. The point-file check is
  what caught it, and every depth the site reports is above mean sea level because of it.
- **The drain data is traceable to the sheets it came from.** Every drain links back
  to the GCC base map it was digitised from, and where the two disagree the site says so
  instead of quietly choosing.
- **The DEM is conditioned on real mapped features**, not smoothed by eye: bridges and
  canals come from OpenStreetMap, and the settings were chosen by checking that real
  water bodies (Pallikaranai marsh, Porur lake, Puzhal lake) survive while building
  artefacts do not.
- **The rainfall driving the model is the same forecast shown on the map**, verified
  against the other feed from the same provider rather than assumed.

## What is not yet cross-checked

Stated plainly, because the site is meant for real use:

- **No flood depth or timing has been compared with a recorded flood.** Until a real
  event is used (GCC waterlogging reports, Cyclone Michaung in December 2023,
  December 2015), the reliable output is the order and timing in which streets flood,
  not exact centimetres. Depths over about 1 m are usually DEM error.
- **Some surveyed drain sizes look wrong.** Amman Koil Street RHS reaches 207 % of
  capacity at 0.3 mm/h, which its recorded section cannot be right for.
- **Ward boundaries are inferred**, by the nearest surveyed drain within 400 m. GCC
  ward boundary polygons would make that exact.
