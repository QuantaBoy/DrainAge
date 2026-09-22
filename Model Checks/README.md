# Model Checks

Runs that set the rain-on-streets model's knobs. These are the model against itself,
not against official data: comparisons with official sources live in
`Cross-Checked Data/`.

```
python "Model Checks/model_checks.py"
```

Needs the DEMs from `fetch_dem.py` and the street and waterway caches in `app/data/`.

| File | Check |
|---|---|
| `dem_smoothing_check.csv` | How much the DEM is smoothed before hollows are filled |
| `waterway_burning_check.csv` | How deep canals are burned into the DEM |
| `rain_feeds_15min_vs_hourly.csv` | Whether the two Open-Meteo rain feeds agree |

## DEM smoothing

At 30 m, GEDTM30 in a dense block such as T. Nagar jumps 3-5 m between neighbouring
cells where buildings bleed through, and filling each of those pits to its rim put
metres of "water" on real streets. A median filter removes them, but too wide a filter
erases real lakes too. Run at 100 mm of rain with the drains full:

| Median filter | Hollows | Venkatanarayana Road | Pallikaranai marsh | Porur lake |
|---|---|---|---|---|
| none | 28,281 | 371 cm | 1,370 cm | 490 cm |
| **3 × 3 (90 m)** | **9,309** | **30 cm** | **1,126 cm** | **301 cm** |
| 5 × 5 | 5,031 | 0 cm | 414 cm | 50 cm |
| 7 × 7 | 3,275 | 0 cm | 20 cm | 6 cm |

**Set to 3 × 3** (`SMOOTH_CELLS` in `app/services/rain_ponding.py`): it cuts the
T. Nagar spike twelvefold and keeps Pallikaranai and Porur, which are real water
bodies filling their own basins. At 5 × 5 Porur lake starts to disappear.

## Burning waterways into the DEM

At 30 m a road embankment or culvert across a canal reads as a dam, so the canal
upstream became a closed lake that flooded the streets beside it. The 574 rivers,
canals and nullahs mapped in OpenStreetMap are lowered into the DEM so they drain.
Run at 60 mm with the drains taking 20 mm/h:

| Burn depth | Hollows | 5th Street, Kilpauk (beside the Otteri Nullah) |
|---|---|---|
| none | 8,828 | 212 cm |
| **2 m** | **9,309** | **51 cm** |
| 4 m | 9,447 | 0 cm |

**Set to 2 m** (`BURN_M`). Depths are still measured against the unburned ground, so a
road on a canal bank is never measured from the channel bed. Bridges are handled the
same way: the 1,011 OpenStreetMap ways tagged `bridge` are left out of the street
network, because the DEM under a bridge is the river.

## Rain feeds

The "Rain movement" map (hourly) and the rain-on-streets forecast (15-minute) both
come from Open-Meteo, so they should agree up to rounding. The check sums the
15-minute values into hours and sets them beside the hourly values for central
Chennai; at the last run both were 0.0 mm in every hour. OpenWeather's "Rain" tile
layer is a different provider and can differ from both.

The same comparison found a clock bug: the 15-minute feed's first slot can start
behind the real time, and the page had been labelling "+5 min" from that stale slot
(10:35 shown at 11:18). The feed is now trimmed to start at the current 15 minutes.
