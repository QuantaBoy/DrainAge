// Flood-safe route: a start and a destination, typed or picked on the map, and the
// route round the water the forecast puts on the streets.
//
// The server (app/routes/navigation.py) costs every road by its forecast depth for
// the trip and runs Dijkstra and A* on the street graph. This draws the safe route,
// the plain shortest route beside it, and the flooded stretches the shortest one
// would have driven through. The rainfall and drain settings are the flood nowcast
// panel's, so the route and the flood map always describe the same storm. Uses
// el/opt/esc (drains.js), depthColor (flood.js) and pondingUi (ponding.js).

const routeLayer = L.layerGroup().addTo(map);
const routeState = { request: 0, picking: null };

// ─── Panel ─────────────────────────────────────────────────────────────────────

const routeUi = (() => {
    const container = el("div", "flood-panel route-panel");
    const header = el("div", "drain-header");
    header.append(el("span", "drain-icon", "🧭"), el("span", "drain-title", "Flood-safe route"));
    const note = el("div", "flood-note",
        "Type a place or pick it on the map. The route goes round water over 30 cm and " +
        "avoids shallower water where a dry road is not much longer.");

    // One field: a place-name box, a button to pick on the map, and what it resolved to.
    const field = (label, placeholder) => {
        const wrap = el("div", "route-field");
        const title = el("label", "drain-label", label);
        const row = el("div", "route-row");
        const input = document.createElement("input");
        Object.assign(input, { type: "search", placeholder, autocomplete: "off" });
        input.setAttribute("aria-label", label);
        const pick = el("button", "chip route-pick", "📍 Map");
        pick.type = "button";
        pick.title = `Pick the ${label.toLowerCase()} on the map`;
        row.append(input, pick);
        const found = el("div", "route-found");
        const choices = el("div", "route-choices");
        title.append(row);
        wrap.append(title, found, choices);
        return { wrap, input, pick, found, choices, point: null };
    };
    const from = field("From", "e.g. T Nagar, or pick on the map");
    const to = field("To", "e.g. Velachery, Chennai Central");

    const here = el("button", "chip route-here", "Use my selected location as start");
    here.type = "button";

    const leaveLabel = el("label", "drain-label", "Leave");
    const leave = document.createElement("select");
    for (const [value, label] of [["0", "Now"], ["30", "In 30 min"], ["60", "In 1 hour"],
        ["90", "In 1 h 30"], ["120", "In 2 hours"], ["150", "In 2 h 30"]]) leave.append(opt(value, label));
    leaveLabel.append(leave);

    const algoLabel = el("label", "drain-label", "Search");
    const algorithm = document.createElement("select");
    algorithm.append(opt("astar", "A* (guided by straight-line distance)"),
        opt("dijkstra", "Dijkstra (explores outward evenly)"));
    algoLabel.append(algorithm);

    const go = el("button", "route-go", "Find safe route");
    go.type = "button";
    const clear = el("button", "chip", "Clear");
    clear.type = "button";
    const actions = el("div", "route-actions");
    actions.append(go, clear);

    const result = el("div", "route-result");

    const controls = el("div", "drain-filters");
    controls.append(from.wrap, to.wrap, here, leaveLabel, algoLabel);
    container.append(header, note, controls, actions, result);
    document.getElementById("route-panel")?.append(container);
    return { from, to, here, leave, algorithm, go, clear, result, note };
})();

// ─── Places ────────────────────────────────────────────────────────────────────

function setPoint(field, lat, lon, label) {
    field.point = { lat, lon };
    field.found.textContent = `✓ ${label}`;
    field.choices.replaceChildren();
}

async function resolve(field) {
    if (field.point) return field.point;
    const q = field.input.value.trim();
    if (q.length < 2) throw new Error("Type a place, or pick it on the map");
    field.found.textContent = "Searching…";
    const resp = await fetch(`/data-collection/geocode?q=${encodeURIComponent(q)}`);
    if (!resp.ok) throw new Error(`Place search failed (HTTP ${resp.status})`);
    const { results } = await resp.json();
    if (!results.length) {
        field.found.textContent = "";
        throw new Error(`No place called “${q}” found in Chennai`);
    }
    const [best, ...others] = results;
    setPoint(field, best.lat, best.lon, best.name);
    // The other matches stay offered, in case the first was the wrong one.
    field.choices.replaceChildren(...others.slice(0, 4).map((place) => {
        const button = el("button", "route-choice", place.name);
        button.type = "button";
        button.addEventListener("click", () => {
            setPoint(field, place.lat, place.lon, place.name);
            findRoute();
        });
        return button;
    }));
    return field.point;
}

