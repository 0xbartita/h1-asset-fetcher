"""Bugcrowd scope fetcher for h1-asset-fetcher.

Ported from bbscope (github.com/sw33tLie/bbscope) subcommand "bc":
  - pkg/platforms/bugcrowd/bugcrowd.go
  - cmd/bc.go

Bugcrowd authenticates with the `_bugcrowd_session` cookie (the value of the
session cookie set after logging into https://bugcrowd.com). Pass it via the
`token` argument or the BUGCROWD_TOKEN environment variable.

Flow (mirrors bbscope):
  1. List engagements (programs) from
       https://bugcrowd.com/engagements.json?category=<bug_bounty|vdp>&...&page=N
     paginating with paginationMeta.totalCount, collecting each engagement's
     `briefUrl` and `accessStatus`.
  2. For each program brief URL:
       * "/engagements/..." handles  -> fetch the brief HTML page, pull the
         `data-api-endpoints` attribute off the ResearcherEngagementBrief react
         div, read engagementBriefApi.getBriefVersionDocument, append ".json",
         fetch it, and walk data.scope[].targets[] (inScope/name/uri/category).
       * legacy handles -> fetch <url>/target_groups, then each group's
         targets_url, walking targets[] (name/uri/category) with the group's
         in_scope flag.
  3. Map each native target category (website/api/android/ios/hardware/other/...)
     to an H1 asset_type via map_mobile_asset(); drop everything that is not a
     mobile/executable asset.

Stdlib + requests only.
"""

import os
import re
import json
import time

import requests

from .. import PlatformAuthError, map_mobile_asset


BASE_URL = "https://bugcrowd.com"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:82.0) "
              "Gecko/20100101 Firefox/82.0")

# Bugcrowd's WAF bans aggressive clients; bbscope forces 1 request/second.
REQUEST_DELAY = 1.1
MAX_RETRIES = 3
TIMEOUT = 30

WAF_STATUSES = (403, 406)

# Pull the ResearcherEngagementBrief react div's data-api-endpoints attribute
# out of the HTML brief page without a real HTML parser (no bs4/lxml allowed).
_BRIEF_DIV_RE = re.compile(
    r"data-react-class=(?:\"|')ResearcherEngagementBrief(?:\"|')",
)


def _env_token(token):
    """Resolve the session token from the arg or BUGCROWD_TOKEN."""
    tok = (token or "").strip()
    if not tok:
        tok = (os.environ.get("BUGCROWD_TOKEN") or "").strip()
    if not tok:
        raise PlatformAuthError(
            "Bugcrowd requires the _bugcrowd_session cookie. "
            "Set the BUGCROWD_TOKEN environment variable (or pass --token). "
            "Obtain it by logging in at https://bugcrowd.com and copying the "
            "'_bugcrowd_session' cookie value from your browser's dev tools."
        )
    return tok


def _make_session(token):
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    })
    # Bugcrowd reads the session purely from this cookie.
    s.headers["Cookie"] = "_bugcrowd_session=" + token
    return s


def _throttle(session):
    """Space requests ~REQUEST_DELAY apart (Bugcrowd's WAF bans bursts) without
    pausing before the very first call — so the listing starts responding
    immediately instead of looking frozen on launch."""
    last = getattr(session, "_last_ts", None)
    if last is not None:
        wait = REQUEST_DELAY - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    session._last_ts = time.monotonic()


def _get(session, url, log, label=""):
    """GET with a courtesy delay, a couple of retries, and WAF detection.

    Returns the requests.Response on success (incl. 404), or None on a
    non-recoverable error after retries. Raises PlatformAuthError on a WAF ban
    (403/406), which strongly correlates with a bad/expired session token.
    """
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        _throttle(session)
        try:
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException as exc:
            last_exc = exc
            log(f"  request error ({label or url}): {exc} "
                f"[retry {attempt}/{MAX_RETRIES}]", "WARN")
            time.sleep(REQUEST_DELAY * attempt)
            continue

        if resp.status_code in WAF_STATUSES:
            # bbscope treats this as a hard stop ("WAF banned, change IP").
            raise PlatformAuthError(
                f"Bugcrowd returned HTTP {resp.status_code} (WAF ban or invalid "
                f"session). Verify the BUGCROWD_TOKEN (_bugcrowd_session cookie) "
                f"is current, or change IP / wait before retrying."
            )

        if resp.status_code == 404:
            # Not fatal: the program may have been removed since listing.
            return resp

        if resp.status_code >= 500 or resp.status_code == 429:
            log(f"  HTTP {resp.status_code} ({label or url}) "
                f"[retry {attempt}/{MAX_RETRIES}]", "WARN")
            time.sleep(REQUEST_DELAY * attempt)
            continue

        return resp

    if last_exc is not None:
        log(f"  giving up on {label or url}: {last_exc}", "WARN")
    else:
        log(f"  giving up on {label or url} after {MAX_RETRIES} retries", "WARN")
    return None


