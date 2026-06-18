# Phase 1: Repackage + Plugin Registry Implementation Plan

**Goal:** Turn the `h1-asset-fetcher.py` monolith into an importable `h1_asset_fetcher` package with a pluggable platform registry and an installable `h1-asset-fetcher` command — with byte-identical CLI behavior — so the Phase 2 Textual TUI has a core to call into.

**Architecture:** Extract the script's logic into a `core/` library (pure functions: identifiers, output, HTTP session) plus a `platforms/` package built around a `Platform`/`Cred`/`register` registry. A thin `cli.py` wires argparse to the registry; the old `h1-asset-fetcher.py` becomes a shim. A characterization test pins the current CLI output and must keep passing through every task.

**Tech Stack:** Python 3.8+, `requests`, `pytest` (dev), `setuptools`/`pyproject.toml` packaging. (`textual` is intentionally NOT added in Phase 1.)

---

## Pre-flight

This plan runs on branch `feature/tui-redesign`. The working tree currently holds the (uncommitted) gaps #1–#3 + #8 + `platforms/` work. **Task 1 commits that as the pre-refactor baseline** so the refactor diff is clean.

## File Structure (target)

```
h1_asset_fetcher/
  __init__.py            # version export
  __version__.py         # __version__ = "1.1.0"
  __main__.py            # python -m h1_asset_fetcher -> cli.main()
  cli.py                 # argparse; headless run OR (no args) launch TUI/notice
  core/
    __init__.py
    session.py           # H1Session (rate-limited requests wrapper)
    identifiers.py       # is_valid_pkg, extract_identifier, lookup_itunes,
                         #   resolve_ios_store_links, store_url,
                         #   ASSET_CATEGORY, COLUMN_FIELDS
    fetch.py             # parse_filter, fetch_programs, fetch_scopes, fetch_all
    output.py            # json_entry, save_output
  platforms/
    __init__.py          # Platform, Cred, register, get_platform, all_platforms,
                         #   PlatformAuthError, map_mobile_asset, H1_ASSET_TYPES
    hackerone.py         # HackerOne(Platform)
    bugcrowd.py intigriti.py yeswehack.py immunefi.py
tests/
  conftest.py
  fixtures/cache.json
  test_identifiers.py
  test_filter.py
  test_registry.py
  test_output.py
  test_characterization.py
  test_cli_smoke.py
pyproject.toml
h1-asset-fetcher.py      # shim -> h1_asset_fetcher.cli:main
```

Module mapping from today's `h1-asset-fetcher.py` (line numbers are the current monolith):
- `H1Session` (≈58–95) → `core/session.py`
- `is_valid_pkg`, `extract_identifier`, `lookup_itunes`, `_itunes_cache`, `resolve_ios_store_links`, `store_url`, `ASSET_CATEGORY`, `COLUMN_FIELDS`, `SCOPE_TYPES`, `SCOPE_LABELS`, `KNOWN_PACKAGES*` → `core/identifiers.py` (constants that are shared move to `core/__init__.py` or `identifiers.py`)
- `parse_filter`, `fetch_programs`, `fetch_scopes`, `fetch_all` (≈101–231) → `core/fetch.py`
- `json_entry`, `save_output` → `core/output.py`
- `log` helper → `core/__init__.py` (shared)
- current root `platforms/*` (function form) → `h1_asset_fetcher/platforms/*` (class form)

---

### Task 1: Baseline commit + package skeleton + editable install

**Files:**
- Create: `h1_asset_fetcher/__init__.py`, `h1_asset_fetcher/__version__.py`, `h1_asset_fetcher/core/__init__.py`
- Create: `pyproject.toml`
- Create: `tests/conftest.py`

