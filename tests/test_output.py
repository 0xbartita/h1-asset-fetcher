import json
from types import SimpleNamespace
from h1_asset_fetcher.core.output import save_output


def _args(tmp_path):
    return SimpleNamespace(output=str(tmp_path), scope="android", filter="bbp,private",
                           platform="hackerone", bounty_only=False,
                           columns="t,a,h,u", delimiter="\t")


def test_save_output_writes_files(tmp_path):
    valid = [{"package": "com.x", "program": "P", "handle": "h", "asset_type": "OTHER_APK"}]
    outdir, links = save_output(_args(tmp_path), valid, [], {}, {"h"}, valid, [])
    assert (outdir / "packages.txt").read_text().strip() == "com.x"
    tsv = (outdir / "packages.tsv").read_text().strip()
    assert tsv == "com.x\tOTHER_APK\th\thttps://play.google.com/store/apps/details?id=com.x"
    data = json.loads((outdir / "packages.json").read_text())
    assert data[0]["in_scope"] is True and data[0]["category"] == "android"
    assert (outdir.parent / "programs_cache.json").exists()
