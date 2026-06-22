"""Immunefi scope fetcher for h1-asset-fetcher.

Originally ported from bbscope (github.com/sw33tLie/bbscope), then updated for
Immunefi's Next.js **App Router** redesign (2026): the old ``__NEXT_DATA__``
blob is gone. Data now ships in the React Server Components stream as a series
of ``self.__next_f.push([1,"<escaped json>"])`` script chunks.

Immunefi has NO authentication. Flow:

  1. GET https://immunefi.com/bug-bounty/  -> reassemble the RSC stream and read
     the ``"bounties":[ ... ]`` array. Each entry has ``slug`` / ``url`` /
     ``project`` / ``maxBounty``.
  2. For each program, GET https://immunefi.com/bug-bounty/<slug>/information/
     -> reassemble its RSC stream and read ``"assets":[ ... ]``. Each asset has
     a ``url`` (raw target) and a ``type`` (e.g. ``smart_contract`` or
     ``websites_and_applications``).

Immunefi assets are overwhelmingly smart-contract / web3 with the occasional
``websites_and_applications`` entry that points at a mobile app store listing or
an APK/IPA. We only keep targets that map to a mobile/exe H1 asset_type;
everything else (websites, contracts, APIs) is dropped. Programs with no matching
assets are still returned with an empty ``scopes`` list so the caller never
crashes.

stdlib + requests only. HTML/RSC is handled with json/regex (no bs4/lxml).
"""

import json
import re
import time

import requests

from .. import PlatformAuthError, map_mobile_asset

PLATFORM = "immunefi"
PLATFORM_URL = "https://immunefi.com"
BOUNTY_LIST_URL = PLATFORM_URL + "/bug-bounty/"

# Each RSC chunk is `self.__next_f.push([1,"<json-escaped string>"])`. We grab
# the `[1,"..."]` array literal and json.loads it to get the decoded string.
_RSC_CHUNK_RE = re.compile(r'self\.__next_f\.push\((\[1,".*?"\])\)', re.DOTALL)

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


class _BotChallenge(Exception):
    """Raised when Immunefi serves a JS bot-protection interstitial (Vercel
    Security Checkpoint / Cloudflare) that a plain HTTP client cannot pass."""


# Markers that identify a JS bot-protection page rather than real content.
_CHALLENGE_MARKERS = (
    "security checkpoint", "just a moment", "challenge-platform",
    "cf-browser-verification", "attention required", "enable javascript and cookies",
)


def _is_bot_challenge(resp):
    """True if the response is a bot-protection interstitial (not real content)."""
    if resp is None or resp.status_code not in (403, 429, 503):
        return False
    body = (resp.text or "")[:2000].lower()
    return any(m in body for m in _CHALLENGE_MARKERS)


def _get(session, url, log):
    """GET ``url`` with a few brief retries. Returns response text or None.

    Raises _BotChallenge if the response is a JS bot-protection interstitial —
    retrying or hammering more URLs from a flagged IP only makes it worse.
    """
    last_err = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = session.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if _is_bot_challenge(resp):
                raise _BotChallenge(url)
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


def _rsc_blob(html):
    """Reassemble the Next.js App Router RSC stream into one decoded string."""
    parts = []
    for m in _RSC_CHUNK_RE.finditer(html or ""):
        try:
            parts.append(json.loads(m.group(1))[1])
        except (ValueError, IndexError, TypeError):
            continue
    return "".join(parts)


def _balanced(s, start, open_c="[", close_c="]"):
    """Return the substring spanning a balanced open/close pair starting at
    ``start`` (which must index the opening bracket), or None. String-aware so
    brackets inside JSON string values don't throw off the depth count."""
    depth = 0
    i = start
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            i += 1
            while i < n and s[i] != '"':
                if s[i] == "\\":
                    i += 1
                i += 1
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
        i += 1
    return None


def _json_array_after(blob, key):
    """Find ``"key":[ ... ]`` in the RSC blob and return the parsed list, or []."""
    pos = blob.find(f'"{key}":')
    if pos < 0:
        return []
    bracket = blob.find("[", pos)
    if bracket < 0:
        return []
    raw = _balanced(blob, bracket, "[", "]")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


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

    Immunefi requires no auth, so ``token``/``username``/``prog_filter`` are
    ignored (it exposes no public/private/bbp/vdp distinction). Returns a list of
    program dicts; programs with no matching mobile/exe assets come back with an
    empty ``scopes`` list rather than being dropped.
    """
    asset_types = tuple(asset_types or ())
    session = requests.Session()

    _log(log, "Fetching Immunefi program list", "STEP")

    try:
        list_html = _get(session, BOUNTY_LIST_URL, log)
    except _BotChallenge:
        _log(log, "Immunefi is behind a bot-protection challenge (Vercel Security "
                  "Checkpoint / Cloudflare) that blocks automated scraping from this "
                  "IP. Try again later or from a different/residential IP. (Immunefi "
                  "scope is almost entirely smart contracts — it rarely yields "
                  "mobile/exe assets anyway.)", "ERR")
        return []
    if list_html is None:
        # No network / blocked. Immunefi has no token, so this is not an auth
        # problem — surface a warning and return nothing rather than crash.
        _log(log, "Could not load Immunefi bug-bounty index", "ERR")
        return []

    bounties = _json_array_after(_rsc_blob(list_html), "bounties")
    if not bounties:
        _log(log, "Immunefi index returned no bounties (RSC layout may have "
                  "changed again)", "WARN")

    program_ids = []
    for program in bounties:
        if not isinstance(program, dict):
            continue
        slug = program.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        rel_url = program.get("url") if isinstance(program.get("url"), str) else ""
        detail_url = (PLATFORM_URL + rel_url) if rel_url.startswith("/") else \
            f"{PLATFORM_URL}/bug-bounty/{slug}/information/"
        program_ids.append((slug, program.get("project") or slug, detail_url))

    _log(log, f"Found {len(program_ids)} Immunefi programs", "OK")

    programs = []
    failed = 0
    for idx, (slug, listed_name, detail_url) in enumerate(program_ids, 1):
        time.sleep(_COURTESY_DELAY)
        name = listed_name if isinstance(listed_name, str) and listed_name else slug

        try:
            page_html = _get(session, detail_url, log)
        except _BotChallenge:
            _log(log, f"Immunefi bot-protection challenge hit after {idx - 1} "
                      "program(s) — stopping early to avoid getting this IP further "
                      "blocked. Retry later or from a different/residential IP.", "ERR")
            break
        if page_html is None:
            # Detail page failed to load — name it rather than silently counting
            # it as "no mobile/exe assets" in the summary.
            failed += 1
            _log(log, f"[{idx}/{len(program_ids)}] {name}: page failed to load — skipped",
                 "WARN")
            continue
        assets = _json_array_after(_rsc_blob(page_html), "assets")

        scopes = []
        for asset in assets:
            entry = _build_scope(asset, oos, asset_types, log)
            if entry is not None:
                scopes.append(entry)

        if scopes:
            _log(log, f"[{idx}/{len(program_ids)}] {name}: "
                      f"{len(scopes)} mobile/exe asset(s)", "OK")

        programs.append({
            "handle": slug,
            "name": name,
            "platform": PLATFORM,
            "url": detail_url,
            "submission_state": "open",
            "scopes": scopes,
        })

    matched = sum(1 for p in programs if p["scopes"])
    summary = f"Immunefi: {matched}/{len(programs)} programs with mobile/exe assets"
    if failed:
        summary += f" ({failed} failed to load)"
    _log(log, summary, "STEP")
    return programs
