#!/usr/bin/env python3
"""
HackerOne Asset Fetcher - by 0xbartita
Fetches Android/iOS/Executable assets from your HackerOne programs.
Generates organized output with package lists, store links, and program details.

Usage:
  python3 h1-asset-fetcher.py -u <username> -t <api_token>
  python3 h1-asset-fetcher.py -u <username> -t <api_token> --scope ios
  python3 h1-asset-fetcher.py -u <username> -t <api_token> --scope exe
  python3 h1-asset-fetcher.py -u <username> -t <api_token> --scope all
  python3 h1-asset-fetcher.py -u <username> -t <api_token> --scope all --filter all -o output/
"""

import sys, io, json, time, re, argparse, threading, signal, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

signal.signal(signal.SIGINT, lambda *_: (print("\n\033[91m[!] Interrupted\033[0m"), os._exit(1)))

try:
    import requests
except ImportError:
    print("[!] Missing dependency: pip install requests")
    sys.exit(1)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

H1_API_BASE = "https://api.hackerone.com/v1"
_print_lock = threading.Lock()

# ── Asset type groups ────────────────────────────────────────
SCOPE_TYPES = {
    "android": ("GOOGLE_PLAY_APP_ID", "OTHER_APK"),
    "ios":     ("APPLE_STORE_APP_ID", "TESTFLIGHT", "OTHER_IPA"),
    "exe":     ("DOWNLOADABLE_EXECUTABLES", "WINDOWS_APP_STORE_APP_ID"),
    "all":     ("GOOGLE_PLAY_APP_ID", "OTHER_APK", "APPLE_STORE_APP_ID",
                "TESTFLIGHT", "OTHER_IPA", "DOWNLOADABLE_EXECUTABLES",
                "WINDOWS_APP_STORE_APP_ID"),
}

SCOPE_LABELS = {
    "android": "Android (Play Store / APK)",
    "ios": "iOS (App Store / TestFlight / IPA)",
    "exe": "Executables (Desktop / Windows Store)",
    "all": "All asset types",
}

