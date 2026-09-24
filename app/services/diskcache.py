"""The built city model on disk, so a restart loads it in seconds instead of rebuilding it.

Merging the drain survey with the ward sheets, conditioning the terrain and coupling
the two takes minutes; none of it changes unless its inputs or the code that builds it
do. So each piece is pickled under app/data/cache/, named by a hash of every input
file's size and modification time and of the source files that build it. Change a data
file or the code and the name changes, the old file is ignored, and the piece is
rebuilt once. Delete the folder to force a rebuild.
"""

import hashlib
import pickle
from pathlib import Path
from typing import Any, Callable

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


def _key(inputs: list[Path]) -> str:
    digest = hashlib.sha1()
    for path in sorted(set(inputs), key=str):
        digest.update(str(path).encode())
        if path.exists():
            stat = path.stat()
            digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
        else:
            digest.update(b"missing")
    return digest.hexdigest()[:16]


def cached(name: str, inputs: list[Path], build: Callable[[], Any]) -> Any:
    """Load `name` built from `inputs`, or build it, save it and return it."""
    path = CACHE_DIR / f"{name}-{_key(inputs)}.pkl"
    if path.exists():
        try:
            with path.open("rb") as fh:
                return pickle.load(fh)
        except Exception:          # a torn or stale file is rebuilt, never trusted
            path.unlink(missing_ok=True)
    value = build()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for old in CACHE_DIR.glob(f"{name}-*.pkl"):
        old.unlink(missing_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        pickle.dump(value, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)
    return value


def sources(*modules: Any) -> list[Path]:
    """The .py files of modules, as cache inputs: editing the code invalidates the cache."""
    return [Path(module.__file__) for module in modules]


if __name__ == "__main__":
    import tempfile

    CACHE_DIR = Path(tempfile.mkdtemp())
    src = CACHE_DIR / "input.txt"
    src.write_text("a")
    calls = []
    build = lambda: calls.append(1) or {"v": len(calls)}
    assert cached("t", [src], build) == {"v": 1}
    assert cached("t", [src], build) == {"v": 1} and len(calls) == 1     # from disk
    src.write_text("ab")                                                  # input changed
    assert cached("t", [src], build) == {"v": 2} and len(calls) == 2
    assert len(list(CACHE_DIR.glob("t-*.pkl"))) == 1                      # old one gone
    print("diskcache self-check passed")
