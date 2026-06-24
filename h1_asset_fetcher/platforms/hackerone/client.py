"""HackerOne API client: rate-limited session, program + structured-scope
fetching, and the program filter parser."""
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ...core import log, progress, progress_done
from ...core.identifiers import SCOPE_TYPES

H1_API_BASE = "https://api.hackerone.com/v1"

# Braille spinner frames for the in-place pagination status line.
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Per-endpoint request spacing, sized to HackerOne's documented hacker-API rate
# limits (https://api.hackerone.com/getting-started-hacker-api/#rate-limits):
#   * general read endpoints:        600 requests/min  -> ~0.10s apart
#   * the structured_scopes endpoint: 50 requests/min  (its own, stricter cap)
# Scope fetching is ~one request per program, so on a full scan the 50/min cap
# is the binding constraint. We pace just under each ceiling so we never trip a
# 429 in the first place; the retry logic below is only a safety net.
_READ_INTERVAL = 0.12      # program listing etc. — under the 600/min read cap
_SCOPES_INTERVAL = 1.3     # structured_scopes — ~46/min, under the 50/min cap

# Safety net for stray 429s (shared-IP neighbour, momentary burst): ride them
# out a few times, and only abort if throttling is genuinely sustained.
_MAX_RATE_LIMIT_WAITS = 4      # times to wait out a 429 on one request before skipping
_RATE_GIVEUP_LIMIT = 2         # throttle give-ups before aborting the whole run


class H1RateLimited(Exception):
    """HackerOne is throttling the token hard enough that progress stalls — the
    run should stop and the user should retry in a few minutes."""


def _retry_after_seconds(resp, fallback):
    """Honor a 429 Retry-After header (seconds), clamped to [1, 30]; else fallback."""
    raw = resp.headers.get("Retry-After")
    if raw:
        try:
            return max(1, min(int(float(raw)), 30))
        except (TypeError, ValueError):
            pass
    return fallback


class H1Session:
    """Rate-limited HackerOne API session (HTTP Basic auth)."""

    def __init__(self, username, token):
        self.session = requests.Session()
        self.session.auth = (username, token)
        self.session.headers.update({"Accept": "application/json"})
        self._lock = threading.Lock()
        self._last_request = 0
        self._rate_giveups = 0   # programs skipped due to sustained 429s

    def get(self, url, retries=3, label=None, min_interval=None):
        """Rate-limited GET with retries. Returns parsed JSON, or None on failure.

        `min_interval` is the minimum seconds since the previous request before
        this one fires, sized to the endpoint's documented limit (callers pass
        _SCOPES_INTERVAL for structured_scopes; defaults to _READ_INTERVAL).
        `label` names what's being fetched (e.g. "scopes for acme") so any
        message says what was affected. Transient errors (connection reset /
        timeout) retry **silently** — a recovered blip makes no noise. A 429
        rate limit is waited out patiently (it always clears) honoring
        Retry-After, without burning the transient-error budget. 401 is fatal;
        sustained throttling raises H1RateLimited to stop the run cleanly."""
        label = label or url
        interval = _READ_INTERVAL if min_interval is None else min_interval
        last_err = None
        rate_waits = 0
        attempt = 0
        while attempt < retries:
            with self._lock:
                elapsed = time.time() - self._last_request
                if elapsed < interval:
                    time.sleep(interval - elapsed)
                self._last_request = time.time()
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 401:
                    log("Authentication failed. Check your API credentials.", "ERR")
                    log("Get your token at: https://hackerone.com/settings/api_token/edit", "ERR")
                    sys.exit(1)
                if resp.status_code == 429:
                    rate_waits += 1
                    if rate_waits > _MAX_RATE_LIMIT_WAITS:
                        self._rate_giveups += 1
                        if self._rate_giveups >= _RATE_GIVEUP_LIMIT:
                            raise H1RateLimited(
                                "HackerOne is throttling this token hard (repeated "
                                "429s). Wait a few minutes and re-run.")
                        log(f"Gave up on {label}: still rate-limited after "
                            f"{_MAX_RATE_LIMIT_WAITS} waits — skipped", "WARN")
                        return None
                    wait = _retry_after_seconds(resp, min(2 ** rate_waits, 30))
                    log(f"Rate limited on {label}, waiting {wait}s "
                        f"(rate-limit retry {rate_waits}/{_MAX_RATE_LIMIT_WAITS})...", "WARN")
                    time.sleep(wait)
                    continue  # a 429 doesn't consume the transient-error budget
                # Any other status is not retryable — surface it, don't swallow.
                log(f"{label}: HTTP {resp.status_code}", "WARN")
                return None
            except H1RateLimited:
                raise
            except requests.exceptions.RequestException as exc:
                # Transient (connection reset / timeout / DNS). Retry quietly.
                last_err = str(exc) or exc.__class__.__name__
            except Exception as exc:  # malformed JSON etc. — also retry
                last_err = str(exc) or exc.__class__.__name__
            attempt += 1
            if attempt < retries:
                time.sleep(2)
        # Transient retries exhausted: one proportionate WARN naming what was
        # skipped (not a scary "check your internet" on a self-healing event).
        log(f"Gave up on {label} after {retries} tries ({last_err}) — skipped", "WARN")
        return None


