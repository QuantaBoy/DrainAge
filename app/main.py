"""Application entry point: the dashboard page, its static assets and the API."""

import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

# Running this file directly puts app/ first on the import path, so the project
# root has to be added before the app package can be imported.
sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env")

from app.routes import streets, weather  # noqa: E402

app = FastAPI()
app.include_router(weather.router)
app.include_router(streets.router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "home.html")


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
