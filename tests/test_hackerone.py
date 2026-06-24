"""H1Session.get retry/error handling: transient blips retry quietly, a genuine
give-up logs one WARN that names what was skipped, and 401 stays fatal."""
import requests
import pytest

from h1_asset_fetcher.platforms.hackerone import client as hc


class _Resp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class _Session:
    """Replays a scripted list of responses/exceptions for successive get()s."""
    def __init__(self, script):
        self._script = list(script)
        self.headers = {}
        self.auth = None

    def get(self, url, timeout=None):
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _session(script, monkeypatch, logs):
    monkeypatch.setattr(hc, "log", lambda msg, level="INFO": logs.append((level, msg)))
    monkeypatch.setattr(hc.time, "sleep", lambda *_a, **_k: None)  # no real waiting
    s = hc.H1Session("user", "token")
    s.session = _Session(script)
    return s


def test_success_returns_json(monkeypatch):
    logs = []
    s = _session([_Resp(200, {"data": [1]})], monkeypatch, logs)
    assert s.get("http://x", label="x") == {"data": [1]}
    assert logs == []  # a clean success is silent


def test_transient_then_success_makes_no_error(monkeypatch):
    logs = []
    s = _session([requests.exceptions.ConnectionError("reset"),
                  _Resp(200, {"ok": 1})], monkeypatch, logs)
    assert s.get("http://x", label="programs page 1", retries=3) == {"ok": 1}
    # A blip that recovers must NOT produce a scary [ERR] (the reported bug).
    assert not any(level == "ERR" for level, _ in logs)


def test_giveup_warns_and_names_target(monkeypatch):
    logs = []
    s = _session([requests.exceptions.ConnectionError("reset")] * 3, monkeypatch, logs)
    assert s.get("http://x", label="scopes for acme", retries=3) is None
    # Exactly one give-up line, at WARN (not ERR), naming the skipped program.
    giveups = [(lvl, m) for lvl, m in logs if "Gave up" in m]
    assert len(giveups) == 1
    assert giveups[0][0] == "WARN"
    assert "scopes for acme" in giveups[0][1]
    assert not any(level == "ERR" for level, _ in logs)


def test_non_200_is_surfaced_not_swallowed(monkeypatch):
    logs = []
    s = _session([_Resp(500)], monkeypatch, logs)
    assert s.get("http://x", label="scopes for acme") is None
    assert any("HTTP 500" in m and lvl == "WARN" for lvl, m in logs)


def test_401_is_fatal(monkeypatch):
    logs = []
    s = _session([_Resp(401)], monkeypatch, logs)
    with pytest.raises(SystemExit):
        s.get("http://x", label="x")


def test_rate_limit_is_waited_out_then_succeeds(monkeypatch):
    # A 429 is ridden out and does NOT burn the transient-error retry budget.
    logs = []
    s = _session([_Resp(429), _Resp(200, {"ok": 1})], monkeypatch, logs)
    assert s.get("http://x", label="scopes for acme", retries=3) == {"ok": 1}
    assert not any(level == "ERR" for level, _ in logs)


def test_sustained_rate_limit_aborts_run(monkeypatch):
    # When programs can't get past 429, the run aborts with H1RateLimited
    # instead of silently dropping everything (death-march protection).
    logs = []
    s = _session([_Resp(429)] * 12, monkeypatch, logs)
    assert s.get("http://x", label="scopes for a", retries=3) is None   # 1st give-up
    with pytest.raises(hc.H1RateLimited):
        s.get("http://y", label="scopes for b", retries=3)              # 2nd → abort


def test_request_spacing_respects_documented_caps():
    # Guard against a future tweak silently exceeding HackerOne's documented
    # hacker-API limits (api.hackerone.com/getting-started-hacker-api/#rate-limits).
    assert 60 / hc._READ_INTERVAL <= 600       # general read endpoints: 600/min
    assert 60 / hc._SCOPES_INTERVAL <= 50      # structured_scopes endpoint: 50/min


def test_scope_fetch_uses_the_stricter_scopes_interval(monkeypatch):
    # The structured_scopes endpoint is capped at 50/min, so scope fetches must
    # request the stricter spacing — not the faster general-read interval.
    intervals = []
    s = hc.H1Session("u", "t")
    monkeypatch.setattr(
        s, "get",
        lambda url, retries=3, label=None, min_interval=None: (
            intervals.append(min_interval) or {"data": []}))
    hc.fetch_scopes(s, "acme", asset_types=("OTHER_APK",))
    assert intervals and all(mi == hc._SCOPES_INTERVAL for mi in intervals)


# --- scope cache ----------------------------------------------------------- #

