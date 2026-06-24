"""core.cache: round-trip persistence, isolation via the env override, clearing,
and the human-age formatter."""
from h1_asset_fetcher.core import cache


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("H1_ASSET_FETCHER_CACHE", str(tmp_path / "cache"))


def test_save_load_round_trip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert cache.load("thing") == {}            # nothing yet
    cache.save("thing", {"filters": {"a": 1}})
    got = cache.load("thing")
    assert got["filters"] == {"a": 1}
    assert got["version"] == cache.CACHE_VERSION  # stamped on save


def test_clear_removes_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cache.save("thing", {"x": 1})
    assert cache.load("thing")
    cache.clear("thing")
    assert cache.load("thing") == {}
    cache.clear("thing")  # clearing a missing file is a no-op, not an error


def test_corrupt_cache_is_ignored(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "cache" / "thing.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert cache.load("thing") == {}


def test_human_age():
    assert cache.human_age(5) == "5s"
    assert cache.human_age(120) == "2m"
    assert cache.human_age(3 * 3600) == "3h"
    assert cache.human_age(2 * 86400) == "2d"
