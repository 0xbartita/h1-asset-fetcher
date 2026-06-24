import pytest
from h1_asset_fetcher.platforms import (
    Platform, Cred, register, get_platform, all_platforms, map_mobile_asset)


def test_builtin_platforms_discovered():
    names = {p.name for p in all_platforms()}
    assert {"hackerone", "bugcrowd", "intigriti", "yeswehack", "immunefi"} <= names


def test_register_and_lookup():
    @register
    class Dummy(Platform):
        name = "dummy_test"
        label = "Dummy"
        auth = [Cred("token", secret=True)]

        def fetch(self, creds, scope, filters, oos):
            return []

    assert get_platform("dummy_test").label == "Dummy"
    assert get_platform("dummy_test").auth[0].secret is True


def test_map_mobile_asset():
    assert map_mobile_asset(
        "android", "https://play.google.com/store/apps/details?id=x") == "GOOGLE_PLAY_APP_ID"
    assert map_mobile_asset("android", "com.x.y") == "OTHER_APK"
    assert map_mobile_asset("ios", "com.x.y") == "APPLE_STORE_APP_ID"
    assert map_mobile_asset("website", "https://x.com") is None


def test_map_mobile_asset_desktop_from_label():
    # Bugcrowd files desktop/exe assets under the catch-all "other" category and
    # names the OS only in the human-readable label — these must be detected.
    for label in ("Desktop MFA for Windows", "Okta Verify (Windows)",
                  "Okta Agent Windows", "Mattermost Desktop Apps",
                  "Gestor de Pedidos - Desktop Client",
                  "Intercept X Endpoint (MacOS)"):
        assert map_mobile_asset("other", label) == "DOWNLOADABLE_EXECUTABLES", label
    # Explicit category hints from Intigriti/YesWeHack still resolve.
    assert map_mobile_asset("executable", "https://acme.com/app") == "DOWNLOADABLE_EXECUTABLES"
    # File extensions are a category-independent signal.
    assert map_mobile_asset("", "https://dl.acme.com/setup.exe") == "DOWNLOADABLE_EXECUTABLES"
    assert map_mobile_asset("other", "AcmeInstaller.dmg") == "DOWNLOADABLE_EXECUTABLES"


def test_map_mobile_asset_no_false_positives():
    # An OS word buried in a URL path of a web/"other" target must NOT be read
    # as an executable (URLs aren't label-scanned; bare hosts have no spaces).
    assert map_mobile_asset("other", "https://promo.acme.com/windows-update") is None
    assert map_mobile_asset("website", "https://download.acme.com/windows") is None
    # "mac" as a substring of a hostname is not macOS.
    assert map_mobile_asset("other", "macys.com") is None
    # Microsoft Store pages are a store listing, not a downloadable binary.
    assert map_mobile_asset("other", "https://apps.microsoft.com/detail/9NABC") \
        == "WINDOWS_APP_STORE_APP_ID"


def test_unknown_platform_raises():
    with pytest.raises(KeyError):
        get_platform("does_not_exist")


def test_platform_filter_presets():
    # The wizard offers only filters a platform can actually express.
    vals = lambda name: [v for _, v in get_platform(name).filters]
    # HackerOne & Bugcrowd support VDP.
    assert "vdp" in vals("hackerone")
    assert "vdp" in vals("bugcrowd")
    # Intigriti & YesWeHack have no VDP dimension — must not offer it.
    assert "vdp" not in vals("intigriti")
    assert "vdp" not in vals("yeswehack")
    # Immunefi has no filtering — a single "everything" preset.
    assert vals("immunefi") == ["all"]
