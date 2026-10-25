"""Assemble the flood engine for Hugging Face Spaces: the code, the data it runs on,
and the Space's own README and LFS settings, in one folder ready to push.

    python scripts/prepare_space.py --out ../drainout-space [--with-pdfs] [--with-history]

The data files are not in the GitHub repository (app/data is ignored there), so the
Space gets its own copy. The model cache is left out: the Dockerfile builds it.
Ward PDFs (215 MB) and the historical flood study are only copied when asked for.
"""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SPACE_README = """---
title: DRAIN OUT Flood Engine
emoji: 🌧️
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

The flood-forecast engine behind the DRAIN OUT dashboard: street-level flood
nowcasting for Chennai (rain, drains and terrain simulated together), flood-safe
routing, and the data behind it. The dashboard pages are served from Vercel and call
this engine's /data-collection API.
"""

LFS = "\n".join(f"{pattern} filter=lfs diff=lfs merge=lfs -text" for pattern in (
    "*.pdf", "*.tif", "*.npz", "*.geojson", "*.webp", "app/data/*.json",
    "Cross-Checked*/*.csv", "Historical*/*.csv")) + "\n"


def copy(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "cache", "*.tmp"))
    elif src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="folder to write the Space into")
    parser.add_argument("--with-pdfs", action="store_true", help="include the 92 ward PDFs (215 MB)")
    parser.add_argument("--with-history", action="store_true", help="include the historical flood study")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    for name in ("app", "requirements.txt", "Dockerfile"):
        copy(ROOT / name, out / name)
    copy(ROOT / "scripts" / "oracle_setup.sh", out / "oracle_setup.sh")
    copy(ROOT / "Cross-Checked Data", out / "Cross-Checked Data")
    if args.with_pdfs:
        copy(ROOT / "Ward", out / "Ward")
    if args.with_history:
        copy(ROOT / "Historical Flood", out / "Historical Flood")
    (out / "README.md").write_text(SPACE_README, encoding="utf-8")
    (out / ".gitattributes").write_text(LFS, encoding="utf-8")
    (out / ".dockerignore").write_text(".git\n**/__pycache__\napp/data/cache\n", encoding="utf-8")

    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and ".git" not in p.parts)
    print(f"Space ready in {out} ({size / 2**20:.0f} MB). Next: see DEPLOY.md, step 2.")


if __name__ == "__main__":
    main()
