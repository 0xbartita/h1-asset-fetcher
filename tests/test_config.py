"""Saved-config persistence: round-trip, owner-only permissions, forget."""
import os
import stat

from h1_asset_fetcher.core import config


def _isolate(monkeypatch, tmp_path):
    path = tmp_path / "cfg" / "config.json"
    monkeypatch.setenv("H1_ASSET_FETCHER_CONFIG", str(path))
    return path


def test_save_load_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert config.load() == {}
    config.set_platform_creds("hackerone", {"username": "u", "token": "t"})
    config.set_prefs(platform="hackerone", scope="android", filter="bbp,private")
    assert config.get_platform_creds("hackerone") == {"username": "u", "token": "t"}
    assert config.get_prefs() == {
        "platform": "hackerone", "scope": "android", "filter": "bbp,private"}


def test_token_file_is_owner_only(monkeypatch, tmp_path):
    path = _isolate(monkeypatch, tmp_path)
    config.set_platform_creds("hackerone", {"token": "secret"})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_blank_values_not_persisted(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    config.set_platform_creds("bugcrowd", {"token": "", "username": ""})
    assert config.get_platform_creds("bugcrowd") == {}


def test_forget_removes_file(monkeypatch, tmp_path):
    path = _isolate(monkeypatch, tmp_path)
    config.set_platform_creds("hackerone", {"token": "t"})
    assert path.exists()
    config.forget()
    assert not path.exists()
    assert config.load() == {}


def test_creds_isolated_per_platform(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    config.set_platform_creds("hackerone", {"username": "u", "token": "h1"})
    config.set_platform_creds("bugcrowd", {"token": "bc"})
    assert config.get_platform_creds("hackerone") == {"username": "u", "token": "h1"}
    assert config.get_platform_creds("bugcrowd") == {"token": "bc"}
    assert config.get_platform_creds("intigriti") == {}