for (const field of [routeUi.from, routeUi.to]) {
    // Typing again means a new place: forget the one resolved before.
    field.input.addEventListener("input", () => {
        field.point = null;
        field.found.textContent = "";
        field.choices.replaceChildren();
    });
    field.input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") findRoute();
    });
    field.pick.addEventListener("click", () => {
        routeState.picking = field;
        window.routePicking = true;
        map.getContainer().classList.add("route-picking");
        field.found.textContent = "Click the map…";
    });
}

map.on("click", (event) => {
    const field = routeState.picking;
    if (!field) return;
    const { lat, lng } = event.latlng;
    field.input.value = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    setPoint(field, lat, lng, "picked on the map");
    routeState.picking = null;
    window.routePicking = false;
    map.getContainer().classList.remove("route-picking");
});

routeUi.here.addEventListener("click", () => {
    if (!marker) {
        routeUi.from.found.textContent = "Set a location first (Use My Location, or click the map)";
        return;
    }
    const { lat, lng } = marker.getLatLng();
    routeUi.from.input.value = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    setPoint(routeUi.from, lat, lng, "your selected location");
});

// ─── Route ─────────────────────────────────────────────────────────────────────

async function findRoute() {
    const id = ++routeState.request;
    routeUi.result.replaceChildren(el("div", "flood-note", "Finding the route…"));
    let start, end;
    try {
        [start, end] = await Promise.all([resolve(routeUi.from), resolve(routeUi.to)]);
    } catch (err) {
        if (id === routeState.request) routeUi.result.replaceChildren(el("div", "route-error", err.message));
        return;
    }

    const params = new URLSearchParams({
        from_lat: start.lat.toFixed(6), from_lon: start.lon.toFixed(6),
        to_lat: end.lat.toFixed(6), to_lon: end.lon.toFixed(6),
        leave_in_min: routeUi.leave.value,
        algorithm: routeUi.algorithm.value,
        // Same storm as the flood map: the flood nowcast panel's settings.
        drain_condition: pondingUi.drain.value,
    });
    if (pondingUi.rain.value) params.set("rain_mm_h", pondingUi.rain.value);

    let data;
    try {
        const resp = await fetch(`/data-collection/route?${params}`);
        if (!resp.ok) {
            const body = await resp.json().catch(() => ({}));
            throw new Error(body.detail || `HTTP ${resp.status}`);
        }
        data = await resp.json();
    } catch (err) {
        if (id === routeState.request) routeUi.result.replaceChildren(el("div", "route-error", `Route failed: ${err.message}`));
        return;
    }
    if (id !== routeState.request) return;
    drawRoute(data, start, end);
}

const lineOf = (geometry) => geometry.coordinates.map(([lon, lat]) => [lat, lon]);

