from h1_asset_fetcher.core.identifiers import (
    is_valid_pkg, extract_identifier, store_url, ASSET_CATEGORY, COLUMN_FIELDS)


def test_is_valid_pkg():
    assert is_valid_pkg("com.acme.app")
    assert not is_valid_pkg("not a package")


def test_extract_play_url():
    assert extract_identifier(
        "https://play.google.com/store/apps/details?id=com.x.y",
        "GOOGLE_PLAY_APP_ID") == "com.x.y"


def test_extract_ios_numeric_id():
    assert extract_identifier("https://apps.apple.com/us/app/x/id123456789",
                              "APPLE_STORE_APP_ID") == "123456789"


def test_store_url_play():
    a = {"asset_type": "GOOGLE_PLAY_APP_ID", "package": "com.x.y"}
    assert store_url(a) == "https://play.google.com/store/apps/details?id=com.x.y"


def test_category_and_columns():
    assert ASSET_CATEGORY["OTHER_APK"] == "android"
    a = {"asset_type": "OTHER_APK", "package": "com.x", "handle": "h", "program": "P"}
    assert COLUMN_FIELDS["c"](a) == "android"
    assert COLUMN_FIELDS["t"](a) == "com.x"


def test_web_domains_rejected_for_mobile():
    # Websites a platform mislabeled as iOS/Android apps must be dropped, not
    # emitted as bogus "packages" (regression: Bugcrowd ios-category domains).
    for d in ("www.draftkings.com", "sportsbook.draftkings.com",
              "tracker.bugcrowd.com", "draftkings.com",
              "https://www.example.com/app"):
        assert extract_identifier(d, "APPLE_STORE_APP_ID") is None, d
        assert extract_identifier(d, "GOOGLE_PLAY_APP_ID") is None, d
        assert extract_identifier(d, "OTHER_APK") is None, d


def test_real_packages_kept():
    # Real reverse-DNS app ids (incl. ccTLD roots and .app/.live/.de suffixes).
    for ident, at in (
        ("com.swapcard.apps.android", "GOOGLE_PLAY_APP_ID"),
        ("qa.ooredoo.android", "GOOGLE_PLAY_APP_ID"),
        ("se.atg.live", "GOOGLE_PLAY_APP_ID"),
        ("de.billigermietwagen.app.de", "GOOGLE_PLAY_APP_ID"),
        ("com.privateinternetaccess.android", "OTHER_APK"),
        ("com.burbn.instagram", "APPLE_STORE_APP_ID"),
    ):
        assert extract_identifier(ident, at) == ident, ident


def test_exe_domains_preserved():
    # Executables are legitimately hosted on websites — must NOT be filtered.
    assert extract_identifier("www.privateinternetaccess.com",
                              "DOWNLOADABLE_EXECUTABLES") == "www.privateinternetaccess.com"
    assert extract_identifier("app.easy4ipcloud.com",
                              "DOWNLOADABLE_EXECUTABLES") == "app.easy4ipcloud.com"
