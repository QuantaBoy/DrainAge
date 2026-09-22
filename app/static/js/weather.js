// Current weather and rainfall forecast panels.

const form = document.getElementById("district-form");
const input = document.getElementById("district");
const weatherBox = document.getElementById("weather");
const forecastBox = document.getElementById("forecast");

const WEATHER_ROWS = [
    ["Condition", (w) => w.condition],
    ["Temperature", (w) => `${w.temp_c.toFixed(1)} °C`],
    ["Feels like", (w) => `${w.feels_like_c.toFixed(1)} °C`],
    ["Humidity", (w) => `${w.humidity_pct}%`],
    ["Wind", (w) => `${w.wind_ms} m/s`],
    ["Rain, last 1h", (w) => `${w.rain_1h_mm} mm`],
];

// --- Weather icons ---------------------------------------------------------------
// Drawn as SVG instead of shipped as images: they scale, follow the palette through
// the CSS variables in their gradients, and cost no extra request.

const SUN_RAYS = Array.from({ length: 8 }, (unused, i) =>
    `<line class="wx-ray" x1="32" y1="5" x2="32" y2="12" transform="rotate(${i * 45} 32 32)"/>`).join("");
const CLOUD = '<g class="wx-cloud"><circle cx="25" cy="31" r="11"/>' +
    '<circle cx="38" cy="29" r="13"/><rect x="17" y="34" width="32" height="13" rx="6.5"/></g>';
const DROPS = [22, 32, 42].map((x, i) =>
    `<line class="wx-drop" x1="${x}" y1="${50 + (i % 2) * 3}" x2="${x - 3}" y2="${58 + (i % 2) * 3}"/>`).join("");

const ICONS = {
    clear: { label: "Clear", shapes: `${SUN_RAYS}<circle class="wx-sun" cx="32" cy="32" r="12"/>` },
    night: { label: "Clear night", shapes: '<path class="wx-sun" d="M38 13a19 19 0 1 0 15 29A21 21 0 0 1 38 13Z"/>' },
    partly: {
        label: "Partly cloudy",
        shapes: '<circle class="wx-sun" cx="41" cy="22" r="10"/>' +
            `<g transform="translate(-2 4)">${CLOUD}</g>`,
    },
    cloud: { label: "Cloudy", shapes: CLOUD },
    rain: { label: "Rain", shapes: `<g transform="translate(0 -6)">${CLOUD}</g>${DROPS}` },
    storm: {
        label: "Thunderstorm",
        shapes: `<g transform="translate(0 -6)">${CLOUD}</g>` +
            '<path class="wx-bolt" d="M34 41 24 55h7l-3 9 12-14h-8l5-9z"/>',
    },
};

// WMO weather code, as Open-Meteo reports it for each forecast day.
function iconForCode(code) {
    if (code === 0) return "clear";
    if (code <= 2) return "partly";
    if (code <= 48) return "cloud";      // cloudy, fog
    if (code <= 86) return "rain";       // drizzle, rain, snow, showers
    return "storm";                      // 95+ thunderstorm
}

// OpenWeather's icon code for current conditions, e.g. "10d": condition plus day/night.
function iconForCurrent(code) {
    const night = String(code).endsWith("n");
    switch (String(code).slice(0, 2)) {
        case "01": return night ? "night" : "clear";
        case "02": case "03": return "partly";
        case "09": case "10": return "rain";
        case "11": return "storm";
        default: return "cloud";
    }
}

function weatherIcon(kind) {
    const icon = ICONS[kind] || ICONS.cloud;
    const span = document.createElement("span");
    span.className = "wx-icon";
    // Static markup from the table above; no API text reaches this string.
    span.innerHTML = `<svg viewBox="0 0 64 64" role="img" aria-label="${icon.label}">${icon.shapes}</svg>`;
    return span;
}

// Requests overlap (a location fix, a map click, a search), and a slower older
// response must not overwrite a newer one, so each panel draws only its latest request.
const latestRequest = { weather: 0, forecast: 0 };

