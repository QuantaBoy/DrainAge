# Data sources and cross-checks

Every layer the site computes on, where it comes from, and what it was checked
against. Nothing here is generated or assumed: each row is real data from a named
source, and each was cross-checked against a second, independent one before use.

`Cross-Checked Data/` holds the datasets that were supplied and checked, including
`gcc_storm_water_drains (1).csv` (the drain survey the site runs on) and
`dem_points (1).csv` (the elevation points the DEM was checked against).

| Layer | Source used | Cross-checked against | Result |
|---|---|---|---|
| **Drainage network** | The GCC / SECON-JBA ward sheets' own drawn network, read by `scripts/digitise_wards.py`: 12,566 drains, 1,010 km on 92 sheets, each from manhole to manhole with the section and invert measured at its manholes (65 % of sections measured, 29 % carried along the drain from the nearest measured manhole; inverts surveyed or interpolated linearly along the drain). Outside the sheets, the GCC survey CSV (7,472 drains, 1,457 km) | Flow direction against the surveyors' own flow arrows (5,498 drains carry one); topology against the manholes' chain numbering; positions against the CSV | 96.8 % of manhole-to-manhole drains stay within one numbered chain. Where no arrow is drawn, the numbering (87 % agreement with arrows), continuation along the drain, then the surveyed fall set the direction. The CSV is a separate survey (inverts a median 0.63 m lower, 97.5 % square sections): where a sheet drain lies along a CSV drain the sheet's replaces it, and the CSV lends it condition, material and name |
| **Ward base-map survey** | 92 SECON-JBA sheets, surveyed Dec 2020, UTM 44N, levels above MSL: 7,951 manholes with drain top, invert, road-edge level, width and depth, read by OCR | Each manhole's map label against its table row; the sheets' UTM grid against their latitude graticule | All 92 georeferenced from their own grid lines to 0.1 m (16 sheets are stored rotated 270 degrees and are read in their displayed frame). 5,115 labels agree with their table row to the millimetre |
| **Ward and zone** | Title blocks of the 92 GCC / SECON-JBA base-map sheets, indexed in `app/data/ward_sheets.json` | The zone recorded on each surveyed drain | 196 wards across the two sources; the one disagreement is flagged in the drain popup |
| **Terrain (DEM)** | GEDTM30, 1 arc-second (about 30 m) bare-earth terrain, from OpenTopography | 1. Copernicus COP30 over the same box. 2. The supplied `Cross-Checked Data/dem_points (1).csv` | COP30 sits 1.00 m above GEDTM30 (median on land), the expected gap between a surface and a bare-earth model. The point file is ellipsoidal: it sits 92.1 m below the DEM, and the EGM2008 geoid at Chennai is -92.03 m, so it confirms the DEM's datum |
| **Terrain correction** | The DEM less its building clutter: DEM - surveyed road edge at 4,523 manholes fits 2.87 m + 3.18 m x OSM building cover; the cover term and each surveyed area's own departures are taken off, floored at the lowest ground within 300 m | Each ward left out and predicted from the others; railway station levels (Chennai Central 3.47 m, Egmore 8 m, Mambalam 13 m, Guindy 12 m); Marina beach | The manhole-to-manhole spread of DEM - road edge halves (IQR 2.93 m to 1.68 m). The constant 2.87 m is **not** applied: open ground, the beach and three of four stations do not show it, so it is more likely the sheets' levelling datum than the DEM, and it does not change where water runs |
| **Buildings** | OpenStreetMap footprints, 373,298, as each cell's built share (`scripts/fetch_buildings.py`) | The DEM error at surveyed manholes, which rises with it | Water stands only between buildings, and a cell face passes water only through the gaps |
| **Water bodies** | OpenStreetMap lakes, tanks, ponds and reservoirs, 917 ways | Kept by the pit filling, which removes closed hollows under 16 cells, and hollows over 1 m deep under 64, as DEM noise | Street points inside a mapped water body are not reported as flooded streets: the DEM there is the water surface, not a road |
| **Streets** | OpenStreetMap ways, 80,206 roads, cached in `app/data/chennai_streets.geojson` | OSM `bridge` tags, fetched separately | 1,011 bridges excluded from flooding: the DEM under a bridge is the river below it |
| **Waterways** | OpenStreetMap rivers, canals, nullahs and drains: 574 ways including the Adyar, Buckingham Canal, Otteri Nullah, Mambalam Canal | Depths measured on the DEM with and without them burned in | Burning them 2 m removes false canal "lakes": 5th Street, Kilpauk drops from 212 cm to 51 cm |
| **Basin channels** | Macro / micro drains, canals, rivers and surplus channels ([Chennai Basin Drainage Maps, OpenCity](https://data.opencity.in/dataset/chennai-basin-drainage-maps), `Cross-Checked Data/macro_drains.csv` and four more): 934 channels with names and river subbasins | OSM waterways and the GCC drain survey; stated length vs drawn geometry; our DEM catchments vs the official basins | 78 km inside the city on neither layer, now burned into the DEM. Lengths agree to 0.3 %. 96.0 % of catchment cells fall in the same official basin as the hollow they drain to |
| **Place search** | OpenStreetMap Nominatim, limited to the Chennai box, for the route form's From and To | Our own street layer, as the fallback when Nominatim finds nothing | Search results are offered, not assumed: the other matches stay on screen to pick from |
| **Rainfall** | Open-Meteo: 15-minute precipitation for the flood forecast, hourly for the rain-movement map | The two feeds summed to the same hours | They agree hour for hour, as they should from one provider. The check also caught a clock bug, where the 15-minute feed's first slot can start behind the real time |
| **Weather tiles** | OpenWeather raster tiles (rain, temperature, wind, cloud) | Not a model input | Pictures only; a different provider, so they can differ from the Open-Meteo forecast |

## What the cross-checking buys

- **The elevation is on the right datum.** A 92 m datum error is the difference
  between a street 10 m above the sea and one 80 m below it. The point-file check and
  the EGM2008 geoid (-92.03 m at Chennai) agree.
- **The terrain is corrected by a survey, not by eye.** The ward sheets' road-edge
  levels showed GEDTM30 metres high in built-up streets, rising with building cover;
  that part is removed, validated on wards held out of the fit.
- **Every drain says where its size came from**, and a square section the survey
  cannot vouch for is marked unverified rather than trusted.
- **The DEM is conditioned on real mapped features**, not smoothed by eye: bridges and
  canals come from OpenStreetMap, and the settings were chosen by checking that real
  water bodies (Pallikaranai marsh, Porur lake, Puzhal lake) survive while building
  artefacts do not.
- **The rainfall driving the model is the same forecast shown on the map**, verified
  against the other feed from the same provider rather than assumed.

## What is not yet cross-checked

Stated plainly, because the site is meant for real use:

- **Checked against the flood record** (`scripts/validate_history.py`, results in
  `app/data/validation.json`). At 30 mm/h the model floods a street within 250 m of 82%
  of the 753 places that flooded in December 2015, against 42% for random street points;
  the margin (+42 points) holds on the corporation zones held out of the choice of
  settings. Streets in High and Very High hazard zones flood 7x as often as streets
  outside any zone. The settings in use (runoff 0.75, drains as surveyed) were as good
  as any other combination tried. Recorded depths match only loosely (rank correlation
  0.17-0.25, and the source does not state its unit): the reliable output is where and
  when streets flood, not exact centimetres; depths over about 1 m are usually DEM error.
  The 2015 floods were worsened by the Chembarambakkam release and the rivers
  overtopping, which this model (rain and drains) does not simulate.
- **Outside the 92 sheets, drain sizes are unverified**: the CSV's sections are square.
  798 drains with no size at all take the typical measured section (0.88 x 0.70 m).
- **4,045 drains end where no mapped canal or river takes over** (blind ends). Their water
  is put on the ground there, not made to vanish; treating them as free outfalls instead
  gives about 30 % fewer impassable streets at 30 mm/h. A layer of the small channels and
  culverts they discharge into would settle it.
- **A 2.87 m constant between the sheets' levels and the DEM is unexplained**: the
  sheets' datum or the DEM's, to be settled from GCC's benchmark records.
- **Sheets 63 and 141** could not be georeferenced.
- **Ward boundaries are inferred**, by the nearest surveyed drain within 400 m. GCC
  ward boundary polygons would make that exact.