- [ ] **Step 1: Commit the pre-refactor baseline (the gaps #1–#3, #8, platforms/ work)**

```bash
git add h1-asset-fetcher.py README.md platforms/
git commit -m "feat: per-asset eligibility, --oos, packages.tsv, multi-platform plugins"
```

- [ ] **Step 2: Create the package skeleton**

`h1_asset_fetcher/__version__.py`:
```python
__version__ = "1.1.0"
```

`h1_asset_fetcher/__init__.py`:
```python
from .__version__ import __version__

__all__ = ["__version__"]
```

`h1_asset_fetcher/core/__init__.py`:
```python
import threading

_print_lock = threading.Lock()

_COLORS = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m",
           "ERR": "\033[91m", "STEP": "\033[96m"}


def log(msg, level="INFO"):
    """Coloured, thread-safe stderr/stdout logger (moved verbatim from the monolith)."""
    with _print_lock:
        print(f"{_COLORS.get(level, '')}[{level}]\033[0m {msg}")
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "h1-asset-fetcher"
version = "1.1.0"
description = "Fetch, download, and decompile mobile/exe assets from bug bounty programs"
requires-python = ">=3.8"
dependencies = ["requests>=2.28.0"]

[project.optional-dependencies]
telegram = ["telethon>=1.28.0"]
browser = ["playwright>=1.40.0"]
dev = ["pytest>=7.0"]

[project.scripts]
h1-asset-fetcher = "h1_asset_fetcher.cli:main"

[tool.setuptools.packages.find]
include = ["h1_asset_fetcher*"]
```

- [ ] **Step 4: Create `tests/conftest.py`** (lets tests import the package + locate fixtures before install)

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).resolve().parent / "fixtures"
```

- [ ] **Step 5: Editable install + confirm import**

Run: `pip install -e ".[dev]"`
Then run: `python3 -c "import h1_asset_fetcher; print(h1_asset_fetcher.__version__)"`
Expected: `1.1.0`

- [ ] **Step 6: Commit**

```bash
git add h1_asset_fetcher/ pyproject.toml tests/conftest.py
git commit -m "build: package skeleton + pyproject + editable install"
```

---

### Task 2: Characterization test (pin current CLI output)

**Files:**
- Create: `tests/fixtures/cache.json`
- Create: `tests/test_characterization.py`

- [ ] **Step 1: Create the fixture cache**

`tests/fixtures/cache.json`:
```json
[
  {"handle":"acme","name":"Acme Corp","submission_state":"open","scopes":[
    {"asset_type":"GOOGLE_PLAY_APP_ID","asset_identifier":"com.acme.app","eligible_for_submission":true,"eligible_for_bounty":true},
    {"asset_type":"GOOGLE_PLAY_APP_ID","asset_identifier":"com.acme.beta","eligible_for_submission":false,"eligible_for_bounty":false},
    {"asset_type":"OTHER_APK","asset_identifier":"com.acme.free","eligible_for_submission":true,"eligible_for_bounty":false}
  ]},
  {"handle":"globex","name":"Globex","submission_state":"open","scopes":[
    {"asset_type":"OTHER_APK","asset_identifier":"com.globex.app","eligible_for_submission":true,"eligible_for_bounty":true}
  ]}
]
```

- [ ] **Step 2: Write the characterization test**

`tests/test_characterization.py`:
```python
import json, subprocess, sys
from pathlib import Path
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
    assert tsv[0] == "com.acme.app,android,acme,https://play.google.com/store/apps/details?id=com.acme.app"
    data = json.loads((a / "packages.json").read_text())
    beta = [d for d in data if d["package"] == "com.acme.beta"][0]
    assert beta["in_scope"] is False and beta["eligible_for_submission"] is False


def test_bounty_only(tmp_path):
    out = _run(tmp_path, "--scope", "android", "-b")
    assert (out / "android" / "packages.txt").read_text().split() == [
        "com.acme.app", "com.globex.app"]
```

- [ ] **Step 3: Run against the CURRENT monolith — must pass now**

Run: `pytest tests/test_characterization.py -v`
Expected: 2 passed (this pins today's behavior; it must stay green through every later task).

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/cache.json tests/test_characterization.py
git commit -m "test: characterization tests pinning CLI output"
```

---

### Task 3: Extract `core/identifiers.py`

**Files:**
- Create: `h1_asset_fetcher/core/identifiers.py`
- Create: `tests/test_identifiers.py`

- [ ] **Step 1: Write failing unit tests**

`tests/test_identifiers.py`:
```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_identifiers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'h1_asset_fetcher.core.identifiers'`

- [ ] **Step 3: Create the module by moving code verbatim**

