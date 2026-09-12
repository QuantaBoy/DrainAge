// Determine base URL dynamically
const getBaseUrl = () => window.location.protocol.startsWith("http") ? "" : "http://127.0.0.1:8000";

// Initialize Leaflet GIS World Map centered at world view [20, 0]
const map = L.map('map').setView([20, 0], 2);

// Add OpenStreetMap GIS base tile layer
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap contributors'
}).addTo(map);

let currentMarker = null;
const rainfallLayer = L.layerGroup().addTo(map);

// Function to fetch weather by query string (city name or lat/lon)
async function fetchWeather(queryParams) {
    const resultElement = document.getElementById("result");
    resultElement.textContent = "Loading weather data...";

    const url = `${getBaseUrl()}/data-collection/weather?${queryParams}`;

    try {
        const res = await fetch(url);
        const data = await res.json();

        if (!res.ok) {
            const errorMsg = data.detail?.message || data.detail || res.statusText || "Error fetching weather data";
            resultElement.textContent = `Error (${res.status}): ${errorMsg}`;
            return;
        }

        const textOutput = [
            `City: ${data.name || 'Location'}${data.sys?.country ? ', ' + data.sys.country : ''}`,
            `Coordinates: Lat ${data.coord?.lat}, Lon ${data.coord?.lon}`,
            `Temperature: ${data.main?.temp}°C (Feels like: ${data.main?.feels_like}°C)`,
            `Condition: ${data.weather?.[0]?.main || ''} - ${data.weather?.[0]?.description || ''}`,
            `Humidity: ${data.main?.humidity}%`,
            `Wind Speed: ${data.wind?.speed} m/s`,
            `Pressure: ${data.main?.pressure} hPa`
        ].join('\n');

        resultElement.textContent = textOutput;

        // Update GIS World Map marker and view
        if (data.coord && data.coord.lat !== undefined && data.coord.lon !== undefined) {
            const lat = data.coord.lat;
            const lon = data.coord.lon;

            map.setView([lat, lon], 8);

            if (currentMarker) {
                map.removeLayer(currentMarker);
            }

            const popupContent = `<b>${data.name || 'Selected Location'}</b><br>${data.main?.temp}°C - ${data.weather?.[0]?.description}`;
            currentMarker = L.marker([lat, lon]).addTo(map).bindPopup(popupContent).openPopup();

            // Auto-fill the Flood Nowcast bounding box based on the fetched location
            const pad = 0.1;
            document.getElementById("min_lat").value = (lat - pad).toFixed(4);
            document.getElementById("max_lat").value = (lat + pad).toFixed(4);
            document.getElementById("min_lon").value = (lon - pad).toFixed(4);
            document.getElementById("max_lon").value = (lon + pad).toFixed(4);
            
            // Automatically trigger the flood nowcast grid fetch!
            document.getElementById("rainfall-form").requestSubmit();
        }

    } catch (err) {
        resultElement.textContent = `Network Error: Ensure backend server is running.\nDetails: ${err.message}`;
    }
}

// Form submit event (Search by City)
document.getElementById("weather-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const city = document.getElementById("city").value.trim();
    if (city) {
        fetchWeather(`city=${encodeURIComponent(city)}`);
    }
});

// Click anywhere on World Map event (Fetch Weather by Coordinates).
// Also the manual override when the browser's own fix is too rough to trust.
map.on('click', (e) => {
    const lat = e.latlng.lat.toFixed(4);
    const lon = e.latlng.lng.toFixed(4);

    if (typeof accuracyCircle !== "undefined" && accuracyCircle) {
        map.removeLayer(accuracyCircle);
        accuracyCircle = null;
        document.getElementById("location-status").innerHTML =
            `<span style="color:#137333">Set manually: ${lat}, ${lon}</span>`;
    }
    fetchWeather(`lat=${lat}&lon=${lon}`);
});

// Live Location
//
// A device with no GPS chip silently falls back to WiFi/IP lookup, which lands on
// the ISP's node and can be tens of km out. The browser reports that uncertainty in
// coords.accuracy (metres), so it is read, shown, and used to decide whether the fix
// is trustworthy rather than assumed good.
const GOOD_FIX_M = 100;      // GPS-grade, stop refining
const USABLE_FIX_M = 2000;   // usable for an ~8km rainfall cell
let geoWatchId = null;
let accuracyCircle = null;

function describeAccuracy(m) {
    if (m <= GOOD_FIX_M) return { label: "GPS-grade", css: "#137333", warn: false };
    if (m <= USABLE_FIX_M) return { label: "approximate", css: "#8a4b00", warn: false };
    return { label: "very rough, likely IP-based", css: "#b3261e", warn: true };
}

