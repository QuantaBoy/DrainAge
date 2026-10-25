// Resources page: the dataset catalogue. What each dataset is, where it came from, what
// was done with it, what it was checked against and what that found. Whether each file
// is on this server comes live from /data-collection/inventory (app/services/inventory.py),
// matched by `file` to the inventory's names. Built as nodes: no API text is parsed as HTML.

const CATEGORIES = [
    ["all", "All"],
    ["drainage", "Drainage"],
    ["terrain", "Terrain"],
    ["city", "City map"],
    ["record", "Flood record"],
    ["live", "Live feeds"],
];

const DATASETS = [
    {
        cat: "drainage", file: "Storm water drain survey",
        name: "Storm water drain survey",
        source: "Greater Chennai Corporation", link: "https://data.opencity.in/dataset/chennai-stormwater-drain-swd-maps",
        use: "What every drain can carry",
        did: [
            "Read every drain's route, section, invert levels, material and condition",
            "Set each drain's flow direction from its invert levels, rejecting impossible levels (one read 104,860 m)",
            "Worked out each drain's full capacity from its size, slope and wall material, reduced for silt and obstructions",
            "Added up the area draining to each drain down the whole network",
        ],
        checked: "The GCC / SECON-JBA ward map survey: 7,951 manholes with measured levels and sizes",
        found: [
            "Ward and zone agree in 90 of 91 wards",
            "The two surveys' drain levels differ by a median 0.63 m, and 97.5% of this survey's sections are square",
            "So wherever a ward map draws a drain, the ward map's measured drain replaces this one",
        ],
    },
    {
        cat: "drainage", file: "Ward base-map sheets",
        name: "Ward base maps (92 sheets)",
        source: "GCC / SECON-JBA survey, December 2020", link: null,
        use: "The measured drain network, manhole by manhole",
        did: [
            "Rendered each scanned sheet in tiles and read its drawn text by OCR",
            "Placed each sheet on the map from its own UTM grid lines, to 0.1 m",
            "Read 16 sheets that are stored rotated in the frame they are shown in",
        ],
        checked: "Each manhole's label on the map against its row in the sheet's table; the grid against the sheet's latitude lines",
        found: [
            "All 92 sheets placed correctly",
            "5,115 manhole labels match their table row to the millimetre",
        ],
    },
    {
        cat: "drainage", file: "Digitised ward network",
        name: "Digitised ward drain network",
        source: "Read from the ward maps by our own tool", link: null,
        use: "The drains the flood model routes water through",
        did: [
            "Drew 12,566 drains (1,010 km) from manhole to manhole",
            "Took each section from the manhole survey (65%) or from the nearest measured manhole along the drain (29%)",
            "Set flow direction from the surveyors' arrows, then the manhole numbering, then the fall",
            "Joined it with the city survey into one network; closed gaps up to 30 m",
            "Gave 798 drains with no size the typical measured section, marked as assumed",
            "Let 4,045 drains that end with no channel put their water back on the ground",
        ],
        checked: "The surveyors' own flow arrows (5,498 drains) and the manholes' chain numbering",
        found: [
            "96.8% of drains stay within one numbered manhole chain",
            "The numbering agrees with the arrows 87% of the time",
        ],
    },
    {
        cat: "drainage", file: "Ward and zone index",
        name: "Ward and zone index",
        source: "Title blocks of the ward maps", link: null,
        use: "Which ward and zone every drain and street belongs to",
        did: ["Indexed the ward and zone printed on each sheet"],
        checked: "The zone recorded on each drain in the city survey",
        found: ["196 wards across the two sources; the single disagreement is flagged on the map"],
    },
    {
        cat: "drainage", file: "Basin channel layer",
        name: "Basin channels (canals, rivers, drains)",
        source: "Chennai Basin Drainage Maps, OpenCity", link: "https://data.opencity.in/dataset/chennai-basin-drainage-maps",
        use: "Where drains empty, and which river basin each flooded street belongs to",
        did: [
            "Loaded 934 named channels with their river sub-basin",
            "Cut the 78 km of channel missing from other maps into the terrain",
        ],
        checked: "OpenStreetMap waterways, the city drain survey, and the official river basins",
        found: [
            "Stated lengths match the drawn channels to 0.3%",
            "96.0% of our terrain's catchments fall in the same official basin",
        ],
    },
    {
        cat: "terrain", file: "Terrain model (GEDTM30)",
        name: "Ground height (GEDTM30, 30 m)",
        source: "OpenGeoHub via OpenTopography", link: "https://portal.opentopography.org/datasets",
        use: "Where rain runs and where it stands",
        did: [
            "Smoothed out single-cell spikes from buildings",
            "Corrected it to the road level surveyed at 4,523 manholes, taking off building clutter",
            "Filled small pits that are noise, keeping every mapped lake and tank",
            "Cut the city into the hollows water collects in",
        ],
        checked: "A second terrain model, a survey point file, railway station levels and Marina beach",
        found: [
            "The error against surveyed road levels halves (spread 2.93 m to 1.68 m)",
            "A constant 2.87 m difference is left unapplied: open ground and the beach do not show it",
        ],
    },
    {
        cat: "terrain", file: "Terrain cross-check (COP30 + tiles)",
        name: "Second terrain model (Copernicus)",
        source: "ESA Copernicus via OpenTopography", link: "https://portal.opentopography.org/datasets",
        use: "The check on the main terrain model",
        did: ["Compared cell by cell with the main terrain over the same area"],
        checked: "The main terrain model",
        found: ["Sits 1.00 m higher on land: exactly the expected gap between a surface model and bare earth"],
    },
    {
        cat: "terrain", file: "Elevation check points",
        name: "Elevation check points",
        source: "Supplied survey points", link: null,
        use: "Proof the heights are above sea level",
        did: ["Compared 1,644 surveyed points with the terrain model"],
        checked: "The terrain model and the EGM2008 geoid",
        found: ["They sit 92.1 m below the terrain, where the geoid is -92.03 m: the terrain is on the right datum"],
    },
    {
        cat: "city", file: "Streets",
        name: "Streets",
        source: "OpenStreetMap", link: "https://www.openstreetmap.org/",
        use: "Where depth is reported, and the roads routes are found over",
        did: [
            "Downloaded 80,206 roads over the city",
            "Placed a point every 20 m along every road to read depth",
            "Built the road network the safe routes are found on",
        ],
        checked: "OpenStreetMap's own bridge tags",
        found: ["1,011 bridges are kept out of flooding: the terrain under a bridge is the river below it"],
    },
    {
        cat: "city", file: "Waterways",
        name: "Rivers, canals and nullahs",
        source: "OpenStreetMap", link: "https://www.openstreetmap.org/",
        use: "Keeping channels draining in the terrain",
        did: ["Cut 574 waterways 2 m into the terrain, so road embankments do not act as dams"],
        checked: "Depths with and without them",
        found: ["False canal 'lakes' disappear: 5th Street, Kilpauk drops from 212 cm to 51 cm"],
    },
    {
        cat: "city", file: "Building cover",
        name: "Building footprints",
        source: "OpenStreetMap", link: "https://www.openstreetmap.org/",
        use: "Room for water between buildings",
        did: ["Turned 373,298 footprints into the built share of every 30 m cell"],
        checked: "The terrain's error at surveyed manholes, which rises with building cover",
        found: ["Water stands only between buildings, so a street between houses fills faster and deeper"],
    },
    {
        cat: "city", file: "Water bodies",
        name: "Lakes, tanks and ponds",
        source: "OpenStreetMap", link: "https://www.openstreetmap.org/",
        use: "Real hollows the terrain cleaning must keep",
        did: ["Mapped 917 water bodies onto the terrain grid"],
        checked: "The pit filling that removes terrain noise",
        found: ["Every mapped lake and tank survives; streets inside them are not reported as flooded roads"],
    },
    {
        cat: "record", file: "Flood hazard zones",
        name: "Flood hazard zones",
        source: "Chennai flood study", link: null,
        use: "The flood history of every street",
        did: ["Painted 7,453 zones, Very Low to Very High, onto the model's 30 m grid"],
        checked: "The model's own flooding",
        found: ["Streets in High and Very High zones flood 7 times as often in the model as streets outside"],
    },
    {
        cat: "record", file: "Flood extents, 5 to 200 year",
        name: "Flood extents, 5 to 200 year",
        source: "Chennai flood study", link: null,
        use: "How often each street floods by the record",
        did: ["Painted 3,102 extent polygons onto the grid, keeping the most frequent one covering each cell"],
        checked: "The model's flooded streets",
        found: ["Each flooded street shows whether it lies in a frequent flood extent"],
    },
    {
        cat: "record", file: "December 2015 flooding points",
        name: "December 2015 flood places",
        source: "Chennai flood study", link: null,
        use: "The test the model is held to",
        did: [
            "Ran the model for three storms at 27 settings",
            "Chose the settings on half the city's zones and measured on the other half",
        ],
        checked: "Random street points at the same overall wetness (chance)",
        found: [
            "At 30 mm/h the model finds 82% of the 753 places, against 42% by chance",
            "The lead holds on zones it was not tuned on (+42 points)",
            "The settings already in use were as good as any tried",
        ],
    },
    {
        cat: "record", file: "Inundation depth points",
        name: "Recorded flood depths",
        source: "Chennai flood study", link: null,
        use: "How deep the model's water is against reality",
        did: ["Compared 192 recorded depths with the deepest modelled water within 100 m"],
        checked: "The model's depths, by rank (the file states no unit)",
        found: ["Only a loose match (rank correlation 0.17 to 0.25): trust where and when, treat centimetres as indicative"],
    },
    {
        cat: "live", service: "Open-Meteo",
        name: "Rainfall forecast", source: "Open-Meteo", link: "https://open-meteo.com/",
        use: "The rain for the next 3 hours, every 15 minutes",
        did: ["Spread each 15-minute value over three 5-minute steps", "Cached for 5 minutes per area"],
        checked: "The same provider's hourly feed, summed to the same hours",
        found: ["They agree hour for hour; the check also caught a clock error in the first time slot"],
    },
    {
        cat: "live", service: "OpenWeather",
        name: "Weather map tiles", source: "OpenWeather", link: "https://openweathermap.org/",
        use: "Rain, temperature, wind and cloud pictures on the map",
        did: ["Passed through the server so the API key never reaches the browser"],
        checked: "Not a model input",
        found: ["Pictures only; from a different provider, so they can differ from the forecast"],
    },
    {
        cat: "live", service: "OpenStreetMap Nominatim",
        name: "Place search", source: "OpenStreetMap Nominatim", link: "https://nominatim.openstreetmap.org/",
        use: "Turning a typed place into a point for routes",
        did: ["Limited to the Chennai area; every answer cached"],
        checked: "Our own street layer, as the fallback when it finds nothing",
        found: ["Other matches stay on screen to pick from, never silently assumed"],
    },
    {
        cat: "live", service: "OpenStreetMap Overpass",
        name: "Map downloads", source: "OpenStreetMap Overpass", link: "https://overpass-api.de/",
        use: "Fetching the roads and waterways",
        did: ["Downloaded once and kept on the server"],
        checked: "OpenStreetMap's own tags",
        found: ["Bridges and tunnels are marked, so they are treated correctly"],
    },
    {
        cat: "live", service: "OpenTopography",
        name: "Terrain download", source: "OpenTopography", link: "https://opentopography.org/",
        use: "Fetching the terrain models",
        did: ["Downloaded exactly the city's box, once"],
        checked: "The second terrain model",
        found: ["Both terrain models came through intact and on the same datum"],
    },
];

