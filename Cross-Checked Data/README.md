# Cross-Checked Data

The data checks behind the DEM integration and the rain-on-streets model: what was
compared, against what, and what was decided because of it.

Every CSV here except `dem_points.csv` is produced by `cross_check.py`. Re-run it after
changing a DEM, a model setting or a data file:

```
python "Cross-Checked Data/cross_check.py"
```

It needs the DEMs from `fetch_dem.py` (`app/data/dem/`) and the street and waterway
caches the app builds in `app/data/`. Check 5 calls Open-Meteo, so it records the
forecast at the moment it runs.

| File | Check |
|---|---|
| `dem_points.csv` | The point file that was supplied, kept as received |
| `dem_points_vs_dem.csv` | 1. That file against the DEMs the app reads |
| `dem_sources_gedtm30_vs_cop30.csv` | 2. The two DEMs against each other |
| `dem_smoothing_check.csv` | 3. How much DEM smoothing the rain model uses |
| `waterway_burning_check.csv` | 4. How deep canals are burned into the DEM |
| `rain_feeds_15min_vs_hourly.csv` | 5. The two Open-Meteo rain feeds against each other |

## 1. `dem_points.csv`: rejected

1,644 points, 0.02° apart (about 2.2 km), 12.0-14.0° N, 80.0-80.54° E.

- **Wrong vertical datum.** On the 238 land points inside the app's DEM, the file is a
  steady **92.1 m below GEDTM30** (median, standard deviation 5.0 m). That offset is
  the geoid height at Chennai, so these are heights above the WGS 84 ellipsoid, not
  above sea level. Example: 13.26° N, 80.10° E reads −72.5 m in the file and 17.2 m
  in GEDTM30.
- **Noise over the sea.** East of about 80.3° E it runs from −399.6 to +1107.0 m.
- **Too coarse.** One point per 2.2 km cannot separate one street from the next.

**Decision:** not used. The app reads GEDTM30 at 1 arc-second (about 30 m), in
metres above mean sea level.

## 2. GEDTM30 against COP30

Over the Chennai box, on land (COP30 sets the sea to exactly 0 m, so those cells are
left out):

| Measure | Value |
|---|---|
| Median COP30 − GEDTM30 | 1.00 m |
| 5th / 95th percentile | −0.27 m / 3.65 m |

COP30 is a surface model (roofs and trees included); GEDTM30 is bare earth. COP30
reading about 1 m higher, and up to about 4 m higher in built-up areas, is what that
difference should look like, and the two agree otherwise.

**Decision:** GEDTM30 is the default (`DEM_SOURCE` in `.env` can change it).

## 3. DEM smoothing

At 30 m, GEDTM30 in dense blocks such as T. Nagar jumps 3-5 m between neighbouring
cells where buildings bleed through. Filling every such pit to its rim put metres of
"water" on streets. A median filter removes the pits, but too wide a filter also
erases real lakes. Test: 100 mm of rain, drains full, waterways burned 2 m.

| Median filter | Hollows | Venkatanarayana Road | Pallikaranai marsh | Porur lake |
|---|---|---|---|---|
| none | 28,281 | 371 cm | 1,370 cm | 490 cm |
| **3 × 3 (90 m)** | **9,309** | **30 cm** | **1,126 cm** | **301 cm** |
| 5 × 5 | 5,031 | 0 cm | 414 cm | 50 cm |
| 7 × 7 | 3,275 | 0 cm | 20 cm | 6 cm |

**Decision:** 3 × 3 (`SMOOTH_CELLS = 3` in `app/services/rain_ponding.py`). It cuts
the T. Nagar spike by a factor of 12 and keeps Pallikaranai and Porur. At 5 × 5, Porur
lake starts to vanish. The marsh and lake depths are their own basins filling, not
street water.

## 4. Burning waterways into the DEM

Road embankments and culverts across a canal read as dams at 30 m, so the canal
upstream of each one became a closed "lake" and flooded the streets beside it. The
574 rivers, canals and nullahs in OpenStreetMap are lowered into the DEM so they
drain. Test: 60 mm of rain, drains taking 20 mm/h.

| Burn depth | Hollows | 5th Street, Kilpauk (by Otteri Nullah) |
|---|---|---|
| none | 8,828 | 212 cm |
| **2 m** | **9,309** | **51 cm** |
| 4 m | 9,447 | 0 cm |

**Decision:** 2 m (`BURN_M = 2.0`). It removes most of the false canal lake; 4 m adds
little elsewhere. Depths are still measured against the unburned ground, so a road
along a canal bank is never measured from the channel bed. Bridges (1,011 OSM ways
tagged `bridge`) are left out of the street network for the same reason: the DEM
under a bridge is the river.

## 5. Rain feeds

The "Rain movement" map (hourly forecast) and the rain-on-streets forecast (15-minute
nowcast) both come from Open-Meteo. The check sums the 15-minute values into hours
and sets them next to the hourly values for central Chennai.

At the last run the two agreed hour for hour (both 0.0 mm). Because both come from
the same provider, they should always agree up to rounding. OpenWeather's "Rain" tile
layer is a different provider and can differ from both.

The same work found and fixed a clock bug: the 15-minute feed's first slot can start
behind the real time, and the page had been labelling "+5 min" off that stale slot
(10:35 shown at 11:18). The feed is now cut to start at the current 15 minutes.

## Not yet checked

- **Depths and timings against a real flood.** No run has been compared with streets
  recorded under water in a real event (GCC waterlogging reports, Cyclone Michaung in
  December 2023, December 2015). Until then, the order in which streets flood is
  the reliable output; single depths over about 1 m are usually DEM error.
- **Drain survey sizes.** Some surveyed drains look far too small for their
  catchment. For example, Amman Koil Street RHS shows 207 % of capacity at 0.3 mm/h.