function applyFix(lat, lon, accuracy, final) {
    const statusEl = document.getElementById("location-status");
    const q = describeAccuracy(accuracy);
    const km = accuracy >= 1000 ? `${(accuracy / 1000).toFixed(1)}km` : `${Math.round(accuracy)}m`;

    statusEl.innerHTML =
        `<span style="color:${q.css}">${final ? "" : "refining… "}` +
        `${lat.toFixed(4)}, ${lon.toFixed(4)} &plusmn;${km} (${q.label})</span>` +
        (q.warn && final
            ? `<br><span style="color:#b3261e;font-size:.85em">This device has no GPS fix, so this is your ` +
              `network's location, not yours. Click your actual position on the map to correct it.</span>`
            : "");

    if (accuracyCircle) map.removeLayer(accuracyCircle);
    accuracyCircle = L.circle([lat, lon], {
        radius: accuracy, color: q.css, weight: 1, fillOpacity: 0.08,
    }).addTo(map).bindPopup(`Reported accuracy: &plusmn;${km}`);

    if (final) fetchWeather(`lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}`);
}

document.getElementById("use-location").addEventListener("click", () => {
    const statusEl = document.getElementById("location-status");

    if (!navigator.geolocation) {
        statusEl.textContent = "Geolocation not supported by this browser.";
        return;
    }
    if (!window.isSecureContext) {
        statusEl.textContent = "Location needs HTTPS or localhost.";
        return;
    }

    if (geoWatchId !== null) navigator.geolocation.clearWatch(geoWatchId);
    statusEl.textContent = "Locating…";

    let best = null;
    let settled = false;

    const settle = () => {
        if (settled || !best) return;
        settled = true;
        navigator.geolocation.clearWatch(geoWatchId);
        geoWatchId = null;
        applyFix(best.coords.latitude, best.coords.longitude, best.coords.accuracy, true);
    };

    // GPS reports a coarse fix first and tightens over several seconds, so keep the
    // best reading rather than accepting whatever arrives first.
    geoWatchId = navigator.geolocation.watchPosition(
        (pos) => {
            if (!best || pos.coords.accuracy < best.coords.accuracy) best = pos;
            if (settled) return;
            if (best.coords.accuracy <= GOOD_FIX_M) settle();
            else applyFix(best.coords.latitude, best.coords.longitude, best.coords.accuracy, false);
        },
        (err) => {
            if (settled) return;
            settled = true;
            if (geoWatchId !== null) navigator.geolocation.clearWatch(geoWatchId);
            geoWatchId = null;
            const hint = err.code === err.PERMISSION_DENIED
                ? "Permission denied. Allow location access, or click your position on the map."
                : `${err.message}. Click your position on the map instead.`;
            statusEl.innerHTML = `<span style="color:#b3261e">${hint}</span>`;
        },
        { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
    );

    // Take the best fix seen so far rather than waiting out the full GPS timeout
    setTimeout(settle, 12000);
});

// Rainfall Form Submit (Flood Nowcast Grid)
document.getElementById("rainfall-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const min_lat = document.getElementById("min_lat").value;
    const max_lat = document.getElementById("max_lat").value;
    const min_lon = document.getElementById("min_lon").value;
    const max_lon = document.getElementById("max_lon").value;

    const resultElement = document.getElementById("rainfall-result");
    resultElement.textContent = "Loading live 3-hour rainfall runoff grid...";

    const url = `${getBaseUrl()}/data-collection/rainfall/latest?min_lat=${min_lat}&max_lat=${max_lat}&min_lon=${min_lon}&max_lon=${max_lon}`;

    try {
        const res = await fetch(url);
        const data = await res.json();
        
        if (!res.ok) {
            resultElement.textContent = `Error: ${data.detail || res.statusText}`;
            return;
        }
        
        // Store grid data globally for the timeline slider
        lastGridData = data;
        rainfallLayer.clearLayers();

        const maxSteps = data.grid[0]?.timestamps?.length || 1;
        const slider = document.getElementById("timeline-slider");
        slider.max = maxSteps - 1;
        slider.value = 0;

        // Tick marks so a coarse (e.g. 3-step hourly) timeline shows honestly as
        // a few discrete stops, not a smooth scrub bar it can't back up
        const ticks = document.getElementById("timeline-ticks");
        ticks.innerHTML = "";
        for (let i = 0; i < maxSteps; i++) {
            const opt = document.createElement("option");
            opt.value = i;
            ticks.appendChild(opt);
        }

        const totalMinutes = (maxSteps - 1) * (data.interval_minutes || 15);
        const endH = Math.floor(totalMinutes / 60);
        const endM = totalMinutes % 60;
        document.getElementById("timeline-scale-end").textContent = `+${endH}h${endM.toString().padStart(2, '0')}`;

        document.getElementById("timeline-container").style.display = "block";

        // Draw the initial frame (step 0) and the summary
        renderTimeStep(0);

        let totalCityVolume = 0;
        data.grid.forEach(cell => {
            totalCityVolume += cell.runoff_volume_m3.reduce((a, b) => a + b, 0);
        });
        
        const interval = data.interval_minutes;

        const rows = data.grid.map(cell => {
            const maxPrecip = cell.precipitation_mm.length > 0 ? Math.max(...cell.precipitation_mm) : 0;
            const vol = cell.runoff_volume_m3.reduce((a, b) => a + b, 0);
            return `${cell.lat},${cell.lon}\tpeak ${maxPrecip.toFixed(1)}mm\tvol ${vol.toFixed(0)}m³`;
        }).join('\n');

        const forecastTimes = data.grid[0]?.timestamps || [];
        const timeStart = forecastTimes[0] || "N/A";
        const timeEnd = forecastTimes[forecastTimes.length - 1] || "N/A";

        const dryNote = totalCityVolume === 0
            ? "\nNo rainfall forecast anywhere in this area for this window, so the map stays clear.\n"
            : "";

        resultElement.textContent = `LIVE NOWCAST — ${data.source}\n\nFetched ${data.grid.length} grid cells at ${data.step_deg}° (~${Math.round(data.step_deg * 111)}km) resolution, ${interval}-minute steps.\nForecast window: ${timeStart} to ${timeEnd}\nTotal predicted runoff, next 3 hrs: ${totalCityVolume.toFixed(2)} cubic meters.\n${dryNote}\nPer-cell variation (lat,lon | peak ${interval}min precip | total 3hr runoff):\n${rows}`;
        
        // Center map to the middle of the bounding box
        const centerLat = (parseFloat(min_lat) + parseFloat(max_lat)) / 2;
        const centerLon = (parseFloat(min_lon) + parseFloat(max_lon)) / 2;
        map.setView([centerLat, centerLon], 11);
        
    } catch (err) {
        resultElement.textContent = `Network Error: ${err.message}`;
    }
});

