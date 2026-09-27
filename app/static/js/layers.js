// Weather overlays on the shared map, and the animated rain-movement forecast.

// --- Current-conditions overlays ------------------------------------------------
// Tiles come through our own proxy, never straight from OpenWeather, so the API key
// is not exposed to the browser.

const WEATHER_LAYERS = [
    ["Rain", "rain"],
    ["Temperature", "temp"],
    ["Wind", "wind"],
    ["Clouds", "clouds"],
];

const weatherOverlays = {};
for (const [label, layer] of WEATHER_LAYERS) {
    weatherOverlays[label] = L.tileLayer(`/data-collection/tiles/${layer}/{z}/{x}/{y}.png`, {
        opacity: 0.6,
        // OpenWeather has no tiles past zoom 12; scaling those up keeps the overlays
        // usable when the map zooms in on a location, instead of Leaflet disabling them.
        maxNativeZoom: 12,
        maxZoom: 19,
        attribution: "Weather &copy; OpenWeather",
    });
}

// The layer list sits on the map it changes: a Layers button top-right that opens the
// list under it. The model's own layers come first, the weather pictures after.
const LAYER_ORDER = ["Flood forecast", "Storm Water Drains", "Elevation", "Streets", "Rain",
                     "Temperature", "Wind", "Clouds"];
const layerRank = (name) => {
    const i = LAYER_ORDER.findIndex((prefix) => name.startsWith(prefix));
    return i < 0 ? LAYER_ORDER.length : i;
};
const layerControl = L.control.layers(null, weatherOverlays, {
    collapsed: false, position: "topright", sortLayers: true,
    sortFunction: (a, b, nameA, nameB) => layerRank(nameA) - layerRank(nameB),
});
const LayerButton = L.Control.extend({
    options: { position: "topright" },
    onAdd() {
        const button = L.DomUtil.create("button", "layer-button");
        button.type = "button";
        button.setAttribute("aria-expanded", "false");
        button.setAttribute("aria-controls", "layer-list");
        button.innerHTML = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">' +
            '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/></svg><span>Layers</span>';
        L.DomEvent.disableClickPropagation(button);
        L.DomEvent.on(button, "click", () => setLayersOpen(!document.body.classList.contains("layers-open")));
        return button;
    },
});
const layerButton = new LayerButton().addTo(map);
layerControl.addTo(map);
layerControl.getContainer().id = "layer-list";
layerControl.getContainer().classList.add("layer-list");
function setLayersOpen(open) {
    document.body.classList.toggle("layers-open", open);
    layerButton.getContainer().setAttribute("aria-expanded", String(open));
}
// Closes on a click on the map or Escape, like any menu.
map.on("click", () => setLayersOpen(false));
document.addEventListener("keydown", (event) => { if (event.key === "Escape") setLayersOpen(false); });

// --- Street network --------------------------------------------------------------
// Chennai's roads, fetched once and cached server-side; this is the geometry later
// flood-depth and routing features attach data to.

// Magenta stands apart from the base map's own orange/yellow/white roads, so the
// layer is visibly ours; main roads are drawn heavier than side streets.
const STREET_WEIGHTS = { motorway: 5, trunk: 5, primary: 4, secondary: 3, tertiary: 2.5 };
// 80k lines as SVG elements makes panning sluggish; one canvas stays responsive.
const streetRenderer = L.canvas({ padding: 0.5 });
const STREET_ZOOM = 15;
const CHENNAI_CENTRE = [13.0827, 80.2707];
// Matches the area the server maps (streets.CHENNAI_BBOX).
const CHENNAI_BOUNDS = L.latLngBounds([12.85, 80.10], [13.25, 80.35]);

// Zoomed out, a Chennai-wide layer merges into one blob, so ticking one zooms in: on
// the selected place if it is in Chennai, else wherever the map already is in
// Chennai, else the city centre. Never zooms out.
function zoomIntoChennai(zoom) {
    if (map.getZoom() >= zoom) return;
    const target = [marker && marker.getLatLng(), map.getCenter()]
        .find((point) => point && CHENNAI_BOUNDS.contains(point)) || CHENNAI_CENTRE;
    map.setView(target, zoom);
}

