# Storm Water Drains Interactive Map for Chennai

Map all **10,257 GCC storm water drains** from the CSV onto the Leaflet map with interactive filtering by Zone, Ward, Drain Type, and Status. Selecting a drain highlights it and shows its full details in a popup.

## Proposed Changes

### Backend — New API Route

#### [NEW] [`drains.py`](file:///c:/Users/quant/SIH26085/app/routes/drains.py)

- FastAPI route `GET /data-collection/drains` that reads the CSV, parses the `wkt_geometry` column into GeoJSON `LineString` features, and returns a `FeatureCollection`.
- Each feature's `properties` will carry: `feature_no`, `DRAIN_DETL` (Open/Closed), `DRAIN_TYPE` (Side Drain / SWD), `DRAIN_SIZE`, `DRAIN_DEP`, `DRAIN_WID`, `DRAIN_LEN`, `STATUS`, `ST_NAME`, `LOCATION`, `WARD`, `ZONE`, `WATER_FLOW`, `SWD_MAT`, `COVER`, `OBSTACLES`, `PUCA_KACHA`, `computed_length_m`.
- Supports optional query params `?zone=N07&ward=N082&drain_type=Side Drain&status=Good` to filter server-side and keep payload small.
- Result cached in memory after first CSV parse.

---

### Backend — Register the Route

#### [MODIFY] [`main.py`](file:///c:/Users/quant/SIH26085/app/main.py)

- Import and include `drains.router`.

---

### Frontend — Drain Layer on the Map

#### [NEW] [`drains.js`](file:///c:/Users/quant/SIH26085/app/static/js/drains.js)

- Adds a **"Storm Water Drains"** overlay to the existing Leaflet layer control.
- On toggle, shows a **filter panel** (Leaflet Control, bottom-right) with dropdowns for:
  - **Zone** (N01–N15)
  - **Ward** (populated dynamically based on selected zone)
  - **Drain Type** (Side Drain, SWD, Open Drain, Closed)
  - **Status** (Good, Bad)
- Fetches filtered data from `/data-collection/drains?zone=...&ward=...` and renders drains as colored polylines:
  - **Open drains** → cyan/teal dashed line
  - **Closed drains** → solid blue line
  - **SWD (Storm Water Drain)** → thicker solid purple line
- Clicking a drain shows a **rich popup** with all drain details (street name, dimensions, material, status, length, obstacles, etc.)
- Color-codes drain status: Good = green tint, Bad = red tint.
- A **legend** in the filter panel explains the color/style coding.
- Uses Leaflet Canvas renderer for performance (10k+ features).

---

### Frontend — Styles

#### [MODIFY] [`home.css`](file:///c:/Users/quant/SIH26085/app/static/css/home.css)

- Add styles for the drain filter panel, drain legend, and drain popups (glassmorphism card styling, color-coded badges for status/type).

---

### Frontend — Load the Script

#### [MODIFY] [`home.html`](file:///c:/Users/quant/SIH26085/app/templates/home.html)

- Add `<script src="drains.js">` after the existing scripts.

---

## Verification Plan

### Manual Verification
1. Run `python -m app.main` and open the dashboard.
2. Toggle the **"Storm Water Drains"** overlay → map should zoom into Chennai.
3. Select Zone **N07** → only drains in Zone N07 appear.
4. Select Ward **N082** → further filters to that ward.
5. Click a drain line → popup shows full drain metadata.
6. Verify open/closed/SWD drains have distinct visual styles.
7. Confirm other existing overlays (Rain, Streets, Water Flow) still work alongside drains.