// Now plus the next 3 hours: the nowcast window.
const NOWCAST_STEPS = 4;
// Hours start at the current hour, so 48 always reaches the end of tomorrow.
const FORECAST_HOURS = 48;

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
    const header = document.createElement("div");
    header.className = "wx-head";
    header.append(heading, weatherIcon(iconForCurrent(body.icon)));
    const table = document.createElement("table");
    for (const [label, value] of WEATHER_ROWS) {
        const row = table.insertRow();
        row.insertCell().textContent = label;
        row.insertCell().textContent = value(body);
    }
    weatherBox.replaceChildren(header, table);
    return { body, where };
}

async function renderForecast(query, title) {
    forecastBox.textContent = "Loading forecast…";
    const body = await fetchForPanel("forecast", forecastBox, `/data-collection/forecast?${query}&hours=${FORECAST_HOURS}`);
    if (!body) return;

    const where = title || [body.district, body.state].filter(Boolean).join(", ");
    const heading = document.createElement("strong");
    heading.textContent = where ? `Rainfall forecast, ${where}` : "Rainfall forecast";
    forecastBox.replaceChildren(
        heading,
        buildNextRain(body.next_rain, body.heaviest_24h, body.days.length),
        buildTimeline(body),
        buildWeekTable(body.days),
    );
    loadRainGrid(body.lat, body.lon);
}

// Standard rain-rate bands, in mm/h.
function describeIntensity(mmPerHour) {
    if (mmPerHour < 2.5) return "Light rain";
    if (mmPerHour < 7.6) return "Moderate rain";
    if (mmPerHour < 50) return "Heavy rain";
    return "Violent rain";
}

function describeLikelihood(chancePct) {
    if (chancePct >= 70) return "expected";
    if (chancePct >= 40) return "likely";
    return "possible";
}

