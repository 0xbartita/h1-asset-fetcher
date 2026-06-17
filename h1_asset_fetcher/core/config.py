"""Local config persistence so wizard users don't re-type their details.

Remembers per-platform credentials and the last platform/scope/filter, stored as
JSON at ``~/.config/h1-asset-fetcher/config.json``. The path can be overridden
with the ``H1_ASSET_FETCHER_CONFIG`` env var (used by tests and power users).

Secrets are kept in **plaintext**, protected only by filesystem permissions: the
directory is created 0700 and the file 0600 (owner read/write only), the same
posture as the aws/gh/npm CLIs. Anyone who can read your home directory or your
backups can read the token — rotate it if that machine is shared.
"""
import os
import json
import stat
from pathlib import Path

CONFIG_VERSION = 1
_ENV_OVERRIDE = "H1_ASSET_FETCHER_CONFIG"


def config_path():
    """Resolve the config file path (env override > XDG > ~/.config)."""
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "h1-asset-fetcher" / "config.json"


def load():
    """Return the config dict, or {} if it's missing, unreadable, or corrupt."""
    try:
        data = json.loads(config_path().read_text())
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data):
    """Write the config atomically with owner-only (0600) permissions."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, stat.S_IRWXU)  # 0700
    except OSError:
        pass

    data = dict(data)
    data.setdefault("version", CONFIG_VERSION)

    tmp = path.with_name(path.name + ".tmp")
    # Create the file 0600 from the start (don't rely on umask), then replace.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass
    return path


def get_platform_creds(platform_name):
    """Return saved {cred_key: value} for a platform (empty dict if none)."""
    return dict((load().get("credentials") or {}).get(platform_name) or {})


def set_platform_creds(platform_name, creds):
    """Persist a platform's credentials, dropping any blank values."""
    data = load()
    section = dict(data.get("credentials") or {})
    section[platform_name] = {k: v for k, v in creds.items() if v}
    data["credentials"] = section
    return save(data)


def get_prefs():
    """Return the last-used {platform, scope, filter} choices."""
    return dict(load().get("last") or {})


def set_prefs(platform=None, scope=None, filter=None):
    """Remember the last-used platform/scope/filter (only the args passed)."""
    data = load()
    last = dict(data.get("last") or {})
    if platform is not None:
        last["platform"] = platform
    if scope is not None:
        last["scope"] = scope
    if filter is not None:
        last["filter"] = filter
    data["last"] = last
    return save(data)


def forget():
    """Delete the whole config file (credentials + preferences). Returns path."""
    path = config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return path
