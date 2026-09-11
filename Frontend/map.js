/**
 * Flood Nowcast & GIS Mapping Engine
 * Handles interactive GIS layers for rainfall monitoring and flood depth predictions.
 */
class FloodNowcastMap {
    static CONFIG = {
        DEFAULT_CENTER: [13.0827, 80.2707], // Default focus area (Chennai)
        DEFAULT_ZOOM: 13,
        REFRESH_INTERVAL_MS: 5 * 60 * 1000, // 5 minutes
        TILE_LAYER_URL: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        TILE_ATTRIBUTION: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        ENDPOINTS: {
            RAINFALL: "/api/rainfall/latest",
            FLOOD_PREDICTION: "/api/flood/predict",
        },
    };

    constructor(containerId = "map") {
        this.containerId = containerId;
        this.map = null;
        this.rainfallLayer = L.layerGroup();
        this.floodLayer = L.layerGroup();
        this.pollTimer = null;
    }

    /**
     * Initializes the Leaflet map and layer controllers.
     */
    init() {
        const container = document.getElementById(this.containerId);
        if (!container) {
            console.error(`[FloodNowcastMap] Container element #${this.containerId} not found.`);
            return;
        }

        // Initialize Map Instance
        this.map = L.map(this.containerId).setView(
            FloodNowcastMap.CONFIG.DEFAULT_CENTER,
            FloodNowcastMap.CONFIG.DEFAULT_ZOOM
        );

        // Add OpenStreetMap Base Tile Layer
        L.tileLayer(FloodNowcastMap.CONFIG.TILE_LAYER_URL, {
            attribution: FloodNowcastMap.CONFIG.TILE_ATTRIBUTION,
            maxZoom: 19,
        }).addTo(this.map);

        // Attach Layer Groups to Map
        this.rainfallLayer.addTo(this.map);
        this.floodLayer.addTo(this.map);

        // Initial Data Load & Event Bindings
        this.refreshData();
        this.bindEvents();
        this.startAutoRefresh();
    }

    /**
     * Fetches and renders live rainfall monitoring data.
     */
    async loadRainfallData() {
        try {
            this.rainfallLayer.clearLayers();

            // Mock Data (Replace with API fetch call when backend route /api/rainfall/latest is ready)
            const points = [
                { lat: 13.09, lon: 80.27, mm: 12 },
                { lat: 13.07, lon: 80.28, mm: 28 },
            ];

            points.forEach(({ lat, lon, mm }) => {
                L.circle([lat, lon], {
                    radius: 300,
                    color: "#0066cc",
                    fillColor: "#3399ff",
                    fillOpacity: 0.35,
                    weight: 2,
                })
                    .bindPopup(`<strong>Rainfall Monitor</strong><br/>Intensity: <strong>${mm} mm/hr</strong>`)
                    .addTo(this.rainfallLayer);
            });
        } catch (error) {
            console.error("[FloodNowcastMap] Failed to load rainfall data:", error);
        }
    }

    /**
     * Fetches and renders flood depth prediction data.
     */
    async loadFloodPredictionData() {
        try {
            this.floodLayer.clearLayers();

            // Mock Data (Replace with API fetch call when backend route /api/flood/predict is ready)
            const points = [
                { lat: 13.085, lon: 80.275, depth_cm: 22 },
                { lat: 13.06, lon: 80.26, depth_cm: 8 },
            ];

            points.forEach(({ lat, lon, depth_cm }) => {
                const isHighSeverity = depth_cm > 15;
                const markerColor = isHighSeverity ? "#dc2626" : "#f59e0b";

                L.circleMarker([lat, lon], {
                    radius: 9,
                    color: markerColor,
                    fillColor: markerColor,
                    fillOpacity: 0.85,
                    weight: 2,
                })
                    .bindPopup(`<strong>Flood Risk Alert</strong><br/>Predicted Depth: <strong>${depth_cm} cm</strong>`)
                    .addTo(this.floodLayer);
            });
        } catch (error) {
            console.error("[FloodNowcastMap] Failed to load flood prediction data:", error);
        }
    }

    /**
     * Executes parallel data refreshing.
     */
    async refreshData() {
        await Promise.allSettled([
            this.loadRainfallData(),
            this.loadFloodPredictionData(),
        ]);
    }

    /**
     * Schedules periodic background data polling.
     */
    startAutoRefresh() {
        if (this.pollTimer) clearInterval(this.pollTimer);
        this.pollTimer = setInterval(
            () => this.refreshData(),
            FloodNowcastMap.CONFIG.REFRESH_INTERVAL_MS
        );
    }

    /**
     * Binds DOM control toggles for layer visibility.
     */
    bindEvents() {
        const toggleRainfall = document.getElementById("toggle-rainfall");
        const toggleFlood = document.getElementById("toggle-flood");

        if (toggleRainfall) {
            toggleRainfall.addEventListener("change", (e) => {
                if (e.target.checked) {
                    this.map.addLayer(this.rainfallLayer);
                } else {
                    this.map.removeLayer(this.rainfallLayer);
                }
            });
        }

        if (toggleFlood) {
            toggleFlood.addEventListener("change", (e) => {
                if (e.target.checked) {
                    this.map.addLayer(this.floodLayer);
                } else {
                    this.map.removeLayer(this.floodLayer);
                }
            });
        }
    }
}

// Instantiate and initialize when DOM is ready
document.addEventListener("DOMContentLoaded", () => {
    const floodMapApp = new FloodNowcastMap("map");
    floodMapApp.init();
});

