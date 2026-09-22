// Flood nowcast layer: where the streets go under in the next three hours.
//
// The server couples rainfall, drains and terrain and hands back one point per
// manhole that surcharges, each carrying its depth at every 15 minute step. This
// file draws that, and lets the time be scrubbed through the window.

const FLOOD_STEP_MINUTES = 15;

// Depth bands, in centimetres: what the water does to a street at each one.
const DEPTH_BANDS = [
    { min: 30, color: "#7b1fa2", label: "Over 30 cm — impassable" },
    { min: 15, color: "#c62828", label: "15–30 cm — cars stall" },
    { min: 5, color: "#ef6c00", label: "5–15 cm — standing water" },
    { min: 0, color: "#f9a825", label: "Under 5 cm — wet" },
];

function depthColor(cm) {
    return (DEPTH_BANDS.find((band) => cm >= band.min) || DEPTH_BANDS.at(-1)).color;
}

const floodLayer = L.layerGroup();
layerControl.addOverlay(floodLayer, "Flood nowcast (0–3 h)");
// A city-wide run is twenty thousand street stretches: as SVG they would stall panning.
const floodRenderer = L.canvas({ padding: 0.5 });

// ─── Elevation: the ground the water runs over ─────────────────────────────────
// Drawn once by the server from the DEM: colour for height, shading for which way the
// ground falls. It is what the flood model reads, so the two can be checked by eye:
// flooded streets should sit in the blues.

const elevationLayer = L.layerGroup();
layerControl.addOverlay(elevationLayer, "Elevation (DEM)");

const elevationLegend = L.control({ position: "bottomright" });
elevationLegend.onAdd = () => {
    const box = el("div", "elev-legend");
    box.append(el("div", "elev-title", "Ground height (m)"));
    box.append(el("div", "elev-bar"), el("div", "elev-ticks"));
    L.DomEvent.disableClickPropagation(box);
    return box;
};

let elevationInfo = null;

async function showElevation() {
    if (!elevationInfo) {
        const resp = await fetch("/data-collection/elevation");
        if (!resp.ok) return;
        elevationInfo = await resp.json();
        const image = L.imageOverlay(elevationInfo.image, elevationInfo.bounds, {
            opacity: 0.75, interactive: false,
            attribution: "DEM &copy; Copernicus GLO-30",
        });
        elevationLayer.addLayer(image);
    }
    elevationLegend.addTo(map);
    const box = elevationLegend.getContainer();
    const stops = elevationInfo.stops;
    const low = stops[0].m, high = stops.at(-1).m;
    // The ramp is uneven on purpose - most colours sit in the bottom 10 m - so the bar
    // shows the stops at equal widths and labels each one.
    box.querySelector(".elev-bar").style.background =
        `linear-gradient(90deg, ${stops.map((s) => s.color).join(", ")})`;
    box.querySelector(".elev-ticks").replaceChildren(...stops.map((s) => el("span", "", String(s.m))));
    const source = elevationInfo.source;
    const kind = source.kind === "terrain" ? "bare earth" : "surface, roofs included";
    box.querySelector(".elev-title").textContent = `Ground height (m) · ${source.name}, ${kind}`;
    box.title = `Range ${elevationInfo.range_m[0]} to ${elevationInfo.range_m[1]} m ` +
        `(${low} to ${high} m shown). ${source.files.join(", ")}`;
}

map.on("overlayadd", (event) => {
    if (event.layer === elevationLayer) showElevation();
});
map.on("overlayremove", (event) => {
    if (event.layer === elevationLayer) elevationLegend.remove();
});

const floodState = {
    request: 0,
    data: null,
    step: 0,
};

// ─── Panel: the time scrub and the headline numbers ────────────────────────────

const floodUi = (() => {
    const container = el("div", "flood-panel");
    container.hidden = true;

    const header = el("div", "drain-header");
    header.append(el("span", "drain-icon", "🌊"), el("span", "drain-title", "Flood nowcast"));

    const note = el("div", "flood-note", "Turn on the layer to run the nowcast.");

    // The live nowcast is the real answer; the fixed rates are for asking what a
    // storm of a given intensity would do to this network, which is the question a
    // planner asks on a dry afternoon.
    const scenarioLabel = el("label", "drain-label", "Rainfall");
    const scenario = document.createElement("select");
    for (const [value, label] of [
        ["", "Live nowcast"],
        ["15", "What if: 15 mm/h (heavy)"],
        ["30", "What if: 30 mm/h (very heavy)"],
        ["60", "What if: 60 mm/h (red alert)"],
        ["100", "What if: 100 mm/h (2015 flood)"],
    ]) scenario.append(opt(value, label));
    scenarioLabel.append(scenario);

    const slider = document.createElement("input");
    Object.assign(slider, { type: "range", min: 0, max: 11, value: 0, step: 1 });
    slider.className = "flood-slider";
    slider.setAttribute("aria-label", "Minutes ahead");

    const clock = el("div", "flood-clock", "Now");
    const stats = el("div", "flood-stats");
    const legend = el("div", "drain-legend");
    for (const band of DEPTH_BANDS) {
        const item = el("span", "drain-legend-item");
        const swatch = el("i", "drain-swatch");
        swatch.style.background = band.color;
        item.append(swatch, band.label);
        legend.append(item);
    }

    container.append(header, note, scenarioLabel, clock, slider, stats, legend);
    document.getElementById("flood-panel")?.append(container);

    return { container, note, slider, clock, stats, scenario };
})();

