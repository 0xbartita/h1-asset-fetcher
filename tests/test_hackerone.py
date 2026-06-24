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
