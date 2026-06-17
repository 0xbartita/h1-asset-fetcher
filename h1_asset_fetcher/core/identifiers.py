"""Asset-type vocabulary, identifier extraction, store-URL generation, and the
iTunes bundle-id resolver. Platform-agnostic — the H1 asset_type names are the
shared normalization target every platform maps into."""
import re
import time
import json
from pathlib import Path

import requests

from . import log

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

# Normalized category per H1 asset type (used by the --columns 'c' field).
ASSET_CATEGORY = {
    "GOOGLE_PLAY_APP_ID": "android",
    "OTHER_APK": "android",
    "APPLE_STORE_APP_ID": "ios",
    "TESTFLIGHT": "ios",
    "OTHER_IPA": "ios",
    "DOWNLOADABLE_EXECUTABLES": "exe",
    "WINDOWS_APP_STORE_APP_ID": "exe",
}

# Composable columns for packages.tsv (--columns). Each renders one field of an
# asset dict. store_url is resolved lazily so it picks up iTunes-resolved links.
COLUMN_FIELDS = {
    "t": lambda a: a["package"],                                  # target / identifier
    "a": lambda a: a["asset_type"],                               # raw H1 asset type
    "c": lambda a: ASSET_CATEGORY.get(a["asset_type"], "other"),  # normalized category
    "h": lambda a: a["handle"],                                   # program handle
    "p": lambda a: a["program"],                                  # program name
    "u": lambda a: store_url(a),                                  # store / download URL
}

# Optional custom mappings file at the repo root (absent by default).
KNOWN_PACKAGES_FILE = Path(__file__).resolve().parents[2] / "known_packages.json"


def load_known_packages():
    """Load custom package mappings from known_packages.json if it exists."""
    if KNOWN_PACKAGES_FILE.exists():
        try:
            return json.loads(KNOWN_PACKAGES_FILE.read_text())
        except Exception:
            pass
    return {}


KNOWN_PACKAGES = load_known_packages()
SKIP_IDENTIFIERS = set()


def is_valid_pkg(s):
    return bool(re.match(r'^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$', s.strip()))


# Common TLDs for distinguishing a forward web hostname (www.brand.com) from a
# reverse-DNS app id (com.brand.app). Two-letter labels are treated as ccTLDs.
_WEB_TLDS = {
    "com", "org", "net", "io", "co", "app", "dev", "ai", "me", "tv", "gg", "xyz",
    "info", "biz", "online", "site", "store", "tech", "cloud", "live", "life",
    "world", "shop", "page", "email", "mobi", "name", "pro", "edu", "gov", "top",
    "vip", "club", "games", "fun", "link", "media",
}


def _is_tld_like(label):
    label = label.lower()
    return label in _WEB_TLDS or (len(label) == 2 and label.isalpha())


def looks_like_web_host(s):
    """True if `s` is a forward web hostname/URL (www.x.com, api.x.io/path)
    rather than a reverse-DNS app id (com.x.app). Reverse-DNS ids START with a
    TLD; web hostnames END with one. Used to reject websites that a platform
    mislabeled as a mobile app (executables may legitimately be domains, so the
    caller decides which asset types to apply this to)."""
    s = (s or "").strip().lower()
    if not s:
        return False
    if s.startswith(("http://", "https://")) or "/" in s:
        return True
    labels = s.split(".")
    if len(labels) < 2:
        return False
    return _is_tld_like(labels[-1]) and not _is_tld_like(labels[0])


def extract_identifier(raw_identifier, asset_type=None):
    """Extract a clean package/app identifier from raw scope data."""
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

        # A website mislabeled as an iOS app (e.g. www.brand.com) is not an app.
        if looks_like_web_host(identifier):
            return None

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

    # A website mislabeled as an Android app is not a package. Only filter the
    # app types — DOWNLOADABLE_EXECUTABLES / Windows assets may be domains.
    if asset_type in ("GOOGLE_PLAY_APP_ID", "OTHER_APK") and looks_like_web_host(identifier):
        return None

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
