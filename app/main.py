"""Application entry point: the FastAPI app, its routers, and the start-up warm-up.

Layout:
  app/config.py    paths, the study area and settings, defined once
  app/errors.py    the errors services raise, and how they become HTTP responses
  app/api/         the JSON endpoints: validate the request, call a service, return
  app/services/    everything the endpoints do: upstream APIs, the drain network, the
                   terrain, the coupled flood model, routing
  app/pages.py     the HTML pages; app/templates and app/static are their assets
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

# Running this file directly puts app/ first on the import path, so the project root
# has to be added before the app package can be imported.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app import config, pages  # noqa: E402
from app.api import drains, flood, navigation, streets, wards, weather  # noqa: E402
from app.errors import ServiceError, service_error_handler  # noqa: E402
from app.services import compute, drain_network, forecast, routing, street_index  # noqa: E402


def _warm_flood_model() -> None:
    # The forecast couples the drains onto the terrain, so it builds after both; routing
    # costs roads by the forecast's water, so it warms after that.
    street_index.load()
    forecast.warm()
    routing.warm(forecast.surface_model())


async def _start_forecasting() -> None:
    # This process builds (or loads) the model first, so the workers find it on disk and
    # never build it side by side; then the pool starts and computes the likely storms.
    loop = asyncio.get_running_loop()
    await asyncio.gather(loop.run_in_executor(None, drain_network.load_drains),
                         loop.run_in_executor(None, _warm_flood_model))
    compute.start(config.FORECAST_WORKERS)
    await compute.precompute_forever()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Building the drain network and the flood model takes seconds from the disk cache
    # and minutes without it; doing it here, in the background, keeps it off the first
    # request. Requests that arrive first run in a thread until the pool is up.
    task = asyncio.create_task(_start_forecasting())
    yield
    task.cancel()
    compute.stop()


app = FastAPI(title="Chennai flood nowcast", lifespan=lifespan)
# Flood and drain GeoJSON is numbers in repeated keys, which compresses about fivefold.
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_exception_handler(ServiceError, service_error_handler)
for router in (weather.router, streets.router, drains.router, flood.router, wards.router,
               navigation.router, pages.router):
    app.include_router(router)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


if __name__ == "__main__":
    # Browsers only expose GPS to secure pages, so HTTPS is used whenever certificates
    # exist. Generate them with scripts/make_cert.py.
    cert, key = config.CERT_DIR / "cert.pem", config.CERT_DIR / "key.pem"
    ssl_options = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)} if cert.exists() else {}
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=config.RELOAD,
        # The reloader imports "app.main" in a fresh process, which needs the project
        # root on its path to start from any working directory.
        app_dir=str(config.ROOT_DIR),
        **ssl_options,
    )
