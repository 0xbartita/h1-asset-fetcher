"""Intigriti scope fetcher for h1-asset-fetcher.

Ported from bbscope (github.com/sw33tLie/bbscope) subcommand "it":
pkg/platforms/intigriti/intigriti.go + cmd/it.go.

Intigriti exposes a researcher API authenticated with a Bearer token:

  * Program listing:
      GET /external/researcher/v1/programs?statusId=3&limit=500&offset=0
    paginated via offset += len(records) until offset >= maxCount.
    Records live under "records"; total count under "maxCount".

  * Per-program scope:
      GET /external/researcher/v1/programs/{programId}
    Targets live under "domains.content[]". Each entry carries:
      endpoint      -> the raw target (URL / package / bundle id)
      type.id       -> numeric category code (see CATEGORY_* below)
      type.value    -> human-readable category string
      tier.id       -> 5 means OUT OF SCOPE; 1 means a no-bounty/info tier
      description

Asset-type classification (bbscope GetCategoryID):
      1 url, 2 android (mobile), 3 apple (mobile), 4 cidr,
      5 device, 6 other, 7 wildcard
We keep only mobile (android/apple) and device/other entries that
map_mobile_asset() can resolve to a mobile/executable H1 asset type.

Auth token: `token` arg, falling back to env var INTIGRITI_TOKEN.
"""

import os
import re
import time

import requests

from . import PlatformAuthError, map_mobile_asset

API_BASE = "https://api.intigriti.com/external/researcher/v1"
SITE_BASE = "https://app.intigriti.com/researcher"

PAGE_LIMIT = 500
COURTESY_DELAY = 0.3        # seconds between requests
MAX_RETRIES = 3
RETRY_DELAY = 2.0           # seconds, matches bbscope's "Request blocked" backoff
REQUEST_TIMEOUT = 30

# Intigriti numeric category codes (type.id) -> coarse map_mobile_asset hint.
# Codes we cannot turn into a mobile/exe asset are intentionally omitted
# (1 url, 4 cidr, 7 wildcard) so they get dropped.
CATEGORY_ANDROID = 2
CATEGORY_APPLE = 3
CATEGORY_DEVICE = 5
CATEGORY_OTHER = 6

CATEGORY_HINTS = {
    CATEGORY_ANDROID: "android",
    CATEGORY_APPLE: "ios",
    CATEGORY_DEVICE: "executable",
    CATEGORY_OTHER: "other",
}

# tier.id == 5 means out-of-scope; tier.id == 1 is a no-bounty/info-only tier.
OOS_TIER_ID = 5
NO_BOUNTY_TIER_ID = 1

# confidentialityLevel.id: 1 InviteOnly, 2 Application, 3 Registered, 4 Public.
# bbscope treats 1/2/3 as private and 4 as public.
PUBLIC_CONFIDENTIALITY_ID = 4


def _resolve_token(token):
    token = token or os.environ.get("INTIGRITI_TOKEN")
    if not token:
        raise PlatformAuthError(
            "Intigriti requires an API token. Set the INTIGRITI_TOKEN "
            "environment variable (or pass --token). Obtain a researcher API "
            "token at https://app.intigriti.com/researcher/profile/personal-access-tokens"
        )
    return token


def _parse_filter(prog_filter):
    """Translate the H1-style prog_filter comma string into the dimensions
    Intigriti can actually express: bounty-only and private/public.

    Returns (bbp_only, pvt_only, public_only).
    """
    tokens = {t.strip().lower() for t in (prog_filter or "all").split(",") if t.strip()}
    if not tokens or "all" in tokens:
        return False, False, False
    bbp_only = bool(tokens & {"bbp", "bounty", "paying"})
    pvt_only = "private" in tokens
    public_only = "public" in tokens
    # If both private and public requested, neither constraint is meaningful.
    if pvt_only and public_only:
        pvt_only = public_only = False
    return bbp_only, pvt_only, public_only


