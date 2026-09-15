// Storm water drains overlay: GCC drain network on the shared Leaflet map.
// Provides filter controls for Zone, Ward, Drain Type, and Status, with
// color-coded polylines and rich detail popups.

// ─── Constants ─────────────────────────────────────────────────────────────────

const DRAIN_ZOOM = 14;

// Visual styles by drain detail (open/closed).
const DRAIN_STYLES = {
    open:   { color: "#00bcd4", dashArray: "8 5",  weight: 3, opacity: 0.9 },
    closed: { color: "#1565c0", dashArray: null,    weight: 3, opacity: 0.9 },
    swd:    { color: "#7b1fa2", dashArray: null,    weight: 4, opacity: 0.9 },
};

// Status tint overrides.
const STATUS_COLORS = {
    Good: "#2e7d32",
    Bad:  "#c62828",
    Yes:  "#ef6c00",
};

// Flow comes only from each drain's own invert levels in the GCC survey CSV. Pink
// marks reverse-gradient drains, as on the GCC storm water drain base maps.
const FLOW_COLORS = {
    forward: "#1565c0",
    reverse: "#e91e63",
    unknown: "#9e9e9e",
};
// SVG lines can march in their flow direction; past this many the map slows, so
// larger selections are drawn on canvas without animation.
const ANIMATE_MAX_DRAINS = 2500;
// Arrowheads show each drain's flow direction once the map is close enough to read
// individual drains, as on the GCC base maps (1:5000 is about zoom 16).
const ARROW_ZOOM = 16;
const COLOR_MODES = [
    ["flow", "Water flow (invert levels)"],
    ["condition", "Condition"],
];

// ─── Helpers ───────────────────────────────────────────────────────────────────

function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
}

function opt(value, label) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = label || value;
    return o;
}

// ─── Drain layer & renderer ────────────────────────────────────────────────────

const drainRenderer = L.canvas({ padding: 0.5 });
const drainLayer = L.layerGroup();
const arrowLayer = L.layerGroup().addTo(drainLayer);
layerControl.addOverlay(drainLayer, "Storm Water Drains");

const drainState = {
    request: 0,   // monotonic request counter to discard stale fetches
    geojsonLayer: null,
};

// ─── Filter panel (Leaflet Control, top-right) ─────────────────────────────────

const drainUi = (() => {
    const container = el("div", "drain-control leaflet-bar");
    container.hidden = true;

    // Header
    const header = el("div", "drain-header");
    const icon = el("span", "drain-icon", "🌊");
    const title = el("span", "drain-title", "Storm Water Drains");
    header.append(icon, title);

    // Filters
    const filtersWrap = el("div", "drain-filters");

    const zoneLabel = el("label", "drain-label", "Zone");
    const zoneSelect = el("select");
    zoneSelect.id = "drain-zone";
    zoneSelect.append(opt("", "All Zones"));

    const wardLabel = el("label", "drain-label", "Ward");
    const wardSelect = el("select");
    wardSelect.id = "drain-ward";
    wardSelect.append(opt("", "All Wards"));

    const typeLabel = el("label", "drain-label", "Drain Type");
    const typeSelect = el("select");
    typeSelect.id = "drain-type";
    typeSelect.append(opt("", "All Types"));

    const statusLabel = el("label", "drain-label", "Status");
    const statusSelect = el("select");
    statusSelect.id = "drain-status";
    statusSelect.append(opt("", "All Statuses"));

    const colorLabel = el("label", "drain-label", "Colour by");
    const colorSelect = el("select");
    for (const [value, label] of COLOR_MODES) colorSelect.append(opt(value, label));
    colorLabel.append(colorSelect);

    zoneLabel.append(zoneSelect);
    wardLabel.append(wardSelect);
    typeLabel.append(typeSelect);
    statusLabel.append(statusSelect);
    filtersWrap.append(zoneLabel, wardLabel, typeLabel, statusLabel, colorLabel);

    // Status text
    const status = el("div", "drain-status-text");

    // Legend, rebuilt for the colour mode
    const legend = el("div", "drain-legend");

    container.append(header, filtersWrap, status, legend);
    L.DomEvent.disableClickPropagation(container);
    L.DomEvent.disableScrollPropagation(container);

    const DrainControl = L.Control.extend({
        options: { position: "topright" },
        onAdd: () => container,
    });
    new DrainControl().addTo(map);

    return { container, zoneSelect, wardSelect, typeSelect, statusSelect, colorSelect, status, legend };
})();

