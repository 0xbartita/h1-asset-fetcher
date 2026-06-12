from h1_asset_fetcher.platforms.hackerone.client import parse_filter


def test_parse_filter():
    assert parse_filter("bbp,private") == ("bbp", "private")
    assert parse_filter("vdp,public") == ("vdp", "public")
    assert parse_filter("all") == ("all", "all")
    assert parse_filter("bbp") == ("bbp", "all")
    assert parse_filter("private") == ("all", "private")
