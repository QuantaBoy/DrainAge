const GOOD_FIX_M = 100;      // GPS-grade, stop refining
const USABLE_FIX_M = 2000;
const GPS_LOCK_MS = 45000;   // cold GPS lock takes tens of seconds
const LAST_FIX_KEY = "lastFix";

const statusEl = document.getElementById("location-status");
let watchId = null;
let deadlineTimer = null;
let marker = null;
let accuracyCircle = null;

const map = L.map("map").setView([20, 0], 2);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

function describeAccuracy(m) {
    if (m <= GOOD_FIX_M) return { label: "GPS-grade", css: "#137333" };
    if (m <= USABLE_FIX_M) return { label: "Wi-Fi fix, not GPS", css: "#8a4b00" };
    return { label: "very rough, likely IP based", css: "#b3261e" };
}

function render(lat, lon, accuracy, final, stale) {
    const q = stale ? { label: "last known", css: "#5f6368" } : describeAccuracy(accuracy);
    const dist = accuracy >= 1000 ? `${(accuracy / 1000).toFixed(1)}km` : `${Math.round(accuracy)}m`;

    statusEl.innerHTML =
        `<span style="color:${q.css}">${final ? "" : "refining… "}` +
        `${lat.toFixed(5)}, ${lon.toFixed(5)} &plusmn;${dist} (${q.label})</span>` +
        // A Wi-Fi fix reports optimistic accuracy but resolves to whichever mapped network
        // it saw, which can be kilometres off, so anything short of GPS-grade is flagged.
        (final && !stale && accuracy > GOOD_FIX_M
            ? `<br><span class="warn">No GPS on this device, so this is the location of a nearby ` +
              `network, not you &mdash; the real error can be larger than the circle. ` +
              `Click your actual position on the map, or open this page on a phone with GPS.</span>`
            : "");

    if (marker) map.removeLayer(marker);
    if (accuracyCircle) map.removeLayer(accuracyCircle);

    marker = L.marker([lat, lon], { opacity: stale ? 0.5 : 1 }).addTo(map);
    accuracyCircle = L.circle([lat, lon], {
        radius: accuracy, color: q.css, weight: 1, fillOpacity: stale ? 0.04 : 0.08,
        dashArray: stale ? "4" : null,
    }).addTo(map).bindPopup(`Reported accuracy: &plusmn;${dist}`);

    // The circle is the honest picture: the true position is somewhere inside it,
    // so frame that area rather than zooming to a point the fix cannot support.
    map.fitBounds(accuracyCircle.getBounds(), { maxZoom: 17 });

    if (final && !stale) {
        try {
            localStorage.setItem(LAST_FIX_KEY, JSON.stringify({ lat, lon, accuracy, at: Date.now() }));
        } catch (e) { /* private mode or storage disabled */ }
        renderForecast(`lat=${lat}&lon=${lon}`, "your location");
    }
}

// Used by the district search to point the shared map at a looked-up place.
function showPlace(lat, lon, label) {
    if (marker) map.removeLayer(marker);
    if (accuracyCircle) map.removeLayer(accuracyCircle);
    accuracyCircle = null;
    marker = L.marker([lat, lon]).addTo(map).bindPopup(label);
    map.setView([lat, lon], 11);
}

function ago(then) {
    const mins = Math.round((Date.now() - then) / 60000);
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    if (mins < 60) return rtf.format(-mins, "minute");
    if (mins < 1440) return rtf.format(-Math.round(mins / 60), "hour");
    return rtf.format(-Math.round(mins / 1440), "day");
}

// Mirrors what Maps does with no live fix: open on where you were, clearly marked stale,
// rather than a blank world view.
try {
    const saved = JSON.parse(localStorage.getItem(LAST_FIX_KEY) || "null");
    if (saved) {
        render(saved.lat, saved.lon, saved.accuracy, true, true);
        statusEl.innerHTML += `<br><span style="color:#5f6368;font-size:.85em">` +
            `From your last visit, ${ago(saved.at)}. Press the button for a live fix.</span>`;
    }
} catch (e) { /* corrupt or unavailable storage, start blank */ }

map.on("click", (e) => {
    if (watchId !== null) {
        navigator.geolocation.clearWatch(watchId);
        watchId = null;
    }
    clearTimeout(deadlineTimer);
    if (accuracyCircle) map.removeLayer(accuracyCircle);
    accuracyCircle = null;
    if (marker) map.removeLayer(marker);

    const { lat, lng } = e.latlng;
    marker = L.marker([lat, lng]).addTo(map);
    statusEl.innerHTML = `<span style="color:#137333">${lat.toFixed(5)}, ${lng.toFixed(5)} (set manually)</span>`;
});

document.getElementById("use-location").addEventListener("click", () => {
    if (!navigator.geolocation) {
        statusEl.textContent = "Geolocation not supported by this browser.";
        return;
    }
    if (!window.isSecureContext) {
        statusEl.textContent = "Location needs HTTPS or localhost.";
        return;
    }

    if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    if (deadlineTimer) clearTimeout(deadlineTimer);
    statusEl.textContent = "Locating…";

    let best = null;
    let settled = false;

    const settle = () => {
        if (settled) return;
        settled = true;
        if (watchId !== null) navigator.geolocation.clearWatch(watchId);
        watchId = null;
        clearTimeout(deadlineTimer);
        if (best) {
            render(best.coords.latitude, best.coords.longitude, best.coords.accuracy, true);
        } else {
            statusEl.innerHTML = `<span class="warn">No position after ${GPS_LOCK_MS / 1000}s. ` +
                `GPS rarely locks indoors &mdash; try near a window or outside, ` +
                `or click your position on the map.</span>`;
        }
    };

    // A phone answers with a coarse network fix within a second, then tightens as GPS
    // locks satellites, which takes tens of seconds from cold. Settling early would
    // freeze that first coarse fix, so keep the best reading until GPS-grade or deadline.
    watchId = navigator.geolocation.watchPosition(
        (pos) => {
            if (settled) return;
            if (!best || pos.coords.accuracy < best.coords.accuracy) best = pos;
            if (best.coords.accuracy <= GOOD_FIX_M) settle();
            else render(best.coords.latitude, best.coords.longitude, best.coords.accuracy, false);
        },
        (err) => {
            // A timeout still leaves any earlier coarse fix worth showing.
            if (err.code === err.TIMEOUT && best) return settle();
            if (settled) return;
            settled = true;
            if (watchId !== null) navigator.geolocation.clearWatch(watchId);
            watchId = null;
            clearTimeout(deadlineTimer);
            const hint = err.code === err.PERMISSION_DENIED
                ? "Permission denied. Allow location for this site, and check location is on in your phone's system settings."
                : err.message;
            statusEl.innerHTML = `<span class="warn">${hint}</span>`;
        },
        { enableHighAccuracy: true, timeout: GPS_LOCK_MS, maximumAge: 0 }
    );

    deadlineTimer = setTimeout(settle, GPS_LOCK_MS);
});
