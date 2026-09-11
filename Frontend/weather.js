document.getElementById("weather-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const city = document.getElementById("city").value.trim();
    const resultElement = document.getElementById("result");

    if (!city) return;

    resultElement.textContent = "Loading weather data...";

    // Use relative endpoint when served via HTTP (Flask), fallback to http://127.0.0.1:5000 if opened as file
    const baseUrl = window.location.protocol.startsWith("http") ? "" : "http://127.0.0.1:5000";
    const url = `${baseUrl}/data-collection/weather?city=${encodeURIComponent(city)}`;

    try {
        const res = await fetch(url);
        const data = await res.json();

        if (!res.ok) {
            const errorMsg = data.detail?.message || data.detail || res.statusText || "Error fetching weather data";
            resultElement.textContent = `Error (${res.status}): ${errorMsg}`;
            return;
        }

        // Format clean text output
        const textOutput = [
            `City: ${data.name}${data.sys?.country ? ', ' + data.sys.country : ''}`,
            `Temperature: ${data.main?.temp}°C (Feels like: ${data.main?.feels_like}°C)`,
            `Condition: ${data.weather?.[0]?.main || ''} - ${data.weather?.[0]?.description || ''}`,
            `Humidity: ${data.main?.humidity}%`,
            `Wind Speed: ${data.wind?.speed} m/s`,
            `Pressure: ${data.main?.pressure} hPa`
        ].join('\n');

        resultElement.textContent = textOutput;

    } catch (err) {
        resultElement.textContent = `Network / Connection Error: Ensure backend server is running at http://127.0.0.1:8000\nDetails: ${err.message}`;
    }
});