const LEGENDS = {
    flow: [
        { color: FLOW_COLORS.forward, label: "Drain, flows along arrow" },
        { color: FLOW_COLORS.reverse, label: "Reverse gradient (flows back)" },
        { color: FLOW_COLORS.unknown, label: "No usable invert levels" },
    ],
    condition: [
        { style: DRAIN_STYLES.open,   label: "Open Drain" },
        { style: DRAIN_STYLES.closed, label: "Closed Drain" },
        { style: DRAIN_STYLES.swd,    label: "SWD (Main)" },
        { color: STATUS_COLORS.Good,  label: "Good Status" },
        { color: STATUS_COLORS.Bad,   label: "Bad Status" },
    ],
};

function renderLegend() {
    const items = LEGENDS[drainUi.colorSelect.value];
    drainUi.legend.replaceChildren(...items.map((item) => {
        const span = el("span", "drain-legend-item");
        const swatch = el("i", "drain-swatch");
        swatch.style.background = item.style ? item.style.color : item.color;
        if (item.style?.dashArray) swatch.style.borderStyle = "dashed";
        span.append(swatch, item.label);
        return span;
    }));
}

// ─── Populate filter dropdowns ─────────────────────────────────────────────────

async function loadDrainFilters() {
    const zone = drainUi.zoneSelect.value;
    const url = zone ? `/data-collection/drain-filters?zone=${encodeURIComponent(zone)}` : "/data-collection/drain-filters";
    try {
        const resp = await fetch(url);
        if (!resp.ok) return;
        const data = await resp.json();

        // Zones: only populate once (they never change)
        if (drainUi.zoneSelect.options.length <= 1) {
            for (const z of data.zones) drainUi.zoneSelect.append(opt(z));
        }
        // Wards: re-populate based on zone
        const currentWard = drainUi.wardSelect.value;
        drainUi.wardSelect.replaceChildren(opt("", "All Wards"));
        for (const w of data.wards) drainUi.wardSelect.append(opt(w));
        // Restore selection if it still exists
        if ([...drainUi.wardSelect.options].some(o => o.value === currentWard)) {
            drainUi.wardSelect.value = currentWard;
        }

        // Types & statuses: only populate once
        if (drainUi.typeSelect.options.length <= 1) {
            for (const t of data.drain_types) drainUi.typeSelect.append(opt(t));
        }
        if (drainUi.statusSelect.options.length <= 1) {
            for (const s of data.statuses) drainUi.statusSelect.append(opt(s));
        }
    } catch (err) {
        console.warn("Failed to load drain filters:", err);
    }
}

// ─── Style a drain feature ─────────────────────────────────────────────────────

function drainStyle(feature) {
    const p = feature.properties;
    if (drainUi.colorSelect.value === "flow") {
        return { color: FLOW_COLORS[p.FLOW], weight: p.FLOW === "reverse" ? 5 : 3, opacity: 0.9 };
    }
    return conditionStyle(p);
}

function conditionStyle(p) {
    const detail = (p.DRAIN_DETL || "").toLowerCase();
    const type = (p.DRAIN_TYPE || "").toLowerCase();

    // Base style by type/detail
    let base;
    if (type === "swd") {
        base = { ...DRAIN_STYLES.swd };
    } else if (detail.includes("closed") || detail === "hidden" || detail === "temporary closed") {
        base = { ...DRAIN_STYLES.closed };
    } else {
        base = { ...DRAIN_STYLES.open };
    }

    // Override color by status
    const statusColor = STATUS_COLORS[p.STATUS];
    if (statusColor) base.color = statusColor;

    return base;
}

