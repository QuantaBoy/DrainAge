// Device location on the shared map, reported with its real accuracy.

const GOOD_FIX_M = 100;      // GPS-grade: stop refining
const USABLE_FIX_M = 2000;
const GPS_LOCK_MS = 45000;   // a cold GPS lock takes tens of seconds

const statusEl = document.getElementById("location-status");
let watchId = null;
let deadlineTimer = null;
let marker = null;
let accuracyCircle = null;

const map = L.map("map").setView([20, 0], 2);

// The class is what dark mode inverts in CSS: only the base tiles, never the weather
// overlays drawn on top of them.
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    className: "basemap",
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

// Automatic map moves may pan or zoom in, but never zoom out: a user who has zoomed
// in to read streets should not lose that view because something loaded.
function focusMap(latlng, zoom) {
    map.setView(latlng, Math.max(map.getZoom(), zoom));
}

function describeAccuracy(metres) {
    if (metres <= GOOD_FIX_M) return { label: "GPS-grade", color: "#137333" };
    if (metres <= USABLE_FIX_M) return { label: "Wi-Fi fix, not GPS", color: "#8a4b00" };
    return { label: "very rough, likely IP based", color: "#b3261e" };
}

function formatDistance(metres) {
    return metres >= 1000 ? `${(metres / 1000).toFixed(1)} km` : `${Math.round(metres)} m`;
}

function setStatus(text, color) {
    const span = document.createElement("span");
    span.textContent = text;
    if (color) span.style.color = color;
    statusEl.replaceChildren(span);
}

function warnStatus(text) {
    const span = document.createElement("span");
    span.className = "warn";
    span.textContent = text;
    statusEl.replaceChildren(span);
}

function clearMarker() {
    if (marker) map.removeLayer(marker);
    if (accuracyCircle) map.removeLayer(accuracyCircle);
    marker = null;
    accuracyCircle = null;
}

function stopWatching() {
    if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    clearTimeout(deadlineTimer);
}

function showFix(lat, lon, accuracy, final) {
    const quality = describeAccuracy(accuracy);
    const distance = formatDistance(accuracy);

    setStatus(
        `${final ? "" : "Refining… "}${lat.toFixed(5)}, ${lon.toFixed(5)} ` +
        `±${distance} (${quality.label})`,
        quality.color,
    );
    // A Wi-Fi fix reports optimistic accuracy yet resolves to whichever mapped network
    // it saw, which can be kilometres off, so anything short of GPS-grade is flagged.
    if (final && accuracy > GOOD_FIX_M) {
        const note = document.createElement("span");
        note.className = "warn";
        note.textContent = "No GPS on this device, so this is the location of a nearby " +
            "network, and the real error can exceed the circle. Click your actual " +
            "position on the map, or open this page on a phone with GPS.";
        statusEl.append(document.createElement("br"), note);
    }

    clearMarker();
    marker = L.marker([lat, lon]).addTo(map);
    accuracyCircle = L.circle([lat, lon], {
        radius: accuracy, color: quality.color, weight: 1, fillOpacity: 0.08,
    }).addTo(map).bindPopup(`Reported accuracy: ±${distance}`);

    // The true position lies somewhere inside the circle, so frame that area rather
    // than zooming to a point the fix cannot support.
    focusMap([lat, lon], Math.min(map.getBoundsZoom(accuracyCircle.getBounds()), 17));

    if (final) weatherAt(lat, lon, "Your location");
}

// Points the map at a searched district.
function showPlace(lat, lon, label) {
    clearMarker();
    marker = L.marker([lat, lon]).addTo(map).bindPopup(label);
    focusMap([lat, lon], 11);
}

map.on("click", (event) => {
    // While the route form is picking a start or destination, the click is its.
    if (window.routePicking) return;
    stopWatching();
    const { lat, lng } = event.latlng;
    clearMarker();
    marker = L.marker([lat, lng]).addTo(map);
    setStatus(`${lat.toFixed(5)}, ${lng.toFixed(5)} (set manually)`, "#137333");
    weatherAt(lat, lng, "Selected point");
});

document.getElementById("use-location").addEventListener("click", () => {
    if (!navigator.geolocation) {
        warnStatus("Geolocation is not supported by this browser.");
        return;
    }
    if (!window.isSecureContext) {
        warnStatus("Location needs HTTPS or localhost.");
        return;
    }

    stopWatching();
    setStatus("Locating…");

    let best = null;
    let settled = false;

    const settle = () => {
        if (settled) return;
        settled = true;
        stopWatching();
        if (best) {
            showFix(best.coords.latitude, best.coords.longitude, best.coords.accuracy, true);
        } else {
            warnStatus(`No position after ${GPS_LOCK_MS / 1000}s. GPS rarely locks ` +
                "indoors; try near a window or outside, or click your position on the map.");
        }
    };

    // A phone first answers with a coarse network fix, then tightens as GPS locks
    // satellites. Settling early would freeze that coarse fix, so the best reading is
    // kept until it is GPS-grade or the deadline passes.
    watchId = navigator.geolocation.watchPosition(
        (position) => {
            if (settled) return;
            if (!best || position.coords.accuracy < best.coords.accuracy) best = position;
            if (best.coords.accuracy <= GOOD_FIX_M) {
                settle();
            } else {
                showFix(best.coords.latitude, best.coords.longitude, best.coords.accuracy, false);
            }
        },
        (error) => {
            // A timeout still leaves any earlier coarse fix worth showing.
            if (error.code === error.TIMEOUT && best) return settle();
            if (settled) return;
            settled = true;
            stopWatching();
            warnStatus(error.code === error.PERMISSION_DENIED
                ? "Permission denied. Allow location for this site, and check that " +
                  "location is on in your device settings."
                : error.message);
        },
        { enableHighAccuracy: true, timeout: GPS_LOCK_MS, maximumAge: 0 },
    );

    deadlineTimer = setTimeout(settle, GPS_LOCK_MS);
});
