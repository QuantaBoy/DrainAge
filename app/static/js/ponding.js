// Flood nowcast, minute by minute: which street goes under, when, how deep, and which
// manholes the drains surcharge out of.
//
// The server runs the next 3 hours of rain through the terrain and the drain network
// together (app/services/coupled.py) and sends, for each street stretch, the ground
// under each 20 m point, the storage zone it sits in, and the water level of every
// zone at every 5 minute step; and for each surcharging manhole, its outflow and the
// depth over it. Depth at a point at a step is level - ground, so the slider redraws
// any minute without asking the server again. Uses DEPTH_BANDS and depthColor from
// flood.js, el/opt/esc from drains.js.

const pondingLayer = L.layerGroup();
layerControl.addOverlay(pondingLayer, "Flood forecast, 0–3 h (depth in cm)");
const pondingRenderer = L.canvas({ padding: 0.5 });

const pondingState = { request: 0, data: null, step: 0, filter: "", shown: 300, sort: "soonest", timer: null };

const bandOf = (cm) => DEPTH_BANDS.findIndex((band) => cm >= band.min);
// Manholes appear from this zoom in, street by street.
const MANHOLE_ZOOM = 15;

// ─── Time ──────────────────────────────────────────────────────────────────────

// Step i is the state at the end of minute 5 (i + 1).
function stepOfMinute(minute) {
    return minute / pondingState.data.rain.step_minutes - 1;
}

function clockAt(step) {
    const end = pondingState.data?.rain.ends[step];
    return end ? end.slice(11, 16) : null;
}

// "10:25 (+20 min)" for a live run, "+20 min" for a what-if with no clock.
function whenLabel(minute) {
    if (minute == null) return "—";
    const clock = clockAt(stepOfMinute(minute));
    return clock ? `${clock} (+${minute} min)` : `+${minute} min`;
}

// Depth at each point of a stretch at one step, cm.
function depthsAt(props, step) {
    const water = pondingState.data.water_level_m;
    return props.storage.map((z, i) => {
        const level = z ? water[z]?.[step] : null;
        if (level == null || props.ground_m[i] == null) return 0;
        return Math.max(0, (level - props.ground_m[i]) * 100);
    });
}

// ─── Panel ─────────────────────────────────────────────────────────────────────

const pondingUi = (() => {
    const container = el("div", "flood-panel");
    const header = el("div", "panel-head");
    header.append(el("h2", "", "What-if storm"),
        el("p", "panel-lead", "Try a storm and a drain condition, and see which streets go under."));
    const note = el("div", "flood-note", "");

    const rainLabel = el("label", "drain-label", "Rainfall");
    const rain = document.createElement("select");
    for (const [value, label] of SCENARIO_RATES) rain.append(opt(value, label));
    rainLabel.append(rain);
    // Live or scenario, in words and colour, at the top of the panel.
    const mode = el("div", "mode-badge");

    // How much of the surveyed drain capacity is working. The GCC survey gives each
    // drain's conveyance; what it does not record is how silted or blocked it is, and
    // that is what this asks. Every drain in the network is scaled by it.
    const drainLabel = el("label", "drain-label", "Drains working at");
    const drain = document.createElement("select");
    for (const [value, label] of [
        ["1", "Full surveyed capacity (best case)"],
        ["0.5", "Half of it (silted)"],
        ["0.25", "A quarter of it (badly silted)"],
        ["0", "Nothing (blocked)"],
    ]) drain.append(opt(value, label));
    drain.value = "1";
    drainLabel.append(drain);

    const controls = el("div", "drain-filters");
    controls.append(rainLabel, drainLabel);
    const stats = el("div", "flood-stats");
    container.append(header, mode, controls, note, stats);
    document.getElementById("ponding-panel")?.append(container);

    // The clock lives in the bar under the map: it is the control people use most.
    const byId = (id) => document.getElementById(id);
    return {
        mode, note, rain, drain, stats,
        clock: byId("tb-clock"), rainNow: byId("tb-rain"), chart: byId("tb-bars"),
        slider: byId("tb-slider"), play: byId("tb-play"),
        alerts: byId("alerts"), details: byId("model-details"),
    };
})();

// A banner across the map while a scenario is drawn, so a screenshot of it cannot be
// taken for a forecast.
const scenarioBanner = L.control({ position: "topright" });
scenarioBanner.onAdd = () => el("div", "scenario-banner");

