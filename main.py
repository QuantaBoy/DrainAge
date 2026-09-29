"""Root entry point for Vercel deployment.

Exports the FastAPI ASGI application with Vercel path normalization, serverless adaptations,
and transparent error reporting for diagnostics.
"""

import os
import sys
import traceback
import urllib.parse
from pathlib import Path

# Add project root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# On Vercel / serverless runtime:
os.environ["FORECAST_WORKERS"] = "0"
os.environ.setdefault("RELOAD", "false")

# Redirect disk cache to /tmp
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

import_error = None
fastapi_app = None

try:
    from app.main import app as _base_app
    fastapi_app = _base_app
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse
    from app.pages import home

    # Explicit direct static fallback to guarantee CSS/JS/images are served with exact MIME types
    @fastapi_app.get("/static/{file_path:path}", include_in_schema=False)
    async def vercel_static_file_fallback(file_path: str):
        target = config.STATIC_DIR / file_path
        if target.is_file():
            media_type = "application/octet-stream"
            suffix = target.suffix.lower()
            if suffix == ".css":
                media_type = "text/css"
            elif suffix == ".js":
                media_type = "application/javascript"
            elif suffix == ".svg":
                media_type = "image/svg+xml"
            elif suffix == ".png":
                media_type = "image/png"
            elif suffix in (".jpg", ".jpeg"):
                media_type = "image/jpeg"
            elif suffix in (".json", ".geojson"):
                media_type = "application/json"
            elif suffix == ".ico":
                media_type = "image/x-icon"
            return FileResponse(target, media_type=media_type)
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Static asset not found")

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
        query_bytes = scope.get("query_string", b"")
        query_str = query_bytes.decode("latin-1") if isinstance(query_bytes, bytes) else str(query_bytes)

        target_path = None
        if "__path__=" in query_str:
            parsed_q = urllib.parse.parse_qs(query_str, keep_blank_values=True)
            if "__path__" in parsed_q:
                target_path = parsed_q.pop("__path__")[0]
                new_query_str = urllib.parse.urlencode(parsed_q, doseq=True)
                scope["query_string"] = new_query_str.encode("ascii")

        if target_path:
            if not target_path.startswith("/"):
                target_path = "/" + target_path
            scope["path"] = target_path
        elif path in ("/api/index.py", "/api/index", "/main.py", "/index.py", "/api", "/api/"):
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