fetch("/data-collection/streets")
    .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    })
    .then((geojson) => {
        const streets = L.geoJSON(geojson, {
            renderer: streetRenderer,
            style: (feature) => ({
                color: "#d81b60",
                weight: STREET_WEIGHTS[feature.properties.highway.replace("_link", "")] || 1.5,
                opacity: 0.85,
            }),
            onEachFeature: (feature, layer) => {
                if (feature.properties.name) layer.bindTooltip(feature.properties.name);
            },
        });
        layerControl.addOverlay(streets, "Streets (Chennai)");
        map.on("overlayadd", (event) => {
            if (event.layer === streets) zoomIntoChennai(STREET_ZOOM);
        });
    })
    .catch((err) => console.warn("Street network unavailable:", err.message));

// --- The rain the models run on -------------------------------------------------
// One rate, mm/h, drives the drain loads and anything else drawn from the rainfall.
// Several controls can set it (the live feed, the forecast sliders, a flood scenario);
// whichever did last is recorded with it, so the page can always say what it is
// showing instead of silently mixing sources.

window.rainSource = { mmPerHour: 0, label: "waiting for the live nowcast", kind: "none" };
window.rainNowMmH = 0;

// kind: "live" (the nowcast, now), "forecast" (a future hour someone picked),
// "scenario" (a hypothetical rate).
function publishRain(mmPerHour, label, kind) {
    if (typeof mmPerHour !== "number" || !isFinite(mmPerHour)) return;
    window.rainNowMmH = mmPerHour;
    window.rainSource = { mmPerHour, label, kind };
    window.dispatchEvent(new CustomEvent("rain-change", { detail: window.rainSource }));
}

const LIVE_RAIN_REFRESH_MS = 5 * 60 * 1000;

