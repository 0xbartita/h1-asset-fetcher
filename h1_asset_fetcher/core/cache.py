"""On-disk cache for expensive fetches so repeated runs don't re-hit the API.

HackerOne's structured_scopes endpoint is capped at 50 requests/min, so a full
private-BBP scan takes ~12 minutes. Caching the raw fetch lets a re-run — or a
switch between android/ios/exe scope — complete instantly with zero API calls.

Stored as JSON at ``~/.cache/h1-asset-fetcher/<name>.json`` (honoring
``XDG_CACHE_HOME``); the directory can be overridden with the
``H1_ASSET_FETCHER_CACHE`` env var (used by tests). Contents are program scope
data, not secrets — but we still keep the dir owner-only for tidiness.
"""
import os
import json
import stat
import time
from pathlib import Path

CACHE_VERSION = 1
_ENV_OVERRIDE = "H1_ASSET_FETCHER_CACHE"


def cache_dir():
    """Resolve the cache directory (env override > XDG_CACHE_HOME > ~/.cache)."""
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "h1-asset-fetcher"


def _path(name):
    return cache_dir() / f"{name}.json"


def load(name):
    """Return the cached dict for `name`, or {} if missing/unreadable/corrupt."""
    try:
        data = json.loads(_path(name).read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save(name, data):
    """Write `data` atomically with owner-only permissions. Returns the path."""
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, stat.S_IRWXU)  # 0700
    except OSError:
        pass

    data = dict(data)
    data.setdefault("version", CACHE_VERSION)

    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return path


def clear(name):
    """Delete a cache file. Returns the path (whether or not it existed)."""
    path = _path(name)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return path


def human_age(seconds):
    """Render an age in seconds as a short human string (e.g. '2h', '3d')."""
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)}m"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)}h"
    return f"{round(seconds / 86400)}d"


def now():
    """Current epoch seconds (wrapped so tests can monkeypatch one place)."""
    return time.time()
