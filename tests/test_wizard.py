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


def test_wizard_run_saves_output(monkeypatch, tmp_path):
    from h1_asset_fetcher.tui import app as appmod
    monkeypatch.setattr(appmod, "all_platforms", lambda: [_FakePlatform()])
    monkeypatch.setattr(appmod, "get_platform", lambda name: _FakePlatform())
    # platform, token, scope, filter, bounty?, oos?, download?
    answers = iter(["fake", "x", "android", "bbp,private", False, False, False])
    monkeypatch.setattr(appmod, "_ask", lambda q: next(answers))
    monkeypatch.chdir(tmp_path)

    appmod.run()

    out = tmp_path / "output" / "android"
    assert (out / "packages.txt").read_text().split() == [
        "com.acme.app", "com.acme.free", "com.globex.app"]
    assert (out / "packages.tsv").exists()
