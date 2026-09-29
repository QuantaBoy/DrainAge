"""Root entry point for Vercel deployment.

Exports the FastAPI ASGI application with Vercel path normalization, serverless adaptations,
and transparent error reporting for diagnostics.
"""

import os
import sys
import traceback
from pathlib import Path

# Add project root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# On Vercel / serverless runtime:
# 1. Disable multiprocessing workers so simulation runs in-process
os.environ["FORECAST_WORKERS"] = "0"
os.environ.setdefault("RELOAD", "false")

# 2. Redirect disk cache to /tmp (the only writable directory in serverless runtime)
try:
    from app import config
    config.CACHE_DIR = Path("/tmp/cache")
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import app.services.diskcache
        app.services.diskcache.CACHE_DIR = config.CACHE_DIR
    except Exception:
        pass
except Exception:
    pass

# Try importing the application
import_error = None
fastapi_app = None

try:
    from app.main import app as _base_app
    fastapi_app = _base_app
    from fastapi import Request
    from app.pages import home

    # Fallback routes for raw Vercel entrypoint paths
    @fastapi_app.api_route("/api/index.py", methods=["GET", "POST", "HEAD"], include_in_schema=False)
    @fastapi_app.api_route("/api/index", methods=["GET", "POST", "HEAD"], include_in_schema=False)
    @fastapi_app.api_route("/api", methods=["GET", "POST", "HEAD"], include_in_schema=False)
    @fastapi_app.api_route("/api/", methods=["GET", "POST", "HEAD"], include_in_schema=False)
    @fastapi_app.api_route("/main.py", methods=["GET", "POST", "HEAD"], include_in_schema=False)
    @fastapi_app.api_route("/index.py", methods=["GET", "POST", "HEAD"], include_in_schema=False)
    async def vercel_entrypoint_fallback(request: Request):
        return await home(request)

except Exception:
    import_error = traceback.format_exc()
    print(f"[VERCEL_IMPORT_ERROR]\n{import_error}", flush=True)


async def send_error_response(send, status_code: int, title: str, body: str):
    """Helper to send an HTTP response over ASGI without external dependencies."""
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; background: #0f172a; color: #f8fafc; padding: 32px; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 24px; max-width: 900px; margin: 0 auto; }}
        h1 {{ color: #f43f5e; font-size: 20px; margin-top: 0; }}
        pre {{ background: #090d16; padding: 16px; border-radius: 6px; overflow-x: auto; color: #cbd5e1; font-size: 13px; line-height: 1.5; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>{title}</h1>
        <pre>{body}</pre>
    </div>
</body>
</html>"""
    content = html.encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status_code,
        "headers": [
            (b"content-type", b"text/html; charset=utf-8"),
            (b"content-length", str(len(content)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({
        "type": "http.response.body",
        "body": content,
    })


async def app(scope, receive, send):
    """Vercel top-level ASGI entry point."""
    if import_error is not None:
        if scope["type"] == "http":
            await send_error_response(send, 500, "Vercel Startup / Import Error", import_error)
        return

    if scope["type"] == "http":
        path = scope.get("path", "")

        # Safely extract headers (whether bytes or str)
        headers_dict = {}
        for item in scope.get("headers") or []:
            if len(item) == 2:
                k, v = item
                k_str = k.decode("latin-1").lower() if isinstance(k, bytes) else str(k).lower()
                v_str = v.decode("latin-1") if isinstance(v, bytes) else str(v)
                headers_dict[k_str] = v_str

        matched_path = headers_dict.get("x-matched-path", "").strip()

        # Normalize entrypoint rewritten paths
        if path in ("/api/index.py", "/api/index", "/main.py", "/index.py", "/api", "/api/"):
            if matched_path and matched_path not in (
                "/api/index.py", "/api/index", "/main.py", "/index.py", "/api", "/api/"
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

        if not scope.get("path"):
            scope["path"] = "/"

    try:
        await fastapi_app(scope, receive, send)
    except Exception:
        tb = traceback.format_exc()
        print(f"[VERCEL_RUNTIME_ERROR]\n{tb}", flush=True)
        if scope["type"] == "http":
            await send_error_response(send, 500, "Vercel Runtime Request Error", tb)
        else:
            raise