def _stub_fetch(monkeypatch, tmp_path):
    """Isolate the cache to tmp and stub the network so fetch_all runs offline.
    Returns a dict tracking how many times scopes were actually fetched."""
    monkeypatch.setenv("H1_ASSET_FETCHER_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(hc, "log", lambda *a, **k: None)
    calls = {"scopes": 0}
    progs = [{"handle": "acme", "name": "Acme", "platform": "hackerone"},
             {"handle": "beta", "name": "Beta", "platform": "hackerone"}]
    monkeypatch.setattr(hc, "fetch_programs", lambda s, prog_filter="": [dict(p) for p in progs])

    def fake_scopes(s, handle, asset_types=None):
        calls["scopes"] += 1
        # Each program returns BOTH an android and an iOS asset (all types cached).
        return [{"asset_type": "OTHER_APK", "asset_identifier": f"com.{handle}"},
                {"asset_type": "APPLE_STORE_APP_ID", "asset_identifier": f"{handle}-ios"}]
    monkeypatch.setattr(hc, "fetch_scopes", fake_scopes)
    return calls


def test_fetch_all_caches_then_reuses_without_api(monkeypatch, tmp_path):
    calls = _stub_fetch(monkeypatch, tmp_path)
    android = hc.SCOPE_TYPES["android"]

    first = hc.fetch_all(None, prog_filter="bbp,private", asset_types=android)
    assert calls["scopes"] == 2                       # one fetch per program
    assert {p["handle"] for p in first} == {"acme", "beta"}
    assert all(s["asset_type"] in android for p in first for s in p["scopes"])

    # Second run: served entirely from cache, zero new scope fetches.
    second = hc.fetch_all(None, prog_filter="bbp,private", asset_types=android)
    assert calls["scopes"] == 2                       # unchanged
    assert {p["handle"] for p in second} == {"acme", "beta"}


def test_cache_serves_a_different_scope_offline(monkeypatch, tmp_path):
    # The cache stores ALL asset types, so switching android -> ios needs no API.
    calls = _stub_fetch(monkeypatch, tmp_path)
    hc.fetch_all(None, prog_filter="bbp,private", asset_types=hc.SCOPE_TYPES["android"])
    assert calls["scopes"] == 2
    ios = hc.fetch_all(None, prog_filter="bbp,private", asset_types=hc.SCOPE_TYPES["ios"])
    assert calls["scopes"] == 2                        # still no new fetches
    assert ios and all(s["asset_type"] in hc.SCOPE_TYPES["ios"]
                       for p in ios for s in p["scopes"])


def test_cache_status_and_clear(monkeypatch, tmp_path):
    _stub_fetch(monkeypatch, tmp_path)
    assert hc.cache_status("bbp,private") is None       # nothing cached yet
    hc.fetch_all(None, prog_filter="bbp,private", asset_types=hc.SCOPE_TYPES["android"])
    status = hc.cache_status("bbp,private")
    assert status and status[0] == 2                    # (count, age_str)
    hc.clear_cache("bbp,private")
    assert hc.cache_status("bbp,private") is None


def test_stale_cache_is_ignored(monkeypatch, tmp_path):
    calls = _stub_fetch(monkeypatch, tmp_path)
    hc.fetch_all(None, prog_filter="bbp,private", asset_types=hc.SCOPE_TYPES["android"])
    assert calls["scopes"] == 2
    # Jump now() past the TTL: the cache is too old to reuse -> refetch.
    real_now = hc.cache.now()
    monkeypatch.setattr(hc.cache, "now", lambda: real_now + hc._CACHE_TTL + 1)
    assert hc.cache_status("bbp,private") is None
    hc.fetch_all(None, prog_filter="bbp,private", asset_types=hc.SCOPE_TYPES["android"])
    assert calls["scopes"] == 4                         # fetched again


def test_use_cache_false_bypasses_cache(monkeypatch, tmp_path):
    calls = _stub_fetch(monkeypatch, tmp_path)
    hc.fetch_all(None, prog_filter="bbp,private", asset_types=hc.SCOPE_TYPES["android"])
    assert calls["scopes"] == 2
    hc.fetch_all(None, prog_filter="bbp,private",
                 asset_types=hc.SCOPE_TYPES["android"], use_cache=False)
    assert calls["scopes"] == 4                         # forced fresh fetch


def test_throttled_fetch_is_not_cached(monkeypatch, tmp_path):
    _stub_fetch(monkeypatch, tmp_path)
    # Simulate a throttle-aborted fetch: partial programs + throttled=True.
    monkeypatch.setattr(hc, "_fetch_fresh",
                        lambda s, pf, w: ([{"handle": "acme", "name": "Acme",
                                            "scopes": [{"asset_type": "OTHER_APK",
                                                        "asset_identifier": "com.acme"}]}],
                                          True))
    hc.fetch_all(None, prog_filter="bbp,private", asset_types=hc.SCOPE_TYPES["android"])
    assert hc.cache_status("bbp,private") is None        # partial result not cached