def _json(resp):
    """Best-effort JSON decode; returns {} on any problem."""
    if resp is None:
        return {}
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError):
        return {}


# --------------------------------------------------------------------------- #
# prog_filter handling
# --------------------------------------------------------------------------- #

def _parse_filter(prog_filter):
    """Translate the H1-style comma filter into Bugcrowd dimensions.

    Returns (engagement_types, want_private, want_public).
      engagement_types: subset of ["bug_bounty", "vdp"]
      want_private/want_public: whether to keep private/public programs.

    bbscope's bc only exposes bbpOnly + pvtOnly. We mirror that:
      - bbp / bounty / paying  -> only bug_bounty engagements
      - vdp                    -> only vdp engagements
      - private                -> drop public (accessStatus == "open") programs
      - public                 -> drop private programs
      - all / empty            -> everything
    """
    parts = []
    if prog_filter:
        parts = [p.strip().lower()
                 for p in str(prog_filter).replace("-", ",").split(",")
                 if p.strip()]
    parts_set = set(parts)

    want_bbp = bool(parts_set & {"bbp", "bounty", "paying", "bug_bounty"})
    want_vdp = "vdp" in parts_set

    if want_bbp and not want_vdp:
        engagement_types = ["bug_bounty"]
    elif want_vdp and not want_bbp:
        engagement_types = ["vdp"]
    else:
        engagement_types = ["bug_bounty", "vdp"]

    want_private = "private" in parts_set
    want_public = "public" in parts_set
    if want_private == want_public:
        # both or neither requested -> no visibility filtering
        want_private = want_public = True

    return engagement_types, want_private, want_public


# --------------------------------------------------------------------------- #
# program listing
# --------------------------------------------------------------------------- #

def _list_engagements(session, engagement_type, log):
    """Return a list of (brief_url, access_status) for one engagement category.

    Mirrors bbscope GetProgramHandles: pages through engagements.json until the
    unique-program counter reaches paginationMeta.totalCount (or an empty page).
    """
    results = []
    seen = set()
    total_count = None
    page = 1

    base = (BASE_URL + "/engagements.json?category=" + engagement_type +
            "&sort_by=promoted&sort_direction=desc&page=")

    while True:
        resp = _get(session, base + str(page), log,
                    label=f"engagements[{engagement_type}] p{page}")
        data = _json(resp)
        if not data:
            break

        engagements = data.get("engagements")
        if not isinstance(engagements, list) or len(engagements) == 0:
            break

        if total_count is None:
            meta = data.get("paginationMeta") or {}
            tc = meta.get("totalCount")
            total_count = int(tc) if isinstance(tc, (int, float)) else None

        for eng in engagements:
            if not isinstance(eng, dict):
                continue
            brief_url = eng.get("briefUrl")
            if not brief_url:
                continue
            if brief_url in seen:
                continue
            seen.add(brief_url)
            access_status = eng.get("accessStatus") or ""
            results.append((brief_url, access_status))

        page += 1

        if total_count is not None and len(seen) >= total_count:
            break
        # Safety stop in case totalCount is missing/wrong.
        if page > 2000:
            log(f"  engagements[{engagement_type}]: page cap reached", "WARN")
            break

    return results


# --------------------------------------------------------------------------- #
# per-program scope extraction
# --------------------------------------------------------------------------- #