def _request(session, url, token, log):
    """GET `url` with auth, brief retries, and bbscope-style rate-limit handling.

    Returns parsed JSON dict, or None on persistent failure.
    """
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_err = exc
            log(f"Request error ({exc}); retry {attempt}/{MAX_RETRIES}", "WARN")
            time.sleep(RETRY_DELAY)
            continue

        if resp.status_code == 401:
            raise PlatformAuthError(
                "Intigriti rejected the API token (HTTP 401). Check "
                "INTIGRITI_TOKEN is a valid researcher personal access token."
            )

        # bbscope: body containing "Request blocked" == rate limited -> retry.
        body = resp.text or ""
        if "Request blocked" in body or resp.status_code == 429:
            log(f"Rate limited; retry {attempt}/{MAX_RETRIES}", "WARN")
            time.sleep(RETRY_DELAY)
            continue

        if resp.status_code >= 500:
            last_err = f"HTTP {resp.status_code}"
            log(f"Server error {resp.status_code}; retry {attempt}/{MAX_RETRIES}", "WARN")
            time.sleep(RETRY_DELAY)
            continue

        if resp.status_code != 200:
            log(f"Unexpected HTTP {resp.status_code} for {url}", "WARN")
            return None

        try:
            return resp.json()
        except ValueError:
            last_err = "invalid JSON"
            log(f"Could not parse JSON from {url}; retry {attempt}/{MAX_RETRIES}", "WARN")
            time.sleep(RETRY_DELAY)
            continue

    log(f"Giving up on {url} ({last_err})", "ERR")
    return None


def _program_path(web_links):
    """Extract the program path from webLinks.detail (bbscope splits on '=')."""
    detail = ""
    if isinstance(web_links, dict):
        detail = web_links.get("detail") or ""
    if not isinstance(detail, str) or not detail:
        return None
    # bbscope: strings.Split(detail, "=")[1]
    if "=" in detail:
        return detail.split("=", 1)[1]
    return detail


def _iter_targets(detail_json):
    """Yield each scope entry dict under domains.content[]; defensive on shape."""
    if not isinstance(detail_json, dict):
        return
    domains = detail_json.get("domains")
    if not isinstance(domains, dict):
        return
    content = domains.get("content")
    if not isinstance(content, list):
        return
    for item in content:
        if isinstance(item, dict):
            yield item


def _classify(item):
    """Return (asset_type, identifier) for a scope item, or (None, ident).

    Uses the numeric Intigriti category as a hint and the endpoint string,
    delegating the final mobile/exe decision to map_mobile_asset().
    """
    endpoint = item.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        return None, None
    endpoint = endpoint.strip()

    type_obj = item.get("type") if isinstance(item.get("type"), dict) else {}
    try:
        cat_id = int(type_obj.get("id"))
    except (TypeError, ValueError):
        cat_id = None
    cat_value = type_obj.get("value")
    if not isinstance(cat_value, str):
        cat_value = ""

    # Prefer the numeric-code hint; fall back to the string category value.
    hint = CATEGORY_HINTS.get(cat_id, cat_value)
    asset_type = map_mobile_asset(hint, endpoint)
    return asset_type, endpoint


