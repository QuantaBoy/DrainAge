// Rain on the streets, minute by minute: which street goes under, when, how deep.
//
// The server runs the next 3 hours of rain through every hollow in the DEM in 5 minute
// steps (app/services/rain_ponding.py) and sends, for each street stretch, the ground
// under each 20 m point, the hollow it sits in, and the water level of every hollow at
// every step. Depth at a point at a step is level - ground, so the slider redraws any
// minute without asking the server again. Uses DEPTH_BANDS and depthColor from
// flood.js, el/opt/esc from drains.js.

const pondingLayer = L.layerGroup();
layerControl.addOverlay(pondingLayer, "Rain on streets, 5-min forecast (cm)");
const pondingRenderer = L.canvas({ padding: 0.5 });

const pondingState = { request: 0, data: null, step: 0, filter: "", shown: 300, sort: "soonest", timer: null };

const bandOf = (cm) => DEPTH_BANDS.findIndex((band) => cm >= band.min);

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
    return props.hollow.map((h, i) => {
        if (!h || props.ground_m[i] == null) return 0;
        return Math.max(0, (water[h][step] - props.ground_m[i]) * 100);
    });
}

// ─── Panel ─────────────────────────────────────────────────────────────────────

const pondingUi = (() => {
    const container = el("div", "flood-panel");
    const header = el("div", "drain-header");
    header.append(el("span", "drain-icon", "🌧️"), el("span", "drain-title", "Rain on streets · 5-min forecast"));
    const note = el("div", "flood-note", "Turn on the layer to run the forecast.");

    const rainLabel = el("label", "drain-label", "Rainfall");
    const rain = document.createElement("select");
    for (const [value, label] of SCENARIO_RATES) rain.append(opt(value, label));
    rainLabel.append(rain);
    // Live or scenario, in words and colour, at the top of the panel.
    const mode = el("div", "mode-badge");

    // How much of the surveyed drain capacity is working. The GCC survey gives each
    // drain's conveyance; what it does not record is how silted or blocked it is, and
    // that is what this asks.
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

    // The clock: a rain chart with the current step marked, the slider and play.
    const clock = el("div", "flood-clock", "—");
    const chart = el("div", "rain-bars");
    chart.setAttribute("aria-hidden", "true");
    const slider = document.createElement("input");
    Object.assign(slider, { type: "range", min: 0, max: 35, value: 0, step: 1 });
    slider.className = "flood-slider";
    slider.setAttribute("aria-label", "Minutes ahead, in 5 minute steps");
    const play = el("button", "chip play-button", "▶ Play");
    play.type = "button";

    const stats = el("div", "flood-stats");
    const upcoming = el("div", "upcoming");
    const legend = el("div", "drain-legend");
    for (const band of DEPTH_BANDS) {
        const item = el("span", "drain-legend-item");
        const swatch = el("i", "drain-swatch");
        swatch.style.background = band.color;
        item.append(swatch, band.label);
        legend.append(item);
    }
    const caveat = el("div", "flood-note",
        "Drainage is each hollow's surveyed outfall conveyance, which is a best case: " +
        "the survey does not record silting or gully inlets. 30 m DEM, rain taken as even " +
        "over the city; the order and timing streets flood in are sound, depths over ~1 m " +
        "are usually DEM error.");

    container.append(header, mode, note, controls, clock, chart, slider, play, stats, upcoming, legend, caveat);
    const dock = document.getElementById("ponding-panel");
    dock?.append(container);
    return { dock, mode, note, rain, drain, clock, chart, slider, play, stats, upcoming };
})();

// A banner across the map while a scenario is drawn, so a screenshot of it cannot be
// taken for a forecast.
const scenarioBanner = L.control({ position: "topleft" });
scenarioBanner.onAdd = () => el("div", "scenario-banner");

function showMode(data) {
    const live = data.rain.mode === "live";
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
    streetTable.title.textContent = live
        ? "Street flood forecast · every 5 minutes, next 3 hours"
        : `Street flood scenario (${data.rain.rain_mm_h[0]} mm/h, hypothetical) · every 5 minutes, 3 hours`;
    pondingState.kind = kind;
}

