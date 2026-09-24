// The screen around the map: tabs, the side panel (a bottom sheet on a phone), the
// headline numbers, the live/scenario pill, the loading card and the street search.
// The other scripts fill the panels; they talk to the layout only through
// window.shell, so none of them needs to know whether it is on a desk or in a hand.

(() => {
    const root = document.documentElement;
    const sheet = document.getElementById("sheet");
    const handle = document.getElementById("sheet-handle");
    const tabs = [...document.querySelectorAll("#tabs [role=tab]")];
    const panels = [...document.querySelectorAll(".panel[data-panel]")];
    const detail = document.getElementById("detail");
    const detailBody = document.getElementById("detail-body");
    const topbar = document.querySelector(".topbar");
    const phone = matchMedia("(max-width: 48rem)");
    const NAV_PX = 64;
    const PEEK_PX = 168;

    // ─── Sheet ───────────────────────────────────────────────────────────────────
    // Three heights on a phone: a peek that keeps the map in view and shows the top
    // alert, half, and full. On a desk the sheet is a fixed side panel.

    function heights() {
        const free = window.innerHeight - (topbar?.offsetHeight || 56) - NAV_PX;
        return { peek: PEEK_PX, half: Math.round(free * 0.55), full: free - 8 };
    }

    function setSheet(state, height) {
        sheet.dataset.state = state;
        document.body.dataset.sheet = state;
        const px = height ?? heights()[state];
        root.style.setProperty("--sheet-h", `${phone.matches ? px : 0}px`);
        relayout();
    }

    let relayoutTimer = 0;
    function relayout() {
        clearTimeout(relayoutTimer);
        // `map` is location.js's top-level const: shared by name between scripts, not on window.
        relayoutTimer = setTimeout(() => { if (typeof map !== "undefined") map.invalidateSize({ pan: false }); }, 260);
    }

    handle.addEventListener("click", () => {
        if (dragged) return;
        const next = { peek: "half", half: "full", full: "peek" }[sheet.dataset.state] || "half";
        setSheet(next);
    });

    // Drag the handle: the sheet follows the finger, then snaps to the nearest height.
    let startY = 0, startH = 0, dragged = false;
    handle.addEventListener("pointerdown", (event) => {
        if (!phone.matches) return;
        startY = event.clientY;
        startH = sheet.getBoundingClientRect().height;
        dragged = false;
        handle.setPointerCapture(event.pointerId);
        sheet.classList.add("dragging");
    });
    handle.addEventListener("pointermove", (event) => {
        if (!sheet.classList.contains("dragging")) return;
        const dy = startY - event.clientY;
        if (Math.abs(dy) > 6) dragged = true;
        const h = Math.max(PEEK_PX - 40, Math.min(heights().full, startH + dy));
        root.style.setProperty("--sheet-h", `${h}px`);
    });
    handle.addEventListener("pointerup", (event) => {
        if (!sheet.classList.contains("dragging")) return;
        sheet.classList.remove("dragging");
        handle.releasePointerCapture(event.pointerId);
        if (!dragged) return;
        const now = sheet.getBoundingClientRect().height;
        const h = heights();
        const nearest = Object.entries(h).sort((a, b) => Math.abs(a[1] - now) - Math.abs(b[1] - now))[0][0];
        setSheet(nearest);
        setTimeout(() => { dragged = false; }, 0);
    });

    // ─── Tabs ────────────────────────────────────────────────────────────────────

    function showTab(name) {
        closeDetail();
        for (const tab of tabs) tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
        for (const panel of panels) panel.hidden = panel.dataset.panel !== name;
        document.getElementById("panels").scrollTop = 0;
        if (phone.matches && sheet.dataset.state === "peek") setSheet("half");
        window.dispatchEvent(new CustomEvent("tab-change", { detail: name }));
    }
    for (const tab of tabs) tab.addEventListener("click", () => {
        // Tapping the open tab again on a phone folds the sheet away, like a drawer.
        if (tab.getAttribute("aria-selected") === "true" && phone.matches && sheet.dataset.state !== "peek") {
            setSheet("peek");
            return;
        }
        showTab(tab.dataset.tab);
    });

    // ─── Detail ──────────────────────────────────────────────────────────────────
    // One street, over whatever tab is open, with a way back.

    function openDetail(html) {
        detailBody.innerHTML = html;
        detail.hidden = false;
        document.getElementById("panels").scrollTop = 0;
        sheet.classList.add("has-detail");
        if (phone.matches && sheet.dataset.state === "peek") setSheet("half");
    }
    function closeDetail() {
        detail.hidden = true;
        sheet.classList.remove("has-detail");
    }
    document.getElementById("detail-close").addEventListener("click", closeDetail);

    // ─── Headline ────────────────────────────────────────────────────────────────

    const pill = document.getElementById("mode-pill");
    const kpi = {
        wet: document.getElementById("kpi-wet"),
        blocked: document.getElementById("kpi-blocked"),
        manholes: document.getElementById("kpi-manholes"),
    };
    function setKpis(values) {
        for (const [key, node] of Object.entries(kpi)) {
            const v = values[key];
            node.textContent = v == null ? "–" : Number(v).toLocaleString();
            node.parentElement.classList.toggle("zero", !v);
        }
    }
    function setMode(kind, text) {
        pill.className = `mode-pill ${kind}`;
        pill.textContent = text;
    }

    // ─── Loading ─────────────────────────────────────────────────────────────────
    // The first run on a cold server takes a minute or two; the card says what it is
    // doing and for how long, so the screen never looks frozen.

    const loading = document.getElementById("loading");
    const steps = [...document.querySelectorAll("#loading-steps li")];
    const loadingTime = document.getElementById("loading-time");
    let loadingTimer = 0, loadingShow = 0;
    function setLoading(on, title) {
        clearInterval(loadingTimer);
        clearTimeout(loadingShow);
        if (!on) {
            loading.hidden = true;
            return;
        }
        document.getElementById("loading-title").textContent = title || "Running the flood forecast";
        const began = Date.now();
        // A cached answer comes back at once: only show the card if it does not.
        loadingShow = setTimeout(() => { loading.hidden = false; }, 450);
        const tick = () => {
            const s = (Date.now() - began) / 1000;
            // The server does not report progress, so the steps advance on the times the
            // stages take on a warm server; the last one stays until the answer is in.
            const at = s < 2 ? 0 : s < 6 ? 1 : s < 14 ? 2 : 3;
            steps.forEach((li, i) => { li.className = i < at ? "done" : i === at ? "on" : ""; });
            loadingTime.textContent = s > 20
                ? `${Math.round(s)} s · the first forecast after a restart builds the whole city model`
                : `${Math.round(s)} s`;
        };
        tick();
        loadingTimer = setInterval(tick, 500);
    }

    // ─── Search ──────────────────────────────────────────────────────────────────
    // The top search filters the street table, which is where the answer is.

    document.getElementById("top-search").addEventListener("submit", (event) => {
        event.preventDefault();
        runSearch();
    });
    document.getElementById("top-search-input").addEventListener("input", () => {
        if (document.getElementById("top-search-input").value.length >= 3) runSearch(false);
    });
    function runSearch(open = true) {
        const q = document.getElementById("top-search-input").value;
        const box = document.querySelector("#street-depths input[type=search]");
        if (!box) return;
        box.value = q;
        box.dispatchEvent(new Event("input"));
        if (open) showTab("streets");
    }

    // ─── Start ───────────────────────────────────────────────────────────────────

    phone.addEventListener("change", () => setSheet(phone.matches ? "peek" : "half"));
    window.addEventListener("resize", () => setSheet(sheet.dataset.state));
    setSheet(phone.matches ? "peek" : "half");

    window.shell = {
        tab: showTab,
        detail: openDetail,
        closeDetail,
        // Put the map back in view on a phone: after flying somewhere, or to pick a point.
        peek: () => { if (phone.matches) setSheet("peek"); },
        isPhone: () => phone.matches,
        kpis: setKpis,
        mode: setMode,
        loading: setLoading,
    };
})();