Move these symbols from `h1-asset-fetcher.py` into `h1_asset_fetcher/core/identifiers.py`, bodies unchanged: `SCOPE_TYPES`, `SCOPE_LABELS`, `ASSET_CATEGORY`, `COLUMN_FIELDS`, `KNOWN_PACKAGES_FILE`, `load_known_packages`, `KNOWN_PACKAGES`, `SKIP_IDENTIFIERS`, `is_valid_pkg`, `extract_identifier`, `_itunes_cache`, `lookup_itunes`, `resolve_ios_store_links`, `store_url`. Add at top:
```python
import re, time, json
from pathlib import Path
import requests
from . import log
```
`COLUMN_FIELDS`'s `u` lambda calls `store_url` (defined in the same module) — keep the lambda referencing the module-level `store_url`, which resolves at call time.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_identifiers.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add h1_asset_fetcher/core/identifiers.py tests/test_identifiers.py
git commit -m "refactor: extract core/identifiers from monolith"
```

---

### Task 4: Extract `core/session.py`

**Files:**
- Create: `h1_asset_fetcher/core/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Write failing test (constructor wiring only — no network)**

`tests/test_session.py`:
```python
from h1_asset_fetcher.core.session import H1Session


def test_session_sets_basic_auth():
    s = H1Session("user", "tok")
    assert s.session.auth == ("user", "tok")
    assert s.session.headers["Accept"] == "application/json"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Move `H1Session` verbatim**

Create `h1_asset_fetcher/core/session.py` with the `H1Session` class moved unchanged, headed by:
```python
import sys, time, threading
import requests
from . import log
```
Replace the in-class `sys.exit(1)` on 401 as-is (behavior preserved).

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_session.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add h1_asset_fetcher/core/session.py tests/test_session.py
git commit -m "refactor: extract core/session from monolith"
```

---

### Task 5: Platform registry (`platforms/__init__.py`)

**Files:**
- Create: `h1_asset_fetcher/platforms/__init__.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write failing tests**

`tests/test_registry.py`:
```python
import pytest
from h1_asset_fetcher.platforms import (
    Platform, Cred, register, get_platform, all_platforms,
    map_mobile_asset, PlatformAuthError)


def test_register_and_lookup():
    @register
    class Dummy(Platform):
        name = "dummy"; label = "Dummy"
        auth = [Cred("token", secret=True)]
        def fetch(self, creds, scope, filters, oos):
            return []
    assert "dummy" in {p.name for p in all_platforms()}
    assert get_platform("dummy").label == "Dummy"


def test_map_mobile_asset():
    assert map_mobile_asset("android", "play.google.com/store/apps/details?id=x") == "GOOGLE_PLAY_APP_ID"
    assert map_mobile_asset("android", "com.x.apk".replace("com.x", "x") + "") in ("OTHER_APK", "GOOGLE_PLAY_APP_ID")
    assert map_mobile_asset("website", "https://x.com") is None


def test_unknown_platform_raises():
    with pytest.raises(KeyError):
        get_platform("nope")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the registry**

`h1_asset_fetcher/platforms/__init__.py`:
```python
"""Platform plugin registry. Adding a platform = one new module that defines a
Platform subclass decorated with @register."""

H1_ASSET_TYPES = (
    "GOOGLE_PLAY_APP_ID", "OTHER_APK",
    "APPLE_STORE_APP_ID", "TESTFLIGHT", "OTHER_IPA",
    "DOWNLOADABLE_EXECUTABLES", "WINDOWS_APP_STORE_APP_ID",
)


class PlatformAuthError(Exception):
    """Missing credentials or failed authentication for a platform."""


class Cred:
    """One credential field a platform needs (drives the TUI form + CLI checks)."""
    def __init__(self, key, label=None, secret=False, required=True):
        self.key = key
        self.label = label or key
        self.secret = secret
        self.required = required


class Platform:
    name = ""          # unique slug, e.g. "hackerone"
    label = ""         # display name
    auth = []          # list[Cred]
    env = {}           # {cred_key: ENV_VAR}

    def fetch(self, creds, scope, filters, oos):
        """Return list[program] (H1-normalized). creds is {cred_key: value}."""
        raise NotImplementedError


_REGISTRY = {}


def register(cls):
    if not cls.name:
        raise ValueError(f"{cls.__name__} must set .name")
    _REGISTRY[cls.name] = cls
    return cls


def get_platform(name):
    return _REGISTRY[name]()          # raises KeyError if unknown


def all_platforms():
    _discover()
    return [cls() for cls in _REGISTRY.values()]


_discovered = False


def _discover():
    """Import every sibling module so their @register decorators run."""
    global _discovered
    if _discovered:
        return
    import importlib, pkgutil
    for mod in pkgutil.iter_modules(__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")
    _discovered = True


def map_mobile_asset(category, identifier):
    c = (category or "").lower()
    ident = (identifier or "").lower()
    if "testflight" in c or "testflight.apple.com" in ident:
        return "TESTFLIGHT"
    if "android" in c or "play.google.com" in ident or ident.endswith(".apk"):
        return "GOOGLE_PLAY_APP_ID" if "play.google.com" in ident else "OTHER_APK"
    if ("ios" in c or "iphone" in c or "ipad" in c or "apple" in c
            or "apps.apple.com" in ident or "itunes.apple.com" in ident):
        return "OTHER_IPA" if ident.endswith(".ipa") else "APPLE_STORE_APP_ID"
    if "windows" in c or "microsoft" in c:
        return "WINDOWS_APP_STORE_APP_ID"
    if ("exe" in c or "executable" in c or "mac" in c or "desktop" in c
            or "binary" in c or ident.endswith((".exe", ".dmg", ".pkg", ".msi", ".appx"))):
        return "DOWNLOADABLE_EXECUTABLES"
    return None
```

