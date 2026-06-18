"""Drive the questionary wizard non-interactively by stubbing the prompt layer
(_ask) with canned answers and a fake platform that returns fixture programs."""
import json

from conftest import FIXTURES
from h1_asset_fetcher.platforms import Platform, Cred


class _FakePlatform(Platform):
    name = "fake"
    label = "Fake"
    auth = [Cred("token", secret=True)]

    def fetch(self, creds, scope, filters, oos):
        return json.loads((FIXTURES / "cache.json").read_text())


def test_asset_choice_format():
    from h1_asset_fetcher.tui import app as appmod
    a = {"package": "com.x", "asset_type": "OTHER_APK", "program": "Acme"}
    s = appmod._asset_choice(a)
    assert "com.x" in s and "APK" in s and "Acme" in s


def _force_gplay_supported(monkeypatch, supported):
    from h1_asset_fetcher.download import apkeep as apkmod
    monkeypatch.setattr(apkmod, "gplay_supported", lambda b=None: supported)


def test_gplay_retry_rejects_oauth_token_without_spawning(monkeypatch, tmp_path):
    """Pasting an oauth2_4/… value in the wizard must be refused up front — not
    handed to a doomed apkeep subprocess."""
    from h1_asset_fetcher.tui import app as appmod
    _force_gplay_supported(monkeypatch, True)
    apks = tmp_path / "apks"
    apks.mkdir()
    (apks / "failed_packages.txt").write_text("com.x\n")
    answers = iter([True, "me@gmail.com", "oauth2_4/abc"])  # confirm, email, token
    monkeypatch.setattr(appmod, "_ask", lambda q: next(answers))
    calls = []
    monkeypatch.setattr(appmod.subprocess, "run", lambda *a, **k: calls.append(a))
    appmod._gplay_retry(str(apks))
    assert calls == []   # OAuth token -> refused, no subprocess spawned


def test_gplay_retry_runs_with_aas_token(monkeypatch, tmp_path):
    from h1_asset_fetcher.tui import app as appmod
    _force_gplay_supported(monkeypatch, True)
    apks = tmp_path / "apks"
    apks.mkdir()
    (apks / "failed_packages.txt").write_text("com.x\n")
    answers = iter([True, "me@gmail.com", "aas_et/good"])
    monkeypatch.setattr(appmod, "_ask", lambda q: next(answers))
    argvs = []
    monkeypatch.setattr(appmod.subprocess, "run", lambda *a, **k: argvs.append(a[0]))
    appmod._gplay_retry(str(apks))
    assert len(argvs) == 1
    argv = argvs[0]
    assert "--source" in argv and "google-play" in argv
    assert "aas_et/good" in argv


def test_gplay_retry_disabled_on_old_apkeep(monkeypatch, tmp_path):
    """When apkeep is too old, the wizard must not prompt or spawn anything."""
    from h1_asset_fetcher.tui import app as appmod
    _force_gplay_supported(monkeypatch, False)
    apks = tmp_path / "apks"
    apks.mkdir()
    (apks / "failed_packages.txt").write_text("com.x\n")
    asked = []
    monkeypatch.setattr(appmod, "_ask", lambda q: asked.append(q))
    calls = []
    monkeypatch.setattr(appmod.subprocess, "run", lambda *a, **k: calls.append(a))
    appmod._gplay_retry(str(apks))
    assert asked == []   # no prompt
    assert calls == []   # no subprocess


def test_offer_saved_creds_only_when_token_present():
    """The 'Use saved credentials?' shortcut must appear only when the token is
    actually saved — not when only a non-secret (e.g. username) was persisted."""
    from h1_asset_fetcher.tui import app as appmod
    from h1_asset_fetcher.platforms import get_platform

    # YesWeHack: token + username, BOTH required=False (token OR username+pw).
    ywh = get_platform("yeswehack")
    assert appmod._has_usable_saved_creds(ywh, {}) is False
    assert appmod._has_usable_saved_creds(ywh, {"username": "me@x.com"}) is False
    assert appmod._has_usable_saved_creds(ywh, {"token": "jwt"}) is True

    # HackerOne: username + token, both required.
    h1 = get_platform("hackerone")
    assert appmod._has_usable_saved_creds(h1, {"token": "t"}) is False   # no username
    assert appmod._has_usable_saved_creds(h1, {"username": "u", "token": "t"}) is True

    # Bugcrowd / Intigriti: single required token.
    bc = get_platform("bugcrowd")
    assert appmod._has_usable_saved_creds(bc, {}) is False
    assert appmod._has_usable_saved_creds(bc, {"token": "c"}) is True

    # Immunefi: no auth → never offer.
    imm = get_platform("immunefi")
    assert appmod._has_usable_saved_creds(imm, {}) is False
    assert appmod._has_usable_saved_creds(imm, {"x": "y"}) is False


def test_wizard_run_saves_output(monkeypatch, tmp_path):
    from h1_asset_fetcher.tui import app as appmod
    monkeypatch.setattr(appmod, "all_platforms", lambda: [_FakePlatform()])
    monkeypatch.setattr(appmod, "get_platform", lambda name: _FakePlatform())
    # platform, token, scope, filter, bounty?, oos?, download?
    answers = iter(["fake", "x", "android", "bbp,private", False, False, False])
    monkeypatch.setattr(appmod, "_ask", lambda q: next(answers))
    monkeypatch.setenv("H1_ASSET_FETCHER_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.chdir(tmp_path)

    appmod.run()

    out = tmp_path / "output" / "android"
    assert (out / "packages.txt").read_text().split() == [
        "com.acme.app", "com.acme.free", "com.globex.app"]
    assert (out / "packages.tsv").exists()
