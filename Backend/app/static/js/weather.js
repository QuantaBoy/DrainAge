const form = document.getElementById("district-form");
const input = document.getElementById("district");
const out = document.getElementById("weather");

const ROWS = [
    ["Condition", (w) => w.condition],
    ["Temperature", (w) => `${w.temp_c} °C (feels ${w.feels_like_c} °C)`],
    ["Humidity", (w) => `${w.humidity_pct}%`],
    ["Wind", (w) => `${w.wind_ms} m/s`],
    ["Rain, last 1h", (w) => `${w.rain_1h_mm} mm`],
];

const DEFAULT_DISTRICT = "Chennai";

function failIn(box, message) {
    const span = document.createElement("span");
    span.className = "warn";
    span.textContent = message;
    box.replaceChildren(span);
}

const fail = (message) => failIn(out, message);

form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const district = input.value.trim();
    if (!district) return;

    out.textContent = "Loading…";
    let res, body;
    try {
        res = await fetch(`/data-collection/weather?district=${encodeURIComponent(district)}`);
        body = await res.json();
    } catch (err) {
        return fail("Could not reach the server.");
    }
    if (!res.ok) return fail(String(body.detail || res.statusText));

    const where = [body.district, body.state, body.country].filter(Boolean).join(", ");

    // Built as nodes, not markup: every value here is third-party API text.
    const heading = document.createElement("strong");
    heading.textContent = where;
    const table = document.createElement("table");
    for (const [label, value] of ROWS) {
        const row = table.insertRow();
        row.insertCell().textContent = label;
        row.insertCell().textContent = value(body);
    }
    out.replaceChildren(heading, table);

    showPlace(body.lat, body.lon, where);
    renderForecast(`district=${encodeURIComponent(district)}`, where);
});

async function renderForecast(query, title) {
    const box = document.getElementById("forecast");
    box.textContent = "Loading forecast…";

    let res, body;
    try {
        res = await fetch(`/data-collection/forecast?${query}`);
        body = await res.json();
    } catch (err) {
        return failIn(box, "Could not reach the server for the forecast.");
    }
    // Shown, not swallowed: a hidden forecast looks identical to a missing feature.
    if (!res.ok) return failIn(box, String(body.detail || res.statusText));

    const peak = Math.max(...body.days.map((d) => d.rain_mm), 1);
    const table = document.createElement("table");
    table.className = "forecast";
    for (const day of body.days) {
        const row = table.insertRow();
        const when = new Date(day.date + "T00:00:00");
        row.insertCell().textContent = when.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

        // Bar width is relative to the wettest day shown, so the shape of the week
        // reads at a glance even when totals are small.
        const barCell = row.insertCell();
        const bar = document.createElement("span");
        bar.className = "bar";
        bar.style.width = `${Math.round((day.rain_mm / peak) * 100)}%`;
        barCell.append(bar);

        row.insertCell().textContent = `${day.rain_mm.toFixed(1)} mm`;
        row.insertCell().textContent = day.rain_chance_pct == null ? "" : `${day.rain_chance_pct}%`;
        row.insertCell().textContent = day.temp_max_c == null ? "" : `${Math.round(day.temp_max_c)}°`;
    }

    const heading = document.createElement("strong");
    const where = title || [body.district, body.state].filter(Boolean).join(", ");
    heading.textContent = where ? `Rainfall forecast, ${where}` : "Rainfall forecast";
    box.replaceChildren(heading, buildTimeline(body), table);
}

// One slider across both scales: the next few hours, then the coming days. Flood
// timing is read at hour resolution, while the week gives the build-up around it.
function buildTimeline(body) {
    const steps = [
        ...body.hours.map((h, i) => ({
            label: i === 0 ? "Now" : new Date(h.time).toLocaleTimeString(undefined, { hour: "numeric" }),
            detail: new Date(h.time).toLocaleString(undefined, { weekday: "short", hour: "numeric" }),
            rain: h.rain_mm, chance: h.rain_chance_pct, temp: h.temp_c, unit: "mm this hour",
        })),
        ...body.days.map((d) => {
            const when = new Date(d.date + "T00:00:00");
            return {
                label: when.toLocaleDateString(undefined, { weekday: "short" }),
                detail: when.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }),
                rain: d.rain_mm, chance: d.rain_chance_pct, temp: d.temp_max_c, unit: "mm this day",
            };
        }),
    ];

    const wrap = document.createElement("div");
    wrap.className = "timeline";

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

    const show = () => {
        const step = steps[slider.value];
        readout.replaceChildren();
        const when = document.createElement("strong");
        when.textContent = step.detail;
        const rain = document.createElement("span");
        rain.className = "readout-rain";
        rain.textContent = `${step.rain.toFixed(1)} ${step.unit}`;
        const rest = document.createElement("span");
        rest.className = "readout-rest";
        rest.textContent = [
            step.chance == null ? null : `${step.chance}% chance`,
            step.temp == null ? null : `${Math.round(step.temp)}°C`,
        ].filter(Boolean).join(" · ");
        readout.append(when, rain, rest);

        [...ticks.children].forEach((t, i) => t.classList.toggle("on", i === Number(slider.value)));
    };

    slider.addEventListener("input", show);
    show();

    wrap.append(slider, ticks, readout);
    return wrap;
}

// The forecast is the point of the page, so it loads with the page rather than
// waiting for a search: the last known position if there is one, else a default.
(function initialForecast() {
    let saved = null;
    try {
        saved = JSON.parse(localStorage.getItem(LAST_FIX_KEY) || "null");
    } catch (e) { /* storage unavailable */ }

    if (saved) {
        renderForecast(`lat=${saved.lat}&lon=${saved.lon}`, "your last known location");
    } else {
        renderForecast(`district=${encodeURIComponent(DEFAULT_DISTRICT)}`);
    }
})();
