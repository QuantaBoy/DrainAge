"""Vercel Serverless Function entry point.

Routes incoming HTTP requests to the FastAPI application.
"""

import os
import sys
from pathlib import Path
from starlette.types import ASGIApp, Receive, Scope, Send

# Add project root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
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
from app.main import app as fastapi_app
from fastapi import Request


# Ensure direct hits on /api or /api/ render the dashboard
@fastapi_app.get("/api", include_in_schema=False)
@fastapi_app.get("/api/", include_in_schema=False)
async def api_root(request: Request):
    from app.pages import home
    return await home(request)


class VercelPathMiddleware:
    """Normalizes request paths forwarded by Vercel serverless rewrites."""

    def __init__(self, inner_app: ASGIApp):
        self.inner_app = inner_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            # Handle root /api and function name prefixes forwarded by Vercel
            if path in ("/api", "/api/", "/api/index", "/api/index/", "/api/index.py"):
                scope["path"] = "/"
                scope["raw_path"] = b"/"
            elif path.startswith("/api/index.py/"):
                new_path = path[len("/api/index.py"):]
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode()
            elif path.startswith("/api/index/"):
                new_path = path[len("/api/index"):]
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode()
            elif path.startswith("/api/"):
                new_path = path[len("/api"):]
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode()

        await self.inner_app(scope, receive, send)


app = VercelPathMiddleware(fastapi_app)
