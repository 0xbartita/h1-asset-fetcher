"""H1Session.get retry/error handling: transient blips retry quietly, a genuine
give-up logs one WARN that names what was skipped, and 401 stays fatal."""
import requests
import pytest

from h1_asset_fetcher.platforms.hackerone import client as hc


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

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
