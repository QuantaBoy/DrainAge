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
