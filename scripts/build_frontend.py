"""Export the dashboard's pages as a static site for Vercel, forwarding the API to the
flood engine.

    python scripts/build_frontend.py --engine https://<user>-<space>.hf.space

Writes web/: the Dashboard, Resources and Technology pages as plain HTML, the
static assets, and a vercel.json that forwards /data-collection/* and the ward PDFs
to the engine. The pages call the API by relative paths, so nothing in them changes:
Vercel proxies those paths to the engine, same origin, no CORS.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAGES = {"/": "index.html", "/resources": "resources.html", "/technology": "technology.html"}
FORWARDED = ("/data-collection/:path*", "/resources/ward-map/:path*")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", required=True, help="the engine's URL, e.g. https://you-drainout.hf.space")
    parser.add_argument("--out", type=Path, default=ROOT / "web")
    args = parser.parse_args()
    engine = args.engine.rstrip("/")
    if not engine.startswith("https://"):
        sys.exit("--engine must be an https:// URL")

    # The pages are rendered by the app itself, without starting the model.
    from fastapi.testclient import TestClient

    from app.main import app

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "app" / "static", out / "static",
                    ignore=shutil.ignore_patterns("__pycache__"))
    client = TestClient(app)
    for path, name in PAGES.items():
        html = client.get(path).text
        # url_for gives absolute test-server URLs; the site serves them from its own root.
        html = re.sub(r"https?://testserver", "", html)
        (out / name).write_text(html, encoding="utf-8")

    config = {
        "cleanUrls": True,
        "rewrites": [{"source": src, "destination": engine + src.replace(":path*", ":path*")} for src in FORWARDED],
        "headers": [{"source": "/static/(.*)",
                     "headers": [{"key": "Cache-Control", "value": "public, max-age=86400"}]}],
    }
    (out / "vercel.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    files = sum(1 for p in out.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"{files} files, {size / 2**20:.1f} MB in {out}; API forwarded to {engine}")


if __name__ == "__main__":
    main()
