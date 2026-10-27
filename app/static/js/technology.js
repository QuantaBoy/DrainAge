// Technology page: the forecast engine on this device, and every resource checked on
// disk. Everything is built as nodes, so nothing from the API is parsed as HTML.

const byId = (id) => document.getElementById(id);

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

const num = (v, digits = 0) => Number(v).toLocaleString(undefined, { maximumFractionDigits: digits });
const pct = (v) => `${Math.round(v * 100)}%`;

function tile(label, value, note) {
    const box = el("div", "tech-tile");
    box.append(el("div", "tech-tile-label", label), el("div", "tech-tile-value", value));
    if (note) box.append(el("div", "tech-tile-note", note));
    return box;
}

function drawEngine(e) {
    const d = e.device;
    const tiles = [
        tile("This device", `${d.logical_cores} cores`,
            `${d.physical_cores} physical · ${num(d.total_mb / 1024, 1)} GB memory`),
        tile("Busy with other work now", `${Math.round(d.load_percent)}%`,
            `${num(d.available_mb / 1024, 1)} GB memory free`),
        tile("Storms simulated", num(e.storms_run),
            e.average_run_s ? `${e.average_run_s} s each on average` : "none yet"),
        tile("Served without simulating", e.served_without_simulating == null ? "—" : pct(e.served_without_simulating),
            `${num(e.cache_hits)} from cache · ${num(e.shared_waits)} shared a run`),
        tile("Computed ahead", num(e.precomputed),
            e.paused_refreshes ? `${e.paused_refreshes} refreshes waited for a busy engine` : "live + what-if storms"),
        tile("Answers kept ready", num(e.answers_cached), "compressed, sent as they are"),
    ];
    byId("engine-grid").replaceChildren(...tiles);
    byId("engine-live").textContent = `Live · updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;

}

function drawInventory(inv) {
    const rows = inv.resources.map((r) => {
        const tr = el("tr");
        const name = el("td");
        name.append(el("b", "", r.name));
        const source = el("td");
        if (r.link) {
            const a = el("a", "", r.provider);
            a.href = r.link; a.target = "_blank"; a.rel = "noopener";
            source.append(a);
        } else source.textContent = r.provider;
        const here = el("td", r.present ? "ok" : "missing");
        here.append(el("span", `tech-dot ${r.present ? "ok" : "missing"}`),
            document.createTextNode(r.present
                ? `${r.records ? r.records + " · " : ""}${r.size_mb} MB`
                : "not on this server"));
        tr.append(name, source, here, el("td", "muted", r.used_for));
        return tr;
    });
    byId("res-table").tBodies[0].replaceChildren(...rows);
    byId("res-live").textContent = `${inv.present} of ${inv.total} found · ${num(inv.total_mb, 1)} MB`;

    byId("services").replaceChildren(...inv.services.map((s) => {
        const li = el("li");
        const a = el("a", "", s.name);
        a.href = s.link; a.target = "_blank"; a.rel = "noopener";
        li.append(a, el("span", "", s.used_for));
        return li;
    }));

    const v = inv.validation;
    if (!v) {
        byId("check-lead").textContent = "The validation has not been run on this server (scripts/validate_history.py).";
        return;
    }
    byId("check-lead").textContent =
        `The model was run for three storms and scored against the ${v.points_2015} places that flooded in ` +
        `December 2015 and the city's flood hazard zones. "Found" means a flooded street within 250 m; ` +
        `"chance" is the same for random street points, so the skill is what the model knows beyond how much of ` +
        `the city is wet. Skill is measured on corporation zones that were not used to choose the settings.`;
    byId("check-table").tBodies[0].replaceChildren(...Object.entries(v.storms).map(([rate, s]) => {
        const tr = el("tr");
        tr.append(el("td", "", `${rate} mm/h for 3 h`), el("td", "", pct(s.hit_rate_2015)),
            el("td", "muted", pct(s.chance_rate)), el("td", "", `+${pct(s.skill_holdout)}`),
            el("td", "", `${s.hazard_enrichment}x as often`));
        return tr;
    }));
}

