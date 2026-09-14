// Checkbox overlays on the shared map. Tiles come from our own proxy, never
// straight from OpenWeather, so the API key is not exposed to the browser.
const OVERLAYS = [
    ["Rain", "rain"],
    ["Temperature", "temp"],
    ["Wind", "wind"],
    ["Clouds", "clouds"],
];

const overlays = {};
for (const [label, layer] of OVERLAYS) {
    overlays[label] = L.tileLayer(`/data-collection/tiles/${layer}/{z}/{x}/{y}.png`, {
        opacity: 0.6,
        maxZoom: 12,   // OpenWeather stops producing weather tiles past this zoom
        attribution: "Weather &copy; OpenWeather",
    });
}

L.control.layers(null, overlays, { collapsed: false }).addTo(map);
