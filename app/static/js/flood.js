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

function drawFlood() {
    floodLayer.clearLayers();
    const data = floodState.data;
    if (!data) return;

    const step = floodState.step;
    let wet = 0;
    let blocked = 0;

    for (const feature of data.features) {
        const cm = feature.properties.depth_cm[step] ?? 0;
        if (cm < data.thresholds.reported_cm) continue;
        wet++;
        if (cm >= data.thresholds.impassable_cm) blocked++;

        const [lon, lat] = feature.geometry.coordinates;
        // Radius carries depth as well as colour, so a glance finds the deep water.
        L.circleMarker([lat, lon], {
            radius: Math.min(4 + cm / 6, 16),
            color: depthColor(cm),
            weight: 1,
            fillColor: depthColor(cm),
            fillOpacity: 0.55,
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
            `${wet} street${wet === 1 ? "" : "s"} under water · ${blocked} impassable`),
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