def log(msg, level="INFO"):
    colors = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m", "ERR": "\033[91m", "STEP": "\033[96m"}
    with _print_lock:
        print(f"{colors.get(level, '')}[{level}]\033[0m {msg}")

# ── Rate-limited HackerOne session ───────────────────────────

class H1Session:
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

h1 = None

# ── HackerOne API ────────────────────────────────────────────

def fetch_programs(prog_filter="bbp-private"):
    all_programs = []
    url = f"{H1_API_BASE}/hackers/programs?page[size]=100"
    skipped = {"pub": 0, "vdp": 0, "bbp": 0, "priv": 0}

    while url:
        log(f"  Page ({len(all_programs)} kept, {skipped['pub']} pub/{skipped['vdp']} VDP skip)...", "STEP")
        data = h1.get(url)
        if not data or "data" not in data:
            break

        for prog in data["data"]:
            a = prog.get("attributes", {})
            is_bbp = a.get("offers_bounties", False)
            is_public = a.get("state") == "public_mode"
            is_private = not is_public

            if prog_filter == "bbp" and not is_bbp:
                skipped["vdp"] += 1; continue
            elif prog_filter == "vdp" and is_bbp:
                skipped["bbp"] += 1; continue
            elif prog_filter == "private" and not is_private:
                skipped["pub"] += 1; continue
            elif prog_filter == "public" and is_private:
                skipped["priv"] += 1; continue
            elif prog_filter == "bbp-private":
                if not is_bbp:
                    skipped["vdp"] += 1; continue
                if is_public:
                    skipped["pub"] += 1; continue

            all_programs.append({
                "handle": a.get("handle", ""),
                "name": a.get("name", ""),
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

def fetch_scopes(handle, asset_types=None):
    if asset_types is None:
        asset_types = SCOPE_TYPES["android"]
    scopes = []
    url = f"{H1_API_BASE}/hackers/programs/{handle}/structured_scopes?page[size]=100"
    while url:
        data = h1.get(url)
        if not data or "data" not in data:
            break
        for s in data["data"]:
            a = s.get("attributes", {})
            if a.get("asset_type") in asset_types:
                scopes.append({"asset_type": a["asset_type"], "asset_identifier": a.get("asset_identifier", "")})
        nxt = data.get("links", {}).get("next")
        url = (f"{H1_API_BASE}{nxt}" if nxt and not nxt.startswith("http") else nxt) if nxt and nxt != url else None
    return scopes

def fetch_all(workers=5, prog_filter="bbp-private", asset_types=None):
    if asset_types is None:
        asset_types = SCOPE_TYPES["android"]
    log("Fetching programs from HackerOne API...", "STEP")
    programs = fetch_programs(prog_filter=prog_filter)
    if not programs:
        return []
    log(f"  Found {len(programs)} programs, fetching scopes ({workers} workers)...", "OK")

    found = 0
    def worker(p):
        p["scopes"] = fetch_scopes(p["handle"], asset_types=asset_types); return p

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

# ── Package/Identifier Resolution ────────────────────────────

KNOWN_PACKAGES_FILE = Path(__file__).parent / "known_packages.json"

def load_known_packages():
    """Load known package mappings from external file or use built-in defaults."""
    defaults = {
        "bay wheels android app": "com.motivateco.bayareabikeshare",
        "lyft rider android app": "me.lyft.android",
        "lyft driver android app": "com.lyft.android.driver",
        "ford diagnow android: com.ford.diagnow": "com.ford.diagnow",
        "yulife android": "com.yulife",
        "coinsquare android app": "com.coinsquare.app",
        "bitbuy android app": "com.bitbuy.app",
        "dailypay on-demand pay": "com.dailypay.mobileapp",
        "pnc earnedit": "com.pnc.eide.payday",
        "hopskipdrive caredriver app android": "com.hopskipdrive.driver",
        "hopskipdrive caregiver app androidos": "com.hopskipdrive.caregiver",
        "regions bank": "com.regions.mobbanking",
        "coupang (aos)": "com.coupang.mobile",
        "coupang eats (aos)": "com.coupang.eats",
        "coupang live creator (aos)": "com.coupang.live.creator",
        "coupang play (aos)": "com.coupang.play",
        "sephora.android": "com.sephora",
    }
    if KNOWN_PACKAGES_FILE.exists():
        try:
            custom = json.loads(KNOWN_PACKAGES_FILE.read_text())
            defaults.update(custom)
        except Exception:
            pass
    return defaults

KNOWN_PACKAGES = load_known_packages()
SKIP_IDENTIFIERS = {"splash android", "upgrade-home-android", "boostmoney-android", "app-staging-qa.apk"}

def is_valid_pkg(s):
    return bool(re.match(r'^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$', s.strip()))

def extract_identifier(raw_identifier, asset_type=None):
    """Extract a clean package/app identifier from raw H1 scope data."""
    identifier = raw_identifier.strip()
    if identifier.lower() in SKIP_IDENTIFIERS:
        return None

    # ── iOS App Store: extract numeric ID or bundle ID ──
    if asset_type in ("APPLE_STORE_APP_ID", "OTHER_IPA"):
        # Extract numeric App Store ID from URLs like itunes.apple.com/app/id123456
        m = re.search(r'id(\d{6,})', identifier)
        if m:
            return m.group(1)  # Return just the numeric ID

        # Extract from apps.apple.com URLs
        m = re.search(r'apps\.apple\.com/\w+/app/[^/]*/id(\d+)', identifier)
        if m:
            return m.group(1)

        # Clean up bundle IDs: remove team ID prefix (e.g., LNB245835Z.com.app.name -> com.app.name)
        m = re.match(r'^[A-Z0-9]{10}\.(.*)', identifier)
        if m:
            return m.group(1)

        # Already a clean bundle ID
        if is_valid_pkg(identifier):
            # Skip identifiers that are actually URLs parsed wrong
            if "itunes.apple.com" in identifier or "apps.apple.com" in identifier:
                return None
            return identifier

        # Extract bundle ID pattern from text
        m = re.search(r'([a-zA-Z][a-zA-Z0-9_-]*(?:\.[a-zA-Z][a-zA-Z0-9_-]*){2,})', identifier)
        if m:
            result = m.group(1)
            if "itunes.apple" not in result and "apps.apple" not in result:
                return result

        return identifier if identifier else None

    # ── TestFlight: extract join code or URL ──
    if asset_type == "TESTFLIGHT":
        # Already a URL
        if identifier.startswith("http"):
            return identifier
        # Extract join code from URL
        m = re.search(r'testflight\.apple\.com/join/([a-zA-Z0-9]+)', identifier)
        if m:
            return m.group(1)
        return identifier

    # ── Android / Executables: extract package name ──
    # Try to extract from Play Store URL
    m = re.search(r'id=([a-zA-Z0-9_.]+)', identifier)
    if m:
        return m.group(1)

    # Already a valid package name
    if is_valid_pkg(identifier):
        return identifier

    # Extract package-like pattern from text
    m = re.search(r'([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*){2,})', identifier)
    if m:
        return m.group(1)

    # Check known mappings
    key = identifier.lower().strip()
    if key in KNOWN_PACKAGES:
        return KNOWN_PACKAGES[key]

    # Handle wildcard patterns
    if "*" in identifier:
        base = identifier.replace(".*", "").strip()
        if is_valid_pkg(base):
            return base

    # Return as-is
    return identifier

# ── iTunes Lookup (resolve bundle IDs to App Store URLs) ─────

_itunes_cache = {}

def lookup_itunes(bundle_id):
    """Resolve iOS bundle ID to App Store URL via iTunes Search API."""
    if bundle_id in _itunes_cache:
        return _itunes_cache[bundle_id]
    try:
        resp = requests.get(
            f"https://itunes.apple.com/lookup?bundleId={bundle_id}&country=us",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("resultCount", 0) > 0:
                result = data["results"][0]
                url = result.get("trackViewUrl", "").split("?")[0]  # Remove tracking params
                _itunes_cache[bundle_id] = url
                return url
    except Exception:
        pass
    _itunes_cache[bundle_id] = None
    return None

def resolve_ios_store_links(packages):
    """Batch resolve iOS bundle IDs to App Store URLs."""
    to_resolve = [a for a in packages
                  if a["asset_type"] in ("APPLE_STORE_APP_ID", "OTHER_IPA")
                  and not a["package"].isdigit()]
    if not to_resolve:
        return

    log(f"Resolving {len(to_resolve)} iOS bundle IDs via iTunes API...", "STEP")
    resolved = 0
    for i, asset in enumerate(to_resolve, 1):
        url = lookup_itunes(asset["package"])
        if url:
            asset["_store_url"] = url
            resolved += 1
        if i % 20 == 0:
            log(f"  ... {i}/{len(to_resolve)} ({resolved} resolved)", "STEP")
        time.sleep(0.1)  # Rate limit
    log(f"  Resolved {resolved}/{len(to_resolve)} bundle IDs", "OK")

# ── Store URL generation ─────────────────────────────────────

def store_url(asset):
    """Generate store URL based on asset type."""
    # Use pre-resolved URL if available
    if "_store_url" in asset:
        return asset["_store_url"]

    at = asset["asset_type"]
    pkg = asset["package"]
    if at in ("GOOGLE_PLAY_APP_ID", "OTHER_APK"):
        return f"https://play.google.com/store/apps/details?id={pkg}"
    elif at == "APPLE_STORE_APP_ID":
        if pkg.isdigit():
            return f"https://apps.apple.com/app/id{pkg}"
        # Fallback: search URL (bundle ID couldn't be resolved)
        return f"https://apps.apple.com/search?term={pkg}"
    elif at == "TESTFLIGHT":
        if pkg.startswith("http"):
            return pkg
        return f"https://testflight.apple.com/join/{pkg}"
    elif at == "OTHER_IPA":
        return pkg
    elif at == "WINDOWS_APP_STORE_APP_ID":
        return f"https://apps.microsoft.com/detail/{pkg}"
    else:
        return pkg

# ── Output ───────────────────────────────────────────────────

def save_output(args, valid_packages, programs, prog_info, seen_handles, unique):
    """Save all output files to organized directory."""
    # Create output directory: <output>/<scope>/
    outdir = Path(args.output) / args.scope
    outdir.mkdir(parents=True, exist_ok=True)

    links = [store_url(a) for a in valid_packages]
    pkg_names = [a["package"] for a in valid_packages]

    # Save files
    (outdir / "store_links.txt").write_text("\n".join(links) + "\n")
    (outdir / "packages.txt").write_text("\n".join(pkg_names) + "\n")
    (outdir / "packages.json").write_text(json.dumps([{
        "package": a["package"],
        "program": a["program"],
        "handle": a["handle"],
        "asset_type": a["asset_type"],
        "store_url": store_url(a),
    } for a in valid_packages], indent=2) + "\n")

    # Programs cache (in root output dir)
    cache_path = Path(args.output) / "programs_cache.json"
    cache_path.write_text(json.dumps(programs, indent=2) + "\n")

    # Summary JSON
    summary = {
        "scope": args.scope,
        "filter": args.filter,
        "total_programs": len(seen_handles),
        "total_assets": len(valid_packages),
        "asset_types": list(set(a["asset_type"] for a in valid_packages)),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "files": {
            "packages": str(outdir / "packages.txt"),
            "packages_json": str(outdir / "packages.json"),
            "store_links": str(outdir / "store_links.txt"),
            "programs_cache": str(cache_path),
        }
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    return outdir, links

# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="HackerOne Asset Fetcher — Fetch Android/iOS/Exe assets from H1 programs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -u user -t token                         # Android assets from private BBPs
  %(prog)s -u user -t token --scope ios              # iOS assets
  %(prog)s -u user -t token --scope all --filter all # Everything from all programs
  %(prog)s -u user -t token -o output/ --scope exe   # Executables, save to output/exe/

Environment variables:
  H1_USERNAME    HackerOne API username
  H1_API_TOKEN   HackerOne API token

Get your API token at: https://hackerone.com/settings/api_token/edit
        """)
    parser.add_argument("-u", "--username", default=os.environ.get("H1_USERNAME", ""),
                        help="HackerOne API username (or set H1_USERNAME env var)")
    parser.add_argument("-t", "--token", default=os.environ.get("H1_API_TOKEN", ""),
                        help="HackerOne API token (or set H1_API_TOKEN env var)")
    parser.add_argument("-o", "--output", default="output",
                        help="Output directory (default: output/)")
    parser.add_argument("-f", "--filter",
                        choices=["bbp-private", "bbp", "vdp", "private", "public", "all"],
                        default="bbp-private",
                        help="Filter programs (default: bbp-private)")
    parser.add_argument("-s", "--scope",
                        choices=["android", "ios", "exe", "all"],
                        required=True,
                        help="Asset scope: android, ios, exe, all")
    parser.add_argument("--programs-file", default=None,
                        help="Reuse cached programs_cache.json instead of fetching")
    args = parser.parse_args()

    if not args.username or not args.token:
        log("Username and token required.", "ERR")
        log("  Use -u/-t flags or set H1_USERNAME/H1_API_TOKEN env vars.", "ERR")
        log("  Get your token: https://hackerone.com/settings/api_token/edit", "ERR")
        sys.exit(1)

    asset_types = SCOPE_TYPES[args.scope]

    global h1
    h1 = H1Session(args.username, args.token)

    print("")
    print("  \033[96m╔════════════════════════════════════════════════════════════╗\033[0m")
    print("  \033[96m║\033[93m         H1 Asset Fetcher  |  by 0xbartita                \033[96m║\033[0m")
    print("  \033[96m╚════════════════════════════════════════════════════════════╝\033[0m")
    print("")

    log(f"Scope: {SCOPE_LABELS[args.scope]}", "INFO")
    log(f"Filter: {args.filter} | Output: {args.output}/{args.scope}/", "INFO")

    # Step 1: Get programs
    if args.programs_file:
        log(f"Loading from {args.programs_file}...", "STEP")
        programs = json.loads(Path(args.programs_file).read_text())
        # Re-filter scopes by current scope selection (cache may have different scope)
        for prog in programs:
            prog["scopes"] = [s for s in prog.get("scopes", []) if s["asset_type"] in asset_types]
        programs = [p for p in programs if p["scopes"]]
        log(f"  Filtered to {len(programs)} programs with {args.scope} assets", "OK")
    else:
        programs = fetch_all(prog_filter=args.filter, asset_types=asset_types)

    if not programs:
        log("No programs found.", "ERR")
        log("  - Check your credentials: https://hackerone.com/settings/api_token/edit", "ERR")
        log("  - Try a different filter: --filter all", "ERR")
        log("  - Make sure you have accepted program invitations on H1", "ERR")
        sys.exit(1)

    # Step 2: Collect + deduplicate assets
    prog_info = {p["handle"]: p for p in programs}
    assets = []
    for prog in programs:
        for scope in prog.get("scopes", []):
            assets.append({
                "program": prog["name"],
                "handle": prog["handle"],
                "asset_type": scope["asset_type"],
                "identifier": scope["asset_identifier"],
            })

    seen = set()
    unique = []
    for a in assets:
        pkg = extract_identifier(a["identifier"], asset_type=a["asset_type"])
        if not pkg or pkg in seen:
            continue
        seen.add(pkg)
        a["package"] = pkg
        unique.append(a)

    # Step 3: Programs table
    print(f"\n{'='*100}")
    log(f"PROGRAMS WITH {args.scope.upper()} ASSETS", "STEP")
    print(f"{'='*100}")
    print(f"{'#':<4} {'Program':<35} {'FastPay':<8} {'Triage':<8} {'SafeHbr':<8} {'Split':<7} {'State':<12} {'Assets'}")
    print(f"{'-'*4} {'-'*35} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*12} {'-'*6}")

    def flag(v):
        return "\033[92m✓\033[0m" if v is True else ("\033[91m✗\033[0m" if v is False else "\033[90m-\033[0m")

    seen_handles = set()
    for a in unique:
        h = a["handle"]
        if h in seen_handles:
            continue
        seen_handles.add(h)
        info = prog_info.get(h, {})
        name = info.get("name", a["program"])[:35]
        n = len(info.get("scopes", []))
        st = info.get("submission_state", "?")
        sc = "\033[92m" if st == "open" else "\033[91m"
        print(f"{len(seen_handles):<4} {name:<35} {flag(info.get('fast_payments')):<17} {flag(info.get('triage_active')):<17} {flag(info.get('gold_standard_safe_harbor')):<17} {flag(info.get('allows_bounty_splitting')):<16} {sc}{st:<12}\033[0m {n}")

    print(f"{'='*100}")

    # Step 4: Filter valid packages
    valid_packages = [a for a in unique if a.get("package") and is_valid_pkg(a["package"])]

    print(f"\n\033[92m[OK]\033[0m {len(valid_packages)} valid assets from {len(seen_handles)} programs\n")
    for i, a in enumerate(valid_packages, 1):
        asset_label = {"GOOGLE_PLAY_APP_ID": "PlayStore", "OTHER_APK": "APK",
                      "APPLE_STORE_APP_ID": "AppStore", "TESTFLIGHT": "TestFlight",
                      "OTHER_IPA": "IPA", "DOWNLOADABLE_EXECUTABLES": "EXE",
                      "WINDOWS_APP_STORE_APP_ID": "WinStore"}.get(a["asset_type"], a["asset_type"])
        log(f"  {i:>3}. {a['package']:<50} [{asset_label}] ({a['program'][:25]})", "INFO")

    # Step 5: Resolve iOS store links
    if args.scope in ("ios", "all"):
        resolve_ios_store_links(valid_packages)

    # Step 6: Save output
    outdir, links = save_output(args, valid_packages, programs, prog_info, seen_handles, unique)

    print(f"\n{'='*70}")
    log("OUTPUT", "STEP")
    print(f"{'='*70}")
    log(f"  {outdir}/packages.txt      — {len(valid_packages)} package identifiers", "OK")
    log(f"  {outdir}/packages.json     — Full details (JSON)", "OK")
    log(f"  {outdir}/store_links.txt   — {len(links)} store links", "OK")
    log(f"  {outdir}/summary.json      — Scan summary", "OK")
    log(f"  {args.output}/programs_cache.json — Cached programs (--programs-file)", "OK")
    print(f"{'='*70}")

    # Scope-specific tips
    if args.scope in ("android", "all"):
        log(f"\nNext: Download APKs with apkeep or apk_downloader.py", "INFO")
    if args.scope in ("ios", "all"):
        log(f"\nNext: Download IPAs with ipatool or from a jailbroken device", "INFO")
    if args.scope in ("exe", "all"):
        log(f"\nNext: Download executables from the program's scope page", "INFO")

    log("Done!", "OK")

if __name__ == "__main__":
    main()