def parse_filter(prog_filter):
    """Parse a filter string into a (bounty_type, visibility) tuple.

    Comma-separated: bbp/vdp (bounty type) + private/public (visibility) + all.
    """
    parts = [p.strip().lower() for p in prog_filter.replace("-", ",").split(",")]

    bounty = "all"
    visibility = "all"

    for p in parts:
        if p == "bbp":
            bounty = "bbp"
        elif p == "vdp":
            bounty = "vdp"
        elif p == "private":
            visibility = "private"
        elif p == "public":
            visibility = "public"
        elif p == "all":
            bounty = "all"
            visibility = "all"

    return (bounty, visibility)


def fetch_programs(session, prog_filter="bbp,private"):
    bounty_type, visibility = parse_filter(prog_filter)
    all_programs = []
    url = f"{H1_API_BASE}/hackers/programs?page[size]=100"
    skipped = {"pub": 0, "vdp": 0, "bbp": 0, "priv": 0}
    page = 0
    incomplete = False

    while url:
        page += 1
        progress(f"{_SPIN[page % len(_SPIN)]} Fetching programs · page {page} · "
                 f"{len(all_programs)} kept · {sum(skipped.values())} skipped")
        data = session.get(url, label=f"programs page {page}")
        if data is None:
            incomplete = True
            break
        if "data" not in data:
            break

        for prog in data["data"]:
            a = prog.get("attributes", {})
            is_bbp = a.get("offers_bounties", False)
            is_public = a.get("state") == "public_mode"
            is_private = not is_public

            # Filter by bounty type
            if bounty_type == "bbp" and not is_bbp:
                skipped["vdp"] += 1; continue
            elif bounty_type == "vdp" and is_bbp:
                skipped["bbp"] += 1; continue

            # Filter by visibility
            if visibility == "private" and not is_private:
                skipped["pub"] += 1; continue
            elif visibility == "public" and not is_public:
                skipped["priv"] += 1; continue

            all_programs.append({
                "handle": a.get("handle", ""),
                "name": a.get("name", ""),
                "platform": "hackerone",
                "state": a.get("state"),
                "fast_payments": a.get("fast_payments"),
                "gold_standard_safe_harbor": a.get("gold_standard_safe_harbor"),
                "triage_active": a.get("triage_active"),
                "allows_bounty_splitting": a.get("allows_bounty_splitting"),
                "submission_state": a.get("submission_state"),
                "scopes": []
            })

        nxt = data.get("links", {}).get("next")
        url = (f"{H1_API_BASE}{nxt}" if nxt and not nxt.startswith("http") else nxt) if nxt and nxt != url else None

    progress_done()  # release the in-place line before the summary
    if incomplete:
        log("  Program listing stopped early after a fetch error — "
            "results may be incomplete.", "WARN")
    skip_msg = ", ".join(f"{v} {k}" for k, v in skipped.items() if v > 0) or "none"
    log(f"  Filtered [{prog_filter}]: {len(all_programs)} programs (skipped: {skip_msg})", "OK")
    return all_programs


