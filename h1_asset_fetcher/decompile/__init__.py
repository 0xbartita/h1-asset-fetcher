"""Batch decompilation script (shell). Locate it with script_path():

    APKS_DIR=apks OUT_DIR=decompiled bash "$(python3 -c 'import h1_asset_fetcher.decompile as d; print(d.script_path("jadx"))')"

jadx.sh  — thorough jadx decompile
"""
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def script_path(name):
    """Absolute path to a bundled decompile script (e.g. 'jadx')."""
    p = _DIR / f"{name}.sh"
    if not p.exists():
        raise FileNotFoundError(f"no decompile script '{name}.sh' in {_DIR}")
    return str(p)