function showMode(data) {
    const live = data.rain.mode === "live";
    const started = data.rain.ends?.[0] ? data.rain.ends[0].slice(11, 16) : null;
    window.shell?.mode(live ? "live" : "scenario", live
        ? `LIVE${started ? ` · from ${started}` : ""}`
        : `SCENARIO · ${data.rain.rain_mm_h[0]} mm/h`);
    pondingUi.mode.className = `mode-badge ${live ? "live" : "scenario"}`;
    pondingUi.mode.textContent = live
        ? "LIVE FORECAST · rain from Open-Meteo, next 3 h"
        : `SCENARIO · ${data.rain.rain_mm_h[0]} mm/h held for 3 h · hypothetical, not a forecast`;
    if (live) {
        scenarioBanner.remove();
    } else {
        scenarioBanner.addTo(map);
        scenarioBanner.getContainer().textContent =
            `SCENARIO: ${data.rain.rain_mm_h[0]} mm/h for 3 h. Hypothetical rain, not a forecast.`;
    }
    const kind = live ? "forecast" : "scenario";
    streetTable.title.textContent = live ? "Streets · next 3 hours" : `Streets · ${data.rain.rain_mm_h[0]} mm/h what-if`;
    pondingState.kind = kind;
}

function drawRainChart() {
    const rates = pondingState.data.rain.rain_mm_h;
    const top = Math.max(10, ...rates);
    pondingUi.chart.replaceChildren(...rates.map((rate, i) => {
        const bar = el("i", i === pondingState.step ? "now" : i < pondingState.step ? "past" : "");
        bar.style.height = `${Math.max(4, (rate / top) * 100)}%`;
        bar.title = `${rate} mm/h`;
        return bar;
    }));
}

// ─── Street table ──────────────────────────────────────────────────────────────

const streetTable = (() => {
    const dock = document.getElementById("street-depths");
    const head = el("div", "street-head");
    const title = el("h2", "", "Street flood forecast · every 5 minutes, next 3 hours");
    const summary = el("p", "street-summary");
    const tools = el("div", "street-tools");
    const search = document.createElement("input");
    Object.assign(search, { type: "search", placeholder: "Filter by street, locality or ward" });
    search.setAttribute("aria-label", "Filter streets");
    const sort = document.createElement("select");
    sort.setAttribute("aria-label", "Sort streets");
    for (const [value, label] of [
        ["soonest", "Goes under soonest"],
        ["deepest", "Deepest"],
        ["now", "Deepest right now"],
    ]) sort.append(opt(value, label));
    const download = el("button", "chip", "Download CSV");
    download.type = "button";
    tools.append(search, sort, download);
    head.append(title, summary, tools);

    const scroller = el("div", "street-scroll");
    const table = el("table", "street-table");
    table.innerHTML = `<thead><tr>
        <th>#</th><th>Street</th><th>Locality · ward</th>
        <th>Goes under</th><th>Impassable from</th><th class="num">Peak (cm)</th><th>Peak at</th>
        <th>Clears</th><th class="num">At slider (cm)</th></tr></thead>`;
    const body = el("tbody");
    table.append(body);
    scroller.append(table);
    const more = el("button", "chip street-more", "Show more");
    more.type = "button";
    dock?.append(head, scroller, more);
    return { dock, title, summary, search, sort, download, body, more };
})();

function streetRows() {
    let rows = pondingState.data?.streets ?? [];
    const q = pondingState.filter.trim().toLowerCase();
    if (q) {
        rows = rows.filter((r) =>
            [r.street, r.locality, r.ward, r.zone].some((v) => v && v.toLowerCase().includes(q)));
    }
    const step = pondingState.step;
    if (pondingState.sort === "deepest") rows = [...rows].sort((a, b) => b.max_depth_cm - a.max_depth_cm);
    if (pondingState.sort === "now") rows = [...rows].sort((a, b) => b.series_cm[step] - a.series_cm[step]);
    return rows;
}