Note: `get_platform` calls `_discover()` lazily too — add `_discover()` as the first line of `get_platform`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_registry.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add h1_asset_fetcher/platforms/__init__.py tests/test_registry.py
git commit -m "feat: platform plugin registry (Platform/Cred/register)"
```

---

### Task 6: `core/fetch.py` + HackerOne plugin

**Files:**
- Create: `h1_asset_fetcher/core/fetch.py`
- Create: `h1_asset_fetcher/platforms/hackerone.py`
- Create: `tests/test_filter.py`

- [ ] **Step 1: Write failing test for `parse_filter`**

`tests/test_filter.py`:
```python
from h1_asset_fetcher.core.fetch import parse_filter


def test_parse_filter_defaults():
    assert parse_filter("bbp,private") == ("bbp", "private")
    assert parse_filter("vdp,public") == ("vdp", "public")
    assert parse_filter("all") == ("all", "all")
    assert parse_filter("bbp") == ("bbp", "all")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_filter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Move fetch logic into `core/fetch.py`**

Move `parse_filter`, `fetch_programs`, `fetch_scopes`, `fetch_all` verbatim. Replace the module-global `h1` with an explicit `session` parameter: `fetch_programs(session, prog_filter)`, `fetch_scopes(session, handle, asset_types)`, `fetch_all(session, prog_filter, asset_types)`. Header:
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from . import log
from .identifiers import SCOPE_TYPES
```

- [ ] **Step 4: Create the HackerOne plugin**

`h1_asset_fetcher/platforms/hackerone.py`:
```python
from . import Platform, Cred, register
from ..core.session import H1Session
from ..core import fetch as fetchmod
from ..core.identifiers import SCOPE_TYPES


@register
class HackerOne(Platform):
    name = "hackerone"
    label = "HackerOne"
    auth = [Cred("username"), Cred("token", secret=True)]
    env = {"username": "H1_USERNAME", "token": "H1_API_TOKEN"}

    def fetch(self, creds, scope, filters, oos):
        session = H1Session(creds["username"], creds["token"])
        asset_types = SCOPE_TYPES[scope]
        return fetchmod.fetch_all(session, prog_filter=filters, asset_types=asset_types)
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_filter.py tests/test_registry.py -v`
Expected: all passed; `all_platforms()` now includes `hackerone`.

- [ ] **Step 6: Commit**

```bash
git add h1_asset_fetcher/core/fetch.py h1_asset_fetcher/platforms/hackerone.py tests/test_filter.py
git commit -m "refactor: core/fetch + HackerOne platform plugin"
```

---

### Task 7: Migrate the 4 ported platforms to class form

**Files:**
- Create: `h1_asset_fetcher/platforms/{bugcrowd,intigriti,yeswehack,immunefi}.py`

- [ ] **Step 1: Move each module + wrap its `fetch()` in a Platform subclass**

For each platform, copy the existing `platforms/<name>.py` (the function-form port) into `h1_asset_fetcher/platforms/<name>.py`, change its relative import to `from . import PlatformAuthError, map_mobile_asset`, rename the module-level `fetch` to `_fetch`, and add a wrapper class. Example for bugcrowd (apply the analogous class to the other three with their env vars):
```python
# ... existing ported _fetch(token, username, prog_filter, asset_types, oos, log) ...
import os
from . import Platform, Cred, register
from ..core import log as _log
from ..core.identifiers import SCOPE_TYPES


