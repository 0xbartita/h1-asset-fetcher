"""YesWeHack scope fetcher for h1-asset-fetcher.

Ported faithfully from bbscope (github.com/sw33tLie/bbscope) subcommand "ywh":
  - pkg/platforms/yeswehack/yeswehack.go
  - cmd/ywh.go

YesWeHack API model (api.yeswehack.com):
  * Auth is a Bearer JWT. bbscope accepts a token directly (-t / --token,
    "From api.yeswehack.com"), OR logs in with email + password via
    POST /login, which may then require a TOTP (2FA) step via
    POST /account/totp. We mirror that: a token short-circuits login; otherwise
    we log in with username (email) + YESWEHACK_PASSWORD. 2FA cannot be
    completed non-interactively here (bbscope shells out to an OTP command),
    so if the account has 2FA enabled we raise PlatformAuthError asking for a
    JWT token instead.
  * Program listing:  GET /programs?page=N
        -> {"items": [{"slug","title","bounty","public","disabled", ...}],
            "pagination": {"nb_pages": N}}
      Paginated by pagination.nb_pages (bbscope starts assuming 2 pages then
      corrects from the response).
  * Per-program scope:  GET /programs/{slug}
        -> {"scopes": [{"scope": "<target>", "scope_type": "<category>"}, ...]}

  * Native scope_type categories (from bbscope GetCategoryID):
        url        -> web-application, api, ip-address
        mobile     -> mobile-application, mobile-application-android,
                      mobile-application-ios
        android    -> mobile-application-android
        apple      -> mobile-application-ios
        other      -> other
        executable -> application
    Only the mobile / executable buckets are relevant to this tool; web/api/ip/
    other are dropped.
"""

import os
import time

import requests

from .. import PlatformAuthError, map_mobile_asset

API_BASE = "https://api.yeswehack.com"
PROGRAMS_ENDPOINT = API_BASE + "/programs"
PROGRAM_BASE_ENDPOINT = API_BASE + "/programs/"
LOGIN_ENDPOINT = API_BASE + "/login"
SITE_PROGRAM_BASE = "https://yeswehack.com/programs/"

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
COURTESY_DELAY = 0.3  # seconds between requests


# YesWeHack scope_type -> coarse hint passed to map_mobile_asset().
# Anything not listed here is web/api/other and gets dropped.
_CATEGORY_HINTS = {
    "mobile-application-android": "android",
    "mobile-application-ios": "ios",
    "mobile-application": "mobile",  # platform-agnostic mobile target
    "application": "executable",     # downloadable desktop/exe binaries
}


def _headers(token):
    h = {"Accept": "application/json", "User-Agent": "h1-asset-fetcher"}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def _get(session, url, token, log, params=None):
    """GET a JSON endpoint with a few brief retries. Returns parsed JSON or None."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(
                url,
                headers=_headers(token),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_err = exc
            log("Request to %s failed (attempt %d/%d): %s"
                % (url, attempt, MAX_RETRIES, exc), "WARN")
            time.sleep(COURTESY_DELAY * attempt)
            continue

        if resp.status_code in (401, 403):
            raise PlatformAuthError(
                "YesWeHack authentication failed (HTTP %d). Provide a valid JWT "
                "Bearer token via the YESWEHACK_TOKEN env var (or token=...), "
                "obtained from api.yeswehack.com after logging in at "
                "https://yeswehack.com." % resp.status_code
            )

        if resp.status_code == 429 or resp.status_code >= 500:
            last_err = "HTTP %d" % resp.status_code
            log("Transient HTTP %d from %s (attempt %d/%d)"
                % (resp.status_code, url, attempt, MAX_RETRIES), "WARN")
            time.sleep(COURTESY_DELAY * attempt * 2)
            continue

        if resp.status_code != 200:
            log("Unexpected HTTP %d from %s; skipping"
                % (resp.status_code, url), "WARN")
            return None

        try:
            return resp.json()
        except ValueError:
            log("Non-JSON response from %s; skipping" % url, "WARN")
            return None

    log("Giving up on %s after %d attempts (%s)"
        % (url, MAX_RETRIES, last_err), "ERR")
    return None


def _login(session, username, password, log):
    """Email + password login -> JWT token. Mirrors bbscope Login().

    Returns a token string, or raises PlatformAuthError.
    """
    log("Logging in to YesWeHack as %s" % username, "STEP")
    try:
        resp = session.post(
            LOGIN_ENDPOINT,
            json={"email": username, "password": password},
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": "h1-asset-fetcher"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise PlatformAuthError(
            "YesWeHack login request failed: %s. Alternatively provide a JWT "
            "token via YESWEHACK_TOKEN." % exc
        )

    if resp.status_code != 200:
        raise PlatformAuthError(
            "YesWeHack login failed (HTTP %d). Check the email / YESWEHACK_PASSWORD, "
            "or provide a JWT token via YESWEHACK_TOKEN." % resp.status_code
        )

    try:
        body = resp.json()
    except ValueError:
        body = {}

    # No 2FA: a token comes back directly.
    token = body.get("token")
    if token:
        log("Login successful", "OK")
        return token

    # 2FA: a totp_token is returned and a code must be submitted to
    # /account/totp. bbscope shells out to an OTP-fetch command; we can't do
    # that non-interactively, so direct the user to supply a JWT token instead.
    if body.get("totp_token"):
        raise PlatformAuthError(
            "YesWeHack account requires 2FA (TOTP), which cannot be completed "
            "non-interactively. Provide an already-authenticated JWT Bearer "
            "token via the YESWEHACK_TOKEN env var (or token=...), obtained from "
            "api.yeswehack.com."
        )

    raise PlatformAuthError(
        "YesWeHack login returned neither a token nor a totp_token. Provide a "
        "JWT token via YESWEHACK_TOKEN."
    )


def _parse_filter(prog_filter):
    """Translate the H1-style prog_filter into the dimensions YWH can express.

    YesWeHack programs expose `bounty` (paying) and `public` flags. We map:
        bbp / paying  -> bounty-only
        private       -> private-only (public == False)
        public        -> public-only
    vdp has no direct YWH flag, so it is ignored. 'all' / empty disables both.
    Returns (bbp_only, pvt_only, pub_only).
    """
    tokens = {t.strip().lower() for t in (prog_filter or "").split(",") if t.strip()}
    if not tokens or "all" in tokens:
        return (False, False, False)
    bbp_only = bool(tokens & {"bbp", "bounty", "paying"})
    pvt_only = "private" in tokens
    pub_only = "public" in tokens
    return (bbp_only, pvt_only, pub_only)


def _program_scopes(session, slug, token, asset_types, oos, log):
    """Fetch one program's targets and map them to H1 scope dicts."""
    data = _get(session, PROGRAM_BASE_ENDPOINT + slug, token, log)
    time.sleep(COURTESY_DELAY)
    if not isinstance(data, dict):
        # Error-skip (fetch failed) — name it so it's not mistaken for a program
        # that genuinely has no matching assets.
        log("  Skipped %s: scope fetch failed" % slug, "WARN")
        return []

    out = []
    scopes = data.get("scopes")
    if not isinstance(scopes, list):
        scopes = []

    for entry in scopes:
        if not isinstance(entry, dict):
            continue
        target = entry.get("scope")
        scope_type = (entry.get("scope_type") or "").strip().lower()
        if not target:
            continue

        hint = _CATEGORY_HINTS.get(scope_type)
        if hint is None:
            # web-application / api / ip-address / other / unknown -> not for us.
            continue

        asset_type = map_mobile_asset(hint, target)
        if asset_type is None:
            continue
        if asset_type not in asset_types:
            continue

        # The YWH per-program scopes endpoint lists in-scope targets; bbscope
        # treats them all as in-scope. We have no per-target OOS data here, so
        # everything we emit is eligible_for_submission=True. (oos is honored at
        # the contract level: we never fabricate False entries.)
        out.append({
            "asset_type": asset_type,
            "asset_identifier": target,
            "eligible_for_submission": True,
            "eligible_for_bounty": None,
        })

    return out