def _scrape_program(session, token, record, asset_types, oos, bbp_only, log):
    """Fetch and build a single program dict, or None if it has no kept scope."""
    if not isinstance(record, dict):
        return None

    prog_id = record.get("id")
    if prog_id is None:
        return None
    prog_id = str(prog_id)

    name = record.get("name") or record.get("companyName") or prog_id
    handle = prog_id

    program_path = _program_path(record.get("webLinks"))
    url = (SITE_BASE + program_path) if program_path else None

    # Program-level bounty signal from the listing record.
    max_bounty = 0
    mb = record.get("maxBounty")
    if isinstance(mb, dict):
        try:
            max_bounty = int(mb.get("value") or 0)
        except (TypeError, ValueError):
            max_bounty = 0
    program_pays = max_bounty != 0

    detail = _request(session, f"{API_BASE}/programs/{prog_id}", token, log)
    if detail is None:
        return None

    scopes = []
    for item in _iter_targets(detail):
        # tier.id == 5 -> out of scope.
        tier_obj = item.get("tier") if isinstance(item.get("tier"), dict) else {}
        try:
            tier_id = int(tier_obj.get("id"))
        except (TypeError, ValueError):
            tier_id = None

        is_oos = tier_id == OOS_TIER_ID
        if is_oos and not oos:
            continue

        # bbscope: with bbpOnly, also skip tier 1 (the no-bounty/info tier).
        if bbp_only and tier_id == NO_BOUNTY_TIER_ID and not is_oos:
            continue

        asset_type, identifier = _classify(item)
        if not asset_type or asset_type not in asset_types:
            continue

        # eligible_for_bounty: the program pays, the target is in scope, and
        # it is not on the no-bounty tier.
        if is_oos:
            eligible_for_bounty = False
        elif tier_id == NO_BOUNTY_TIER_ID:
            eligible_for_bounty = False
        elif program_pays:
            eligible_for_bounty = True
        else:
            eligible_for_bounty = False

        scopes.append({
            "asset_type": asset_type,
            "asset_identifier": identifier,
            "eligible_for_submission": not is_oos,
            "eligible_for_bounty": eligible_for_bounty,
        })

    if not scopes:
        return None

    return {
        "handle": handle,
        "name": name,
        "platform": "intigriti",
        "url": url,
        "submission_state": None,
        "scopes": scopes,
    }


def fetch(token=None, username=None, prog_filter="all", asset_types=(),
          oos=False, log=print):
    """Fetch Intigriti programs and return mobile/exe scope in the H1 shape.

    See platforms/__init__.py for the program-dict contract.
    """
    token = _resolve_token(token)
    asset_types = set(asset_types or ())
    if not asset_types:
        log("No asset_types requested; nothing to fetch.", "WARN")
        return []

    bbp_only, pvt_only, public_only = _parse_filter(prog_filter)

    session = requests.Session()
    programs = []
    seen_handles = set()

    offset = 0
    total = None
    log("Listing Intigriti programs", "STEP")

    while True:
        list_url = (
            f"{API_BASE}/programs?statusId=3"
            f"&limit={PAGE_LIMIT}&offset={offset}"
        )
        page = _request(session, list_url, token, log)
        time.sleep(COURTESY_DELAY)
        if page is None:
            break

        if total is None:
            try:
                total = int(page.get("maxCount"))
            except (TypeError, ValueError):
                total = None
            if total is not None:
                log(f"Total programs available: {total}", "INFO")

        records = page.get("records")
        if not isinstance(records, list) or not records:
            break

        for record in records:
            if not isinstance(record, dict):
                continue

            # Private/public filtering via confidentialityLevel.id.
            conf = record.get("confidentialityLevel")
            try:
                conf_id = int(conf.get("id")) if isinstance(conf, dict) else None
            except (TypeError, ValueError):
                conf_id = None
            is_public = conf_id == PUBLIC_CONFIDENTIALITY_ID

            if pvt_only and is_public:
                continue
            if public_only and not is_public:
                continue

            # Bounty-only program filter (bbscope: maxBounty != 0).
            if bbp_only:
                mb = record.get("maxBounty")
                try:
                    max_bounty = int(mb.get("value") or 0) if isinstance(mb, dict) else 0
                except (TypeError, ValueError):
                    max_bounty = 0
                if max_bounty == 0:
                    continue

            prog = _scrape_program(
                session, token, record, asset_types, oos, bbp_only, log,
            )
            time.sleep(COURTESY_DELAY)
            if prog is None:
                continue

            if prog["handle"] in seen_handles:
                continue
            seen_handles.add(prog["handle"])
            programs.append(prog)
            log(f"{prog['name']}: {len(prog['scopes'])} in-scope mobile/exe asset(s)", "OK")

        offset += len(records)
        if total is not None and offset >= total:
            break

    log(f"Intigriti: {len(programs)} program(s) with matching assets", "STEP")
    return programs
