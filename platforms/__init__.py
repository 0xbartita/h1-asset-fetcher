"""Multi-platform scope fetchers for h1-asset-fetcher.

Each module in this package (bugcrowd, intigriti, yeswehack, immunefi) exposes
a single function:

    fetch(token=None, username=None, prog_filter="all",
          asset_types=(...), oos=False, log=print) -> list[program]

Return value: a list of program dicts in the SAME shape the HackerOne path
produces, so the rest of h1-asset-fetcher.py (extract_identifier, store_url,
save_output, dedup, the programs table) works unchanged:

    {
        "handle": str,              # unique program slug on the platform
        "name": str,                # human-readable program name
        "platform": str,            # e.g. "bugcrowd"
        "url": str | None,          # program page URL (optional)
        "submission_state": str | None,   # optional
        # HackerOne-specific flag columns (fast_payments, triage_active, ...)
        # are simply absent for other platforms; the table renders them as "-".
        "scopes": [
            {
                "asset_type": str,  # MUST be one of H1_ASSET_TYPES below
                "asset_identifier": str,
                "eligible_for_submission": bool,   # in-scope vs out-of-scope
                "eligible_for_bounty": bool | None,
            },
            ...
        ],
    }

Only mobile/executable assets relevant to this tool's download/decompile
pipeline are returned; web/api/other targets are dropped. Modules should map
their native categories to H1 asset_type names via map_mobile_asset().
"""

H1_ASSET_TYPES = (
    "GOOGLE_PLAY_APP_ID", "OTHER_APK",
    "APPLE_STORE_APP_ID", "TESTFLIGHT", "OTHER_IPA",
    "DOWNLOADABLE_EXECUTABLES", "WINDOWS_APP_STORE_APP_ID",
)


class PlatformAuthError(Exception):
    """Raised when a platform is missing credentials or authentication fails."""


def map_mobile_asset(category, identifier):
    """Map a coarse platform category + identifier to an H1 asset_type.

    category: a lowercase hint from the source platform, e.g. one of
      'android', 'ios', 'testflight', 'windows', 'exe'/'executable', 'mac'.
    identifier: the raw target string (URL or package/bundle id).

    Returns an H1 asset_type string, or None if it is not a mobile/exe asset.
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
    if "windows" in c or "microsoft" in c:
        return "WINDOWS_APP_STORE_APP_ID"
    if ("exe" in c or "executable" in c or "mac" in c or "desktop" in c
            or "binary" in c or ident.endswith((".exe", ".dmg", ".pkg", ".msi", ".appx"))):
        return "DOWNLOADABLE_EXECUTABLES"
    return None


def get_fetcher(name):
    """Import platforms.<name> and return its fetch() callable."""
    import importlib
    mod = importlib.import_module(f"{__name__}.{name}")
    if not hasattr(mod, "fetch"):
        raise AttributeError(f"platform module '{name}' has no fetch()")
    return mod.fetch