async function load(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

async function refreshEngine() {
    try {
        drawEngine(await load("/data-collection/engine"));
    } catch (err) {
        byId("engine-live").textContent = `Engine not reachable (${err.message})`;
    }
}

load("/data-collection/inventory").then(drawInventory).catch((err) => {
    byId("res-live").textContent = `Could not check the files (${err.message})`;
});
refreshEngine();
setInterval(refreshEngine, 5000);

// ─── Model prediction ────────────────────────────────────────────────────────────

const SERIES = [   // observed first, in ink; models in fixed categorical order
    { key: "observed", label: "Rain that fell", color: "var(--ink)", width: 2.5 },
    { key: "convlstm", label: "ConvLSTM", color: "#2a78d6", width: 2 },
    { key: "gru", label: "GRU", color: "#1baf7a", width: 2 },
    { key: "persistence", label: "Persistence", color: "#eb6834", width: 2, dash: "5 4" },
];
const SVG = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
};
const fmt = (v, d = 2) => Number(v).toFixed(d);
const hourDate = (iso) => new Date(`${iso.slice(0, 13)}:00`);   // "2023-12-01T05", local time

function drawModelNow(live, report) {
    const box = byId("model-now");
    if (!live) {
        box.replaceChildren(el("p", "model-note", "The live model forecast is not available right now; the flood "
            + "simulation falls back to the Open-Meteo rain forecast."));
        return;
    }
    const chosen = report.models[report.chosen].name;
    const head = el("div", "model-now-head");
    head.append(el("b", "", `Next 3 hours at Chennai · ${chosen}, ensemble of ${report.ensemble}`),
        el("span", "", `from the last ${live.made_from.length} hours of rain, ending ${live.made_from.at(-1).slice(11, 16)}`));
    const tiles = el("div", "model-tiles");
    live.hours.forEach((hour, i) => {
        const tile = el("div", "model-tile");
        tile.append(el("span", "model-tile-when", `${hour.slice(11, 16)} (+${i + 1} h)`),
            el("span", "model-tile-value", `${fmt(live.rain_mm_h[i], 1)} mm/h`),
            el("span", "model-tile-spread", `± ${fmt(live.spread_mm_h[i], 1)} between the ${report.ensemble} models`));
        tiles.append(tile);
    });
    box.replaceChildren(head, tiles,
        el("p", "model-note", "This forecast is the rain the live flood simulation runs on (Dashboard → Alerts)."));
}

function drawModelTable(report) {
    const order = ["convlstm", "gru", "lstm", "ridge", "persistence"].filter((k) => report.models[k]);
    const best = (metric, low) => order.reduce((a, b) =>
        (low ? report.models[b][metric] < report.models[a][metric] : report.models[b][metric] > report.models[a][metric]) ? b : a);
    const wins = {
        rmse_mm_h: best("rmse_mm_h", true), city_3h_total_mae_mm: best("city_3h_total_mae_mm", true),
        "csi_0.5": best("csi_0.5", false), "csi_2.0": best("csi_2.0", false),
    };
    byId("model-table").tBodies[0].replaceChildren(...order.map((key) => {
        const m = report.models[key];
        const tr = el("tr", key === report.chosen ? "chosen" : "");
        const name = el("td");
        name.append(el("b", "", m.name));
        if (key === report.chosen) name.append(el("span", "model-badge", "Live model"));
        tr.append(name);
        for (const [metric, digits] of [["rmse_mm_h", 3], ["city_3h_total_mae_mm", 2], ["csi_0.5", 3], ["csi_2.0", 3]]) {
            tr.append(el("td", wins[metric] === key ? "win" : "", fmt(m[metric], digits)));
        }
        return tr;
    }));
    const d = report.data;
    byId("model-test-note").textContent = `Scored on ${d.test_windows.toLocaleString()} rainy hours of the `
        + `${d.test_seasons.join(" and ")} monsoons (the November 2021 floods and Cyclone Michaung), which no model `
        + `trained on. The live model was picked on the ${d.validation_season} season, before these scores were seen; `
        + `best in each column in bold.`;
    byId("model-data-note").textContent = `Data: ${d.source}; ${d.grid}; ${d.seasons}. Every model reads the last `
        + `${d.input_hours} hours and forecasts the next ${d.output_hours}. ERA5 smooths the heaviest bursts, so these `
        + `are area rain rates, not rain-gauge peaks. The live model was then retrained on every season, flood years included.`;
}

