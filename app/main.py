"""Application entry point: the dashboard page, its static assets and the API."""

import sys
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

# Running this file directly puts app/ first on the import path, so the project
# root has to be added before the app package can be imported.
sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env")

from app.routes import drains, flood, navigation, resources, streets, weather  # noqa: E402

app = FastAPI()
# Flood and drain GeoJSON is numbers in repeated keys, which compresses about fivefold.
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(weather.router)
app.include_router(streets.router)
app.include_router(drains.router)
app.include_router(flood.router)
app.include_router(resources.router)
app.include_router(navigation.router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
# Stamped onto every script and stylesheet URL, so a browser never runs yesterday's
# JavaScript against today's page after a restart.
templates.env.globals["asset_v"] = str(int(time.time()))


@app.on_event("startup")
async def warm_caches() -> None:
    # Parsing the drain survey and indexing 80k streets takes seconds; doing it here,
    # in the background, keeps it off the first flood request.
    import asyncio

    from app.services import routing, street_flood

    # warm_ponding couples the drains onto the terrain, so it runs after both load.
    # Routing costs roads by the flood model's water, so it warms after that model.
    for job in (drains._load_csv,
                lambda: (street_flood._load(), flood.warm_ponding(), routing.warm(flood._ponding_model()))):
        asyncio.get_running_loop().run_in_executor(None, job)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "home.html")


@app.get("/resources", response_class=HTMLResponse, include_in_schema=False)
async def resources_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "resources.html")


if __name__ == "__main__":
    # Browsers only expose GPS to secure pages, so HTTPS is used whenever
    # certificates exist. Generate them with make_cert.py.
    cert_dir = ROOT_DIR / "certs"
    ssl_options = {}
    if (cert_dir / "cert.pem").exists():
        ssl_options = {
            "ssl_certfile": str(cert_dir / "cert.pem"),
            "ssl_keyfile": str(cert_dir / "key.pem"),
        }

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        # The reloader imports "app.main" in a fresh process, which needs the
        # project root on its path to start from any working directory.
        app_dir=str(ROOT_DIR),
        **ssl_options,
    )
