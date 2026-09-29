"""Root entry point for Vercel deployment.

Exports the FastAPI ASGI application with Vercel path normalization and serverless adaptations.
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

# Import the base FastAPI application instance
from app.main import app
from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send


class VercelPathNormalizerMiddleware:
    """ASGI middleware to normalize paths rewritten by Vercel serverless functions.
    
    When Vercel uses `rewrites` to route traffic to `api/index.py` or `main.py`,
    `scope['path']` can receive the destination function filename instead of the original URI.
    This middleware detects Vercel headers (`x-matched-path`, `x-forwarded-uri`) and strips
    entrypoint prefixes so FastAPI routes match cleanly.
    """

    def __init__(self, inner_app: ASGIApp):
        self.inner_app = inner_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            path = scope.get("path", "")

            # Extract request headers
            headers_dict = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }

            matched_path = headers_dict.get("x-matched-path", "").strip()

            # Check if path is rewritten to entry point
            if path in ("/api/index.py", "/api/index", "/main.py", "/index.py", "/api", "/api/"):
                if matched_path and matched_path not in (
                    "/api/index.py",
                    "/api/index",
                    "/main.py",
                    "/index.py",
                    "/api",
                    "/api/",
                ):
                    scope["path"] = matched_path
                else:
                    scope["path"] = "/"
            elif path.startswith("/api/index.py/"):
                scope["path"] = path[len("/api/index.py"):]
            elif path.startswith("/main.py/"):
                scope["path"] = path[len("/main.py"):]
            elif path.startswith("/index.py/"):
                scope["path"] = path[len("/index.py"):]

            # Normalize empty path to root
            if not scope.get("path"):
                scope["path"] = "/"

        await self.inner_app(scope, receive, send)


# Register direct route handlers for all possible Vercel entrypoint paths
# so even without middleware rewrite, FastAPI returns the home dashboard.
@app.api_route("/api/index.py", methods=["GET", "POST", "HEAD"], include_in_schema=False)
@app.api_route("/api/index", methods=["GET", "POST", "HEAD"], include_in_schema=False)
@app.api_route("/api", methods=["GET", "POST", "HEAD"], include_in_schema=False)
@app.api_route("/api/", methods=["GET", "POST", "HEAD"], include_in_schema=False)
@app.api_route("/main.py", methods=["GET", "POST", "HEAD"], include_in_schema=False)
@app.api_route("/index.py", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def vercel_entrypoint_fallback(request: Request):
    from app.pages import home
    return await home(request)


# Wrap FastAPI with the Vercel path normalizer
app.add_middleware(VercelPathNormalizerMiddleware)