// The rain falling now over the drainage area: the selected place if it is in
// Chennai, else the city centre. The drains cover Chennai, so rain anywhere else is
// not theirs.
async function loadLiveRain() {
    const point = marker && CHENNAI_BOUNDS.contains(marker.getLatLng())
        ? marker.getLatLng() : L.latLng(CHENNAI_CENTRE);
    try {
        const resp = await fetch(`/data-collection/nowcast?lat=${point.lat.toFixed(4)}&lon=${point.lng.toFixed(4)}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const nowcast = await resp.json();
        // Someone chose a forecast hour or a scenario while this was in flight:
        // theirs stands until they go back to live.
        if (!["live", "none"].includes(window.rainSource.kind)) return;
        const slot = nowcast.times[0] ? nowcast.times[0].slice(11, 16) : "now";
        publishRain(nowcast.rain_mm_h[0] ?? 0, `Live nowcast, ${slot} slot (Open-Meteo 15-min)`, "live");
    } catch (err) {
        console.warn("Live rain unavailable:", err.message);
    }
}

function backToLiveRain() {
    window.rainSource = { ...window.rainSource, kind: "none" };
    loadLiveRain();
}

loadLiveRain();
// Real time: the nowcast moves on every 15 minutes; checking every 5 keeps the drains
// within one slot of it. Only while live, so it never overrides a chosen hour.
setInterval(() => {
    if (["live", "none"].includes(window.rainSource.kind)) loadLiveRain();
}, LIVE_RAIN_REFRESH_MS);

// --- Rain movement forecast -----------------------------------------------------
// Hourly rainfall on a grid around the selected place, animated so the direction and
// timing of approaching rain can be seen.

// Standard rain-rate bands in mm/h, heaviest first.
const RAIN_BANDS = [
    { min: 50, rgb: [106, 27, 154], label: "Violent" },
    { min: 7.6, rgb: [8, 69, 148], label: "Heavy" },
    { min: 2.5, rgb: [33, 113, 181], label: "Moderate" },
    { min: 0.2, rgb: [107, 174, 214], label: "Light" },
];
// Share of the canvas, from each edge, over which rain fades out. Without it the
// grid shows as a hard-edged box rather than weather.
const RAIN_EDGE_FADE = 0.15;
const RAIN_CANVAS_PX = 140;
const MS_PER_FORECAST_HOUR = 700;
// The grid is coarse, so redrawing faster than this adds CPU load and nothing visible.
const RAIN_REDRAW_MS = 66;
const GRID_HOURS = 24;
// A transparent pixel: an empty src would render as a broken image.
const BLANK_IMAGE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

const rainCanvas = document.createElement("canvas");
rainCanvas.width = RAIN_CANVAS_PX;
rainCanvas.height = RAIN_CANVAS_PX;
const rainContext = rainCanvas.getContext("2d");

const rain = {
    grid: null,
    overlay: null,
    bounds: null,
    frame: 0,             // fractional hour index into the grid
    playing: false,
    lastTick: null,
    lastDraw: 0,
    animationId: null,
    request: 0,
    query: null,
    driving: false,       // whether this grid is the rain the drains run on
    publishedHour: null,
};

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

const rainUi = (() => {
    const container = element("div", "rain-control leaflet-bar");
    container.hidden = true;

    const title = element("div", "rain-control-title", "Rain movement, next 24 h");
    const play = element("button", null, "▶ Play");
    play.type = "button";
    const scrubber = element("input");
    Object.assign(scrubber, { type: "range", min: 0, max: 0, value: 0, step: 1 });
    scrubber.setAttribute("aria-label", "Rain movement time");
    const time = element("span", "rain-time");
    const row = element("div", "rain-control-row");
    row.append(play, scrubber, time);

    const legend = element("div", "rain-legend");
    for (const band of [...RAIN_BANDS].reverse()) {
        const item = element("span");
        const swatch = element("i");
        swatch.style.background = `rgb(${band.rgb.join(",")})`;
        item.append(swatch, band.label);
        legend.append(item);
    }
    const note = element("div", "rain-note");

    container.append(title, row, legend, note);

    // Clicks here must not reach the map, where a click sets the selected location.
    L.DomEvent.disableClickPropagation(container);
    L.DomEvent.disableScrollPropagation(container);

    // On the dashboard it sits in the weather panel, clear of the forecast timeline.
    const dock = document.getElementById("rain-movement");
    if (dock) {
        container.classList.remove("leaflet-bar");
        dock.append(container);
    } else {
        const RainControl = L.Control.extend({
            options: { position: "bottomleft" },
            onAdd: () => container,
        });
        new RainControl().addTo(map);
    }

    return { container, play, scrubber, time, note };
})();

function rainColor(mmPerHour) {
    return RAIN_BANDS.find((band) => mmPerHour >= band.min);
}

// Light rain stays faint and heavier rain turns solid, so intensity reads at a glance.
function rainOpacity(mmPerHour) {
    return Math.min(1, 0.35 + mmPerHour / 8);
}

// Rain at a point between grid cells and between hours, so the animation flows
// instead of jumping cell by cell and hour by hour.
function sampleRain(frameA, frameB, frameMix, row, col, size) {
    const r0 = Math.floor(row);
    const c0 = Math.floor(col);
    const r1 = Math.min(r0 + 1, size - 1);
    const c1 = Math.min(c0 + 1, size - 1);
    const fy = row - r0;
    const fx = col - c0;
    const at = (r, c) => {
        const cell = r * size + c;
        return frameA[cell] * (1 - frameMix) + frameB[cell] * frameMix;
    };
    const south = at(r0, c0) * (1 - fx) + at(r0, c1) * fx;
    const north = at(r1, c0) * (1 - fx) + at(r1, c1) * fx;
    return south * (1 - fy) + north * fy;
}

function drawRain(frame) {
    const { lats, rain_mm: frames, times } = rain.grid;
    const size = lats.length;
    const index = Math.floor(frame);
    const next = Math.min(index + 1, frames.length - 1);
    const mix = frame - index;
    const image = rainContext.createImageData(RAIN_CANVAS_PX, RAIN_CANVAS_PX);

    for (let py = 0; py < RAIN_CANVAS_PX; py++) {
        // Canvas rows run north to south; grid rows run south to north.
        const row = Math.min(Math.max(((RAIN_CANVAS_PX - py - 0.5) / RAIN_CANVAS_PX) * size - 0.5, 0), size - 1);
        for (let px = 0; px < RAIN_CANVAS_PX; px++) {
            const col = Math.min(Math.max(((px + 0.5) / RAIN_CANVAS_PX) * size - 0.5, 0), size - 1);
            const mm = sampleRain(frames[index], frames[next], mix, row, col, size);
            const band = rainColor(mm);
            if (!band) continue;
            const edge = Math.min(px, py, RAIN_CANVAS_PX - 1 - px, RAIN_CANVAS_PX - 1 - py);
            const fade = Math.min(1, edge / (RAIN_CANVAS_PX * RAIN_EDGE_FADE));
            const offset = (py * RAIN_CANVAS_PX + px) * 4;
            image.data[offset] = band.rgb[0];
            image.data[offset + 1] = band.rgb[1];
            image.data[offset + 2] = band.rgb[2];
            image.data[offset + 3] = Math.round(200 * rainOpacity(mm) * fade);
        }
    }
    rainContext.putImageData(image, 0, 0);
    rain.overlay.setUrl(rainCanvas.toDataURL());

    rainUi.scrubber.value = index;
    showRainDataHour(index);
    if (rain.driving && index !== rain.publishedHour) publishRainHour(index);
    rainUi.time.textContent = index === 0
        ? "Now"
        : new Date(times[index]).toLocaleString(undefined, {
            weekday: "short", hour: "numeric", minute: "2-digit",
        });
}

// The hour on show becomes the rain the drains run on, read at the centre of the grid,
// which is the selected place. Only once someone scrubs or plays: loading the grid
// shows the forecast, it does not replace the live nowcast.
function publishRainHour(index) {
    const { rain_mm: frames, times, lats } = rain.grid;
    const centre = Math.floor(lats.length / 2) * lats.length + Math.floor(lats.length / 2);
    rain.publishedHour = index;
    const when = new Date(times[index]).toLocaleString(undefined, {
        weekday: "short", hour: "numeric", minute: "2-digit",
    });
    // Hourly totals are mm fallen in the hour, which is the hour's mean rate in mm/h.
    publishRain(frames[index][centre] ?? 0, `Rain movement forecast, ${when} (hourly)`, "forecast");
}

function stopRain() {
    cancelAnimationFrame(rain.animationId);
    rain.playing = false;
    rain.lastTick = null;
    rainUi.play.textContent = "▶ Play";
}

function animateRain(timestamp) {
    if (!rain.playing) return;
    if (rain.lastTick !== null) {
        rain.frame += (timestamp - rain.lastTick) / MS_PER_FORECAST_HOUR;
        if (rain.frame > rain.grid.rain_mm.length - 1) rain.frame = 0;
    }
    rain.lastTick = timestamp;
    if (timestamp - rain.lastDraw >= RAIN_REDRAW_MS) {
        drawRain(rain.frame);
        rain.lastDraw = timestamp;
    }
    rain.animationId = requestAnimationFrame(animateRain);
}

rainUi.play.addEventListener("click", () => {
    if (!rain.grid) return;
    if (rain.playing) {
        stopRain();
        return;
    }
    rain.playing = true;
    rain.driving = true;
    rainUi.play.textContent = "❚❚ Pause";
    rain.animationId = requestAnimationFrame(animateRain);
});

function showRainHour(index) {
    if (!rain.grid) return;
    stopRain();
    rain.frame = index;
    drawRain(index);
}

rainUi.scrubber.addEventListener("input", () => {
    rain.driving = true;
    showRainHour(Number(rainUi.scrubber.value));
});

// Loads the grid around a place and shows its current hour, without moving the map.
async function loadRainGrid(lat, lon) {
    const id = ++rain.request;
    const query = `/data-collection/rain-grid?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}&hours=${GRID_HOURS}`;
    let grid;
    try {
        const response = await fetch(query);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        grid = await response.json();
    } catch (err) {
        if (id !== rain.request) return;
        stopRain();
        // Dropped so Play cannot animate the previous place under this message.
        rain.grid = null;
        rain.query = query;
        if (rain.overlay) rain.overlay.setUrl(BLANK_IMAGE);
        rainUi.container.hidden = false;
        rainUi.note.textContent = "Rain movement is unavailable right now.";
        renderRainData(err.message);
        return;
    }
    if (id !== rain.request) return;

    stopRain();
    rain.driving = false;
    rain.publishedHour = null;
    const half = grid.step_deg / 2;
    const last = grid.lats.length - 1;
    rain.grid = grid;
    rain.query = query;
    rain.frame = 0;
    rain.bounds = L.latLngBounds(
        [grid.lats[0] - half, grid.lons[0] - half],
        [grid.lats[last] + half, grid.lons[last] + half],
    );
    if (rain.overlay) {
        rain.overlay.setBounds(rain.bounds);
    } else {
        rain.overlay = L.imageOverlay(BLANK_IMAGE, rain.bounds, { interactive: false }).addTo(map);
    }

    rainUi.scrubber.max = grid.rain_mm.length - 1;
    const anyRain = grid.rain_mm.some((frame) => frame.some((mm) => mm >= RAIN_BANDS[RAIN_BANDS.length - 1].min));
    rainUi.note.textContent = anyRain ? "" : "No rain forecast in this area for the next 24 h.";
    rainUi.container.hidden = false;
    renderRainData();
    drawRain(0);
}

// --- Rain data panel ------------------------------------------------------------
// Shown while the Rain overlay is ticked: the exact query behind the rain map and the
// data it returned after filtering, so what the map draws can be checked against the
// numbers. The Rain tiles themselves are images with no data, so these numbers come
// from the rain movement grid.

const rainDataPanel = document.getElementById("rain-data");
const rainData = { visible: false, hour: -1, hourRows: [], matrix: null, caption: null };

map.on("overlayadd", (event) => {
    if (event.name !== "Rain") return;
    rainData.visible = true;
    renderRainData();
});

map.on("overlayremove", (event) => {
    if (event.name !== "Rain") return;
    rainData.visible = false;
    rainDataPanel.hidden = true;
});

function formatGridTime(iso) {
    return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

// Pretty JSON with every numeric array kept on one line, so 24 hours x 49 points stays
// readable instead of running to thousands of lines.
function formatGridJson(grid) {
    return JSON.stringify(grid, null, 2).replace(
        /\[\s*(-?[\d.]+(?:\s*,\s*-?[\d.]+)*)\s*\]/g,
        (match, numbers) => `[${numbers.split(/\s*,\s*/).join(", ")}]`,
    );
}

function renderRainData(error) {
    if (!rainData.visible) return;
    rainDataPanel.hidden = false;
    rainData.hour = -1;

    const heading = element("h2", null, "Rain data behind the map");
    if (!rain.query) {
        rainDataPanel.replaceChildren(heading, element("p", null,
            "Search a district, use your location, or click the map to load rain data."));
        return;
    }

    const source = element("p");
    source.append("Query: ", element("code", null, rain.query));

    if (!rain.grid) {
        const failure = element("p", "warn", `The query failed${error ? ` (${error})` : ""}.`);
        rainDataPanel.replaceChildren(heading, source, failure);
        return;
    }

    const grid = rain.grid;
    const size = grid.lats.length;
    const lastHour = grid.times.length - 1;
    const threshold = grid.rain_threshold_mm;

    const filters = element("ul", "rain-data-filters");
    for (const text of [
        `Time window: ${formatGridTime(grid.times[0])} to ${formatGridTime(grid.times[lastHour])}, ` +
            `${grid.times.length} hourly steps`,
        `Past hours removed: ${grid.skipped_past_hours}, so the series starts at the current hour ` +
            `(${formatGridTime(grid.from_hour)})`,
        `Area: ${size} × ${size} points, ${grid.step_deg}° apart, latitude ${grid.lats[0]} to ` +
            `${grid.lats[size - 1]}, longitude ${grid.lons[0]} to ${grid.lons[size - 1]}`,
        `Rain counted from ${threshold} mm/h; lighter values are drawn as dry`,
    ]) {
        filters.append(element("li", null, text));
    }

    // Hour-by-hour summary; choosing a row shows that hour on the map.
    const hoursTable = element("table", "rain-hours");
    const hoursHead = hoursTable.createTHead().insertRow();
    for (const label of ["Hour", "Rainy points", "Max mm/h", "Mean mm/h"]) {
        hoursHead.append(element("th", null, label));
    }
    const hoursBody = hoursTable.createTBody();
    rainData.hourRows = grid.rain_mm.map((values, index) => {
        const row = hoursBody.insertRow();
        const rainy = values.filter((mm) => mm >= threshold).length;
        const mean = values.reduce((sum, mm) => sum + mm, 0) / values.length;
        const time = formatGridTime(grid.times[index]);
        row.append(
            element("td", null, index === 0 ? `Now (${time})` : time),
            element("td", null, `${rainy} / ${values.length}`),
            element("td", null, Math.max(...values).toFixed(1)),
            element("td", null, mean.toFixed(2)),
        );
        row.tabIndex = 0;
        row.addEventListener("click", () => showRainHour(index));
        row.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                showRainHour(index);
            }
        });
        return row;
    });
    const hoursScroll = element("div", "rain-data-scroll");
    hoursScroll.append(hoursTable);
    const hoursBox = element("div");
    hoursBox.append(element("div", "rain-data-caption", "Each hour (click to show on the map)"), hoursScroll);

    rainData.caption = element("div", "rain-data-caption");
    rainData.matrix = element("div", "rain-data-scroll");
    const matrixBox = element("div");
    matrixBox.append(rainData.caption, rainData.matrix);

    const tables = element("div", "rain-data-tables");
    tables.append(hoursBox, matrixBox);

    const raw = element("details");
    raw.append(element("summary", null, "Raw JSON response"), element("pre", null, formatGridJson(grid)));

    rainDataPanel.replaceChildren(heading, source, filters, tables, raw);
    showRainDataHour(Math.floor(rain.frame));
}

// The grid values for one hour, laid out like the map: north at the top.
function showRainDataHour(index) {
    if (!rainData.visible || !rain.grid || index === rainData.hour) return;
    const previous = rainData.hourRows[rainData.hour];
    if (previous) previous.classList.remove("on");
    rainData.hour = index;
    const current = rainData.hourRows[index];
    if (current) current.classList.add("on");

    const grid = rain.grid;
    const size = grid.lats.length;
    const values = grid.rain_mm[index];
    rainData.caption.textContent = `Grid at ${formatGridTime(grid.times[index])} (mm/h, north at top)`;

    const table = element("table", "rain-matrix");
    const head = table.createTHead().insertRow();
    head.append(element("th", null, "lat \\ lon"));
    for (const lon of grid.lons) head.append(element("th", null, lon.toFixed(2)));
    const body = table.createTBody();
    for (let row = size - 1; row >= 0; row--) {
        const tableRow = body.insertRow();
        tableRow.append(element("th", null, grid.lats[row].toFixed(2)));
        for (let col = 0; col < size; col++) {
            const mm = values[row * size + col];
            const band = rainColor(mm);
            const valueCell = element("td", band ? null : "dry", mm.toFixed(1));
            if (band) {
                valueCell.style.background = `rgba(${band.rgb.join(",")}, 0.35)`;
                valueCell.title = `${band.label} rain`;
            }
            tableRow.append(valueCell);
        }
    }
    rainData.matrix.replaceChildren(table);
}