// ─── Drawing ───────────────────────────────────────────────────────────────────

function floodTooltip(props, cm) {
    const rows = [
        ["Depth now", `<b>${cm.toFixed(0)} cm</b>${cm >= 30 ? " — impassable" : ""}`],
        ["Peak in window", `${props.peak_depth_cm} cm`],
        ["Starts", props.floods_in_minutes === 0 ? "already flooding"
            : `in ${props.floods_in_minutes} min`],
        ["Water on street", `${props.peak_volume_m3.toLocaleString()} m³ over ${props.street_m} m`],
        ["Drains surcharging", props.drains_surcharging],
        ["Ground level", props.ground_m === null ? "—" : `${props.ground_m} m`],
        ["Ward / zone", `${props.ward || "—"} · ${props.zone || "—"}`],
    ];
    return `<div class="drain-tip">
        <div class="drain-tip-head">
            <span>${esc(props.street || "Unnamed street")}</span>
            <span class="drain-tip-band" style="background:${depthColor(cm)}">${cm.toFixed(0)} cm</span>
        </div>
        <table>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>
        <div class="drain-tip-foot">${esc(props.location || "")}</div>
    </div>`;
}

// The wet part of one street stretch at one step, as runs of consecutive vertices,
// each with the deepest water on it. A vertex is wet when the water has spread that far
// along the road and the ground there is below the level it stands at.
function wetRuns(stretch, manhole, step) {
    const props = stretch.properties;
    const reach = manhole.reach_m[step] ?? 0;
    const level = manhole.level_m[step];
    const atManhole = manhole.depth_cm[step] ?? 0;
    const runs = [];
    let run = null;
    props.distance_m.forEach((distance, i) => {
        const ground = props.ground_m[i];
        // Terrain decides where the water goes, the manhole how deep: below the water
        // level a street is wet, but never deeper than the water where it came up.
        const depth = level == null || ground == null ? atManhole
            : Math.min((level - ground) * 100, atManhole);
        const wet = atManhole >= floodState.data.thresholds.reported_cm
            && distance <= reach && depth > 0;
        if (wet) {
            if (!run) runs.push(run = { points: [], depth: 0 });
            const [lon, lat] = stretch.geometry.coordinates[i];
            run.points.push([lat, lon]);
            run.depth = Math.max(run.depth, depth);
        } else {
            run = null;
        }
    });
    return runs.filter((r) => r.points.length >= 2);
}

function streetTooltip(name, highway, cm, manhole) {
    const rows = [
        ["Water on the road", `<b>${cm.toFixed(0)} cm</b>${cm >= 30 ? " — impassable" : ""}`],
        ["Road", esc(highway || "—")],
        ["Comes from", esc(manhole.street || "a surcharging drain")],
        ["Starts", manhole.floods_in_minutes === 0 ? "already flooding"
            : `in ${manhole.floods_in_minutes} min`],
    ];
    return `<div class="drain-tip">
        <div class="drain-tip-head">
            <span>${esc(name || "Unnamed street")}</span>
            <span class="drain-tip-band" style="background:${depthColor(cm)}">${cm.toFixed(0)} cm</span>
        </div>
        <table>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>
        <div class="drain-tip-foot">Wet where the DEM puts the road below the water level; no deeper than at the manhole</div>
    </div>`;
}

