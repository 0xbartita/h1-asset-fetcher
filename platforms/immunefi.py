"""Immunefi scope fetcher for h1-asset-fetcher.

Ported from bbscope (github.com/sw33tLie/bbscope) — pkg/platforms/immunefi.

Immunefi has NO authentication. bbscope scrapes immunefi.com:

  1. GET https://immunefi.com/bug-bounty/  -> parse the embedded Next.js
     payload in the ``#__NEXT_DATA__`` <script> tag. The JSON at
     ``props.pageProps.bounties[]`` lists every program; each has an ``id``
     (the slug) and ``is_external`` flag. External programs are skipped
     (their scope lives on a third-party site).
  2. For each internal program, GET
     https://immunefi.com/bug-bounty/<id>/information/  -> parse its
     ``#__NEXT_DATA__`` payload and read ``props.pageProps.bounty.assets[]``.
     Every asset has a ``url`` (the raw target) and a ``type`` such as
     ``websites_and_applications`` or ``smart_contract``.

Immunefi assets are overwhelmingly smart-contract / web3 with the occasional
``websites_and_applications`` entry that points at a mobile app store listing
or an APK/IPA. We only keep targets that map to a mobile/exe H1 asset_type;
everything else (websites, contracts, APIs) is dropped. Programs with no
matching assets are still returned with an empty ``scopes`` list so the caller
never crashes.

stdlib + requests only. HTML is handled with json/regex (no bs4/lxml).
"""

import json
import re
import time

import requests

from . import PlatformAuthError, map_mobile_asset

PLATFORM = "immunefi"
PLATFORM_URL = "https://immunefi.com"
BOUNTY_LIST_URL = PLATFORM_URL + "/bug-bounty/"

# Native Immunefi asset "type" -> coarse map_mobile_asset() category hint.
# bbscope only ever selects these two buckets; we forward both to the mapper
# (which decides mobile/exe vs. drop based on the identifier).
CATEGORY_FILTERS = {
    "web": ["websites_and_applications"],
    "contracts": ["smart_contract"],
    "all": ["websites_and_applications", "smart_contract"],
}

