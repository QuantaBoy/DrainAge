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
    ["load", "Live load (rain vs capacity)"],
    ["flow", "Water flow (invert levels)"],
    ["condition", "Condition"],
];

// How full a drain is running, in the same bands as app/services/hydraulics.py.
const LOAD_COLORS = {
    clear: "#1b8f4d",
    filling: "#e0a200",
    "at capacity": "#ef6c00",
    overflowing: "#c62828",
    unsized: "#9e9e9e",
};

// GCC's 15 corporation zones, in code order (N01 = Zone I, ...).
const ZONE_NAMES = [
    "Thiruvottiyur", "Manali", "Madhavaram", "Tondiarpet", "Royapuram",
    "Thiru Vi Ka Nagar", "Ambattur", "Anna Nagar", "Teynampet", "Kodambakkam",
    "Valasaravakkam", "Alandur", "Adyar", "Perungudi", "Sholinganallur",
];
const ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV"];

function zoneLabel(code) {
    const n = parseInt(code.slice(1), 10);
    return ZONE_NAMES[n - 1] ? `Zone ${ROMAN[n - 1]} – ${ZONE_NAMES[n - 1]}` : code;
}

function wardLabel(code) {
    return `Ward ${parseInt(code.slice(1), 10)}`;
}

// Wards with a GCC base-map sheet in Ward/, from /drain-filters.
let wardSheets = {};

// A ward whose drains were drawn on a sheet is marked; the rest are survey rows only.
function wardOptionLabel(code) {
    const sheet = wardSheets[code];
    return sheet ? `Ward ${sheet.ward} · Zone ${sheet.zone_roman}` : wardLabel(code);
}

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

// The drain whose hover card is showing, so a new one can close it.
let openTooltipLayer = null;

const drainRenderer = L.canvas({ padding: 0.5 });
const drainLayer = L.layerGroup();
const arrowLayer = L.layerGroup().addTo(drainLayer);
layerControl.addOverlay(drainLayer, "Storm Water Drains");

const drainState = {
    request: 0,   // monotonic request counter to discard stale fetches
    geojsonLayer: null,
    // Sent back with every hydraulics request so the page and the server work the
    // catchment out the same way; both come from the /drains response.
    runoffCoeff: 0.75,
    stripM: 60,
    summary: null,
};

// Rainfall the forecast panel is showing, mm/h. Until a forecast is loaded the
// network is drawn dry rather than guessing.
function rainNow() {
    return Number(window.rainNowMmH) || 0;
}

// The Rational method, exactly as app/services/hydraulics.rational_inflow does it.
function liveLoad(p) {
    const catchment = p.CATCH_M2 || 0;
    const inflow = drainState.runoffCoeff * rainNow() * catchment / 3.6e6;
    const capacity = p.Q_CAP || 0;
    const ratio = capacity > 0 ? inflow / capacity : (inflow > 0 ? Infinity : 0);
    return { inflow, capacity, catchment, ratio, band: loadBand(capacity, ratio) };
}

function loadBand(capacity, ratio) {
    if (!(capacity > 0)) return "unsized";
    if (ratio < 0.5) return "clear";
    if (ratio < 0.85) return "filling";
    if (ratio < 1) return "at capacity";
    return "overflowing";
}

const fmt = (value, digits = 2) =>
    typeof value === "number" && isFinite(value) ? value.toFixed(digits) : "—";

// Survey text is third-party data and goes into markup, so it is escaped here.
function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g,
        (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
}

// ─── Filter panel (Leaflet Control, top-right) ─────────────────────────────────

const drainUi = (() => {
    const dock = document.getElementById("drain-panel");
    const container = el("div", dock ? "drain-control docked" : "drain-control leaflet-bar");
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

    if (dock) {
        dock.append(container);
    } else {
        // No dock on the page: fall back to a floating control on the map.
        const DrainControl = L.Control.extend({
            options: { position: "topright" },
            onAdd: () => container,
        });
        new DrainControl().addTo(map);
    }

    return { container, zoneSelect, wardSelect, typeSelect, statusSelect, colorSelect, status, legend };
})();

