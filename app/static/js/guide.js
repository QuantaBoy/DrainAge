// The guide: a short walk through the screen, one part at a time, saying what each
// part is for and what to pick. It opens by itself on a first visit and from the
// Guide button in the top bar after that. Steps that point into a panel open that
// tab first; two steps offer a one-click demo (a 60 mm/h storm, a sample route).

(() => {
    const SEEN_KEY = "guide-seen-v1";
    const remember = () => { try { localStorage.setItem(SEEN_KEY, "1"); } catch (err) {} };
    const seen = () => { try { return localStorage.getItem(SEEN_KEY) === "1"; } catch (err) { return true; } };

    const tab = (name) => () => window.shell?.tab(name);
    const selectRain = (value) => {
        pondingUi.rain.value = value;
        pondingUi.rain.dispatchEvent(new Event("change"));
    };

    const STEPS = [
        {
            title: "Welcome to the flood nowcast",
            text: "This screen forecasts which Chennai streets will flood in the next 3 hours, " +
                  "how deep, and when. This guide takes a minute; you can skip it and reopen " +
                  "it from the Guide button at the top.",
        },
        {
            target: "#mode-pill",
            title: "Live or scenario",
            text: "<b>LIVE</b> means the map uses the real rain forecast. <b>SCENARIO</b> means a " +
                  "what-if storm you picked, never a real forecast. Always check this first.",
        },
        {
            target: "#kpis",
            title: "The three numbers that matter",
            text: "Streets under water, streets too deep to drive (over 30 cm), and manholes " +
                  "overflowing, at the time shown on the timeline.",
        },
        {
            target: "#timebar",
            title: "Move through the next 3 hours",
            text: "Drag the slider, or press play, to watch the water rise and fall in 5-minute " +
                  "steps. The bars show how much rain falls in each step.",
        },
        {
            target: '#tabs [data-tab="alerts"]',
            before: tab("alerts"),
            title: "Alerts: start here",
            text: "Streets going under in the next 30 minutes come first, then streets under water " +
                  "now. Tap one to see its depth over time and where it is on the map.",
        },
        {
            target: '#tabs [data-tab="streets"]',
            before: tab("streets"),
            title: "Streets: the full list",
            text: "Every flooded street, sorted by when it goes under. Filter by street, locality " +
                  "or ward, and download it as a spreadsheet (CSV).",
        },
        {
            target: '#tabs [data-tab="whatif"]',
            before: tab("whatif"),
            title: "What-if: test a storm",
            text: "<b>Rainfall:</b> keep <i>Live forecast</i> for today, or pick a storm, e.g. " +
                  "<i>60 mm/h</i> for a heavy monsoon hour.<br><b>Drains working at:</b> " +
                  "<i>Full capacity</i> is the best case; <i>Half</i> shows silted drains.",
            action: { label: "Try a 60 mm/h storm", run: () => selectRain("60") },
        },
        {
            target: '#tabs [data-tab="route"]',
            before: tab("route"),
            title: "Route: get around the water",
            text: "Type <b>From</b> and <b>To</b> (or press <i>Map</i> and click the map), choose " +
                  "when you <b>Leave</b>, then <i>Find safe route</i>. The blue line avoids " +
                  "roads over 30 cm; the grey dashed line is the shortest route, with its " +
                  "flooded parts marked.",
            action: {
                label: "Show a sample route",
                run: () => {
                    routeUi.from.input.value = "Chennai Central";
                    setPoint(routeUi.from, 13.0827, 80.2707, "Chennai Central");
                    routeUi.to.input.value = "Guindy";
                    setPoint(routeUi.to, 13.0067, 80.2206, "Guindy");
                    routeUi.go.click();
                },
            },
        },
        {
            target: '#tabs [data-tab="more"]',
            before: tab("more"),
            title: "More: weather and drains",
            text: "Current weather and the 7-day rain forecast, and the storm water drain " +
                  "network: switch it on to see which drains are overloaded, by zone or ward.",
        },
        {
            target: ".layer-button",
            title: "Map layers",
            text: "Choose what the map shows. Keep <b>Flood forecast</b> on; add <b>Storm Water " +
                  "Drains</b> or <b>Elevation</b> to see why a street floods, or <b>Rain</b> to " +
                  "see the storm moving.",
            before: () => setLayersOpen(true),
        },
        {
            target: "#top-search",
            title: "Find a street",
            text: "Type a street or area name to jump to it in the street list.",
        },
        {
            title: "You're set",
            text: "Tip: tap any coloured street or manhole on the map for its details. " +
                  "Yellow is under 5 cm, orange 5 to 15, red 15 to 30, purple over 30 cm.",
        },
    ];

    // ─── Overlay ─────────────────────────────────────────────────────────────────

    const dim = document.createElement("div");
    dim.className = "guide-dim";
    const spot = document.createElement("div");
    spot.className = "guide-spot";
    const card = document.createElement("div");
    card.className = "guide-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "guide-title");
    card.innerHTML = `
        <div class="guide-count" id="guide-count"></div>
        <h2 class="guide-title" id="guide-title"></h2>
        <p class="guide-text" id="guide-text"></p>
        <button type="button" class="guide-action" id="guide-action" hidden></button>
        <div class="guide-foot">
            <button type="button" class="guide-skip" id="guide-skip">Skip</button>
            <span class="guide-nav">
                <button type="button" class="guide-back" id="guide-back">Back</button>
                <button type="button" class="guide-next" id="guide-next">Next</button>
            </span>
        </div>`;
    const parts = Object.fromEntries(["count", "title", "text", "action", "skip", "back", "next"]
        .map((id) => [id, card.querySelector(`#guide-${id}`)]));

    let index = -1;
    let returnFocus = null;

    function visible(el) {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }

    function place() {
        if (index < 0) return;
        const step = STEPS[index];
        const target = step.target ? document.querySelector(step.target) : null;
        const pad = 6;
        const vw = window.innerWidth, vh = window.innerHeight;
        const cw = card.offsetWidth, ch = card.offsetHeight;

        if (!visible(target)) {
            spot.hidden = true;
            dim.hidden = false;
            card.style.left = `${Math.max(12, (vw - cw) / 2)}px`;
            card.style.top = `${Math.max(12, (vh - ch) / 2)}px`;
            return;
        }
        dim.hidden = true;                 // the spotlight's own shadow dims the rest
        spot.hidden = false;
        const r = target.getBoundingClientRect();
        Object.assign(spot.style, {
            left: `${r.left - pad}px`, top: `${r.top - pad}px`,
            width: `${r.width + 2 * pad}px`, height: `${r.height + 2 * pad}px`,
        });
        // Below the target if it fits, else above, else beside it; always on screen.
        let top = r.bottom + 14;
        if (top + ch > vh - 12) top = r.top - ch - 14;
        let left = r.left;
        if (top < 12) {
            top = Math.min(Math.max(12, r.top), vh - ch - 12);
            left = r.right + 14 + cw < vw ? r.right + 14 : r.left - cw - 14;
        }
        card.style.left = `${Math.min(Math.max(12, left), vw - cw - 12)}px`;
        card.style.top = `${Math.min(Math.max(12, top), vh - ch - 12)}px`;
    }

    function show(i) {
        index = i;
        const step = STEPS[i];
        step.before?.();
        parts.count.textContent = `${i + 1} of ${STEPS.length}`;
        parts.title.textContent = step.title;
        parts.text.innerHTML = step.text;
        parts.action.hidden = !step.action;
        if (step.action) parts.action.textContent = step.action.label;
        parts.back.hidden = i === 0;
        parts.next.textContent = i === STEPS.length - 1 ? "Done" : i === 0 ? "Start" : "Next";
        parts.skip.hidden = i === STEPS.length - 1;
        // Tabs open and the phone sheet slides first; place once the layout settles.
        requestAnimationFrame(place);
        setTimeout(place, 320);
        parts.next.focus();
    }

    function open() {
        returnFocus = document.activeElement;
        document.body.append(dim, spot, card);
        document.body.classList.add("guide-on");
        show(0);
    }

    function close() {
        remember();
        index = -1;
        dim.remove(); spot.remove(); card.remove();
        document.body.classList.remove("guide-on");
        window.shell?.tab("alerts");
        returnFocus?.focus?.();
    }

    parts.next.addEventListener("click", () => (index < STEPS.length - 1 ? show(index + 1) : close()));
    parts.back.addEventListener("click", () => show(Math.max(0, index - 1)));
    parts.skip.addEventListener("click", close);
    parts.action.addEventListener("click", () => {
        const step = STEPS[index];
        close();
        step.action.run();
    });
    dim.addEventListener("click", close);
    spot.addEventListener("click", close);
    document.addEventListener("keydown", (event) => {
        if (index < 0) return;
        if (event.key === "Escape") close();
        else if (event.key === "ArrowRight") parts.next.click();
        else if (event.key === "ArrowLeft" && index > 0) parts.back.click();
    });
    window.addEventListener("resize", place);

    document.getElementById("guide-open")?.addEventListener("click", open);
    // First visit: open once the page has drawn, so the spotlight lands on real layout.
    if (!seen()) window.addEventListener("load", () => setTimeout(open, 600));

    window.guide = { open };
})();
