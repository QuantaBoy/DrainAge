import sys
from pathlib import Path

# Ensure root 'Backend' directory is included in sys.path for absolute imports
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.routes import rainfall, weather

# Load environment configuration
ENV_FILE = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_FILE)

# Directory Paths Configuration
FRONTEND_DIR = BASE_DIR.parent / "Frontend"


def create_application() -> FastAPI:
    """Factory function to build and configure the FastAPI application instance."""
    app_instance = FastAPI(
        title="Weather & Flood GIS API",
        description="Backend service for collecting, processing, and serving real-time weather and GIS mapping data.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # 1. Configure CORS Middleware
    app_instance.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Register API Routers
    app_instance.include_router(weather.router)
    app_instance.include_router(rainfall.router)

    # 3. Mount Static Assets & Serve Web Frontend Pages
    if FRONTEND_DIR.exists():
        app_instance.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

        @app_instance.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(FRONTEND_DIR / "Weather.html")

        @app_instance.get("/weather.js", include_in_schema=False)
        async def serve_weather_js():
            return FileResponse(FRONTEND_DIR / "weather.js")

        @app_instance.get("/map", include_in_schema=False)
        @app_instance.get("/map.html", include_in_schema=False)
        async def serve_map_html():
            return FileResponse(FRONTEND_DIR / "map.html")

        @app_instance.get("/map.js", include_in_schema=False)
        async def serve_map_js():
            return FileResponse(FRONTEND_DIR / "map.js")

    return app_instance


app = create_application()

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)



