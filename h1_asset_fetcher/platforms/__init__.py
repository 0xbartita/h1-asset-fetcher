"""Platform plugin registry.

Adding a bug-bounty platform = one new sub-package under platforms/ that defines
a Platform subclass decorated with @register. The CLI and (Phase 2) TUI discover
platforms through this registry, and build credential prompts from each
platform's `auth` descriptor.

A platform's fetch() returns programs in a normalized shape so the rest of the
pipeline is reused unchanged:

    {
        "handle": str,            # unique program slug
        "name": str,
        "platform": str,
        "submission_state": str | None,   # optional
        "scopes": [
            {"asset_type": <one of H1_ASSET_TYPES>,
             "asset_identifier": str,
             "eligible_for_submission": bool,
             "eligible_for_bounty": bool | None},
        ],
    }
"""
import importlib
import pkgutil
import re

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


# Standard program-filter presets as (label, filter-string) pairs. Each platform
# exposes the subset it can actually express via its `filters` attribute, so the
# wizard only ever offers filters that platform honors (e.g. Intigriti/YesWeHack
# have no VDP concept; Immunefi has no filter dimensions at all).
PRIVATE_BBP = ("Private BBP", "bbp,private")
PUBLIC_BBP = ("Public BBP", "bbp,public")
ALL_BBP = ("All BBP", "bbp")
VDP_ONLY = ("VDP only", "vdp")
EVERYTHING = ("Everything", "all")
FULL_FILTERS = [PRIVATE_BBP, PUBLIC_BBP, ALL_BBP, VDP_ONLY, EVERYTHING]


class Platform:
    """Base class for a bug-bounty platform plugin."""

    name = ""          # unique slug, e.g. "hackerone"
    label = ""         # display name
    auth = []          # list[Cred]
    env = {}           # {cred_key: ENV_VAR}
    filters = FULL_FILTERS   # (label, value) presets this platform can express

    def fetch(self, creds, scope, filters, oos):
        """Return list[program] (normalized). `creds` is {cred_key: value}."""
        raise NotImplementedError


_REGISTRY = {}
_discovered = False


def register(cls):
    """Class decorator: add a Platform subclass to the registry."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must set .name")
    _REGISTRY[cls.name] = cls
    return cls


def _discover():
    """Import every sub-package so their @register decorators run."""
    global _discovered
    if _discovered:
        return
    for mod in pkgutil.iter_modules(__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")
    _discovered = True


def get_platform(name):
    """Instantiate the platform registered under `name` (KeyError if unknown)."""
    _discover()
    return _REGISTRY[name]()


def all_platforms():
    """Instantiate every registered platform."""
    _discover()
    return [cls() for cls in _REGISTRY.values()]


# Desktop / OS keywords that, when they appear as words in a human-readable
# target label ("Desktop MFA for Windows", "Okta Verify (Windows)"), identify a
# downloadable executable. Word-bounded so "macys.com" or a "/windows-update"
# URL path don't trip it — URLs are excluded from this scan separately.
_DESKTOP_RE = re.compile(
    r"\b(?:windows|win32|win64|macos|mac\s?os|osx|desktop|executables?|"
    r"binary|electron)\b", re.IGNORECASE)

# Downloadable-binary file extensions: a strong, category-independent signal.
_EXE_EXTS = (".exe", ".dmg", ".pkg", ".msi", ".appx")

# Category tokens meaning "web / network / hardware infrastructure". When a
# platform has already bucketed a target this way we trust it and never apply
# the desktop label heuristic (OS names in URL paths would otherwise misfire).
_WEB_CATEGORY_TOKENS = ("website", "url", "api", "network", "ip", "cidr",
                        "domain", "web", "wildcard", "hardware", "iot")


def _is_generic_category(c):
    """True when the category is empty/unknown (e.g. Bugcrowd's catch-all
    "other") rather than an explicit web/network/hardware bucket. Desktop
    label-matching only runs for generic categories."""
    return not any(tok in c for tok in _WEB_CATEGORY_TOKENS)


def map_mobile_asset(category, identifier):
    """Map a coarse platform category + identifier to an H1 asset_type, or None.

    `category`: lowercase hint, e.g. 'android', 'ios', 'testflight',
    'executable', 'other'. `identifier`: the raw target (URL, package id, or a
    human label). Several platforms — Bugcrowd especially — file desktop
    executables under a generic "other" category with the OS named only in the
    label, so when the category is generic we also scan the label's words.
    """
    c = (category or "").lower()
    ident = (identifier or "").lower()

    if "testflight" in c or "testflight.apple.com" in ident:
        return "TESTFLIGHT"
    if "android" in c or "play.google.com" in ident or ident.endswith(".apk"):
        return "GOOGLE_PLAY_APP_ID" if "play.google.com" in ident else "OTHER_APK"
    if ("ios" in c or "iphone" in c or "ipad" in c or "apple" in c
            or "apps.apple.com" in ident or "itunes.apple.com" in ident):
        return "OTHER_IPA" if ident.endswith(".ipa") else "APPLE_STORE_APP_ID"

    # Microsoft Store listing (a store page, not a downloadable binary).
    if ("microsoft" in c or "windows store" in c
            or "apps.microsoft.com" in ident or "microsoft store" in ident):
        return "WINDOWS_APP_STORE_APP_ID"

    # A downloadable binary file — strong signal, independent of category.
    if ident.endswith(_EXE_EXTS):
        return "DOWNLOADABLE_EXECUTABLES"

    # Desktop/OS named in the category hint or a human-readable label. The
    # identifier is only scanned for words when it's a label (contains
    # whitespace) — never a bare URL/host/package id — and only for generic
    # categories, so a "/windows-update" path or "macys.com" can't misfire.
    scan = c + " " + ident if " " in ident else c
    if _is_generic_category(c) and _DESKTOP_RE.search(scan):
        return "DOWNLOADABLE_EXECUTABLES"

    return None
