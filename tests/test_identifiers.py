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