function drawFlood() {
    floodLayer.clearLayers();
    const data = floodState.data;
    if (!data) return;

    const step = floodState.step;
    let wet = 0;
    let blocked = 0;

    // Streets first, so the manholes they come from sit on top.
    const manholes = new Map(data.features.map((f) => [f.properties.node, f.properties]));
    const wetStreets = new Set();
    const blockedStreets = new Set();
    let unnamed = 0;
    const drawn = [];
    for (const stretch of data.streets?.features ?? []) {
        const manhole = manholes.get(stretch.properties.node);
        if (!manhole) continue;
        for (const run of wetRuns(stretch, manhole, step)) {
            // Counted by name: one street is one street, however many stretches of it
            // are wet. Unnamed lanes are counted apart rather than as streets.
            const key = stretch.properties.name;
            if (key) {
                wetStreets.add(key);
                if (run.depth >= data.thresholds.impassable_cm) blockedStreets.add(key);
            } else {
                unnamed++;
            }
            drawn.push({ run, stretch, manhole });
        }
    }
    // Deepest drawn last, so where stretches overlap the worse water shows.
    drawn.sort((a, b) => a.run.depth - b.run.depth);
    for (const { run, stretch, manhole } of drawn) {
        L.polyline(run.points, {
            renderer: floodRenderer,
            color: depthColor(run.depth),
            weight: run.depth >= data.thresholds.impassable_cm ? 7 : 5,
            opacity: 0.9,
            lineCap: "round",
        })
            .bindTooltip(streetTooltip(stretch.properties.name, stretch.properties.highway,
                run.depth, manhole), { sticky: true, className: "drain-tooltip", direction: "auto" })
            .addTo(floodLayer);
    }

    for (const feature of data.features) {
        const cm = feature.properties.depth_cm[step] ?? 0;
        if (cm < data.thresholds.reported_cm) continue;
        wet++;
        if (cm >= data.thresholds.impassable_cm) blocked++;

        const [lon, lat] = feature.geometry.coordinates;
        // Radius carries depth as well as colour, so a glance finds the deep water.
        // The manhole the water comes up at: a small ringed dot, the street is the story.
        L.circleMarker([lat, lon], {
            renderer: floodRenderer,
            radius: 4,
            color: "#fff",
            weight: 1.5,
            fillColor: depthColor(cm),
            fillOpacity: 1,
        })
            .bindTooltip(floodTooltip(feature.properties, cm), {
                sticky: true, className: "drain-tooltip", direction: "auto",
            })
            .addTo(floodLayer);
    }

    const minutes = step * FLOOD_STEP_MINUTES;
    // Live steps are timestamps; a what-if run has no clock of its own, only offsets.
    const stamp = data.nowcast.times[step] || "";
    const wall = stamp.includes("T") ? ` · ${stamp.slice(11, 16)}` : "";
    floodUi.clock.textContent = (minutes === 0 ? "Now" : `+${minutes} min`) + wall;
    floodUi.stats.replaceChildren(
        el("div", "drain-stat-line",
            `${wetStreets.size} named street${wetStreets.size === 1 ? "" : "s"} under water · ` +
            `${blockedStreets.size} impassable`),
        el("div", "drain-stat-line",
            `plus ${unnamed} stretch${unnamed === 1 ? "" : "es"} of unnamed lane`),
        el("div", "drain-stat-line",
            `${wet} manhole${wet === 1 ? "" : "s"} surcharging · ${blocked} over 30 cm`),
        el("div", "drain-stat-line",
            `Rain ${data.nowcast.rain_mm_h[step]} mm/h · deepest ${data.worst_depth_cm} cm in the window`),
    );
}

// ─── Fetch ─────────────────────────────────────────────────────────────────────

async function loadFlood() {
    const id = ++floodState.request;
    floodUi.note.textContent = "Running the nowcast…";

    const params = new URLSearchParams();
    const centre = map.getCenter();
    params.set("lat", centre.lat.toFixed(4));
    params.set("lon", centre.lng.toFixed(4));
    if (drainUi.zoneSelect.value) params.set("zone", drainUi.zoneSelect.value);
    if (drainUi.wardSelect.value) params.set("ward", drainUi.wardSelect.value);
    if (floodUi.scenario.value) params.set("rain_mm_h", floodUi.scenario.value);
    params.set("streets", "true");

    let data;
    try {
        const resp = await fetch(`/data-collection/flood-nowcast?${params}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        data = await resp.json();
    } catch (err) {
        if (id === floodState.request) floodUi.note.textContent = `Nowcast failed: ${err.message}`;
        return;
    }
    if (id !== floodState.request) return;

    floodState.data = data;
    floodState.step = 0;
    // One rainfall for the whole dashboard: a what-if storm drives the drain layer
    // and its load colouring too, so the two panels never disagree about the weather.
    publishRain(data.nowcast.rain_mm_h[0]);
    floodUi.slider.max = String(Math.max(0, data.nowcast.rain_mm_h.length - 1));
    floodUi.slider.value = "0";
    floodUi.note.textContent = `${data.nowcast.source} · ${data.drains_considered.toLocaleString()} drains`;
    drawFlood();
}

floodUi.scenario.addEventListener("change", () => {
    if (map.hasLayer(floodLayer)) loadFlood();
});

floodUi.slider.addEventListener("input", () => {
    floodState.step = Number(floodUi.slider.value);
    drawFlood();
    publishRain(floodState.data?.nowcast.rain_mm_h[floodState.step]);
});

// Hand the rate on show to whatever else draws itself from the rainfall.
function publishRain(mmPerHour) {
    if (typeof mmPerHour !== "number") return;
    window.rainNowMmH = mmPerHour;
    window.dispatchEvent(new CustomEvent("rain-change", { detail: { mmPerHour } }));
}

// Re-run when the filters that scope the drains change.
for (const select of [drainUi.zoneSelect, drainUi.wardSelect]) {
    select.addEventListener("change", () => {
        if (map.hasLayer(floodLayer)) loadFlood();
    });
}

map.on("overlayadd", (event) => {
    if (event.layer !== floodLayer) return;
    floodUi.container.hidden = false;
    document.getElementById("flood-panel")?.removeAttribute("hidden");
    loadFlood();
});
map.on("overlayremove", (event) => {
    if (event.layer !== floodLayer) return;
    floodUi.container.hidden = true;
    document.getElementById("flood-panel")?.setAttribute("hidden", "");
    floodState.request++;
    floodLayer.clearLayers();
});
