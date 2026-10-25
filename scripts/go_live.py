"""Serve the engine from this computer and point the Vercel pages at it.

    <conda End-to-End python> scripts/go_live.py

Starts the engine on port 7860, opens a Cloudflare quick tunnel to it (free, no account),
rebuilds web/ for the tunnel's address and deploys it to Vercel. The site works while
this runs; Ctrl+C stops the engine and the tunnel. The tunnel address changes on every
start, which is why the pages are redeployed each time.
"""

import functools
import re
import subprocess
import sys
import time
from pathlib import Path

print = functools.partial(print, flush=True)
ROOT = Path(__file__).resolve().parent.parent
CLOUDFLARED = Path.home() / "bin" / "cloudflared.exe"


def main() -> None:
    engine = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                               "--port", "7860", "--proxy-headers", "--forwarded-allow-ips=127.0.0.1"], cwd=ROOT)
    # cloudflared logs to a file, not a pipe: an undrained pipe fills up and stalls it.
    log = ROOT / "tunnel.log"
    with open(log, "w") as out:
        tunnel = subprocess.Popen([str(CLOUDFLARED), "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:7860"],
                                  stdout=out, stderr=out)
    try:
        url = None
        for _ in range(60):
            if match := re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log.read_text()):
                url = match.group(0)
                break
            time.sleep(1)
        if not url:
            sys.exit(f"no tunnel address after 60 s, see {log}")
        print(f"Engine tunnel: {url}")
        subprocess.run([sys.executable, "scripts/build_frontend.py", "--engine", url], cwd=ROOT, check=True)
        subprocess.run("npx -y vercel deploy web --prod --yes", cwd=ROOT, shell=True, check=True)
        print("Live at https://web-one-mu-25.vercel.app. Ctrl+C to stop.")
        while engine.poll() is None and tunnel.poll() is None:
            time.sleep(5)
    finally:
        tunnel.terminate()
        engine.terminate()


if __name__ == "__main__":
    main()