function drawRoute(data, start, end) {
    routeLayer.clearLayers();
    const safe = data.safe_route;
    const plain = data.shortest_route;

    // The plain shortest route underneath, dashed, and its flooded stretches on it.
    if (plain) {
        L.polyline(lineOf(plain.geometry), { color: "#6b7488", weight: 4, opacity: 0.8, dashArray: "6 8" })
            .bindTooltip(`Shortest route, ignoring water: ${plain.length_km} km`, { sticky: true })
            .addTo(routeLayer);
    }
    for (const stretch of data.shortest_route_flooded.features) {
        const p = stretch.properties;
        L.polyline(lineOf(stretch.geometry), { color: depthColor(p.depth_cm), weight: 8, opacity: 0.95 })
            .bindTooltip(`${esc(p.name || "Unnamed road")}: ${p.depth_cm.toFixed(0)} cm` +
                (p.impassable ? " — impassable" : "") + " (on the shortest route)", { sticky: true })
            .addTo(routeLayer);
    }
    if (safe) {
        L.polyline(lineOf(safe.geometry), { color: "#fff", weight: 9, opacity: 0.9 }).addTo(routeLayer);
        L.polyline(lineOf(safe.geometry), { color: "#1565c0", weight: 5, opacity: 1 })
            .bindTooltip(`Flood-safe route: ${safe.length_km} km, ~${safe.minutes} min`, { sticky: true })
            .addTo(routeLayer);
    }
    const pin = (point, label, color) => L.circleMarker([point.lat, point.lon], {
        radius: 8, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1,
    }).bindTooltip(label).addTo(routeLayer);
    pin(start, "Start", "#1b8f4d");
    pin(end, "Destination", "#c62828");
    const bounds = L.latLngBounds([[start.lat, start.lon], [end.lat, end.lon]]);
    if (safe) bounds.extend(L.polyline(lineOf(safe.geometry)).getBounds());
    map.fitBounds(bounds, { padding: [30, 30] });

    // The card: the route, what it avoided, and how the two searches compared.
    const w = data.trip_window;
    const when = w.from_clock ? `${w.from_clock}–${w.to_clock}` : `+${w.from_min} to +${w.to_min} min`;
    const lines = [];
    const mode = data.rain.mode === "scenario" ? "Scenario (hypothetical rain)" : "Live forecast";
    lines.push(el("div", `mode-badge ${data.rain.mode === "scenario" ? "scenario" : "live"}`,
        `${mode} · water on the roads ${when}`));
    if (safe) {
        const card = el("div", "route-card safe");
        card.innerHTML = `<b>Flood-safe route</b> · ${safe.length_km} km · ~${safe.minutes} min
            <div>Deepest water on it: <b>${safe.deepest_cm.toFixed(0)} cm</b>${safe.deepest_cm < data.thresholds.wet_cm ? " (dry)" : ""}</div>
            ${safe.roads.length ? `<div class="route-via">via ${safe.roads.slice(0, 6).map(esc).join(" → ")}</div>` : ""}`;
        lines.push(card);
    } else {
        lines.push(el("div", "route-error", data.no_safe_route));
    }
    if (plain) {
        const a = data.avoided;
        const card = el("div", "route-card plain");
        card.innerHTML = `<b>Shortest route</b>, ignoring water · ${plain.length_km} km
            <div>${a.flooded_km_on_shortest > 0
                ? `crosses <b>${a.flooded_km_on_shortest} km</b> of flooded road, deepest ${plain.deepest_cm.toFixed(0)} cm` +
                  (a.impassable_stretches_on_shortest ? `, <b>${a.impassable_stretches_on_shortest} impassable</b>` : "")
                : "is dry: the safe route is the shortest"}</div>
            ${a.extra_km ? `<div>The safe route adds ${a.extra_km} km to avoid it.</div>` : ""}`;
        lines.push(card);
    }
    const s = data.search;
    const search = el("div", "route-card search");
    search.innerHTML = `<b>Search</b> (${data.algorithm === "astar" ? "A*" : "Dijkstra"} drawn)
        <div>A*: ${s.astar.settled_junctions.toLocaleString()} junctions explored, ${s.astar.ms} ms</div>
        <div>Dijkstra: ${s.dijkstra.settled_junctions.toLocaleString()} junctions explored, ${s.dijkstra.ms} ms</div>
        <div class="route-via">${s.astar.cost_km === s.dijkstra.cost_km
            ? `Same route (cost ${s.astar.cost_km} km-equivalent); A* explored ${Math.round(s.dijkstra.settled_junctions / Math.max(1, s.astar.settled_junctions))}× fewer junctions.`
            : "The two searches disagree — this should not happen."}</div>`;
    lines.push(search);
    routeUi.result.replaceChildren(...lines);
}

routeUi.go.addEventListener("click", findRoute);
routeUi.clear.addEventListener("click", () => {
    ++routeState.request;
    routeLayer.clearLayers();
    routeUi.result.replaceChildren();
    for (const field of [routeUi.from, routeUi.to]) {
        field.input.value = "";
        field.point = null;
        field.found.textContent = "";
        field.choices.replaceChildren();
    }
});