# Regex to pull the embedded Next.js JSON out of the page without an HTML
# parser. <script id="__NEXT_DATA__" type="application/json">...</script>
_NEXT_DATA_RE = re.compile(
    r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_HEADERS = {
    "Accept": "*/*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

_COURTESY_DELAY = 0.4   # seconds between requests
_MAX_RETRIES = 3
_TIMEOUT = 30


def _log(log, msg, level="INFO"):
    """Call the host log callback defensively (it may be a plain print)."""
    try:
        log(msg, level)
    except TypeError:
        log(f"[{level}] {msg}")
    except Exception:
        pass


def _get(session, url, log):
    """GET ``url`` with a few brief retries. Returns response text or None."""
    last_err = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = session.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return resp.text
            last_err = f"HTTP {resp.status_code}"
            # 4xx other than rate-limiting won't fix themselves; bail early.
            if resp.status_code in (401, 403, 404):
                _log(log, f"{url} -> {last_err}", "WARN")
                return None
        except requests.RequestException as exc:
            last_err = str(exc)
        if attempt < _MAX_RETRIES:
            time.sleep(_COURTESY_DELAY * attempt)
    _log(log, f"Giving up on {url}: {last_err}", "WARN")
    return None


def _extract_next_data(html):
    """Return the parsed ``__NEXT_DATA__`` JSON object, or {} on any failure."""
    if not html:
        return {}
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return {}
    raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dig(obj, *keys):
    """Safely walk a chain of dict keys; return None if anything is missing."""
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _selected_categories(prog_filter):
    """Map the H1-style prog_filter comma string to Immunefi category buckets.

    Immunefi exposes no public/private or bbp/vdp distinction at the asset
    level, so filter dimensions it can't express are ignored. We always scrape
    every asset type ('all') unless the filter clearly narrows to web-only.
    The bucket choice only affects which native asset *types* we forward to the
    mapper — the mapper still has the final say on mobile/exe vs. drop.
    """
    tokens = {t.strip().lower() for t in (prog_filter or "all").split(",") if t.strip()}
    if not tokens or "all" in tokens:
        return CATEGORY_FILTERS["all"]
    # No real public/private/bbp/vdp equivalent on Immunefi -> default to all.
    return CATEGORY_FILTERS["all"]


def _build_scope(asset, oos, asset_types, log):
    """Turn one Immunefi asset dict into an H1 scope entry, or None to drop it."""
    if not isinstance(asset, dict):
        return None

    target = asset.get("url") or asset.get("target") or ""
    if not isinstance(target, str):
        target = str(target) if target is not None else ""
    target = target.strip()
    if not target:
        return None

    native_type = asset.get("type") or ""
    if not isinstance(native_type, str):
        native_type = ""

    # Immunefi has no per-asset out-of-scope concept (bbscope sets OutOfScope to
    # nil), so every asset we see is in-scope. When oos is False we'd keep them
    # anyway; the flag is honored for shape-parity with the H1 path.
    eligible_for_submission = True
    if not eligible_for_submission and not oos:
        return None

    # First try mapping from the identifier itself (catches play.google.com /
    # apps.apple.com / *.apk / *.ipa regardless of the coarse native type).
    h1_type = map_mobile_asset(native_type, target)
    if h1_type is None:
        # Native type is web/contract; let the identifier drive a final guess.
        h1_type = map_mobile_asset("", target)
    if h1_type is None or h1_type not in asset_types:
        return None

    return {
        "asset_type": h1_type,
        "asset_identifier": target,
        "eligible_for_submission": eligible_for_submission,
        "eligible_for_bounty": True,  # Immunefi is paying-only.
    }


def fetch(token=None, username=None, prog_filter="all", asset_types=(),
          oos=False, log=print):
    """Fetch Immunefi programs and return mobile/exe scopes in the H1 shape.

    Immunefi requires no auth, so ``token``/``username`` are ignored. Returns a
    list of program dicts; programs with no matching mobile/exe assets come back
    with an empty ``scopes`` list rather than being dropped.
    """
    asset_types = tuple(asset_types or ())
    session = requests.Session()

    _log(log, "Fetching Immunefi program list", "STEP")

    list_html = _get(session, BOUNTY_LIST_URL, log)
    if list_html is None:
        # No network / blocked. Immunefi has no token, so this is not an auth
        # problem — surface a warning and return nothing rather than crash.
        _log(log, "Could not load Immunefi bug-bounty index", "ERR")
        return []

    next_data = _extract_next_data(list_html)
    bounties = _dig(next_data, "props", "pageProps", "bounties")
    if not isinstance(bounties, list):
        _log(log, "Immunefi index missing props.pageProps.bounties", "WARN")
        bounties = []

    # Honor prog_filter (best-effort) only to choose native asset buckets.
    _selected_categories(prog_filter)

    program_ids = []
    for program in bounties:
        if not isinstance(program, dict):
            continue
        program_id = program.get("id")
        if not isinstance(program_id, str) or not program_id:
            continue
        if program.get("is_external"):
            continue  # External program: scope hosted off-site, bbscope skips.
        program_ids.append((program_id, program.get("project") or program.get("title")))

    _log(log, f"Found {len(program_ids)} internal Immunefi programs", "OK")

    programs = []
    for idx, (program_id, listed_name) in enumerate(program_ids, 1):
        url = f"{PLATFORM_URL}/bug-bounty/{program_id}/information/"
        time.sleep(_COURTESY_DELAY)

        page_html = _get(session, url, log)
        bounty = _dig(_extract_next_data(page_html), "props", "pageProps", "bounty")
        if not isinstance(bounty, dict):
            # Page renamed/empty: still emit the program with empty scopes.
            programs.append({
                "handle": program_id,
                "name": (listed_name if isinstance(listed_name, str) else program_id) or program_id,
                "platform": PLATFORM,
                "url": url,
                "submission_state": "open",
                "scopes": [],
            })
            continue

        name = bounty.get("project") or bounty.get("title") or listed_name or program_id
        if not isinstance(name, str) or not name:
            name = program_id

        assets = bounty.get("assets")
        if not isinstance(assets, list):
            assets = []

        scopes = []
        for asset in assets:
            entry = _build_scope(asset, oos, asset_types, log)
            if entry is not None:
                scopes.append(entry)

        if scopes:
            _log(log, f"[{idx}/{len(program_ids)}] {name}: {len(scopes)} mobile/exe asset(s)", "OK")

        programs.append({
            "handle": program_id,
            "name": name,
            "platform": PLATFORM,
            "url": url,
            "submission_state": "open",
            "scopes": scopes,
        })

    matched = sum(1 for p in programs if p["scopes"])
    _log(log, f"Immunefi: {matched}/{len(programs)} programs with mobile/exe assets", "STEP")
    return programs
