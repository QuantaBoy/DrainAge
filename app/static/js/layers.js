// Weather overlays on the shared map. Tiles come through our own proxy, never
// straight from OpenWeather, so the API key is not exposed to the browser.

const WEATHER_LAYERS = [
    ["Rain", "rain"],
    ["Temperature", "temp"],
    ["Wind", "wind"],
    ["Clouds", "clouds"],
];

const weatherOverlays = {};
for (const [label, layer] of WEATHER_LAYERS) {
    weatherOverlays[label] = L.tileLayer(`/data-collection/tiles/${layer}/{z}/{x}/{y}.png`, {
        opacity: 0.6,
        // OpenWeather has no tiles past zoom 12; scaling those up keeps the overlays
        // usable when the map zooms in on a location, instead of Leaflet disabling them.
        maxNativeZoom: 12,
        maxZoom: 19,
        attribution: "Weather &copy; OpenWeather",
    });
}

L.control.layers(null, weatherOverlays, { collapsed: false }).addTo(map);
