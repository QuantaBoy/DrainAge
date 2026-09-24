// Shared flood styling, and the elevation layer the flood model runs on.
//
// The flood itself is drawn by ponding.js from the coupled terrain-and-drains model;
// this file holds what that layer and the route panel share: the depth bands, the
// rainfall choices, and the DEM relief.

// Depth bands, in centimetres: what the water does to a street at each one.
const DEPTH_BANDS = [
    { min: 30, color: "#7b1fa2", label: "Over 30 cm — impassable" },
    { min: 15, color: "#c62828", label: "15–30 cm — cars stall" },
    { min: 5, color: "#ef6c00", label: "5–15 cm — standing water" },
    { min: 0, color: "#f9a825", label: "Under 5 cm — wet" },
];

// Rainfall choices: the live feed, or a rate held for 3 hours.
const SCENARIO_RATES = [
    ["", "Live forecast (Open-Meteo)"],
    ...[15, 30, 60, 100].map((rate) =>
        [String(rate), `Scenario: ${rate} mm/h for 3 h (${rate * 3} mm total)`]),
];

function depthColor(cm) {
    return (DEPTH_BANDS.find((band) => cm >= band.min) || DEPTH_BANDS.at(-1)).color;
}

// ─── Elevation: the ground the water runs over ─────────────────────────────────
// Drawn once by the server from the DEM: colour for height, shading for which way the
// ground falls. It is what the flood model reads, so the two can be checked by eye:
// flooded streets should sit in the blues.

const elevationLayer = L.layerGroup();
layerControl.addOverlay(elevationLayer, "Elevation (DEM)");

const elevationLegend = L.control({ position: "bottomright" });
elevationLegend.onAdd = () => {
    const box = el("div", "elev-legend");
    box.append(el("div", "elev-title", "Ground height (m)"));
    box.append(el("div", "elev-bar"), el("div", "elev-ticks"));
    L.DomEvent.disableClickPropagation(box);
    return box;
};

let elevationInfo = null;

async function showElevation() {
    if (!elevationInfo) {
        const resp = await fetch("/data-collection/elevation");
        if (!resp.ok) return;
        elevationInfo = await resp.json();
        const image = L.imageOverlay(elevationInfo.image, elevationInfo.bounds, {
            opacity: 0.75, interactive: false,
            attribution: `DEM: ${esc(elevationInfo.source.name || "unknown")}`,
        });
        elevationLayer.addLayer(image);
    }
    elevationLegend.addTo(map);
    const box = elevationLegend.getContainer();
    const stops = elevationInfo.stops;
    const low = stops[0].m, high = stops.at(-1).m;
    // The ramp is uneven on purpose - most colours sit in the bottom 10 m - so the bar
    // shows the stops at equal widths and labels each one.
    box.querySelector(".elev-bar").style.background =
        `linear-gradient(90deg, ${stops.map((s) => s.color).join(", ")})`;
    box.querySelector(".elev-ticks").replaceChildren(...stops.map((s) => el("span", "", String(s.m))));
    const source = elevationInfo.source;
    const kind = source.kind === "terrain" ? "bare earth" : "surface, roofs included";
    box.querySelector(".elev-title").textContent = `Ground height (m) · ${source.name}, ${kind}`;
    box.title = `Range ${elevationInfo.range_m[0]} to ${elevationInfo.range_m[1]} m ` +
        `(${low} to ${high} m shown). ${source.files.join(", ")}`;
}

map.on("overlayadd", (event) => {
    if (event.layer === elevationLayer) showElevation();
});
map.on("overlayremove", (event) => {
    if (event.layer === elevationLayer) elevationLegend.remove();
});
