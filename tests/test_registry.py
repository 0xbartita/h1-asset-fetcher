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


def test_unknown_platform_raises():
    with pytest.raises(KeyError):
        get_platform("does_not_exist")
