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

// Click anywhere on World Map event (Fetch Weather by Coordinates)
map.on('click', (e) => {
    const lat = e.latlng.lat.toFixed(4);
    const lon = e.latlng.lng.toFixed(4);
    fetchWeather(`lat=${lat}&lon=${lon}`);
});

// Live Location: use browser geolocation to center weather + rainfall grid on the user
document.getElementById("use-location").addEventListener("click", () => {
    const statusEl = document.getElementById("location-status");

    if (!navigator.geolocation) {
        statusEl.textContent = "Geolocation not supported by this browser.";
        return;
    }

    statusEl.textContent = "Locating...";
    navigator.geolocation.getCurrentPosition(
        (pos) => {
            const lat = pos.coords.latitude;
            const lon = pos.coords.longitude;
            statusEl.textContent = `Located: ${lat.toFixed(4)}, ${lon.toFixed(4)}`;

            fetchWeather(`lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}`);

            // Auto-fill and trigger the rainfall grid over a ~0.2deg box around the user
            const pad = 0.1;
            document.getElementById("min_lat").value = (lat - pad).toFixed(4);
            document.getElementById("max_lat").value = (lat + pad).toFixed(4);
            document.getElementById("min_lon").value = (lon - pad).toFixed(4);
            document.getElementById("max_lon").value = (lon + pad).toFixed(4);
            document.getElementById("rainfall-form").requestSubmit();
        },
        (err) => {
            statusEl.textContent = `Location error: ${err.message}`;
        },
        { enableHighAccuracy: true, timeout: 10000 }
    );
});

// Show/hide the time period dropdown when a date is selected
document.getElementById("historical_date").addEventListener("change", (e) => {
    const hourSelect = document.getElementById("historical_hour");
    hourSelect.style.display = e.target.value ? "inline-block" : "none";
});

// Rainfall Form Submit (Flood Nowcast Grid)
document.getElementById("rainfall-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const min_lat = document.getElementById("min_lat").value;
    const max_lat = document.getElementById("max_lat").value;
    const min_lon = document.getElementById("min_lon").value;
    const max_lon = document.getElementById("max_lon").value;
    const histDate = document.getElementById("historical_date").value;
    const histHour = document.getElementById("historical_hour").value;
    
    const resultElement = document.getElementById("rainfall-result");
    resultElement.textContent = histDate 
        ? `Loading ${histHour === 'auto' ? 'Peak' : histHour + ':00'} 3-Hour Storm for ${histDate}...` 
        : "Loading live 3-hour rainfall runoff grid...";

    let url = `${getBaseUrl()}/data-collection/rainfall/latest?min_lat=${min_lat}&max_lat=${max_lat}&min_lon=${min_lon}&max_lon=${max_lon}`;
    if (histDate) {
        url += `&date=${histDate}`;
        if (histHour !== "auto") {
            url += `&start_hour=${histHour}`;
        }
    }
    
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
        document.getElementById("timeline-container").style.display = "block";

        // Draw the initial frame (step 0) and the summary
        renderTimeStep(0);

        let totalCityVolume = 0;
        data.grid.forEach(cell => {
            totalCityVolume += cell.runoff_volume_m3.reduce((a, b) => a + b, 0);
        });
        
        const rows = data.grid.map(cell => {
            const maxPrecip = cell.precipitation_mm.length > 0 ? Math.max(...cell.precipitation_mm) : 0;
            const vol = cell.runoff_volume_m3.reduce((a, b) => a + b, 0);
            return `${cell.lat},${cell.lon}\tpeak ${maxPrecip.toFixed(1)}mm\tvol ${vol.toFixed(0)}m³`;
        }).join('\n');

        const forecastTimes = data.grid[0]?.timestamps || [];
        const timeStart = forecastTimes[0] || "N/A";
        const timeEnd = forecastTimes[forecastTimes.length - 1] || "N/A";
        const modeLabel = histDate ? `Peak 3-Hour Storm on ${histDate}` : "next 3 hrs";
        resultElement.textContent = `Success! Fetched ${data.grid.length} grid cells at ${data.step_deg}° (~${Math.round(data.step_deg * 111)}km) resolution.\nForecast window: ${timeStart} to ${timeEnd}\nTotal predicted runoff volume in ${modeLabel}: ${totalCityVolume.toFixed(2)} cubic meters.\n\nPer-cell variation (lat,lon | peak 15min precip | total 3hr runoff):\n${rows}`;
        
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
    let totalPrecipAtStep = 0;

    lastGridData.grid.forEach(cell => {
        const p = (cell.precipitation_mm[stepIdx] ?? 0);
        totalPrecipAtStep += p;
        
        // Single-step color: use per-15-min thresholds (scaled down from 3hr)
        // IMD: >2mm/15min = severe, >0.5mm/15min = moderate
        const color = p >= 2.0 ? "red" : (p >= 0.5 ? "orange" : "blue");

        const bounds = [[cell.lat, cell.lon], [cell.lat + step, cell.lon + step]];
        L.rectangle(bounds, {color: color, weight: 1, fillOpacity: 0.35})
         .bindPopup(`<b>Grid: ${cell.lat}, ${cell.lon}</b><br>Time: ${cell.timestamps[stepIdx] || 'N/A'}<br>Precip: <b>${p.toFixed(2)} mm</b><br>Runoff: <b>${(cell.runoff_volume_m3[stepIdx] ?? 0).toFixed(0)} m³</b>`)
         .addTo(rainfallLayer);
    });

    // Update slider labels
    const ts = lastGridData.grid[0]?.timestamps[stepIdx] || "N/A";
    const minutes = stepIdx * 15;
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    document.getElementById("timeline-label").textContent = `${ts}  (T+${h}h${m.toString().padStart(2,'0')}m)`;
    
    const avgPrecip = lastGridData.grid.length > 0 ? (totalPrecipAtStep / lastGridData.grid.length) : 0;
    document.getElementById("timeline-precip").textContent = `Avg: ${avgPrecip.toFixed(2)} mm | Grid Total: ${totalPrecipAtStep.toFixed(2)} mm`;
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