function drawTable() {
    const rows = streetRows();
    const step = pondingState.step;
    const shown = rows.slice(0, pondingState.shown);
    streetTable.body.replaceChildren(...shown.map((row, i) => {
        const tr = el("tr");
        const now = row.series_cm[step];
        const peakBand = DEPTH_BANDS[bandOf(row.max_depth_cm)];
        const nowBand = DEPTH_BANDS[bandOf(now)];
        if (now >= pondingState.data.thresholds.reported_cm) tr.className = "is-wet";
        tr.innerHTML = `<td>${i + 1}</td>
            <td><b>${esc(row.street || "Unnamed road")}</b>
                <span class="street-kind">${esc(row.highway || "")}</span></td>
            <td>${esc(row.locality || "—")}${row.ward ? ` · ${esc(row.ward)}` : ""}</td>
            <td>${whenLabel(row.floods_at_min)}</td>
            <td>${whenLabel(row.impassable_at_min)}</td>
            <td class="num"><span class="depth-pill" style="background:${peakBand.color}">${row.max_depth_cm.toFixed(0)}</span></td>
            <td>${whenLabel(row.peak_at_min)}</td>
            <td>${row.clears_at_min == null ? "still wet at 3 h" : whenLabel(row.clears_at_min)}</td>
            <td class="num">${now >= 1 ? `<span class="depth-pill" style="background:${nowBand.color}">${now.toFixed(0)}</span>` : "dry"}</td>`;
        tr.tabIndex = 0;
        const go = () => {
            window.shell?.peek();
            map.flyTo(row.deepest, 17, { duration: 0.8 });
        };
        tr.addEventListener("click", go);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
        return tr;
    }));
    streetTable.more.hidden = rows.length <= pondingState.shown;
    streetTable.more.textContent = `Show more (${(rows.length - shown.length).toLocaleString()} left)`;
}