def _extract_data_api_endpoints(html):
    """Pull the data-api-endpoints JSON object off the brief react div.

    The markup looks like:
      <div data-react-class="ResearcherEngagementBrief"
           data-api-endpoints="{...escaped json...}" ...>
    HTML-escapes the JSON (&quot; etc). We locate the div, then read the
    attribute value, unescape it, and parse it.
    """
    if not html:
        return {}

    m = _BRIEF_DIV_RE.search(html)
    if not m:
        return {}

    # Search within the same tag: from the matched react-class back to the
    # opening '<' and forward to the closing '>'.
    start = html.rfind("<", 0, m.start())
    end = html.find(">", m.end())
    if start == -1 or end == -1:
        # Fall back to scanning a window after the match.
        end = m.end() + 8000
    tag = html[start:end + 1] if end != -1 else html[start:start + 8000]

    am = re.search(r"data-api-endpoints\s*=\s*([\"'])(.*?)\1", tag, re.DOTALL)
    if not am:
        return {}

    raw = am.group(2)
    # Unescape common HTML entities that appear in attribute-encoded JSON.
    raw = (raw.replace("&quot;", '"')
              .replace("&#34;", '"')
              .replace("&#x22;", '"')
              .replace("&apos;", "'")
              .replace("&#39;", "'")
              .replace("&amp;", "&")
              .replace("&lt;", "<")
              .replace("&gt;", ">"))
    try:
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return {}


def _scope_from_engagement(session, brief_url, log):
    """Extract (in_scope_targets, oos_targets) for an /engagements/ handle.

    Each target is a dict: {name, uri, category}.
    """
    resp = _get(session, BASE_URL + brief_url, log, label="brief " + brief_url)
    if resp is None or resp.status_code == 404:
        return [], []

    endpoints = _extract_data_api_endpoints(resp.text)
    if not endpoints:
        if "ResearcherEngagementCompliance" in (resp.text or ""):
            log(f"  compliance required, skipping: {brief_url}", "WARN")
        else:
            log(f"  brief api endpoints not found: {brief_url}", "WARN")
        return [], []

    brief_api = endpoints.get("engagementBriefApi") or {}
    doc_path = brief_api.get("getBriefVersionDocument")
    if not doc_path:
        log(f"  no getBriefVersionDocument for {brief_url}", "WARN")
        return [], []

    doc_url = doc_path + ".json"
    doc_resp = _get(session, BASE_URL + doc_url, log, label="briefdoc")
    doc = _json(doc_resp)
    if not doc:
        return [], []

    in_scope, oos = [], []
    scope_list = ((doc.get("data") or {}).get("scope")) or []
    if not isinstance(scope_list, list):
        return [], []

    for scope_el in scope_list:
        if not isinstance(scope_el, dict):
            continue
        is_in = bool(scope_el.get("inScope"))
        targets = scope_el.get("targets") or []
        if not isinstance(targets, list):
            continue
        for tgt in targets:
            if not isinstance(tgt, dict):
                continue
            name = (tgt.get("name") or "").strip()
            uri = (tgt.get("uri") or "").strip()
            category = tgt.get("category") or ""
            if not uri:
                uri = name
            if not uri:
                continue
            entry = {"name": name, "uri": uri, "category": category}
            (in_scope if is_in else oos).append(entry)

    return in_scope, oos


def _scope_from_target_groups(session, brief_url, log):
    """Extract (in_scope_targets, oos_targets) for a legacy (non-engagement)
    handle via the /target_groups + targets_url endpoints."""
    program_url = BASE_URL + "/" + brief_url.lstrip("/")
    resp = _get(session, program_url + "/target_groups", log,
                label="target_groups " + brief_url)
    if resp is None or resp.status_code == 404:
        return [], []

    data = _json(resp)
    groups = data.get("groups") or []
    if not isinstance(groups, list):
        return [], []

    in_scope, oos = [], []
    for group in groups:
        if not isinstance(group, dict):
            continue
        targets_url = group.get("targets_url")
        if not targets_url:
            continue
        group_in_scope = bool(group.get("in_scope"))

        t_resp = _get(session, BASE_URL + targets_url, log, label="targets")
        t_data = _json(t_resp)
        targets = t_data.get("targets") or []
        if not isinstance(targets, list):
            continue
        for tgt in targets:
            if not isinstance(tgt, dict):
                continue
            name = (tgt.get("name") or "").strip()
            uri = (tgt.get("uri") or "").strip()
            category = tgt.get("category") or ""
            if not uri:
                uri = name
            if not uri:
                continue
            entry = {"name": name, "uri": uri, "category": category}
            (in_scope if group_in_scope else oos).append(entry)

    return in_scope, oos