// ─── Popup content ─────────────────────────────────────────────────────────────

function drainPopup(feature) {
    const p = feature.properties;
    const statusClass = (p.STATUS || "").toLowerCase() === "good" ? "good" : "bad";

    const rows = [
        ["Street", p.ST_NAME],
        ["Location", p.LOCATION],
        ["Drain Type", p.DRAIN_TYPE],
        ["Detail", p.DRAIN_DETL],
        ["Size", p.DRAIN_SIZE],
        ["Depth (m)", p.DRAIN_DEP],
        ["Width (m)", p.DRAIN_WID],
        ["Length (m)", typeof p.DRAIN_LEN === "number" ? p.DRAIN_LEN.toFixed(1) : p.DRAIN_LEN],
        ["Computed Length", typeof p.computed_length_m === "number" ? p.computed_length_m.toFixed(1) + " m" : p.computed_length_m],
        ["Material", p.SWD_MAT],
        ["Type Material", p.TYP_MAT],
        ["Cover", p.COVER],
        ["Inlet Shape", p.INLET_SHP],
        ["MH Shape", p.MH_SHAPE],
        ["MH Size", p.MH_SIZE],
        ["Pucca/Kacha", p.PUCA_KACHA],
        ["Obstacles", p.OBSTACLES],
        ["Water Flow", p.WATER_FLOW],
        ["Invert Start", p.INVERT_SP],
        ["Invert End", p.INVERT_EP],
        ["Flow", {
            forward: "Start → end (start invert higher)",
            reverse: "Reverse gradient: end invert higher, flows back to start",
            unknown: "Unknown (invert levels missing or equal)",
        }[p.FLOW]],
        ["Ward", p.WARD],
        ["Zone", p.ZONE],
    ].filter(([, v]) => v && v !== "NA" && v !== "");

    const html = `
        <div class="drain-popup">
            <div class="drain-popup-header">
                <span class="drain-popup-title">${p.ST_NAME || "Unnamed Drain"}</span>
                <span class="drain-popup-badge ${statusClass}">${p.STATUS || "N/A"}</span>
            </div>
            <div class="drain-popup-id">ID: ${p.feature_no} · ${p.ZONE} / ${p.WARD}</div>
            <table class="drain-popup-table">
                ${rows.map(([k, v]) => `<tr><td class="drain-popup-key">${k}</td><td>${v}</td></tr>`).join("")}
            </table>
        </div>
    `;
    return html;
}

// ─── Fetch & render drains ─────────────────────────────────────────────────────

