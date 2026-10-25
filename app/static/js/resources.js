// Resources page: the ward maps as a catalogue. Zones down the side, a card per ward
// with a preview of its drains, pages of twelve, the wards needing attention, and a
// detail view with the full map. Built as nodes: nothing from the API is parsed as HTML.

(() => {
    const PER_PAGE = 12;
    const byId = (id) => document.getElementById(id);
    const grid = byId("wm-grid"), pages = byId("wm-pages"), count = byId("wm-count");
    const search = byId("wm-search"), mapOnly = byId("wm-map-only"), sort = byId("wm-sort");
    const zonesEl = byId("wm-zones"), rail = byId("wm-rail"), dialog = byId("wm-dialog");

    let wards = [], zones = [], zone = "", page = 1;

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const num = (v, d = 1) => Number(v).toLocaleString(undefined, { maximumFractionDigits: d });
    const zoneShort = (label) => (label || "").split("–").pop().trim() || "No zone";
    const poorShare = (w) => (w.drains ? w.poor / w.drains : 0);

    function preview(w, className, eager = false) {
        const box = el("div", className);
        if (w.preview_url) {
            const img = el("img");
            img.src = w.preview_url;
            img.alt = `Drain map of ward ${w.number}`;
            img.loading = eager ? "eager" : "lazy";
            img.decoding = "async";
            box.append(img);
        } else box.append(el("span", "wm-noimg", "Survey only · no base map"));
        box.append(el("span", "wm-chip", zoneShort(w.zone_label)));
        return box;
    }

    function actions(w) {
        const row = el("div", "wm-actions");
        const details = el("button", "wm-btn light", "Details");
        details.type = "button";
        details.addEventListener("click", () => openDetail(w));
        row.append(details);
        if (w.map_url) {
            const open = el("a", "wm-btn dark", "Open map");
            open.href = w.map_url; open.target = "_blank"; open.rel = "noopener";
            row.append(open);
        } else row.append(el("span", "wm-btn disabled", "No map"));
        return row;
    }

    function card(w) {
        const art = el("article", "wm-card");
        art.append(preview(w, "wm-img"));
        const head = el("div", "wm-card-head");
        head.append(el("h3", "", `Ward ${w.number}`), el("span", "wm-price", `${num(w.length_km)} km`));
        const meta = el("div", "wm-meta");
        meta.append(el("span", "", `${num(w.drains, 0)} drains`),
            el("span", w.poor ? "wm-poor" : "", w.poor ? `${num(w.poor, 0)} in poor condition` : "none in poor condition"));
        art.append(head, meta, actions(w));
        return art;
    }

    function filtered() {
        const q = search.value.trim().toLowerCase();
        // A number narrows by ward ("8" finds 8 and 80-89); anything else by zone.
        const hit = (w) => !q || (/^\d+$/.test(q) ? String(w.number).startsWith(q)
            : w.zone_label.toLowerCase().includes(q));
        const list = wards.filter((w) => (!mapOnly.checked || w.map_url) && (!zone || w.zone === zone) && hit(w));
        const key = {
            number: (a, b) => a.number - b.number,
            drains: (a, b) => b.drains - a.drains,
            poor: (a, b) => b.poor - a.poor,
            capacity: (a, b) => b.capacity_m3s - a.capacity_m3s,
        }[sort.value];
        return list.sort(key);
    }

    function drawZones() {
        const base = wards.filter((w) => !mapOnly.checked || w.map_url);
        const item = (code, label, n) => {
            const li = el("li");
            const b = el("button", "", "");
            b.type = "button";
            b.setAttribute("aria-pressed", String(zone === code));
            b.append(el("span", "", label), el("span", "wm-zn", String(n)));
            b.addEventListener("click", () => { zone = code; page = 1; drawZones(); draw(); });
            li.append(b);
            return li;
        };
        zonesEl.replaceChildren(item("", "All wards", base.length),
            ...zones.map((z) => [z, base.filter((w) => w.zone === z.code).length])
                .filter(([, n]) => n)
                .map(([z, n]) => item(z.code, zoneShort(z.label), n)));
    }

    function draw() {
        const list = filtered();
        const total = Math.max(1, Math.ceil(list.length / PER_PAGE));
        page = Math.min(page, total);
        const shown = list.slice((page - 1) * PER_PAGE, page * PER_PAGE);
        grid.replaceChildren(...(shown.length ? shown.map(card) : [el("p", "wm-empty", "No ward matches.")]));
        count.textContent = `${list.length} ward${list.length === 1 ? "" : "s"}` +
            (zone ? ` in ${zoneShort(zones.find((z) => z.code === zone)?.label)}` : "");

        const btn = (label, target, cls = "") => {
            const b = el("button", `wm-pg ${cls}`, label);
            b.type = "button";
            b.disabled = target < 1 || target > total || target === page;
            if (target === page) b.setAttribute("aria-current", "page");
            b.addEventListener("click", () => { page = target; draw(); grid.scrollIntoView({ behavior: "smooth", block: "start" }); });
            return b;
        };
        const nums = [];
        for (let i = 1; i <= total; i++) {
            if (i === 1 || i === total || Math.abs(i - page) <= 1) nums.push(btn(String(i), i, "n"));
            else if (nums.at(-1)?.textContent !== "…") nums.push(el("span", "wm-gap", "…"));
        }
        const mid = el("div", "wm-pg-nums");
        mid.append(...nums);
        pages.replaceChildren(btn("← Previous", page - 1, "edge"), mid, btn("Next →", page + 1, "edge"));
        pages.hidden = total <= 1;
    }

    function drawRail() {
        // Wards with enough drains for a share to mean something, the worst first.
        const picks = wards.filter((w) => w.map_url && w.drains >= 20 && w.poor)
            .sort((a, b) => poorShare(b) - poorShare(a)).slice(0, 10);
        rail.replaceChildren(...picks.map((w) => {
            const art = el("article", "wm-card wm-pick");
            // A sideways rail: lazy loading would wait for a scroll that never comes.
            art.append(preview(w, "wm-img", true));
            const head = el("div", "wm-card-head");
            head.append(el("h3", "", `Ward ${w.number}`), el("span", "wm-price wm-poor", `${Math.round(poorShare(w) * 100)}%`));
            art.append(head, el("div", "wm-meta", `${num(w.poor, 0)} of ${num(w.drains, 0)} drains in poor condition`), actions(w));
            return art;
        }));
    }

    function openDetail(w) {
        const body = byId("wm-d-body");
        const img = el("div", "wm-d-img");
        if (w.preview_url) {
            const i = el("img");
            i.src = w.preview_url; i.alt = `Drain map of ward ${w.number}`;
            img.append(i);
        }
        const info = el("div", "wm-d-info");
        const title = el("h2", "", `Ward ${w.number}`);
        title.id = "wm-d-title";
        info.append(el("span", "wm-chip static", w.zone_label || "No zone"), title);
        if (w.map_zone_label) info.append(el("p", "wm-flag", `The base map lists this ward in ${w.map_zone_label}.`));
        const stats = el("dl", "wm-d-stats");
        for (const [k, v] of [["Drains", num(w.drains, 0)], ["Drain length", `${num(w.length_km)} km`],
            ["Drain capacity", `${num(w.capacity_m3s)} m³/s`], ["Poor condition", num(w.poor, 0)]]) {
            const d = el("div");
            d.append(el("dt", "", k), el("dd", "", v));
            stats.append(d);
        }
        info.append(stats, el("p", "wm-d-note", w.map_url
            ? "Surveyed by GCC / SECON-JBA in December 2020: every manhole's level and every drain's size, read into the flood model."
            : "This ward has survey rows but no base-map sheet."));
        if (w.map_url) {
            const open = el("a", "wm-btn dark wide", "Open the full map (PDF)");
            open.href = w.map_url; open.target = "_blank"; open.rel = "noopener";
            info.append(open);
        }
        body.replaceChildren(img, info);
        dialog.showModal();
    }
    byId("wm-d-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

    byId("wm-search-form").addEventListener("submit", (e) => {
        e.preventDefault();
        page = 1; draw();
        grid.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    search.addEventListener("input", () => { page = 1; draw(); });
    mapOnly.addEventListener("change", () => { page = 1; drawZones(); draw(); });
    sort.addEventListener("change", () => { page = 1; draw(); });
    byId("wm-prev").addEventListener("click", () => rail.scrollBy({ left: -rail.clientWidth * 0.8, behavior: "smooth" }));
    byId("wm-next").addEventListener("click", () => rail.scrollBy({ left: rail.clientWidth * 0.8, behavior: "smooth" }));

    fetch("/data-collection/wards")
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((data) => {
            wards = data.wards;
            zones = data.zones;
            // The banner behind the title: a collage of real ward maps.
            byId("wm-collage").replaceChildren(...wards.filter((w) => w.preview_url).slice(0, 12).map((w) => {
                const i = el("img");
                i.src = w.preview_url; i.alt = ""; i.decoding = "async";
                return i;
            }));
            drawZones(); draw(); drawRail();
        })
        .catch((err) => { count.textContent = `Could not load the wards: ${err.message}`; });
})();
