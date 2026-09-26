"""The on-disk model cache: built once, rebuilt when an input changes."""

from app.services import diskcache


def test_cache_follows_inputs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(diskcache, "CACHE_DIR", tmp_path)
    src = tmp_path / "input.txt"
    src.write_text("a")
    calls = []
    build = lambda: calls.append(1) or {"v": len(calls)}
    assert diskcache.cached("t", [src], build) == {"v": 1}
    assert diskcache.cached("t", [src], build) == {"v": 1} and len(calls) == 1     # from disk
    src.write_text("ab")                                                  # input changed
    assert diskcache.cached("t", [src], build) == {"v": 2} and len(calls) == 2
    assert len(list(tmp_path.glob("t-*.pkl"))) == 1                      # old one gone
