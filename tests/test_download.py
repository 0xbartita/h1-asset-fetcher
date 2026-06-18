"""APK downloader: apkeep command construction, Google Play credential
resolution, and the interactive 'retry failed via Google Play' flow."""
import json

from h1_asset_fetcher.download import apkeep


# --- build_apkeep_cmd ------------------------------------------------------

def test_cmd_apk_pure_no_creds_no_sleep():
    cmd = apkeep.build_apkeep_cmd("apkeep", "com.x", "/out/com.x", "apk-pure", 0)
    assert cmd == ["apkeep", "-a", "com.x", "-d", "apk-pure", "/out/com.x"]


def test_cmd_includes_sleep_when_positive():
    cmd = apkeep.build_apkeep_cmd("apkeep", "com.x", "/out/com.x", "apk-pure", 200)
    assert "-s" in cmd and "200" in cmd


def test_cmd_apk_pure_never_has_google_flags():
    cmd = apkeep.build_apkeep_cmd("apkeep", "com.x", "/out/com.x", "apk-pure", 0,
                                  gplay_email="me@gmail.com", gplay_token="aas_et/x")
    assert "-e" not in cmd
    assert "--accept-tos" not in cmd


def test_cmd_google_play_includes_email_token_and_tos():
    cmd = apkeep.build_apkeep_cmd("apkeep", "com.x", "/out/com.x", "google-play", 0,
                                  gplay_email="me@gmail.com", gplay_token="aas_et/x")
    assert "-d" in cmd and "google-play" in cmd
    assert cmd[cmd.index("-e") + 1] == "me@gmail.com"
    assert cmd[cmd.index("-t") + 1] == "aas_et/x"   # AAS token -> -t
    assert "--accept-tos" in cmd


def test_cmd_uses_aas_token_flag_only_never_oauth():
    """Per apkeep's USAGE-google-play: downloads use only -t. --oauth-token is a
    SEPARATE one-time exchange (the OAuth token is single-use, so it can't be
    reused across one-apkeep-process-per-package)."""
    cmd = apkeep.build_apkeep_cmd("apkeep", "com.x", "/out/com.x", "google-play", 0,
                                  gplay_email="me@gmail.com", gplay_token="oauth2_4/abc")
    assert "--oauth-token" not in cmd
    assert cmd[cmd.index("-t") + 1] == "oauth2_4/abc"


def test_is_oauth_token():
    assert apkeep.is_oauth_token("oauth2_4/abc") is True
    assert apkeep.is_oauth_token("aas_et/abc") is False
    assert apkeep.is_oauth_token("") is False


def test_offer_rejects_oauth_token_with_guidance():
    """If the user pastes the oauth2_4/… cookie value, refuse it (it would fail
    'Invalid payload') and tell them how to exchange it for an AAS token."""
    msgs = []
    inputs = iter(["y", "me@gmail.com"])
    creds = apkeep.offer_gplay_retry(
        2, input_fn=lambda _="": next(inputs),
        getpass_fn=lambda _="": "oauth2_4/abc",
        out=lambda *a: msgs.append(" ".join(str(x) for x in a)))
    assert creds is None
    blob = "\n".join(msgs)
    assert "--oauth-token" in blob and ("AAS" in blob or "aas" in blob)


# --- resolve_gplay_creds ---------------------------------------------------

def test_resolve_creds_flags_win_over_env():
    env = {"APKEEP_GPLAY_EMAIL": "env@x", "APKEEP_GPLAY_TOKEN": "env_tok"}
    assert apkeep.resolve_gplay_creds("flag@x", "flag_tok", env) == ("flag@x", "flag_tok")


def test_resolve_creds_falls_back_to_env():
    env = {"APKEEP_GPLAY_EMAIL": "env@x", "APKEEP_GPLAY_TOKEN": "env_tok"}
    assert apkeep.resolve_gplay_creds("", "", env) == ("env@x", "env_tok")


def test_resolve_creds_empty_when_missing():
    assert apkeep.resolve_gplay_creds("", "", {}) == ("", "")


def test_resolve_creds_strips_whitespace():
    assert apkeep.resolve_gplay_creds("  a@x ", " tok ", {}) == ("a@x", "tok")


# --- apkeep version gate (Google Play needs >= 1.0.0) ----------------------

def test_parse_apkeep_version():
    assert apkeep.parse_apkeep_version("apkeep 0.18.0") == (0, 18, 0)
    assert apkeep.parse_apkeep_version("apkeep 1.0.0") == (1, 0, 0)
    assert apkeep.parse_apkeep_version("apkeep 1.2.10\n") == (1, 2, 10)
    assert apkeep.parse_apkeep_version("not a version") is None


def test_gplay_supported_requires_1_0_0(monkeypatch):
    monkeypatch.setattr(apkeep, "apkeep_version", lambda b: (0, 18, 0))
    assert apkeep.gplay_supported("apkeep") is False
    monkeypatch.setattr(apkeep, "apkeep_version", lambda b: (1, 0, 0))
    assert apkeep.gplay_supported("apkeep") is True
    monkeypatch.setattr(apkeep, "apkeep_version", lambda b: (1, 3, 2))
    assert apkeep.gplay_supported("apkeep") is True
    monkeypatch.setattr(apkeep, "apkeep_version", lambda b: None)   # unknown -> blocked
    assert apkeep.gplay_supported("apkeep") is False