def fetch_scopes(session, handle, asset_types=None):
    if asset_types is None:
        asset_types = SCOPE_TYPES["android"]
    scopes = []
    url = f"{H1_API_BASE}/hackers/programs/{handle}/structured_scopes?page[size]=100"
    while url:
        data = session.get(url, label=f"scopes for {handle}",
                           min_interval=_SCOPES_INTERVAL)
        if not data or "data" not in data:
            break
        for s in data["data"]:
            a = s.get("attributes", {})
            if a.get("asset_type") in asset_types:
                scopes.append({
                    "asset_type": a["asset_type"],
                    "asset_identifier": a.get("asset_identifier", ""),
                    # Per-asset eligibility: H1 marks each structured scope
                    # individually, so a paid program can still contain
                    # out-of-scope or non-bounty assets. Default to in-scope /
                    # unknown when absent so older cached scopes keep working.
                    "eligible_for_submission": a.get("eligible_for_submission", True),
                    "eligible_for_bounty": a.get("eligible_for_bounty"),
                    "max_severity": a.get("max_severity"),
                })
        nxt = data.get("links", {}).get("next")
        url = (f"{H1_API_BASE}{nxt}" if nxt and not nxt.startswith("http") else nxt) if nxt and nxt != url else None
    return scopes


def fetch_all(session, prog_filter="bbp,private", asset_types=None, workers=1):
    # workers defaults to 1: the structured_scopes endpoint is capped at 50/min,
    # so requests are serialized ~1.3s apart regardless — extra workers add no
    # throughput and only risk bursting past the cap into 429s.
    if asset_types is None:
        asset_types = SCOPE_TYPES["android"]
    log("Fetching programs from HackerOne API...", "STEP")
    try:
        programs = fetch_programs(session, prog_filter=prog_filter)
    except H1RateLimited as e:
        progress_done()  # release the in-place pagination line if it was active
        log(str(e), "ERR")
        return []
    if not programs:
        return []
    # ~1.3s spacing plus per-request latency works out to ~40 programs/min in
    # practice (measured), so estimate against that rather than the raw spacing.
    eta_min = max(1, -(-len(programs) // 40))  # ceil division
    log(f"  Found {len(programs)} programs. Fetching scopes at ~40/min to stay "
        f"under HackerOne's 50/min scope limit — about {eta_min} min.", "OK")

    found = 0
    throttled = False

    def worker(p):
        p["scopes"] = fetch_scopes(session, p["handle"], asset_types=asset_types)
        return p

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, p): p for p in programs}
        for i, f in enumerate(as_completed(futures), 1):
            try:
                p = f.result()
            except H1RateLimited as e:
                throttled = True
                log(str(e), "ERR")
                for fut in futures:
                    fut.cancel()
                break
            if p["scopes"]:
                found += 1
                log(f"  [{found}] {p['name']} -> {len(p['scopes'])} asset(s)", "OK")
            if i % 200 == 0:
                log(f"  ... {i}/{len(programs)}, {found} with assets", "STEP")

    result = [p for p in programs if p.get("scopes")]
    if throttled:
        log(f"  Stopped early due to throttling — got {len(result)} program(s). "
            f"Wait a few minutes and re-run for the rest.", "WARN")
    log(f"  Done: {len(result)} programs, "
        f"{sum(len(p['scopes']) for p in result)} total assets", "OK")
    return result
