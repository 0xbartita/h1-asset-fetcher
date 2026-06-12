import os
import subprocess
import sys

from conftest import ROOT


def _env_without_creds():
    env = dict(os.environ)
    for k in ("H1_USERNAME", "H1_API_TOKEN", "BUGCROWD_TOKEN",
              "INTIGRITI_TOKEN", "YESWEHACK_TOKEN", "YESWEHACK_USERNAME"):
        env.pop(k, None)
    return env


def test_bugcrowd_missing_token_exits_clean():
    r = subprocess.run([sys.executable, "-m", "h1_asset_fetcher",
                        "--platform", "bugcrowd", "--scope", "android"],
                       cwd=ROOT, capture_output=True, text=True,
                       env=_env_without_creds())
    assert r.returncode == 1
    assert "BUGCROWD_TOKEN" in (r.stderr + r.stdout)


def test_hackerone_missing_creds_exits_clean():
    r = subprocess.run([sys.executable, "-m", "h1_asset_fetcher",
                        "--scope", "android"],
                       cwd=ROOT, capture_output=True, text=True,
                       env=_env_without_creds())
    assert r.returncode == 1
    assert "token" in (r.stderr + r.stdout).lower()
