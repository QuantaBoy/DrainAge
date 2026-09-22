# Cross-Checked Data

Data checked against official sources, ward by ward and point by point, with the
result of each check kept here as evidence. Where two official sources disagree, the
site shows the surveyed value and flags the disagreement; it never silently corrects
either one.

Model tuning runs (DEM smoothing, waterway burning, rain feeds) are **not** here;
they are in `Model Checks/`.

| File | What it is |
|---|---|
| `ward_cross_check.py` | Re-runs the GCC ward check |
| `gcc_wards_vs_survey.csv` | Every ward: what the base-map sheet says, what the survey says |
| `data_cross_check.py` | Re-runs the elevation checks |
| `dem_points.csv` | The elevation point file supplied, kept exactly as received |
| `dem_points_vs_dem.csv` | That file against both DEMs, point by point |
| `dem_sources_gedtm30_vs_cop30.csv` | The two DEMs against each other |

```
python "Cross-Checked Data/ward_cross_check.py"
python "Cross-Checked Data/data_cross_check.py"
```

## 1. GCC ward base maps vs the drain survey

Two official sources for the same 196 wards:

- **`Ward/*.pdf`** - the storm water drain base-map sheets produced for the Greater
  Chennai Corporation under the Real Time Flood Forecasting SDSS project (SECON-JBA,
  funded through TNUIFSL). These are the paper source the survey was digitised from.
  Each sheet is a raster page with a text title block and no vector geometry, so what
  is read from it is the ward and zone printed there, indexed in
  `app/data/ward_sheets.json`.
- **`gcc_storm_water_drains (1).csv`** - the digitised drain survey the site maps,
  sizes hydraulically and floods.

| Result | Wards |
|---|---|
| Both sources present and agreeing | 90 |
| **Zone disagrees** | **1** |
| Sheet here, ward absent from the survey | 1 |
| In the survey, no sheet in this repository | 104 |
| **Total wards known to either source** | **196** |

Wards in the survey: 195. Base-map sheets indexed: 92, and all 92 PDFs are in `Ward/`.
91 wards have both.

**The two disagreements, both carried through to the site rather than patched:**

- **Ward 33** - the sheet's title block puts it in Zone III (Madhavaram, `N03`); the
  survey rows put it in `N04`. 35 drains are affected. The drain popup shows the
  surveyed zone and flags the disagreement.
- **Ward 35** - a sheet exists (Zone IV, Tondiarpet) but the survey holds no drains
  for it: a gap in the digitisation, not a ward without drains.

The 104 wards with no sheet are wards whose sheets are not in this repository; the
survey still covers them, and the site reports them with no base map to link.

## 2. The supplied elevation points vs the DEMs: rejected

`dem_points.csv`, 1,644 points 0.02° apart (about 2.2 km), 12.0-14.0° N, 80.0-80.54° E.

- **Wrong vertical datum.** Over the 238 land points inside the app's Chennai box the
  file sits a steady **92.1 m below GEDTM30** (median; standard deviation 5.0 m). That
  offset is the geoid height at Chennai, so these are heights above the WGS 84
  ellipsoid, not above sea level. At 13.26° N, 80.10° E the file reads −72.5 m where
  GEDTM30 reads 17.2 m.
- **Noise over the sea**, from −399.6 to +1107.0 m east of about 80.3° E.
- **Too coarse** at 2.2 km spacing to tell one street from the next.

**Not used.** The app reads GEDTM30 at 1 arc-second (about 30 m) above mean sea level.

## 3. GEDTM30 vs COP30

Both from OpenTopography, compared over the app's Chennai box on land (COP30 flattens
the sea to exactly 0 m, so those cells are excluded):

| Measure | Value |
|---|---|
| Median COP30 − GEDTM30 | 1.00 m |
| 5th / 95th percentile | −0.27 m / 3.65 m |

COP30 is a surface model (roofs and canopy included), GEDTM30 is bare earth, so COP30
reading about a metre higher, and up to about 4 m higher in built-up blocks, is the
expected difference. **GEDTM30 is the default** (`DEM_SOURCE` in `.env` overrides it).

## Still to be cross-checked

- **Flood depths and timings against a real event.** No model run has yet been
  compared with streets recorded under water in a real flood (GCC waterlogging
  reports, Cyclone Michaung in December 2023, December 2015). Until that is done the
  order in which streets flood is the trustworthy output, not the exact centimetres.
- **Drain sizes that look wrong in the survey.** Amman Koil Street RHS, for one,
  reaches 207 % of capacity at 0.3 mm/h, which its surveyed section cannot be right for.
- **Ward boundaries.** The site places a street in a ward by the nearest surveyed
  drain, within 400 m. GCC ward boundary polygons would replace that guess.