async function loadDrains() {
    const id = ++drainState.request;
    drainUi.status.textContent = "Loading drains…";

    const params = new URLSearchParams();
    if (drainUi.zoneSelect.value) params.set("zone", drainUi.zoneSelect.value);
    if (drainUi.wardSelect.value) params.set("ward", drainUi.wardSelect.value);
    if (drainUi.typeSelect.value) params.set("drain_type", drainUi.typeSelect.value);
    if (drainUi.statusSelect.value) params.set("status", drainUi.statusSelect.value);

    const qs = params.toString();
    const url = `/data-collection/drains${qs ? "?" + qs : ""}`;

    let data;
    try {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        data = await resp.json();
    } catch (err) {
        if (id === drainState.request) {
            drainUi.status.textContent = `Failed to load drains: ${err.message}`;
        }
        return;
    }
    if (id !== drainState.request) return;

    // Clear old layer
    if (drainState.geojsonLayer) drainLayer.removeLayer(drainState.geojsonLayer);
    drainState.geojsonLayer = null;
    arrowLayer.clearLayers();

    if (data.features.length === 0) {
        drainUi.status.textContent = "No drains match the selected filters.";
        return;
    }

    const animate = data.features.length <= ANIMATE_MAX_DRAINS;
    const geoLayer = L.geoJSON(data.features, {
        // SVG paths get the marching-dash class; lines are drawn in flow direction.
        renderer: animate ? L.svg() : drainRenderer,
        // Lines with no usable invert levels have no direction to show.
        style: (feature) => ({
            ...drainStyle(feature),
            className: animate && feature.properties.FLOW !== "unknown" ? "water-flow" : "",
        }),
        onEachFeature: (feature, layer) => {
            const p = feature.properties;
            layer.bindPopup(drainPopup(feature), { maxWidth: 340, className: "drain-popup-container" });
            layer.bindTooltip(`${p.ST_NAME || "Drain"} (${p.DRAIN_TYPE || ""})`, { sticky: true });

            // Highlight on hover
            layer.on("mouseover", () => {
                layer.setStyle({ weight: 6, opacity: 1 });
                layer.bringToFront();
            });
            layer.on("mouseout", () => {
                geoLayer.resetStyle(layer);
            });
        },
    });

    geoLayer.addTo(drainLayer);
    drainState.geojsonLayer = geoLayer;
    renderLegend();

    // Centre on the loaded drains; focusMap zooms in to frame them but never out.
    const bounds = geoLayer.getBounds();
    if (bounds.isValid()) {
        focusMap(bounds.getCenter(), Math.min(map.getBoundsZoom(bounds.pad(0.05)), ARROW_ZOOM));
    }
    drawArrows();

    const zone = drainUi.zoneSelect.value || "All";
    const ward = drainUi.wardSelect.value || "All";
    const reverse = data.features.filter((f) => f.properties.FLOW === "reverse").length;
    drainUi.status.textContent = `${data.total.toLocaleString()} drains · ${reverse} reverse gradient · ` +
        `Zone: ${zone} · Ward: ${ward}`;
}

// ─── Flow-direction arrows ─────────────────────────────────────────────────────

// One arrowhead at the middle of each drain in view, pointing the way water runs
// (the server orders coordinates in flow direction).
function drawArrows() {
    arrowLayer.clearLayers();
    const geoLayer = drainState.geojsonLayer;
    if (!geoLayer || map.getZoom() < ARROW_ZOOM) return;
    const view = map.getBounds();
    geoLayer.eachLayer((layer) => {
        const { FLOW } = layer.feature.properties;
        if (FLOW === "unknown" || !view.intersects(layer.getBounds())) return;
        const points = layer.getLatLngs();
        const i = Math.floor((points.length - 1) / 2);
        const a = map.latLngToLayerPoint(points[i]);
        const b = map.latLngToLayerPoint(points[i + 1]);
        const angle = Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;
        const html = `<span style="color:${FLOW_COLORS[FLOW]};transform:rotate(${angle}deg)">➤</span>`;
        L.marker(map.layerPointToLatLng(a.add(b).divideBy(2)), {
            icon: L.divIcon({ className: "drain-arrow", html, iconSize: [14, 14] }),
            interactive: false,
            keyboard: false,
        }).addTo(arrowLayer);
    });
}

map.on("moveend", () => {
    if (map.hasLayer(drainLayer)) drawArrows();
});

// ─── Event wiring ──────────────────────────────────────────────────────────────

drainUi.zoneSelect.addEventListener("change", () => {
    // When zone changes, re-populate wards, reset ward selection
    drainUi.wardSelect.replaceChildren(opt("", "All Wards"));
    loadDrainFilters().then(loadDrains);
});

drainUi.wardSelect.addEventListener("change", loadDrains);
drainUi.typeSelect.addEventListener("change", loadDrains);
drainUi.statusSelect.addEventListener("change", loadDrains);
drainUi.colorSelect.addEventListener("change", () => {
    drainState.geojsonLayer?.setStyle(drainStyle);
    renderLegend();
});

renderLegend();

// Show/hide panel and load data when the overlay is toggled.
map.on("overlayadd", (event) => {
    if (event.layer !== drainLayer) return;
    drainUi.container.hidden = false;
    loadDrainFilters().then(loadDrains);
});
map.on("overlayremove", (event) => {
    if (event.layer !== drainLayer) return;
    drainUi.container.hidden = true;
    drainState.request++;
});

// The drainage network is the dashboard's base layout, so it is shown from the start.
drainLayer.addTo(map);
