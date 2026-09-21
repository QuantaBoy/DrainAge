// Resources page: the wards behind the model, as a filterable grid of cards.

const gridEl = document.getElementById("ward-grid");
const searchEl = document.getElementById("ward-search");
const zoneEl = document.getElementById("ward-zone");
const mapOnlyEl = document.getElementById("ward-map-only");
const countEl = document.getElementById("ward-count");

let wards = [];

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

const num = (value, digits = 1) => value.toLocaleString(undefined, { maximumFractionDigits: digits });

// Built as nodes, not markup, so nothing from the API is ever parsed as HTML.
function card(ward) {
    const article = el("article", "ward-card");

    const title = el("div");
    title.append(el("div", "ward-label", "Ward"), el("div", "ward-no", String(ward.number)));
    const top = el("div", "ward-top");
    top.append(title, el("span", `ward-badge ${ward.map_url ? "has" : "none"}`,
        ward.map_url ? "Base map" : "Survey only"));
    article.append(top, el("div", "ward-zone", ward.zone_label));

    if (ward.map_zone_label) {
        article.append(el("p", "ward-flag", `The base map lists this ward in ${ward.map_zone_label}.`));
    }

    if (ward.drains === 0) {
        article.append(el("p", "ward-none", "No drains for this ward in the survey."));
    } else {
        const stats = el("dl", "ward-stats");
        for (const [label, value] of [
            ["Drains", num(ward.drains, 0)],
            ["Length", `${num(ward.length_km)} km`],
            ["Capacity", `${num(ward.capacity_m3s)} m³/s`],
            ["Poor condition", num(ward.poor, 0)],
        ]) {
            const cell = el("div");
            cell.append(el("dt", undefined, label), el("dd", undefined, value));
            stats.append(cell);
        }
        article.append(stats);
    }

    if (ward.map_url) {
        const link = el("a", "ward-map", "Open base map (PDF)");
        link.href = ward.map_url;
        link.target = "_blank";
        link.rel = "noopener";
        article.append(link);
    } else {
        article.append(el("span", "ward-nomap", "No base-map sheet"));
    }
    return article;
}

function render() {
    const query = searchEl.value.trim().toLowerCase();
    // A number narrows by ward ("8" finds 8 and 80-89, not 108); anything else by zone.
    const matches = (ward) => !query
        || (/^\d+$/.test(query) ? String(ward.number).startsWith(query)
            : ward.zone_label.toLowerCase().includes(query));

    const shown = wards.filter((ward) =>
        (!mapOnlyEl.checked || ward.map_url) && (!zoneEl.value || ward.zone === zoneEl.value) && matches(ward));

    gridEl.replaceChildren(...(shown.length ? shown.map(card) : [el("p", "ward-empty", "No ward matches.")]));
    countEl.textContent = `${shown.length} of ${wards.length} wards`;
}

async function load() {
    try {
        const resp = await fetch("/data-collection/wards");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        wards = data.wards;
        for (const zone of data.zones) {
            const option = el("option", undefined, zone.label);
            option.value = zone.code;
            zoneEl.append(option);
        }
    } catch (err) {
        gridEl.replaceChildren(el("p", "ward-empty warn", `Could not load the wards: ${err.message}`));
        return;
    }
    render();
}

for (const control of [searchEl, zoneEl, mapOnlyEl]) {
    control.addEventListener(control === searchEl ? "input" : "change", render);
}
load();