def fetch(token=None, username=None, prog_filter="all", asset_types=(),
          oos=False, log=print):
    """Fetch YesWeHack programs and their mobile/exe scopes.

    Auth: a JWT Bearer token (token arg or YESWEHACK_TOKEN env var) is used
    directly. If absent, falls back to email/password login using `username`
    (email) + the YESWEHACK_PASSWORD env var.
    """
    asset_types = tuple(asset_types or ())
    token = token or os.environ.get("YESWEHACK_TOKEN")

    session = requests.Session()

    if not token:
        username = username or os.environ.get("YESWEHACK_EMAIL")
        password = os.environ.get("YESWEHACK_PASSWORD")
        if not username or not password:
            raise PlatformAuthError(
                "YesWeHack requires authentication. Set the YESWEHACK_TOKEN env "
                "var to a JWT Bearer token from api.yeswehack.com, OR provide a "
                "username (email) plus the YESWEHACK_PASSWORD env var to log in."
            )
        token = _login(session, username, password, log)

    bbp_only, pvt_only, pub_only = _parse_filter(prog_filter)

    log("Listing YesWeHack programs", "STEP")
    programs = []
    seen_slugs = set()

    page = 1
    nb_pages = 1  # corrected from the first response's pagination.nb_pages
    while page <= nb_pages:
        data = _get(session, PROGRAMS_ENDPOINT, token, log, params={"page": page})
        time.sleep(COURTESY_DELAY)
        if not isinstance(data, dict):
            break

        items = data.get("items")
        if not isinstance(items, list):
            items = []

        pagination = data.get("pagination")
        if isinstance(pagination, dict):
            try:
                nb_pages = int(pagination.get("nb_pages") or nb_pages)
            except (TypeError, ValueError):
                pass

        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("disabled"):
                continue

            slug = item.get("slug")
            if not slug or slug in seen_slugs:
                continue

            is_public = bool(item.get("public"))
            is_bounty = bool(item.get("bounty"))

            if pvt_only and is_public:
                continue
            if pub_only and not is_public:
                continue
            if bbp_only and not is_bounty:
                continue

            seen_slugs.add(slug)

            scopes = _program_scopes(
                session, slug, token, asset_types, oos, log)
            if not scopes:
                continue

            name = item.get("title") or slug
            programs.append({
                "handle": slug,
                "name": name,
                "platform": "yeswehack",
                "url": SITE_PROGRAM_BASE + slug,
                "submission_state": "open" if not item.get("disabled") else "disabled",
                "scopes": scopes,
            })
            log("  %s: %d in-scope asset(s)" % (slug, len(scopes)), "OK")

        page += 1

    log("YesWeHack: %d program(s) with matching assets" % len(programs), "OK")
    return programs