function formatHour(iso) {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function formatDayHour(iso) {
    return new Date(iso).toLocaleString(undefined, {
        weekday: "short", hour: "numeric", minute: "2-digit",
    });
}

// When the next rain starts and stops, how heavy, and how sure the forecast is.
function buildNextRain(next, heaviest, dayCount) {
    const box = document.createElement("div");
    box.className = "next-rain";
    const headline = document.createElement("div");
    headline.className = "next-rain-headline";

    if (!next) {
        headline.textContent = `No rain expected in the next ${dayCount} days`;
        box.append(headline);
        return box;
    }

    const kind = `${describeIntensity(next.peak_mm)} ${describeLikelihood(next.chance_pct)}`;
    const sameDay = next.start.slice(0, 10) === next.end.slice(0, 10);
    const until = sameDay ? formatHour(next.end) : formatDayHour(next.end);
    headline.textContent = next.starts_in_hours === 0
        ? `${kind} now, until ${until}`
        : `${kind} ${formatDayHour(next.start)} – ${until} (in ${next.starts_in_hours} h)`;

    const detail = document.createElement("div");
    detail.className = "next-rain-detail";
    detail.textContent = [
        `Heaviest around ${formatHour(next.peak_time)}, ${next.peak_mm} mm/h`,
        `${next.total_mm} mm total`,
        `${next.chance_pct}% chance`,
    ].join(" · ");

    box.append(headline, detail);

    // A stronger downpour after this event would otherwise go unmentioned.
    if (heaviest && (heaviest.time < next.start || heaviest.time >= next.end)) {
        const later = document.createElement("div");
        later.className = "next-rain-detail";
        later.textContent = `Heaviest in the next 24 h: ${heaviest.rain_mm} mm/h around ` +
            `${formatDayHour(heaviest.time)}` +
            (heaviest.chance_pct == null ? "" : `, ${heaviest.chance_pct}% chance`);
        box.append(later);
    }

    const note = document.createElement("div");
    note.className = "next-rain-note";
    note.textContent = "Hourly model forecast; the timing can shift by an hour or two.";
    box.append(note);
    return box;
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
        row.insertCell().append(weatherIcon(iconForCode(day.code)));

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

function hourStep(hour, label) {
    return {
        label, detail: formatDayHour(hour.time),
        rain: hour.rain_mm, chance: hour.rain_chance_pct, temp: hour.temp_c,
    };
}

// Two windows on one slider: "Today" is the 0–3 h nowcast that flood warnings act on,
// "Tomorrow" is tomorrow hour by hour, for planning ahead.
function buildTimeline(body) {
    const tomorrow = body.days[1];
    const views = {
        now: {
            steps: body.hours.slice(0, NOWCAST_STEPS)
                .map((hour, i) => hourStep(hour, i === 0 ? "Now" : `+${i} h`)),
            note: "Next 3 hours",
        },
        future: {
            // Every hour stays selectable, but only every third is labelled so 24 fit.
            steps: body.hours.filter((hour) => hour.time.startsWith(tomorrow.date))
                .map((hour, i) => hourStep(hour, i % 3 === 0
                    ? new Date(hour.time).toLocaleTimeString(undefined, { hour: "numeric" })
                    : "")),
            note: `Tomorrow: ${tomorrow.rain_mm.toFixed(1)} mm total` +
                (tomorrow.rain_chance_pct == null ? "" : `, ${tomorrow.rain_chance_pct}% chance`),
        },
    };
    let steps = views.now.steps;

    const toggle = document.createElement("div");
    toggle.className = "mode-toggle";
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "Forecast window");
    const buttons = {};
    for (const [key, text, number] of [["now", "Today", "01"], ["future", "Tomorrow", "02"]]) {
        const button = document.createElement("button");
        button.type = "button";
        const sup = document.createElement("sup");
        sup.textContent = number;
        button.append(text, sup);
        button.addEventListener("click", () => select(key));
        buttons[key] = button;
        toggle.append(button);
    }

    const note = document.createElement("div");
    note.className = "timeline-note";

    const slider = document.createElement("input");
    Object.assign(slider, { type: "range", min: 0, value: 0, step: 1 });
    slider.setAttribute("aria-label", "Forecast time");

    const ticks = document.createElement("div");
    ticks.className = "ticks";

    const readout = document.createElement("div");
    readout.className = "readout";

    const showStep = (publish = true) => {
        const index = Number(slider.value);
        const step = steps[index];

        const when = document.createElement("strong");
        when.textContent = step.detail;
        const rain = document.createElement("span");
        rain.className = "readout-rain";
        rain.textContent = `${step.rain.toFixed(1)} mm this hour`;
        const extra = document.createElement("span");
        extra.className = "readout-extra";
        extra.textContent = [
            step.chance == null ? null : `${step.chance}% chance`,
            step.temp == null ? null : `${Math.round(step.temp)}°C`,
        ].filter(Boolean).join(" · ");

        readout.replaceChildren(when, rain, extra);
        // Moving the slider moves the drain network with it. Drawing the panel does
        // not: the network stays on the live nowcast until someone picks an hour.
        if (publish) publishRain(step.rain, `Forecast panel, ${step.detail} (hourly)`, "forecast");
        [...ticks.children].forEach((tick, i) => tick.classList.toggle("on", i === index));
    };

    const select = (key, publish = true) => {
        steps = views[key].steps;
        for (const [name, button] of Object.entries(buttons)) {
            button.setAttribute("aria-pressed", String(name === key));
        }
        note.textContent = views[key].note;
        slider.max = steps.length - 1;
        slider.value = 0;
        ticks.replaceChildren(...steps.map((step) => {
            const tick = document.createElement("span");
            tick.textContent = step.label;
            return tick;
        }));
        showStep(publish);
    };

    slider.addEventListener("input", () => showStep());
    select("now", false);

    const timeline = document.createElement("div");
    timeline.className = "timeline";
    timeline.append(toggle, note, slider, ticks, readout);
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
