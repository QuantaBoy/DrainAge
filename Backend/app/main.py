import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

BASE_DIR = Path(__file__).resolve().parent

# Running this file directly makes its own folder the import root, so "app" is only
# importable once Backend/ is on the path. Must precede the app.* import below.
sys.path.insert(0, str(BASE_DIR.parent))

load_dotenv(BASE_DIR.parent / ".env")

from app.routes import weather

app = FastAPI()
app.include_router(weather.router)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

if __name__ == "__main__":
    # Browsers only expose GPS on a secure context, so serve HTTPS when certs exist.
    # Run make_cert.py to create them.
    certs = BASE_DIR.parent / "certs"
    ssl = {}
    if (certs / "cert.pem").exists():
        ssl = {"ssl_certfile": str(certs / "cert.pem"), "ssl_keyfile": str(certs / "key.pem")}

    # Run directly from any working directory: the reloader re-imports "app.main"
    # in a fresh process, which only resolves if Backend/ is on the path.
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        app_dir=str(BASE_DIR.parent),
        **ssl,
    )