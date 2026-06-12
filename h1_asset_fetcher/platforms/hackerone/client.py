"""HackerOne API client: rate-limited session, program + structured-scope
fetching, and the program filter parser."""
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ...core import log
from ...core.identifiers import SCOPE_TYPES

H1_API_BASE = "https://api.hackerone.com/v1"


class H1Session:
    """Rate-limited HackerOne API session (HTTP Basic auth)."""

    def __init__(self, username, token):
        self.session = requests.Session()
        self.session.auth = (username, token)
        self.session.headers.update({"Accept": "application/json"})
        self._lock = threading.Lock()
        self._last_request = 0

    def get(self, url, retries=3):
        for attempt in range(retries):
            with self._lock:
                elapsed = time.time() - self._last_request
                if elapsed < 0.12:
                    time.sleep(0.12 - elapsed)
                self._last_request = time.time()
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 401:
                    log("Authentication failed. Check your API credentials.", "ERR")
                    log("Get your token at: https://hackerone.com/settings/api_token/edit", "ERR")
                    sys.exit(1)
                elif resp.status_code == 429:
                    wait = min(2 ** attempt * 2, 30)
                    log(f"Rate limited, waiting {wait}s...", "WARN")
                    time.sleep(wait)
                    continue
                else:
                    return None
            except requests.exceptions.ConnectionError:
                log("Connection error. Check your internet connection.", "ERR")
                if attempt < retries - 1:
                    time.sleep(2)
            except Exception:
                if attempt < retries - 1:
                    time.sleep(1)
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

    while url:
        log(f"  Page ({len(all_programs)} kept, {skipped['pub']} pub/{skipped['vdp']} VDP skip)...", "STEP")
        data = session.get(url)
        if not data or "data" not in data:
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

    skip_msg = ", ".join(f"{v} {k}" for k, v in skipped.items() if v > 0) or "none"
    log(f"  Filtered [{prog_filter}]: {len(all_programs)} programs (skipped: {skip_msg})", "OK")
    return all_programs


def fetch_scopes(session, handle, asset_types=None):
    if asset_types is None:
        asset_types = SCOPE_TYPES["android"]
    scopes = []
    url = f"{H1_API_BASE}/hackers/programs/{handle}/structured_scopes?page[size]=100"
    while url:
        data = session.get(url)
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


def fetch_all(session, prog_filter="bbp,private", asset_types=None, workers=5):
    if asset_types is None:
        asset_types = SCOPE_TYPES["android"]
    log("Fetching programs from HackerOne API...", "STEP")
    programs = fetch_programs(session, prog_filter=prog_filter)
    if not programs:
        return []
    log(f"  Found {len(programs)} programs, fetching scopes ({workers} workers)...", "OK")

    found = 0

    def worker(p):
        p["scopes"] = fetch_scopes(session, p["handle"], asset_types=asset_types)
        return p

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, p): p for p in programs}
        for i, f in enumerate(as_completed(futures), 1):
            p = f.result()
            if p["scopes"]:
                found += 1
                log(f"  [{found}] {p['name']} -> {len(p['scopes'])} asset(s)", "OK")
            if i % 200 == 0:
                log(f"  ... {i}/{len(programs)}, {found} with assets", "STEP")

    result = [p for p in programs if p["scopes"]]
    log(f"  Done: {len(result)} programs, {sum(len(p['scopes']) for p in result)} total assets", "OK")
    return result