streetTable.search.addEventListener("input", () => {
    pondingState.filter = streetTable.search.value;
    pondingState.shown = 300;
    drawTable();
});
streetTable.sort.addEventListener("change", () => {
    pondingState.sort = streetTable.sort.value;
    drawTable();
});
streetTable.more.addEventListener("click", () => {
    pondingState.shown += 300;
    drawTable();
});
streetTable.download.addEventListener("click", () => {
    const data = pondingState.data;
    const rows = streetRows();
    const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const at = (minute) => minute == null ? "" : (clockAt(stepOfMinute(minute)) || `+${minute} min`);
    // One column per 5 minute step as well, so the whole forecast is in the file.
    const steps = data.rain.rain_mm_h.map((_, i) => clockAt(i) || `+${(i + 1) * data.rain.step_minutes}min`);
    const lines = [["street", "road_type", "locality", "ward", "zone", "goes_under", "impassable_from",
        "peak_cm", "peak_at", "clears", "under_water_m", "deepest_lat", "deepest_lon",
        ...steps.map((s) => `cm_${s}`)].join(",")];
    for (const r of rows) {
        lines.push([r.street, r.highway, r.locality, r.ward, r.zone, at(r.floods_at_min),
            at(r.impassable_at_min), r.max_depth_cm, at(r.peak_at_min),
            r.clears_at_min == null ? "after 3 h" : at(r.clears_at_min), r.wet_m,
            r.deepest[0], r.deepest[1], ...r.series_cm].map(cell).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const link = Object.assign(document.createElement("a"), {
        href: URL.createObjectURL(blob),
        // "scenario-30mmh" or "forecast": the file says which it is, like the page.
        download: `street-flood-${pondingState.kind === "scenario" ? `scenario-${data.rain.rain_mm_h[0]}mmh`
            : "forecast"}-${new Date().toISOString().slice(0, 16).replace(":", "")}.csv`,
    });
    link.click();
    URL.revokeObjectURL(link.href);
});

// ─── Map ───────────────────────────────────────────────────────────────────────

// A surcharging manhole at one step.
function manholeTooltip(p, step) {
    const flow = p.surcharge_l_s[step];
    const rows = [
        ["Coming out now", `<b>${flow.toFixed(0)} L/s</b>`],
        ["Water over it", `${p.depth_cm[step].toFixed(0)} cm`],
        ["Peak outflow", `${p.peak_l_s.toFixed(0)} L/s`],
        ["Total over 3 h", `${p.surcharge_m3.toLocaleString()} m³`],
        ["Starts", whenLabel(p.starts_at_min)],
        ["Full drain", esc(p.street || "unnamed")],
        ["Ward / zone", `${esc(p.ward || "—")} · ${esc(p.zone || "—")}`],
    ];
    return `<div class="drain-tip">
        <div class="drain-tip-head"><span>Manhole surcharging</span>
            <span class="drain-tip-band" style="background:${depthColor(p.depth_cm[step])}">${flow.toFixed(0)} L/s</span></div>
        <table>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>
        <div class="drain-tip-foot">The drain below it is full: water comes back up here</div>
    </div>`;
}

// Distance along a stretch at each vertex, metres, for the profile's x axis.
function chainage(coords) {
    const out = [0];
    for (let i = 1; i < coords.length; i++) {
        out.push(out[i - 1] + map.distance([coords[i - 1][1], coords[i - 1][0]], [coords[i][1], coords[i][0]]));
    }
    return out;
}

// Bars in an SVG: values scaled to the box, each coloured by its depth band, with
// the 30 cm line and an optional marker bar.
function barsSvg(values, positions, span, marker, label) {
    const width = 260, height = 64, top = Math.max(30, ...values);
    const bar = Math.max(2, width / values.length - 1);
    const bars = values.map((cm, i) => {
        const h = (cm / top) * (height - 10);
        const x = (positions[i] / span) * (width - bar);
        const cls = i === marker ? ' class="marked"' : "";
        return `<rect${cls} x="${x.toFixed(1)}" y="${(height - h).toFixed(1)}" width="${bar.toFixed(1)}"
            height="${h.toFixed(1)}" fill="${cm > 0 ? depthColor(cm) : "transparent"}"/>`;
    }).join("");
    const guide = height - (30 / top) * (height - 10);
    return `<svg class="depth-profile" viewBox="0 0 ${width} ${height}" role="img" aria-label="${label}">
        <line x1="0" x2="${width}" y1="${guide}" y2="${guide}" class="depth-guide"/>${bars}</svg>`;
}

function stretchPopup(p, coords) {
    const step = pondingState.step;
    const data = pondingState.data;
    const now = depthsAt(p, step);
    const along = chainage(coords);
    const span = along.at(-1) || 1;
    const steps = p.series_cm.map((_, i) => i);
    const first = data.rain.step_minutes;
    const last = first * p.series_cm.length;
    const rows = now.map((cm, i) => cm > 0
        ? `<tr><td>${Math.round(along[i])} m</td><td><b>${cm.toFixed(0)} cm</b></td>
            <td>${p.ground_m[i] == null ? "—" : p.ground_m[i].toFixed(2) + " m"}</td></tr>` : "").join("");
    const nowMax = Math.max(...now);
    return `<div class="drain-tip">
        <div class="drain-tip-head">
            <span>${esc(p.name || "Unnamed road")}</span>
            <span class="drain-tip-band" style="background:${depthColor(nowMax)}">${nowMax.toFixed(0)} cm now</span>
        </div>
        <div class="verdict ${p.impassable_at_min != null ? "bad" : "warn"}">${p.impassable_at_min != null
            ? `Impassable from ${whenLabel(p.impassable_at_min)}: avoid this road`
            : `Standing water from ${whenLabel(p.floods_at_min)}; passable with care`}</div>
        <table>
            <tr><td>Where</td><td>${esc(p.locality || "—")} · ward ${esc(p.ward || "—")} · zone ${esc(p.zone || "—")}</td></tr>
            <tr><td>Goes under</td><td><b>${whenLabel(p.floods_at_min)}</b></td></tr>
            <tr><td>Impassable</td><td>${p.impassable_at_min == null ? "never over 30 cm" : "from " + whenLabel(p.impassable_at_min)}</td></tr>
            <tr><td>Peak</td><td>${p.max_depth_cm.toFixed(0)} cm at ${whenLabel(p.peak_at_min)}</td></tr>
            <tr><td>Clears</td><td>${p.clears_at_min == null ? "still wet at 3 h" : whenLabel(p.clears_at_min)}</td></tr>
            <tr><td>History</td><td>${historyLine(p.history)}</td></tr>
            ${p.tunnel ? "<tr><td>Note</td><td>underpass: the DEM reads the road above, real water is deeper</td></tr>" : ""}
        </table>
        <div class="chart-title">Deepest water on this stretch, every 5 min</div>
        ${barsSvg(p.series_cm, steps, steps.length - 1 || 1, step, "Depth through time")}
        <div class="depth-axis"><span>+${first} min</span><span>slider</span><span>+${last} min</span></div>
        <div class="chart-title">Along the street at the slider (${p.wet_m} m under at peak)</div>
        ${barsSvg(now, along, span, -1, "Depth along the street")}
        <div class="depth-axis"><span>0 m</span><span>30 cm line</span><span>${Math.round(span)} m</span></div>
        <details class="depth-points"><summary>Every point at the slider (along · depth · ground)</summary>
            <table>${rows || "<tr><td>dry at this step</td></tr>"}</table></details>
    </div>`;
}

function tooltipFor(p, depth) {
    return `<b>${esc(p.name || "Unnamed road")}</b> · <b>${depth.toFixed(0)} cm</b> here now` +
        `<br>${esc(p.locality || "")}${p.ward ? " · " + esc(p.ward) : ""}` +
        `<br>Under from ${whenLabel(p.floods_at_min)}` +
        (p.impassable_at_min != null ? ` · impassable from ${whenLabel(p.impassable_at_min)}` : "") +
        `<br><i>click for the 5-minute detail</i>`;
}

function drawPonding() {
    pondingLayer.clearLayers();
    const data = pondingState.data;
    if (!data) return;
    const step = pondingState.step;
    const wetCm = data.thresholds.reported_cm;

    // Each stretch is cut into runs of one band at this step, so the colour changes
    // along a street where the depth does, and dry parts are not drawn at all.
    const pieces = [];
    for (const feature of data.features) {
        const p = feature.properties;
        if (p.series_cm[step] < wetCm) continue;
        const coords = feature.geometry.coordinates;
        const d = depthsAt(p, step);
        // Segment j joins vertex j to j+1 and takes the band of its deeper end.
        const segDepth = (j) => Math.max(d[j], d[j + 1]);
        let run = null;
        for (let j = 0; j < coords.length - 1; j++) {
            const depth = segDepth(j);
            const band = depth >= wetCm ? bandOf(depth) : -1;
            if (run && band === run.band) {
                run.points.push(coords[j + 1]);
                run.depth = Math.max(run.depth, depth);
                continue;
            }
            if (run && run.band >= 0) pieces.push(run);
            run = { band, depth, feature, points: [coords[j], coords[j + 1]] };
        }
        if (run && run.band >= 0) pieces.push(run);
    }
    // Deepest last, so it draws on top where streets meet.
    pieces.sort((a, b) => a.depth - b.depth);
    // Thin lines across the city, full width street by street, so a district reads as
    // streets and not as one blot.
    const zoom = map.getZoom();
    const width = zoom >= 15 ? [5, 7] : zoom >= 13 ? [3, 4.5] : [2, 3];
    const hover = matchMedia("(hover: hover)").matches;
    for (const { depth, feature, points } of pieces) {
        const p = feature.properties;
        const line = L.polyline(points.map(([lon, lat]) => [lat, lon]), {
            renderer: pondingRenderer,
            color: depthColor(depth),
            weight: depth >= data.thresholds.impassable_cm ? width[1] : width[0],
            opacity: 0.92,
            lineCap: "round",
        })
            .on("click", () => window.shell?.detail(stretchPopup(p, feature.geometry.coordinates)))
            .addTo(pondingLayer);
        // A hover card needs a pointer: on a touch screen it sticks, and a tap opens the
        // detail in the panel anyway.
        if (hover) line.bindTooltip(() => tooltipFor(p, depth), { sticky: true, className: "drain-tooltip" });
    }

    // Manholes on top of the streets: where the drains put water back on the road,
    // sized by how fast it comes out.
    // Counted at every zoom, drawn only close up: across the city thousands of dots
    // would bury the streets they are about.
    let surcharging = 0;
    const showManholes = map.getZoom() >= MANHOLE_ZOOM;
    for (const feature of data.manholes.features) {
        const p = feature.properties;
        const flow = p.surcharge_l_s[step];
        if (!(flow > 0)) continue;
        surcharging++;
        if (!showManholes) continue;
        const [lon, lat] = feature.geometry.coordinates;
        const manhole = L.circleMarker([lat, lon], {
            renderer: pondingRenderer,
            radius: Math.min(10, 3 + Math.sqrt(flow) / 3),
            color: "#fff", weight: 1.5, fillColor: "#0d47a1", fillOpacity: 0.9,
        }).on("click", () => window.shell?.detail(manholeTooltip(p, pondingState.step)))
            .addTo(pondingLayer);
        if (hover) manhole.bindTooltip(() => manholeTooltip(p, pondingState.step),
            { sticky: true, className: "drain-tooltip", direction: "auto" });
    }

    const minute = (step + 1) * data.rain.step_minutes;
    const clock = clockAt(step);
    pondingUi.clock.textContent = `${clock || "+" + minute + " min"}${clock ? ` · +${minute} min` : ""}`;
    pondingUi.rainNow.textContent = `Rain ${data.rain.rain_mm_h[step]} mm/h`;
    const counts = data.per_step[step];
    window.shell?.kpis({ wet: counts.wet, blocked: counts.impassable, manholes: surcharging });
    const stat = (label, value) => {
        const row = el("div", "stat");
        row.append(el("span", "", label), el("b", "", value));
        return row;
    };
    pondingUi.stats.replaceChildren(
        el("div", "stats-head", "Over the whole 3 hours"),
        stat("Streets that go under", data.named_streets.toLocaleString()),
        stat("Of them impassable (30 cm+)", data.impassable_streets.toLocaleString()),
        ...(data.recorded_streets != null
            ? [stat("Backed by the flood record", data.recorded_streets.toLocaleString())] : []),
        stat("Road under water", `${data.wet_km} km`),
        stat("Manholes surcharging", data.surcharging_manholes.toLocaleString()),
        stat("Rain in total", `${data.rain.total_mm} mm`),
    );
    pondingUi.details.replaceChildren(
        el("p", "", `Rain runs over a 30 m terrain model corrected with the GCC survey, into the drains ` +
            `through their gratings, along the surveyed network up to each drain's capacity, and back out ` +
            `of the manholes where a drain is full.`),
        el("p", "", `Drains at ${Math.round(data.drain_condition * 100)}% of the survey · ` +
            `${data.model.gratings.toLocaleString()} gratings, ${data.model.open_drain_edge_km} km of open drain edge. ` +
            `${data.model.unsurveyed_km2} km² has no surveyed drain and is given drains built for ` +
            `${data.model.unsurveyed_design_mm_h} mm/h (assumed). ${data.model.blind_ends_to_ground?.toLocaleString?.() ?? "Some"} ` +
            `drains end with no mapped channel; their water is put on the ground there.`),
        el("p", "", massBalance(data.balance_m3)),
        validationNote(data.validation),
    );
    drawAlerts();
    drawRainChart();
    drawTable();
}

// The model's accuracy against Chennai's flood record, from scripts/validate_history.py.
function validationNote(v) {
    if (!v) {
        return el("p", "note", "Not yet checked against a recorded flood: the order and timing streets " +
            "flood in are the reliable output; depths over about 1 m are usually terrain error.");
    }
    const s = v.storms["30"] || Object.values(v.storms)[0];
    const pct = (x) => `${Math.round(x * 100)}%`;
    return el("p", "note",
        `Checked against the ${v.points_2015} places that flooded in December 2015: at 30 mm/h the model ` +
        `floods a street within 250 m of ${pct(s.hit_rate_2015)} of them, against ${pct(s.chance_rate)} for ` +
        `random street points, a margin that holds on zones it was not tuned on (+${pct(s.skill_holdout)}). ` +
        `Streets in High hazard zones flood ${s.hazard_enrichment}x as often as others. Recorded depths ` +
        `are matched only loosely (rank correlation ${s.depth_rank_correlation}): trust where and when, ` +
        `treat exact centimetres as indicative.`);
}

// Where the runoff went, and how well the books close: the model's own audit.
function massBalance(b) {
    const share = (v) => `${Math.round((100 * v) / Math.max(b.runoff, 1))}%`;
    return `Runoff ${(b.runoff / 1e6).toFixed(2)} Mm³: ${share(b.on_surface)} on the ground ` +
        `(${share(b.blind_ends_to_ground || 0)} of it out of drains with no mapped channel at their end), ` +
        `${share(b.outfall)} out through outfalls to canals and rivers, ${share(b.assumed_drainage)} by assumed drains, ` +
        `${share(b.in_drains)} in the pipes, ${share(b.to_sea)} over land to the sea · ` +
        `balance error ${Math.abs(b.error_m3).toExponential(1)} m³`;
}

// The Alerts tab: what goes under next, soonest first, and what is deepest now.
// What the flood record says about a street: the forecast is backed by history when
// the street went under in 2015, lies in a frequent flood extent or a High hazard zone.
function historyTag(r) {
    if (r.flooded_2015) return `<span class="hist-tag known">Flooded in 2015</span>`;
    if (r.recorded) return `<span class="hist-tag known">Recorded hotspot${r.hazard ? ` · ${esc(r.hazard)} hazard` : ""}</span>`;
    return `<span class="hist-tag new">New: not in the flood record</span>`;
}

function historyLine(h) {
    if (!h) return "not available";
    const parts = [];
    if (h.flooded_2015) parts.push("flooded in December 2015");
    if (h.return_period_years) parts.push(`in the ${h.return_period_years}-year flood extent`);
    if (h.hazard) parts.push(`${h.hazard} hazard zone`);
    return parts.length ? parts.join(" · ") : "no recorded flooding: the model alone predicts this";
}

function drawAlerts() {
    const data = pondingState.data;
    const step = pondingState.step;
    const nowMin = (step + 1) * data.rain.step_minutes;
    const reported = data.thresholds.reported_cm;
    const named = data.streets.filter((r) => r.street);
    const soon = named
        .filter((r) => r.floods_at_min != null && r.floods_at_min > nowMin && r.floods_at_min <= nowMin + 30)
        .slice(0, 12);
    const deepest = named
        .filter((r) => r.series_cm[step] >= reported)
        .sort((a, b) => b.series_cm[step] - a.series_cm[step])
        .slice(0, 12);

    const item = (r, badge, badgeClass, sub) => {
        const button = el("button", "alert-item");
        button.type = "button";
        const peak = DEPTH_BANDS[bandOf(r.max_depth_cm)];
        button.innerHTML = `<span class="alert-bar" style="background:${peak.color}"></span>
            <span class="alert-text"><b>${esc(r.street)}</b>
                <small>${esc(r.locality || "")}${r.ward ? ` · ${esc(r.ward)}` : ""}</small>
                <small>${sub}</small>${historyTag(r)}</span>
            <span class="alert-badge ${badgeClass}">${badge}</span>`;
        button.addEventListener("click", () => {
            window.shell?.peek();
            map.flyTo(r.deepest, 17, { duration: 0.8 });
        });
        return button;
    };
    const section = (title, rows, empty) => {
        const box = el("section", "alert-group");
        box.append(el("h3", "", title));
        box.append(...(rows.length ? rows : [el("p", "alert-empty", empty)]));
        return box;
    };

    pondingUi.alerts.replaceChildren(
        section(`Going under in the next 30 minutes`, soon.map((r) => item(r,
            `in ${r.floods_at_min - nowMin} min`, "soon",
            `peaks ${r.max_depth_cm.toFixed(0)} cm${r.impassable_at_min != null ? " · impassable" : ""}`)),
            "No named street goes under in the next 30 minutes."),
        section(`Under water at ${clockAt(step) || `+${nowMin} min`}`, deepest.map((r) => item(r,
            `${r.series_cm[step].toFixed(0)} cm`, r.series_cm[step] >= data.thresholds.impassable_cm ? "bad" : "wet",
            r.clears_at_min == null ? "still wet at 3 h" : `clears ${whenLabel(r.clears_at_min)}`)),
            "No named street is under water at this time."),
    );
}

// ─── Fetch ─────────────────────────────────────────────────────────────────────

async function loadPonding() {
    const id = ++pondingState.request;
    stopPlaying();
    pondingUi.note.textContent = "";
    window.shell?.loading(true, pondingUi.rain.value
        ? `Running a ${pondingUi.rain.value} mm/h storm` : "Running the live flood forecast");
    const params = new URLSearchParams();
    // The rain over the drained city: the selected place if it is in Chennai, else the
    // city centre - never wherever the map happens to be panned to.
    const point = marker && CHENNAI_BOUNDS.contains(marker.getLatLng())
        ? marker.getLatLng() : L.latLng(CHENNAI_CENTRE);
    params.set("lat", point.lat.toFixed(4));
    params.set("lon", point.lng.toFixed(4));
    if (pondingUi.rain.value) params.set("rain_mm_h", pondingUi.rain.value);
    params.set("drain_condition", pondingUi.drain.value);
    if (drainUi.zoneSelect.value) params.set("zone", drainUi.zoneSelect.value);
    if (drainUi.wardSelect.value) params.set("ward", drainUi.wardSelect.value);

    let data;
    try {
        const resp = await fetch(`/data-collection/rain-ponding/streets?${params}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        data = await resp.json();
    } catch (err) {
        if (id === pondingState.request) {
            window.shell?.loading(false);
            window.shell?.mode("error", "Forecast unavailable");
            pondingUi.alerts.replaceChildren(el("p", "alert-empty warn",
                `The forecast could not be run (${err.message}). It will try again in a minute.`));
            setTimeout(() => { if (id === pondingState.request) loadPonding(); }, 60_000);
        }
        return;
    }
    if (id !== pondingState.request) return;
    window.shell?.loading(false);
    pondingState.data = data;
    pondingState.shown = 300;
    showMode(data);
    // Start at the first step anything is under water, so the map is not blank.
    const firstWet = data.per_step.findIndex((s) => s.wet > 0);
    pondingState.step = Math.max(0, firstWet);
    pondingUi.slider.max = String(data.rain.rain_mm_h.length - 1);
    pondingUi.slider.value = String(pondingState.step);
    pondingUi.note.textContent = data.stretches
        ? `${data.rain.source}`
        : `${data.rain.source}: ${data.rain.total_mm} mm, no street goes under.`;
    streetTable.summary.textContent =
        `${data.named_streets.toLocaleString()} streets go under, ${data.impassable_streets.toLocaleString()} ` +
        `past 30 cm. Soonest first; tap one to see it on the map.`;
    drawPonding();
    publishPondingRain();
}

// ─── Playback ──────────────────────────────────────────────────────────────────

function setStep(step) {
    pondingState.step = step;
    pondingUi.slider.value = String(step);
    drawPonding();
    publishPondingRain();
}

// The drains run on the same rain as the step on show (publishRain is in layers.js).
function publishPondingRain() {
    const data = pondingState.data;
    if (!data) return;
    const step = pondingState.step;
    const clock = clockAt(step);
    const when = `+${(step + 1) * data.rain.step_minutes} min${clock ? ` (${clock})` : ""}`;
    if (data.rain.mode === "scenario") {
        publishRain(data.rain.rain_mm_h[step], `Flood nowcast scenario, ${when} (hypothetical)`, "scenario");
    } else {
        publishRain(data.rain.rain_mm_h[step], `Flood nowcast forecast, ${when} (Open-Meteo)`, "forecast");
    }
}

function stopPlaying() {
    clearInterval(pondingState.timer);
    pondingState.timer = null;
    pondingUi.play.textContent = "▶";
    pondingUi.play.setAttribute("aria-label", "Play the forecast");
}

pondingUi.play.addEventListener("click", () => {
    if (pondingState.timer) return stopPlaying();
    if (!pondingState.data) return;
    const last = pondingState.data.rain.rain_mm_h.length - 1;
    if (pondingState.step >= last) setStep(0);
    pondingUi.play.textContent = "❚❚";
    pondingUi.play.setAttribute("aria-label", "Pause");
    pondingState.timer = setInterval(() => {
        if (pondingState.step >= last) return stopPlaying();
        setStep(pondingState.step + 1);
    }, 700);
});

// A drag fires input on every pixel; draw once per frame.
let pendingFrame = 0;
pondingUi.slider.addEventListener("input", () => {
    stopPlaying();
    cancelAnimationFrame(pendingFrame);
    pendingFrame = requestAnimationFrame(() => setStep(Number(pondingUi.slider.value)));
});

for (const select of [pondingUi.rain, pondingUi.drain, drainUi.zoneSelect, drainUi.wardSelect]) {
    select.addEventListener("change", () => {
        if (map.hasLayer(pondingLayer)) loadPonding();
    });
}

map.on("overlayadd", (event) => {
    if (event.layer !== pondingLayer) return;
    pondingUi.dock?.removeAttribute("hidden");
    streetTable.dock?.removeAttribute("hidden");
    loadPonding();
});
map.on("overlayremove", (event) => {
    if (event.layer !== pondingLayer) return;
    stopPlaying();
    window.shell?.kpis({});
    window.shell?.mode("wait", "Flood layer off");
    scenarioBanner.remove();
    // The drains were running on this panel's rain; with it gone, back to live.
    if (window.rainSource.label.startsWith("Flood nowcast")) backToLiveRain();
    pondingUi.dock?.setAttribute("hidden", "");
    streetTable.dock?.setAttribute("hidden", "");
    pondingState.request++;
    pondingLayer.clearLayers();
});

// The flood forecast is what the page is for: it runs as soon as the page opens, and
// the live one again every 15 minutes, when the rainfall feed moves on.
pondingLayer.addTo(map);
setInterval(() => {
    if (map.hasLayer(pondingLayer) && !pondingUi.rain.value && !pondingState.timer) loadPonding();
}, 15 * 60 * 1000);

// Zooming redraws: line widths follow the zoom, and manholes come and go at MANHOLE_ZOOM.
map.on("zoomend", () => { if (pondingState.data && map.hasLayer(pondingLayer)) drawPonding(); });