// ============================================================
// TIMELINE SLIDER: Animate the storm step-by-step (15 min each)
// ============================================================
let lastGridData = null;
let timelineInterval = null;

function renderTimeStep(stepIdx) {
    if (!lastGridData) return;
    rainfallLayer.clearLayers();

    const step = lastGridData.step_deg;
    const toMmPerHr = 60 / (lastGridData.interval_minutes || 15);
    let totalPrecipAtStep = 0;

    lastGridData.grid.forEach(cell => {
        const p = (cell.precipitation_mm[stepIdx] ?? 0);
        totalPrecipAtStep += p;

        // Normalise to mm/hr so thresholds hold for both 15-min and hourly steps
        const mmPerHr = p * toMmPerHr;
        const color = mmPerHr >= 8.0 ? "red" : (mmPerHr >= 2.0 ? "orange" : "blue");

        const bounds = [[cell.lat, cell.lon], [cell.lat + step, cell.lon + step]];
        L.rectangle(bounds, {
            color: color, weight: 1,
            // Dry cell: outline only. A filled blue box on a dry cell reads as
            // standing water, so fill only shows once there is actual rain.
            fillOpacity: p > 0 ? 0.35 : 0,
        })
         .bindPopup(`<b>Grid: ${cell.lat}, ${cell.lon}</b><br>Time: ${cell.timestamps[stepIdx] || 'N/A'}<br>Precip: <b>${p.toFixed(2)} mm</b> (${mmPerHr.toFixed(1)} mm/hr)<br>Runoff: <b>${(cell.runoff_volume_m3[stepIdx] ?? 0).toFixed(0)} m³</b>`)
         .addTo(rainfallLayer);
    });

    // Update slider labels
    const ts = lastGridData.grid[0]?.timestamps[stepIdx] || "N/A";
    const minutes = stepIdx * (lastGridData.interval_minutes || 15);
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    document.getElementById("timeline-label").textContent = `${ts}  (T+${h}h${m.toString().padStart(2,'0')}m)`;
    
    const avgPrecip = lastGridData.grid.length > 0 ? (totalPrecipAtStep / lastGridData.grid.length) : 0;
    document.getElementById("timeline-precip").textContent = totalPrecipAtStep > 0
        ? `Avg: ${avgPrecip.toFixed(2)} mm | Grid Total: ${totalPrecipAtStep.toFixed(2)} mm`
        : "No rain in this step";
}

// Slider drag event
document.getElementById("timeline-slider").addEventListener("input", (e) => {
    renderTimeStep(parseInt(e.target.value));
});

// Play / Pause button
document.getElementById("timeline-play").addEventListener("click", () => {
    const btn = document.getElementById("timeline-play");
    const slider = document.getElementById("timeline-slider");
    
    if (timelineInterval) {
        // Pause
        clearInterval(timelineInterval);
        timelineInterval = null;
        btn.textContent = "▶ Play";
    } else {
        // Play: animate from current position
        btn.textContent = "⏸ Pause";
        timelineInterval = setInterval(() => {
            let val = parseInt(slider.value);
            if (val >= parseInt(slider.max)) {
                val = 0; // Loop back to start
            } else {
                val++;
            }
            slider.value = val;
            renderTimeStep(val);
        }, 700); // 700ms per frame = ~8.4 seconds for full 3-hour animation
    }
});

