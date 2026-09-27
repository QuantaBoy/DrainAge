# Chennai street flood nowcast (SIH 26085)

A web dashboard that forecasts, street by street, where Chennai floods over the next
three hours. Rainfall runs off the terrain into the storm water drains; the drains
carry what they can; the water they cannot take surcharges out of the manholes and
spreads over the streets. The page shows when each street goes under, how deep it gets,
when it clears, and a route that avoids the water.

Where every dataset comes from, and what it was checked against, is in
[DATA_SOURCES.md](DATA_SOURCES.md).

## Run it

```sh
pip install -r requirements.txt
python app/main.py              # http://localhost:8000 (https:// when certs/ exists)
```

Settings go in `.env` at the project root (copy `.env.example`):

| Variable | Needed for |
|---|---|
| `OPENWEATHER_API_KEY` | Current weather and the weather map tiles |
| `OPENTOPOGRAPHY_API_KEY` | `scripts/fetch_dem.py` only |
| `DEM_SOURCE` | Optional: which DEM to use (`GEDTM30`, `COP30`, `tiles`) |
| `RELOAD` | Optional: `1` restarts the server when code changes |
| `FORECAST_WORKERS` | Optional: CPU cores for flood forecasts (default: half the cores, at most 6; `0` runs them in the server process) |

The first start builds the drain network, the terrain and the coupled model, which
takes a few minutes. The result is saved in `app/data/cache/`, so later starts take
seconds. Phones need HTTPS to share GPS: `python scripts/make_cert.py` makes a local
certificate.

## Layout

```
app/
  main.py          FastAPI app: routers, error handler, start-up warm-up
  config.py        paths, the study area and settings, defined once
  errors.py        errors the services raise, turned into JSON responses
  pages.py         the dashboard and resources pages
  api/             HTTP endpoints: check the request, call a service, return JSON
    weather.py     current weather, rainfall forecast, rain grid, map tiles
    streets.py     OpenStreetMap roads and waterways
    drains.py      the drain network, one drain's hydraulics, network load
    flood.py       elevation layer, rainfall nowcast, street flood forecast
    navigation.py  place search, flood-safe route
    wards.py       ward list and base-map PDFs
  services/        the work behind the endpoints
    weather.py     OpenWeather and Open-Meteo clients
    rainfall.py    the next three hours of rain, in 5 minute steps
    osm.py         Overpass downloads of roads and waterways
    drain_network.py  GCC survey + ward maps merged into one drain graph
    hydraulics.py  Manning capacity, normal depth, Rational method, graph routing
    terrain.py     the DEM: lookups, grids, the relief image
    surface.py     the DEM conditioned and cut into storage zones; depth on streets
    channels.py    the basin model's rivers, canals and drains
    coupled.py     the coupled drain and surface model, minute by minute
    forecast.py    one storm through the coupled model, reported street by street
    routing.py     the street graph, Dijkstra and A*
    navigation.py  geocoding and the flood-safe trip
    street_index.py, wards.py, diskcache.py
  templates/, static/   the front end
scripts/           one-off data preparation: DEM download, building grid, ward-map
                   digitising, local HTTPS certificate
tests/             pytest suite
```

## Tests

```sh
pip install -r requirements-dev.txt
pytest              # fast checks: formulas, synthetic terrain, the web layer
pytest -m city      # against the real Chennai data (needs app/data; slow on a cold cache)
```