def _program_scope(session, brief_url, log):
    """Dispatch to the engagement or target-group extractor (cf. GetProgramScope)."""
    if brief_url.startswith("/engagements/"):
        return _scope_from_engagement(session, brief_url, log)
    return _scope_from_target_groups(session, brief_url, log)


# --------------------------------------------------------------------------- #
# category mapping
# --------------------------------------------------------------------------- #

def _map_targets(targets, eligible, asset_types, oos):
    """Map native targets -> H1 scope dicts, filtered to `asset_types`.

    Bugcrowd categories observed: website, api, android, ios, hardware, other.
    map_mobile_asset() handles android/ios/testflight/windows/exe; we hint it
    with the native category and the raw identifier.
    """
    out = []
    if not eligible and not oos:
        # Out-of-scope entries are only requested when oos=True.
        return out

    for tgt in targets:
        category = (tgt.get("category") or "").strip().lower()
        identifier = tgt.get("uri") or tgt.get("name") or ""
        if not identifier:
            continue

        # Drop obvious non-mobile/exe buckets early; map_mobile_asset would
        # return None anyway, but this avoids surprises on odd category names.
        asset_type = map_mobile_asset(category, identifier)
        if asset_type is None:
            continue
        if asset_types and asset_type not in asset_types:
            continue

        out.append({
            "asset_type": asset_type,
            "asset_identifier": identifier,
            "eligible_for_submission": bool(eligible),
            "eligible_for_bounty": None,
        })
    return out


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #

def fetch(token=None, username=None, prog_filter="all", asset_types=(),
          oos=False, log=print):
    """Fetch Bugcrowd programs and their mobile/executable scope.

    See platforms/__init__.py for the return-shape contract.
    """
    token = _env_token(token)
    session = _make_session(token)

    engagement_types, want_private, want_public = _parse_filter(prog_filter)
    asset_types = tuple(asset_types or ())

    log(f"Listing Bugcrowd engagements ({', '.join(engagement_types)})...",
        "STEP")

    # Collect unique (brief_url, access_status) across the requested categories.
    listed = {}
    for et in engagement_types:
        try:
            for brief_url, access_status in _list_engagements(session, et, log):
                # First occurrence wins; access status is stable per program.
                listed.setdefault(brief_url, access_status)
        except PlatformAuthError:
            raise
        except Exception as exc:  # be defensive, keep partial progress
            log(f"  failed listing {et}: {exc}", "WARN")

    # Apply visibility filter. accessStatus == "open" => public.
    handles = []
    for brief_url, access_status in listed.items():
        is_public = (str(access_status).lower() == "open")
        if is_public and not want_public:
            continue
        if (not is_public) and not want_private:
            continue
        handles.append((brief_url, access_status))

    log(f"  {len(handles)} programs to scan", "OK")

    programs = []
    found = 0
    for idx, (brief_url, access_status) in enumerate(handles, 1):
        try:
            in_targets, oos_targets = _program_scope(session, brief_url, log)
        except PlatformAuthError:
            raise
        except Exception as exc:
            log(f"  error on {brief_url}: {exc}", "WARN")
            continue

        scopes = _map_targets(in_targets, True, asset_types, oos)
        if oos:
            scopes += _map_targets(oos_targets, False, asset_types, oos)

        if not scopes:
            if idx % 25 == 0:
                log(f"  ... {idx}/{len(handles)} scanned, "
                    f"{found} with assets", "STEP")
            continue

        handle = brief_url.strip("/").split("/")[-1] or brief_url.strip("/")
        name = handle.replace("-", " ").title()
        is_public = (str(access_status).lower() == "open")

        programs.append({
            "handle": handle,
            "name": name,
            "platform": "bugcrowd",
            "url": BASE_URL + "/" + brief_url.lstrip("/"),
            "submission_state": "public" if is_public else "private",
            "scopes": scopes,
        })
        found += 1
        log(f"  [{found}] {handle} -> {len(scopes)} asset(s)", "OK")

    total_assets = sum(len(p["scopes"]) for p in programs)
    log(f"Bugcrowd done: {len(programs)} programs, "
        f"{total_assets} mobile/exe assets", "OK")
    return programs