@register
class Bugcrowd(Platform):
    name = "bugcrowd"
    label = "Bugcrowd"
    auth = [Cred("token", label="_bugcrowd_session cookie", secret=True)]
    env = {"token": "BUGCROWD_TOKEN"}

    def fetch(self, creds, scope, filters, oos):
        return _fetch(token=creds.get("token"), username=creds.get("username"),
                      prog_filter=filters, asset_types=SCOPE_TYPES[scope],
                      oos=oos, log=_log)
```
Env vars: bugcrowd→`BUGCROWD_TOKEN`, intigriti→`INTIGRITI_TOKEN`, yeswehack→`YESWEHACK_TOKEN` (+ `Cred("username", required=False)` and a `YESWEHACK_PASSWORD` note), immunefi→no auth (`auth = []`, `env = {}`).

- [ ] **Step 2: Verify discovery picks all 5 up**

Run: `python3 -c "from h1_asset_fetcher.platforms import all_platforms; print(sorted(p.name for p in all_platforms()))"`
Expected: `['bugcrowd', 'hackerone', 'immunefi', 'intigriti', 'yeswehack']`

- [ ] **Step 3: Compile + commit**

```bash
python3 -m py_compile h1_asset_fetcher/platforms/*.py
git add h1_asset_fetcher/platforms/
git commit -m "refactor: migrate Bugcrowd/Intigriti/YesWeHack/Immunefi to Platform classes"
```

---

### Task 8: `core/output.py`

**Files:**
- Create: `h1_asset_fetcher/core/output.py`
- Create: `tests/test_output.py`

- [ ] **Step 1: Write failing test**

`tests/test_output.py`:
```python
import json
from types import SimpleNamespace
from h1_asset_fetcher.core.output import save_output


def _args(tmp_path):
    return SimpleNamespace(output=str(tmp_path), scope="android", filter="bbp,private",
                           platform="hackerone", bounty_only=False,
                           columns="t,a,h,u", delimiter="\t")


def test_save_output_writes_files(tmp_path):
    valid = [{"package": "com.x", "program": "P", "handle": "h",
              "asset_type": "OTHER_APK"}]
    outdir, links = save_output(_args(tmp_path), valid, [], {}, {"h"}, valid, [])
    assert (outdir / "packages.txt").read_text().strip() == "com.x"
    assert (outdir / "packages.tsv").read_text().strip().startswith("com.x\tOTHER_APK\th\t")
    data = json.loads((outdir / "packages.json").read_text())
    assert data[0]["in_scope"] is True and data[0]["category"] == "android"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_output.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Move `json_entry` + `save_output` verbatim**

Create `h1_asset_fetcher/core/output.py` with both functions moved unchanged, headed by:
```python
import json, time
from pathlib import Path
from .identifiers import ASSET_CATEGORY, COLUMN_FIELDS, store_url
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_output.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add h1_asset_fetcher/core/output.py tests/test_output.py
git commit -m "refactor: extract core/output from monolith"
```

---

### Task 9: `cli.py` + `__main__.py`

**Files:**
- Create: `h1_asset_fetcher/cli.py`
- Create: `h1_asset_fetcher/__main__.py`
- Create: `tests/test_cli_smoke.py`

- [ ] **Step 1: Write failing smoke test (auth-error path, no network)**

`tests/test_cli_smoke.py`:
```python
import subprocess, sys
from conftest import ROOT


def test_bugcrowd_missing_token_exits_clean():
    r = subprocess.run([sys.executable, "-m", "h1_asset_fetcher",
                        "--platform", "bugcrowd", "--scope", "android"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1
    assert "BUGCROWD_TOKEN" in (r.stderr + r.stdout)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_cli_smoke.py -v`
Expected: FAIL (`No module named h1_asset_fetcher.__main__` or cli)

- [ ] **Step 3: Implement `cli.py`**

Build `main()` from the monolith's `main()` (argparse block + the orchestration in Step-1..Step-6), with these changes:
- All argparse flags identical to today (incl. `--platform`, `-b/--bounty-only`, `--oos`, `--columns`, `--delimiter`).
- Resolve credentials from flags → env (`platform.env`), build a `creds` dict per the platform's `auth`.
- Dispatch: `programs = get_platform(args.platform).fetch(creds, args.scope, args.filter, args.oos)` (replacing the old inline `fetch_all`/`fetch_platform`). The `--programs-file` path stays as-is (reads cache, filters by `asset_types`).
- Missing required `Cred` for a platform → print a message naming the env var and `sys.exit(1)`.
- The Step-2..Step-6 logic (eligibility/OOS gating, dedup, table, `resolve_ios_store_links`, `save_output`) moves verbatim, importing from `core`.
- Add `main()` no-args behavior:
```python
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        try:
            from .tui.app import run as run_tui   # Phase 2 drop-in
        except ImportError:
            print("TUI not available yet (Phase 2). Run with --help for the CLI.",
                  file=sys.stderr)
            argv = ["--help"]
        else:
            return run_tui()
    return _run_cli(argv)
```
Put the argparse-based logic in `_run_cli(argv)`.

- [ ] **Step 4: Implement `__main__.py`**

`h1_asset_fetcher/__main__.py`:
```python
from .cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_cli_smoke.py -v`
Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add h1_asset_fetcher/cli.py h1_asset_fetcher/__main__.py tests/test_cli_smoke.py
git commit -m "feat: cli entry point dispatching via platform registry"
```

---

### Task 10: Root shim + full regression

**Files:**
- Modify: `h1-asset-fetcher.py` (replace monolith body with shim)

- [ ] **Step 1: Replace the monolith with a shim**

`h1-asset-fetcher.py` (entire file):
```python
#!/usr/bin/env python3
"""Backward-compat shim. The implementation now lives in the h1_asset_fetcher
package. `python3 h1-asset-fetcher.py ...` and the `h1-asset-fetcher` command
are equivalent."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from h1_asset_fetcher.cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the FULL suite — characterization must still pass**

Run: `pytest -v`
Expected: all tests pass, including `tests/test_characterization.py` (proves the shim → package path is byte-identical to the old monolith).

- [ ] **Step 3: Verify the installed command works**

Run: `h1-asset-fetcher --platform intigriti --scope android`
Expected: clean `INTIGRITI_TOKEN` auth-error message, exit 1 (no traceback).

Run: `h1-asset-fetcher --programs-file tests/fixtures/cache.json --scope android --oos -o /tmp/h1regress`
Expected: same files/values as `tests/test_characterization.py` asserts.

- [ ] **Step 4: Commit**

```bash
git add h1-asset-fetcher.py
git commit -m "refactor: h1-asset-fetcher.py is now a shim over the package"
```

---

### Task 11: Docs + finalize

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README install section**

Add under Installation:
````markdown
### Install as a command

```bash
pip install -e .            # or: pipx install .
h1-asset-fetcher --help     # the command is now on your PATH
# optional extras:
pip install -e ".[telegram,browser]"
```
The legacy `python3 h1-asset-fetcher.py ...` still works.
````

- [ ] **Step 2: Run full suite once more**

Run: `pytest -v`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document pip install + h1-asset-fetcher command"
```

---

## Self-Review

**Spec coverage:** core/UI split (Tasks 3,4,6,8) ✓ · platform registry (Task 5) ✓ · 5 platforms migrated (Tasks 6,7) ✓ · pyproject + entry point + extras (Task 1) ✓ · backward-compat shim (Task 10) ✓ · no-args→TUI hook (Task 9) ✓ · behavior-preserving (characterization Tasks 2,10) ✓. Phase 2 TUI is explicitly out of scope. **Gap:** `core/download.py` / `core/decompile.py` from the spec layout are NOT in Phase 1 — they wrap existing standalone scripts and are only needed by the Phase-2 TUI; deferred to the Phase 2 plan. Noted, intentional.

**Placeholder scan:** every code step contains complete code; "move verbatim" steps name exact symbols + source lines + new imports. No TBD/TODO.

**Type consistency:** `Platform.fetch(creds, scope, filters, oos)` signature is consistent across Tasks 5/6/7. `save_output(args, valid_packages, programs, prog_info, seen_handles, unique, oos_packages)` matches the monolith's current call. `get_platform` raises `KeyError` (test_registry) ✓.
