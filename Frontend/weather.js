// Determine base URL dynamically
const getBaseUrl = () => window.location.protocol.startsWith("http") ? "" : "http://127.0.0.1:5000";

// Initialize Leaflet GIS World Map centered at world view [20, 0]
const map = L.map('map').setView([20, 0], 2);

// Add OpenStreetMap GIS base tile layer
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap contributors'
}).addTo(map);

let currentMarker = null;

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


