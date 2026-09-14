// Current weather and rainfall forecast panels.

const form = document.getElementById("district-form");
const input = document.getElementById("district");
const weatherBox = document.getElementById("weather");
const forecastBox = document.getElementById("forecast");

const WEATHER_ROWS = [
    ["Condition", (w) => w.condition],
    ["Temperature", (w) => `${w.temp_c} °C (feels ${w.feels_like_c} °C)`],
    ["Humidity", (w) => `${w.humidity_pct}%`],
    ["Wind", (w) => `${w.wind_ms} m/s`],
    ["Rain, last 1h", (w) => `${w.rain_1h_mm} mm`],
];

// Requests overlap (a location fix, a map click, a search), and a slower older
// response must not overwrite a newer one, so each panel draws only its latest request.
const latestRequest = { weather: 0, forecast: 0 };

function showError(box, message) {
    const span = document.createElement("span");
    span.className = "warn";
    span.textContent = message;
    box.replaceChildren(span);
}

// Fetches JSON for a panel. Resolves to the body, or null when the request failed
// (error shown) or was superseded by a newer one (left alone).
async function fetchForPanel(panel, box, url) {
    const id = ++latestRequest[panel];
    let response;
    let body;
    try {
        response = await fetch(url);
        body = await response.json();
    } catch (err) {
        if (id === latestRequest[panel]) showError(box, "Could not reach the server.");
        return null;
    }
    if (id !== latestRequest[panel]) return null;
    if (!response.ok) {
        // Shown, not swallowed: a hidden panel looks identical to a missing feature.
        showError(box, String(body.detail || response.statusText));
        return null;
    }
    return body;
}

async function renderWeather(query, prefix) {
    weatherBox.textContent = "Loading…";
    const body = await fetchForPanel("weather", weatherBox, `/data-collection/weather?${query}`);
    if (!body) return null;

    const where = [body.district, body.state, body.country].filter(Boolean).join(", ");

    // Built as nodes, not markup: every value here is third-party API text.
    const heading = document.createElement("strong");
    heading.textContent = prefix ? `${prefix}: ${where}` : where;
    const table = document.createElement("table");
    for (const [label, value] of WEATHER_ROWS) {
        const row = table.insertRow();
        row.insertCell().textContent = label;
        row.insertCell().textContent = value(body);
    }
    weatherBox.replaceChildren(heading, table);
    return { body, where };
}

async function renderForecast(query, title) {
    forecastBox.textContent = "Loading forecast…";
    const body = await fetchForPanel("forecast", forecastBox, `/data-collection/forecast?${query}`);
    if (!body) return;

    const where = title || [body.district, body.state].filter(Boolean).join(", ");
    const heading = document.createElement("strong");
    heading.textContent = where ? `Rainfall forecast, ${where}` : "Rainfall forecast";
    forecastBox.replaceChildren(heading, buildTimeline(body), buildWeekTable(body.days));
}

// Weather and forecast for a point, leaving the map as it is: the location marker and
// its accuracy circle are already drawn there.
function weatherAt(lat, lon, label) {
    const query = `lat=${lat.toFixed(5)}&lon=${lon.toFixed(5)}`;
    renderWeather(query, label);
    renderForecast(query, label.toLowerCase());
}

function buildWeekTable(days) {
    // Bars are scaled to the wettest day shown, so the shape of the week reads at a
    // glance even when totals are small.
    const peak = Math.max(...days.map((day) => day.rain_mm), 1);
    const table = document.createElement("table");
    table.className = "forecast";

    for (const day of days) {
        const row = table.insertRow();
        const date = new Date(`${day.date}T00:00:00`);
        row.insertCell().textContent = date.toLocaleDateString(undefined, {
            weekday: "short", day: "numeric", month: "short",
        });

        const bar = document.createElement("span");
        bar.className = "bar";
        bar.style.width = `${Math.round((day.rain_mm / peak) * 100)}%`;
        row.insertCell().append(bar);

        row.insertCell().textContent = `${day.rain_mm.toFixed(1)} mm`;
        row.insertCell().textContent = day.rain_chance_pct == null ? "" : `${day.rain_chance_pct}%`;
        row.insertCell().textContent = day.temp_max_c == null ? "" : `${Math.round(day.temp_max_c)}°`;
    }
    return table;
}

// One slider across both scales: the next few hours, then the coming days. Flood
// timing is read at hour resolution, while the week shows the build-up around it.
function buildTimeline(body) {
    const steps = [
        ...body.hours.map((hour, index) => {
            const time = new Date(hour.time);
            return {
                label: index === 0 ? "Now" : time.toLocaleTimeString(undefined, { hour: "numeric" }),
                detail: time.toLocaleString(undefined, { weekday: "short", hour: "numeric" }),
                rain: hour.rain_mm, chance: hour.rain_chance_pct, temp: hour.temp_c,
                unit: "mm this hour",
            };
        }),
        ...body.days.map((day) => {
            const date = new Date(`${day.date}T00:00:00`);
            return {
                label: date.toLocaleDateString(undefined, { weekday: "short" }),
                detail: date.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }),
                rain: day.rain_mm, chance: day.rain_chance_pct, temp: day.temp_max_c,
                unit: "mm this day",
            };
        }),
    ];

    const slider = document.createElement("input");
    Object.assign(slider, { type: "range", min: 0, max: steps.length - 1, value: 0, step: 1 });
    slider.setAttribute("aria-label", "Forecast time");

    const ticks = document.createElement("div");
    ticks.className = "ticks";
    for (const step of steps) {
        const tick = document.createElement("span");
        tick.textContent = step.label;
        ticks.append(tick);
    }

    const readout = document.createElement("div");
    readout.className = "readout";

    const showStep = () => {
        const index = Number(slider.value);
        const step = steps[index];

        const when = document.createElement("strong");
        when.textContent = step.detail;
        const rain = document.createElement("span");
        rain.className = "readout-rain";
        rain.textContent = `${step.rain.toFixed(1)} ${step.unit}`;
        const extra = document.createElement("span");
        extra.className = "readout-extra";
        extra.textContent = [
            step.chance == null ? null : `${step.chance}% chance`,
            step.temp == null ? null : `${Math.round(step.temp)}°C`,
        ].filter(Boolean).join(" · ");

        readout.replaceChildren(when, rain, extra);
        [...ticks.children].forEach((tick, i) => tick.classList.toggle("on", i === index));
    };

    slider.addEventListener("input", showStep);
    showStep();

    const timeline = document.createElement("div");
    timeline.className = "timeline";
    timeline.append(slider, ticks, readout);
    return timeline;
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const district = input.value.trim();
    if (!district) return;

    const result = await renderWeather(`district=${encodeURIComponent(district)}`);
    if (!result) return;
    showPlace(result.body.lat, result.body.lon, result.where);
    renderForecast(`district=${encodeURIComponent(district)}`, result.where);
});