function drawRainChart() {
    const rates = pondingState.data.rain.rain_mm_h;
    const top = Math.max(10, ...rates);
    pondingUi.chart.replaceChildren(...rates.map((rate, i) => {
        const bar = el("i", i === pondingState.step ? "now" : i < pondingState.step ? "past" : "");
        bar.style.height = `${Math.max(2, (rate / top) * 100)}%`;
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
            map.flyTo(row.deepest, 18, { duration: 0.8 });
            map.getContainer().scrollIntoView({ behavior: "smooth", block: "center" });
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
        <table>
            <tr><td>Where</td><td>${esc(p.locality || "—")} · ward ${esc(p.ward || "—")} · zone ${esc(p.zone || "—")}</td></tr>
            <tr><td>Goes under</td><td><b>${whenLabel(p.floods_at_min)}</b></td></tr>
            <tr><td>Impassable</td><td>${p.impassable_at_min == null ? "never over 30 cm" : "from " + whenLabel(p.impassable_at_min)}</td></tr>
            <tr><td>Peak</td><td>${p.max_depth_cm.toFixed(0)} cm at ${whenLabel(p.peak_at_min)}</td></tr>
            <tr><td>Clears</td><td>${p.clears_at_min == null ? "still wet at 3 h" : whenLabel(p.clears_at_min)}</td></tr>
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
    for (const { depth, feature, points } of pieces) {
        const p = feature.properties;
        L.polyline(points.map(([lon, lat]) => [lat, lon]), {
            renderer: pondingRenderer,
            color: depthColor(depth),
            weight: depth >= data.thresholds.impassable_cm ? 7 : 5,
            opacity: 0.92,
            lineCap: "round",
        })
            .bindTooltip(() => tooltipFor(p, depth), { sticky: true, className: "drain-tooltip" })
            .bindPopup(() => stretchPopup(p, feature.geometry.coordinates),
                { maxWidth: 320, className: "drain-popup-container" })
            .addTo(pondingLayer);
    }

    const minute = (step + 1) * data.rain.step_minutes;
    const clock = clockAt(step);
    pondingUi.clock.textContent = `+${minute} min${clock ? ` · ${clock}` : ""}`;
    const counts = data.per_step[step];
    pondingUi.stats.replaceChildren(
        el("div", "drain-stat-line", `${counts.wet.toLocaleString()} named streets under water now · ` +
            `${counts.impassable.toLocaleString()} impassable`),
        el("div", "drain-stat-line", `Rain now ${data.rain.rain_mm_h[step]} mm/h · ${data.rain.total_mm} mm over 3 h`),
        el("div", "drain-stat-line", `Drains at ${Math.round(data.drain_condition * 100)}% of the GCC survey · ` +
            `${data.drainage.hollows_from_the_survey.toLocaleString()} hollows drained by surveyed drains, ` +
            `the rest at the surveyed rate (${data.drainage.surveyed_rate_mm_h} mm/h)`),
        el("div", "drain-stat-line", `By 3 h: ${data.named_streets.toLocaleString()} streets go under, ` +
            `${data.impassable_streets.toLocaleString()} impassable, ${data.wet_km} km of road`),
    );
    drawUpcoming();
    drawRainChart();
    drawTable();
}

// The headline: named streets that go under in the next 30 minutes, soonest first.
function drawUpcoming() {
    const data = pondingState.data;
    const nowMin = (pondingState.step + 1) * data.rain.step_minutes;
    const soon = data.streets
        .filter((r) => r.street && r.floods_at_min != null && r.floods_at_min > nowMin && r.floods_at_min <= nowMin + 30)
        .slice(0, 6);
    const head = el("div", "upcoming-head", soon.length
        ? "Going under in the next 30 minutes"
        : "No named street goes under in the next 30 minutes");
    pondingUi.upcoming.replaceChildren(head, ...soon.map((r) => {
        const item = el("button", "upcoming-item");
        item.type = "button";
        item.innerHTML = `<span><b>${esc(r.street)}</b> · ${esc(r.locality || r.ward || "")}</span>
            <span>${whenLabel(r.floods_at_min)}${r.impassable_at_min != null ? " · 30 cm by " +
                (clockAt(stepOfMinute(r.impassable_at_min)) || "+" + r.impassable_at_min + " min") : ""}</span>`;
        item.addEventListener("click", () => map.flyTo(r.deepest, 18, { duration: 0.8 }));
        return item;
    }));
}

// ─── Fetch ─────────────────────────────────────────────────────────────────────

async function loadPonding() {
    const id = ++pondingState.request;
    stopPlaying();
    pondingUi.note.textContent = "Running the rain through the DEM, 36 steps…";
    const params = new URLSearchParams();
    const centre = map.getCenter();
    params.set("lat", centre.lat.toFixed(4));
    params.set("lon", centre.lng.toFixed(4));
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
        if (id === pondingState.request) pondingUi.note.textContent = `Failed: ${err.message}`;
        return;
    }
    if (id !== pondingState.request) return;
    pondingState.data = data;
    pondingState.shown = 300;
    showMode(data);
    // Start at the first step anything is under water, so the map is not blank.
    const firstWet = data.per_step.findIndex((s) => s.wet > 0);
    pondingState.step = Math.max(0, firstWet);
    pondingUi.slider.max = String(data.rain.rain_mm_h.length - 1);
    pondingUi.slider.value = String(pondingState.step);
    pondingUi.note.textContent = data.stretches
        ? `${data.rain.source} · ${data.stretches.toLocaleString()} street stretches go under`
        : `${data.rain.source}: ${data.rain.total_mm} mm, no street goes under.`;
    streetTable.summary.textContent =
        `${data.rain.source}, ${data.rain.total_mm} mm in 3 h, drains at ` +
        `${Math.round(data.drain_condition * 100)}% of the GCC survey: ` +
        `${data.named_streets.toLocaleString()} named streets go under, ` +
        `${data.impassable_streets.toLocaleString()} past 30 cm. Soonest first; click a street to go to it.`;
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
        publishRain(data.rain.rain_mm_h[step], `Rain on streets scenario, ${when} (hypothetical)`, "scenario");
    } else {
        publishRain(data.rain.rain_mm_h[step], `Rain on streets forecast, ${when} (Open-Meteo)`, "forecast");
    }
}

function stopPlaying() {
    clearInterval(pondingState.timer);
    pondingState.timer = null;
    pondingUi.play.textContent = "▶ Play";
}

pondingUi.play.addEventListener("click", () => {
    if (pondingState.timer) return stopPlaying();
    if (!pondingState.data) return;
    const last = pondingState.data.rain.rain_mm_h.length - 1;
    if (pondingState.step >= last) setStep(0);
    pondingUi.play.textContent = "⏸ Pause";
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
    scenarioBanner.remove();
    // The drains were running on this panel's rain; with it gone, back to live.
    if (window.rainSource.label.startsWith("Rain on streets")) backToLiveRain();
    pondingUi.dock?.setAttribute("hidden", "");
    streetTable.dock?.setAttribute("hidden", "");
    pondingState.request++;
    pondingLayer.clearLayers();
});