function drawModelChart(report) {
    const c = report.michaung;
    const series = SERIES.filter((s) => s.key === "observed" || c.models[s.key])
        .map((s) => ({ ...s, values: s.key === "observed" ? c.observed_3h_ahead : c.models[s.key] }));
    const n = c.made.length;
    // Drawn at the width it is shown, so the text stays 11px; narrow screens rely on the legend, not end labels.
    const W = Math.max(320, byId("model-chart").clientWidth || 920), narrow = W < 600;
    const H = narrow ? 240 : 300, pad = { l: 40, r: narrow ? 12 : 104, t: 16, b: 34 };
    const top = Math.ceil(Math.max(1, ...series.flatMap((s) => s.values)));
    const x = (i) => pad.l + (i / (n - 1)) * (W - pad.l - pad.r);
    const y = (v) => H - pad.b - (v / top) * (H - pad.t - pad.b);
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "model-svg" });
    const stepY = Math.max(1, Math.round(top / 4));
    for (let v = 0; v <= top; v += stepY) {
        svg.append(svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "grid" }));
        const t = svgEl("text", { x: pad.l - 8, y: y(v) + 4, class: "tick", "text-anchor": "end" });
        t.textContent = v;
        svg.append(t);
    }
    const unit = svgEl("text", { x: pad.l - 8, y: pad.t - 4, class: "tick", "text-anchor": "end" });
    unit.textContent = "mm/h";
    svg.append(unit);
    c.made.forEach((made, i) => {
        if (made.slice(11, 13) !== "00" || (narrow && Number(made.slice(8, 10)) % 2 === 0)) return;
        const t = svgEl("text", { x: x(i), y: H - 12, class: "tick", "text-anchor": "middle" });
        t.textContent = hourDate(made).toLocaleDateString(undefined, { day: "numeric", month: "short" });
        svg.append(t);
    });
    const labels = [];
    for (const s of series) {
        const d = s.values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
        svg.append(svgEl("path", { d, fill: "none", stroke: s.color, "stroke-width": s.width,
            "stroke-dasharray": s.dash || "", "stroke-linejoin": "round", "stroke-linecap": "round" }));
        labels.push({ s, y: y(s.values.at(-1)) });
    }
    // Direct labels at the right edge, nudged apart so they never overlap.
    labels.sort((a, b) => a.y - b.y);
    labels.forEach((l, i) => { if (i && l.y - labels[i - 1].y < 13) l.y = labels[i - 1].y + 13; });
    for (const l of narrow ? [] : labels) {
        const t = svgEl("text", { x: W - pad.r + 8, y: l.y + 4, class: "series-label" });
        t.textContent = l.s.label;
        svg.append(t);
    }
    const hair = svgEl("line", { y1: pad.t, y2: H - pad.b, class: "hair", visibility: "hidden" });
    svg.append(hair);
    const box = byId("model-chart");
    const tip = el("div", "model-tip");
    tip.hidden = true;
    box.replaceChildren(svg, tip);
    svg.addEventListener("pointermove", (event) => {
        const r = svg.getBoundingClientRect();
        const px = (event.clientX - r.left) * (W / r.width);
        const i = Math.max(0, Math.min(n - 1, Math.round((px - pad.l) / (W - pad.l - pad.r) * (n - 1))));
        hair.setAttribute("x1", x(i)); hair.setAttribute("x2", x(i)); hair.setAttribute("visibility", "visible");
        // The forecast for 3 hours after it was made.
        const when = hourDate(c.made[i]);
        when.setHours(when.getHours() + 3);
        tip.replaceChildren(el("div", "model-tip-head", when.toLocaleString(undefined,
            { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })));
        for (const s of series) {
            const row = el("div", "model-tip-row");
            const key = el("span", "model-key");
            key.style.borderTop = `${s.width}px ${s.dash ? "dashed" : "solid"} ${s.color}`;
            row.append(key, el("b", "", `${fmt(s.values[i], 2)} mm/h`), el("span", "", s.label));
            tip.append(row);
        }
        tip.hidden = false;
        tip.style.left = `${Math.max(0, Math.min((x(i) / W) * r.width + 12, r.width - 200))}px`;
    });
    svg.addEventListener("pointerleave", () => { tip.hidden = true; hair.setAttribute("visibility", "hidden"); });
    byId("model-legend").replaceChildren(...series.map((s) => {
        const item = el("span", "model-legend-item");
        const key = el("span", "model-key");
        key.style.borderTop = `${s.width}px ${s.dash ? "dashed" : "solid"} ${s.color}`;
        item.append(key, document.createTextNode(s.label));
        return item;
    }));
}

load("/data-collection/rain-model").then(({ report, live }) => {
    if (!report) throw new Error("no trained model on this server");
    byId("model-live").textContent = `Trained ${report.trained} · live model: ${report.models[report.chosen].name}`;
    drawModelNow(live, report);
    drawModelTable(report);
    drawModelChart(report);
}).catch((err) => {
    byId("model-live").textContent = `Model not available (${err.message})`;
});