const LEGENDS = {
    load: [
        { color: LOAD_COLORS.clear, label: "Under half full" },
        { color: LOAD_COLORS.filling, label: "Filling (50–85%)" },
        { color: LOAD_COLORS["at capacity"], label: "At capacity (85–100%)" },
        { color: LOAD_COLORS.overflowing, label: "Over capacity: spills to street" },
        { color: LOAD_COLORS.unsized, label: "No surveyed size" },
    ],
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
        wardSheets = data.ward_sheets || wardSheets;

        // Zones: only populate once (they never change)
        if (drainUi.zoneSelect.options.length <= 1) {
            for (const z of data.zones) drainUi.zoneSelect.append(opt(z, zoneLabel(z)));
        }
        // Wards: re-populate based on zone
        const currentWard = drainUi.wardSelect.value;
        drainUi.wardSelect.replaceChildren(opt("", "All Wards"));
        for (const w of data.wards) drainUi.wardSelect.append(opt(w, wardOptionLabel(w)));
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
    if (drainUi.colorSelect.value === "load") {
        const { band } = liveLoad(p);
        return {
            color: LOAD_COLORS[band],
            // An overloaded drain is drawn heavier: it is the thing to look at.
            weight: band === "overflowing" ? 6 : band === "at capacity" ? 4.5 : 3,
            opacity: band === "unsized" ? 0.5 : 0.9,
        };
    }
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

// ─── Hover card ────────────────────────────────────────────────────────────────

// Everything about the drain that is already on the client: the surveyed section,
// what Manning makes of it, and what this hour's rain is doing to it.
function drainTooltip(p) {
    const live = liveLoad(p);
    const band = live.band;
    const size = p.DRAIN_SIZE || `${fmt(p.DRAIN_WID, 2)} × ${fmt(p.DRAIN_DEP, 2)} m`;
    const length = p.computed_length_m || p.DRAIN_LEN;
    const slope = p.SLOPE ? `1 in ${Math.round(1 / p.SLOPE)}` : "—";

    const rows = [
        ["Section (W × D)", `${esc(size)} · ${esc(p.DRAIN_DETL || p.DRAIN_TYPE || "")}`],
        ["Length", `${fmt(length, 1)} m`],
        ["Bed slope", `${slope} · inverts ${fmt(p.INVERT_SP, 2)} → ${fmt(p.INVERT_EP, 2)} m`],
        ["Material / condition", `${esc(p.SWD_MAT || p.TYP_MAT || "—")} · ${esc(p.STATUS || "—")}`],
        ["Capacity, as built", `${fmt(p.Q_BUILT, 3)} m³/s at ${fmt(p.V_FULL, 2)} m/s`],
        ["Capacity, in its condition", `<b>${fmt(p.Q_CAP, 3)} m³/s</b>`],
        ["Catchment reaching it", `${fmt(live.catchment / 10000, 2)} ha`],
        [`Inflow at ${fmt(rainNow(), 1)} mm/h`, `${fmt(live.inflow, 3)} m³/s`],
        ["Load", live.capacity > 0
            ? `<b>${isFinite(live.ratio) ? Math.round(live.ratio * 100) : "∞"}%</b> — ${band}`
            : "no surveyed size"],
    ];

    return `<div class="drain-tip">
        <div class="drain-tip-head">
            <span>${esc(p.ST_NAME || "Unnamed drain")}</span>
            <span class="drain-tip-band" style="background:${LOAD_COLORS[band]}">${esc(band)}</span>
        </div>
        <table>${rows.map(([k, v]) =>
            `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>
        <div class="drain-tip-foot">Click for the full hydraulics and where each number comes from</div>
    </div>`;
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
        ["Ward", p.WARD && wardLabel(p.WARD)],
        ["Zone", p.ZONE && zoneLabel(p.ZONE)],
    ].filter(([, v]) => v && v !== "NA" && v !== "");

    const html = `
        <div class="drain-popup">
            <div class="drain-popup-header">
                <span class="drain-popup-title">${p.ST_NAME || "Unnamed Drain"}</span>
                <span class="drain-popup-badge ${statusClass}">${p.STATUS || "N/A"}</span>
            </div>
            <div class="drain-popup-id">ID: ${p.feature_no} · ${zoneLabel(p.ZONE)} / ${wardLabel(p.WARD)}</div>
            <div class="drain-popup-section">Hydraulics</div>
            <div class="drain-popup-hydro">Loading hydraulics…</div>
            <details class="drain-popup-record">
                <summary>Survey record (${rows.length} fields)</summary>
                <table class="drain-popup-table">
                    ${rows.map(([k, v]) =>
                        `<tr><td class="drain-popup-key">${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}
                </table>
            </details>
        </div>
    `;
    return html;
}

// ─── Full hydraulics, computed on the server ───────────────────────────────────

// The click-through report: capacity from the survey, the state this hour's rain
// puts the drain in, and a plain list of where each number came from.
async function fillHydraulics(layer) {
    const popup = layer.getPopup();
    const box = popup.getElement()?.querySelector(".drain-popup-hydro");
    if (!box) return;

    const params = new URLSearchParams({
        feature_no: layer.feature.properties.feature_no,
        rain_mm_h: rainNow().toFixed(2),
        strip_m: drainState.stripM,
        runoff_coeff: drainState.runoffCoeff,
    });
    let data;
    try {
        const resp = await fetch(`/data-collection/drain-hydraulics?${params}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        data = await resp.json();
    } catch (err) {
        box.textContent = `Hydraulics unavailable: ${err.message}`;
        return;
    }

    const c = data.capacity;
    const s = data.state;
    const capacityRows = [
        ["Section", `${fmt(c.width_m, 2)} × ${fmt(c.depth_m, 2)} m ${c.closed ? "closed box" : "open channel"}`],
        ["Flow area", `${fmt(c.area_m2, 3)} m²`],
        ["Wetted perimeter", `${fmt(c.wetted_perimeter_m, 3)} m`],
        ["Hydraulic radius", `${fmt(c.hydraulic_radius_m, 3)} m`],
        ["Bed slope", `1 in ${Math.round(1 / c.slope)} (${esc(c.slope_note)})`],
        ["Manning's n", `${c.manning_n} (${esc(c.material)})`],
        ["Capacity, as built", `${fmt(c.capacity_m3s, 3)} m³/s · ${fmt(c.capacity_m3s * 1000, 0)} L/s`],
        ["Full-bore velocity", `${fmt(c.full_velocity_ms, 2)} m/s`],
        ["Condition factor", `×${c.condition_factor} (${c.condition_notes.map(esc).join("; ")})`],
        ["Capacity now", `<b>${fmt(c.effective_capacity_m3s, 3)} m³/s</b>`],
    ];
    const stateRows = [
        ["Rainfall used", `${fmt(s.rain_mm_h, 1)} mm/h`],
        ["Catchment reaching it", `${fmt(s.catchment_m2 / 10000, 2)} ha (C = ${s.runoff_coefficient})`],
        ["Inflow", `${fmt(s.inflow_m3s, 3)} m³/s · ${fmt(s.inflow_ls, 0)} L/s`],
        ["Load", s.load_pct === null ? "—" : `<b>${fmt(s.load_pct, 0)}%</b> of capacity — ${esc(s.band)}`],
        ["Flow depth", s.flow_depth_m === null
            ? "over the top: surcharged" : `${fmt(s.flow_depth_m, 2)} m of ${fmt(c.depth_m, 2)} m`],
        ["Freeboard", s.freeboard_m === null ? "none" : `${fmt(s.freeboard_m, 2)} m`],
        ["Velocity", `${fmt(s.velocity_ms, 2)} m/s (Froude ${fmt(s.froude, 2)}, ${esc(s.regime)})`],
        ["Time to run its length", s.travel_time_s === null ? "—" : `${fmt(s.travel_time_s / 60, 1)} min`],
        ["Spare capacity", `${fmt(s.spare_m3s, 3)} m³/s`],
    ];
    const table = (rows) => `<table class="drain-popup-table">${rows.map(([k, v]) =>
        `<tr><td class="drain-popup-key">${esc(k)}</td><td>${v}</td></tr>`).join("")}</table>`;

    // Where the drain itself is recorded, and whether the two sources agree.
    const source = data.drain.base_map || {};
    const sourceRow = source.sheet
        ? `<tr><td class="drain-popup-key">Base map</td><td>${esc(source.sheet)}</td></tr>`
        : "";
    const disagreement = source.agrees === false
        ? `<div class="drain-popup-flag">${esc(source.note)}</div>` : "";

    box.innerHTML = `
        ${disagreement}
        ${table(capacityRows)}
        <div class="drain-popup-section">Right now, at this rainfall</div>
        ${table(stateRows)}
        <details class="drain-popup-source">
            <summary>Where these numbers come from</summary>
            <table class="drain-popup-table">${sourceRow}</table>
            <dl>${Object.entries(data.provenance).map(([what, how]) =>
                `<dt>${esc(what)}</dt><dd>${esc(how)}</dd>`).join("")}</dl>
        </details>`;
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
    drainState.runoffCoeff = data.runoff_coefficient ?? drainState.runoffCoeff;
    drainState.stripM = data.strip_m ?? drainState.stripM;

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
            layer.bindPopup(drainPopup(feature), { maxWidth: 360, className: "drain-popup-container" });
            // The hover card is rebuilt each time, so it always shows the hour the
            // forecast slider is on.
            layer.bindTooltip(() => drainTooltip(layer.feature.properties),
                { sticky: true, className: "drain-tooltip", direction: "auto" });
            layer.on("popupopen", () => fillHydraulics(layer));
            // Leaflet leaves a hover card open if the pointer leaves a line while it
            // is being redrawn, which stacks cards over the map. Only the newest one
            // is kept.
            layer.on("tooltipopen", () => {
                if (openTooltipLayer && openTooltipLayer !== layer) openTooltipLayer.closeTooltip();
                openTooltipLayer = layer;
            });

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

    refreshSummary();
}

// ─── Network state, for the whole selection ────────────────────────────────────

// One line of live truth about the selected network: how much it can carry, how
// much this hour's rain is putting into it, and how much of it is over the top.
async function refreshSummary() {
    const params = new URLSearchParams({ rain_mm_h: rainNow().toFixed(2), strip_m: drainState.stripM });
    if (drainUi.zoneSelect.value) params.set("zone", drainUi.zoneSelect.value);
    if (drainUi.wardSelect.value) params.set("ward", drainUi.wardSelect.value);

    let summary;
    try {
        const resp = await fetch(`/data-collection/network-summary?${params}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        summary = await resp.json();
    } catch (err) {
        drainUi.status.textContent = `Network summary unavailable: ${err.message}`;
        return;
    }
    drainState.summary = summary;

    // The narrower filter is the one worth naming.
    const where = drainUi.wardSelect.value ? wardOptionLabel(drainUi.wardSelect.value)
        : drainUi.zoneSelect.value ? zoneLabel(drainUi.zoneSelect.value)
        : "all zones";

    // Which rain these loads are for: the live nowcast, an hour someone picked, or a
    // scenario. Never left for the reader to guess.
    const source = window.rainSource;
    const rainLine = el("div", `rain-source ${source.kind}`,
        `Rain ${fmt(summary.rain_mm_h, 1)} mm/h · ${source.label}`);
    if (source.kind !== "live" && source.kind !== "none") {
        const back = el("button", "rain-source-back", "Back to live");
        back.type = "button";
        back.addEventListener("click", backToLiveRain);
        rainLine.append(back);
    }

    drainUi.status.replaceChildren(
        rainLine,
        el("div", "drain-stat-line",
            `${summary.drains.toLocaleString()} drains · ${summary.length_km.toLocaleString()} km · ${where}`),
        el("div", "drain-stat-line",
            `Conveyance ${summary.effective_capacity_m3s.toLocaleString()} m³/s ` +
            `(as built ${summary.built_capacity_m3s.toLocaleString()})`),
        el("div", "drain-stat-line",
            `Runoff now ${summary.inflow_m3s.toLocaleString()} m³/s from ` +
            `${summary.catchment_km2} km² at ${fmt(summary.rain_mm_h, 1)} mm/h`),
        el("div", summary.overloaded ? "drain-stat-alert" : "drain-stat-line",
            summary.overloaded
                ? `${summary.overloaded.toLocaleString()} ` +
                  `${summary.overloaded === 1 ? "drain" : "drains"} over capacity, ` +
                  `spilling ${fmt(summary.spill_m3s, 1)} m³/s` +
                  (summary.worst[0] ? ` · worst ${summary.worst[0].street || "unnamed"} ` +
                      `at ${Math.round(summary.worst[0].load_pct)}%` : "")
                : "No drain over capacity at this rainfall"),
    );
}

// Moving the forecast slider moves the network with it: same hour, same numbers.
window.addEventListener("rain-change", () => {
    if (!map.hasLayer(drainLayer) || !drainState.geojsonLayer) return;
    if (drainUi.colorSelect.value === "load") drainState.geojsonLayer.setStyle(drainStyle);
    refreshSummary();
});

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
    document.getElementById("drain-panel")?.removeAttribute("hidden");
    loadDrainFilters().then(loadDrains);
});
map.on("overlayremove", (event) => {
    if (event.layer !== drainLayer) return;
    drainUi.container.hidden = true;
    document.getElementById("drain-panel")?.setAttribute("hidden", "");
    drainState.request++;
});

// The drainage network is the dashboard's base layout, so it is shown from the start.
drainLayer.addTo(map);
