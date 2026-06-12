"""Pin the CLI output: the repackaged tool (via the h1-asset-fetcher.py shim)
must produce byte-identical files to the pre-refactor monolith."""
import json
import subprocess
import sys

from conftest import ROOT, FIXTURES


def _run(tmp_path, *args):
    out = tmp_path / "out"
    cmd = [sys.executable, str(ROOT / "h1-asset-fetcher.py"),
           "--programs-file", str(FIXTURES / "cache.json"),
           "-o", str(out), *args]
    subprocess.run(cmd, check=True, cwd=ROOT, capture_output=True, text=True)
    return out


def test_oos_split_and_tsv(tmp_path):
    out = _run(tmp_path, "--scope", "android", "--oos",
               "--columns", "t,c,h,u", "--delimiter", ",")
    a = out / "android"
    assert (a / "packages.txt").read_text().split() == [
        "com.acme.app", "com.acme.free", "com.globex.app"]
    assert (a / "oos_packages.txt").read_text().split() == ["com.acme.beta"]
    tsv = (a / "packages.tsv").read_text().strip().splitlines()
    assert tsv[0] == ("com.acme.app,android,acme,"
                      "https://play.google.com/store/apps/details?id=com.acme.app")
    data = json.loads((a / "packages.json").read_text())
    beta = [d for d in data if d["package"] == "com.acme.beta"][0]
    assert beta["in_scope"] is False and beta["eligible_for_submission"] is False


def test_bounty_only(tmp_path):
    out = _run(tmp_path, "--scope", "android", "-b")
    assert (out / "android" / "packages.txt").read_text().split() == [
        "com.acme.app", "com.globex.app"]