def test_gplay_retry_skipped_on_old_apkeep(monkeypatch):
    """On apkeep < 1.0.0 the retry is disabled: it neither offers nor runs."""
    from types import SimpleNamespace
    monkeypatch.setattr(apkeep, "gplay_supported", lambda b: False)
    called = {"offer": False, "run": False}
    monkeypatch.setattr(apkeep, "offer_gplay_retry", lambda *a, **k: called.__setitem__("offer", True))
    monkeypatch.setattr(apkeep, "run_downloads", lambda *a, **k: called.__setitem__("run", True) or ([], []))
    failed = [{"package": "com.x", "program": "P", "reason": "r"}]
    args = SimpleNamespace(workers=4, sleep=0, outdir="apks")
    out = apkeep._gplay_retry("apkeep", failed, args, ("e@x", "aas_et/t"), [])
    assert out is failed
    assert called == {"offer": False, "run": False}


# --- GPLAY_TOKEN_HELP ------------------------------------------------------

def test_help_text_explains_how_to_get_token():
    h = apkeep.GPLAY_TOKEN_HELP
    assert "oauth_token" in h
    assert "EmbeddedSetup" in h
    assert "--oauth-token" in h
    assert "AAS" in h or "aas" in h


# --- offer_gplay_retry -----------------------------------------------------

def test_offer_declined_returns_none():
    inputs = iter(["n"])
    creds = apkeep.offer_gplay_retry(
        3, input_fn=lambda _="": next(inputs), getpass_fn=lambda _="": "x", out=lambda *_: None)
    assert creds is None


def test_offer_accepted_collects_email_and_token():
    inputs = iter(["y", "me@gmail.com"])
    creds = apkeep.offer_gplay_retry(
        3, input_fn=lambda _="": next(inputs),
        getpass_fn=lambda _="": "aas_et/tok", out=lambda *_: None)
    assert creds == ("me@gmail.com", "aas_et/tok")


def test_offer_accepted_but_blank_token_returns_none():
    inputs = iter(["y", "me@gmail.com"])
    creds = apkeep.offer_gplay_retry(
        3, input_fn=lambda _="": next(inputs),
        getpass_fn=lambda _="": "   ", out=lambda *_: None)
    assert creds is None


def test_offer_handles_eof_returns_none():
    """Ctrl-D / closed stdin at any prompt must skip cleanly, not crash."""
    def boom(_=""):
        raise EOFError
    creds = apkeep.offer_gplay_retry(
        2, input_fn=boom, getpass_fn=lambda _="": "x", out=lambda *_: None)
    assert creds is None


def test_offer_handles_keyboardinterrupt_returns_none():
    def boom(_=""):
        raise KeyboardInterrupt
    creds = apkeep.offer_gplay_retry(
        2, input_fn=boom, getpass_fn=lambda _="": "x", out=lambda *_: None)
    assert creds is None


# --- source selection ------------------------------------------------------

def test_no_auth_sources_excludes_google_play():
    assert "google-play" not in apkeep.NO_AUTH_SOURCES
    assert apkeep.NO_AUTH_SOURCES[0] == "apk-pure"


def test_cmd_google_play_missing_creds_omits_flags_keeps_tos():
    cmd = apkeep.build_apkeep_cmd("apkeep", "com.x", "/o/com.x", "google-play", 0)
    assert "-e" not in cmd and "-t" not in cmd
    assert "--accept-tos" in cmd


# --- apkeep invocation never blocks on stdin -------------------------------

def test_try_source_uses_devnull_stdin(tmp_path, monkeypatch):
    """apkeep must get EOF on stdin so it fails fast instead of blocking on its
    own interactive Email/AAS-token prompt."""
    captured = {}

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        captured.update(kw)
        (tmp_path / "app.apk").write_text("x")
        return Done()

    monkeypatch.setattr(apkeep.subprocess, "run", fake_run)
    ok, _ = apkeep._try_source("apkeep", "com.x", tmp_path, "apk-pure", 0)
    assert ok is True
    assert captured.get("stdin") is apkeep.subprocess.DEVNULL


# --- clear_failed_report ---------------------------------------------------

def test_clear_failed_report_removes_both(tmp_path):
    apkeep.write_failed_report(tmp_path, [{"package": "com.a", "program": "P"}])
    assert (tmp_path / "failed_packages.txt").exists()
    apkeep.clear_failed_report(tmp_path)
    assert not (tmp_path / "failed_packages.txt").exists()
    assert not (tmp_path / "failed_packages.json").exists()


def test_clear_failed_report_safe_when_absent(tmp_path):
    apkeep.clear_failed_report(tmp_path)  # must not raise


# --- write_failed_report ---------------------------------------------------

def test_write_failed_report_writes_txt_and_json(tmp_path):
    failed = [{"package": "com.a", "program": "P1", "reason": "boom"},
              {"package": "com.b", "program": "P2", "reason": "nope"}]
    txt, js = apkeep.write_failed_report(tmp_path, failed)
    assert txt.read_text().splitlines() == ["com.a", "com.b"]
    assert [e["package"] for e in json.loads(js.read_text())] == ["com.a", "com.b"]
