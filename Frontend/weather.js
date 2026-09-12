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

// Rainfall Form Submit (Flood Nowcast Grid)
document.getElementById("rainfall-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const min_lat = document.getElementById("min_lat").value;
    const max_lat = document.getElementById("max_lat").value;
    const min_lon = document.getElementById("min_lon").value;
    const max_lon = document.getElementById("max_lon").value;
    
    const resultElement = document.getElementById("rainfall-result");
    resultElement.textContent = "Loading 3-hour rainfall runoff grid...";

    const url = `${getBaseUrl()}/data-collection/rainfall/latest?min_lat=${min_lat}&max_lat=${max_lat}&min_lon=${min_lon}&max_lon=${max_lon}`;
    
    try {
        const res = await fetch(url);
        const data = await res.json();
        
        if (!res.ok) {
            resultElement.textContent = `Error: ${data.detail || res.statusText}`;
            return;
        }
        
        let totalCityVolume = 0;
        rainfallLayer.clearLayers();

        // Draw grid boxes on map
        data.grid.forEach(cell => {
            // Sum all precip in the 3 hour window for this cell to get logical intensity
            const cellTotalPrecipMm = cell.precipitation_mm.reduce((a, b) => a + b, 0);
            
            // Sum all volumes in the 3 hour window for this cell for reporting
            const cellTotalVol = cell.runoff_volume_m3.reduce((a, b) => a + b, 0);
            totalCityVolume += cellTotalVol;
            
            // Logically define rain severity based on depth (mm), NOT absolute volume.
            // This makes the color coding mathematically sound regardless of grid box size!
            // IMD 3-Hour thresholds (approx): >20mm = Severe/Red, >5mm = Moderate/Orange
            const color = cellTotalPrecipMm >= 20.0 ? "red" : (cellTotalPrecipMm >= 5.0 ? "orange" : "blue");

            
            const step = data.step_deg;
            const bounds = [[cell.lat, cell.lon], [cell.lat + step, cell.lon + step]];
            L.rectangle(bounds, {color: color, weight: 1, fillOpacity: 0.3})
             .bindPopup(`<b>Grid: ${cell.lat}, ${cell.lon}</b><br>Window: ${cell.timestamps[0]} to ${cell.timestamps[cell.timestamps.length - 1]}<br>Total 3hr Runoff: <b>${cellTotalVol.toFixed(2)} m³</b>`)
             .addTo(rainfallLayer);
        });
        
        const rows = data.grid.map(cell => {
            const maxPrecip = Math.max(...cell.precipitation_mm);
            const vol = cell.runoff_volume_m3.reduce((a, b) => a + b, 0);
            return `${cell.lat},${cell.lon}\tpeak ${maxPrecip.toFixed(1)}mm\tvol ${vol.toFixed(0)}m³`;
        }).join('\n');

        const forecastTimes = data.grid[0]?.timestamps || [];
        resultElement.textContent = `Success! Fetched ${data.grid.length} grid cells at ${data.step_deg}° (~${Math.round(data.step_deg * 111)}km) resolution.\nForecast window: ${forecastTimes[0]} to ${forecastTimes[forecastTimes.length - 1]}\nTotal predicted runoff volume in next 3 hrs: ${totalCityVolume.toFixed(2)} cubic meters.\n\nPer-cell variation (lat,lon | peak 15min precip | total 3hr runoff):\n${rows}`;
        
        // Center map to the middle of the bounding box
        const centerLat = (parseFloat(min_lat) + parseFloat(max_lat)) / 2;
        const centerLon = (parseFloat(min_lon) + parseFloat(max_lon)) / 2;
        map.setView([centerLat, centerLon], 11);
        
    } catch (err) {
        resultElement.textContent = `Network Error: ${err.message}`;
    }
});


