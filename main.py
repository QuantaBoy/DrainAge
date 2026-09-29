"""Root entry point for Vercel deployment.

Exports the FastAPI ASGI application for direct root routing.
"""

import os
import sys
from pathlib import Path

# Add project root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# On Vercel / serverless runtime:
# 1. Disable multiprocessing workers so simulation runs in-process
os.environ.setdefault("FORECAST_WORKERS", "0")

# 2. Redirect disk cache to /tmp (the only writable directory in serverless runtime)
if os.getenv("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    from app import config
    config.CACHE_DIR = Path("/tmp/cache")
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import app.services.diskcache
        app.services.diskcache.CACHE_DIR = config.CACHE_DIR
    except Exception:
        pass

# Import the FastAPI application instance
from app.main import app
from fastapi import Request


# Ensure fallback for /api or /api/
@app.get("/api", include_in_schema=False)
@app.get("/api/", include_in_schema=False)
async def api_root(request: Request):
    from app.pages import home
    return await home(request)