(() => {
    const list = document.getElementById("ds-list");
    const chips = document.getElementById("ds-chips");
    const search = document.getElementById("ds-search");
    const expand = document.getElementById("ds-expand");
    let category = "all";
    let found = {};         // inventory name -> what the server found
    let services = new Set();

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const size = (bytes) => bytes >= 2 ** 20 ? `${(bytes / 2 ** 20).toFixed(1)} MB`
        : `${Math.max(1, Math.round(bytes / 1024))} KB`;
    const catName = (key) => (CATEGORIES.find(([k]) => k === key) || [])[1];

    function status(d) {
        if (d.service) {
            const known = services.has(d.service);
            return el("span", `rs-status ${known ? "ok" : ""}`, known ? "Live service" : "Service");
        }
        const f = found[d.file];
        if (!f) return el("span", "rs-status", "Checking…");
        if (!f.present) return el("span", "rs-status missing", "Not on this server");
        return el("span", "rs-status ok", [f.records, size(f.size_bytes)].filter(Boolean).join(" · "));
    }

    function column(title, items) {
        const box = el("div", "rs-col");
        box.append(el("h4", "", title));
        if (Array.isArray(items)) {
            const ul = el("ul");
            ul.append(...items.map((t) => el("li", "", t)));
            box.append(ul);
        } else box.append(el("p", "", items));
        return box;
    }

    function card(d) {
        const item = el("details", `rs-item cat-${d.cat}`);
        const head = el("summary", "rs-summary");
        const main = el("div", "rs-main");
        const title = el("div", "rs-title");
        title.append(el("span", `rs-tag cat-${d.cat}`, catName(d.cat)), el("h3", "", d.name));
        const src = el("div", "rs-source");
        if (d.link) {
            const a = el("a", "", d.source);
            a.href = d.link; a.target = "_blank"; a.rel = "noopener";
            a.addEventListener("click", (e) => e.stopPropagation());
            src.append(a);
        } else src.textContent = d.source;
        main.append(title, src, el("p", "rs-use", d.use));
        head.append(main, status(d), el("span", "rs-chevron"));
        const body = el("div", "rs-body");
        body.append(column("What we did", d.did), column("Checked against", d.checked),
            column("What we found", d.found));
        item.append(head, body);
        return item;
    }

    function render() {
        // Cards someone has opened stay open when the list is redrawn (the live file check
        // can finish after the first click).
        const opened = new Set([...list.querySelectorAll("details[open]")].map((d) => d.dataset.name));
        const q = search.value.trim().toLowerCase();
        const shown = DATASETS.filter((d) => (category === "all" || d.cat === category) &&
            (!q || [d.name, d.source, d.use, ...d.did, d.checked, ...d.found].join(" ").toLowerCase().includes(q)));
        list.replaceChildren(...(shown.length ? shown.map((d) => {
            const item = card(d);
            item.dataset.name = d.name;
            item.open = opened.has(d.name);
            return item;
        }) : [el("p", "rs-empty", "No dataset matches.")]));
        expand.textContent = "Open all";
    }

    chips.replaceChildren(...CATEGORIES.map(([key, label]) => {
        const count = key === "all" ? DATASETS.length : DATASETS.filter((d) => d.cat === key).length;
        const b = el("button", "rs-chip", `${label} ${count}`);
        b.type = "button";
        b.setAttribute("role", "tab");
        b.setAttribute("aria-selected", String(key === category));
        b.addEventListener("click", () => {
            category = key;
            for (const c of chips.children) c.setAttribute("aria-selected", String(c === b));
            render();
        });
        return b;
    }));
    search.addEventListener("input", render);
    expand.addEventListener("click", () => {
        const items = [...list.querySelectorAll("details")];
        const open = items.some((d) => !d.open);
        items.forEach((d) => { d.open = open; });
        expand.textContent = open ? "Close all" : "Open all";
    });
    render();

    fetch("/data-collection/inventory")
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((inv) => {
            found = Object.fromEntries(inv.resources.map((r) => [r.name, r]));
            services = new Set(inv.services.map((s) => s.name));
            document.getElementById("st-found").textContent = `${inv.present} of ${inv.total}`;
            document.getElementById("st-size").textContent = `${Math.round(inv.total_mb)} MB`;
            document.getElementById("st-services").textContent = String(inv.services.length);
            const v = inv.validation?.storms?.["30"];
            document.getElementById("st-check").textContent = v
                ? `${Math.round(v.hit_rate_2015 * 100)}% of 753` : "not run";
            render();
        })
        .catch(() => {
            document.getElementById("st-found").textContent = "unavailable";
        });
})();
