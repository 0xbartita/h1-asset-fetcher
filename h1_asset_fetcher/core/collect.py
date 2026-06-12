"""Turn fetched programs into deduplicated, validated assets — the shared step
between the CLI and the TUI. Applies per-asset eligibility gating (gap #1) and
splits out out-of-scope assets (gap #2)."""
from .identifiers import extract_identifier, is_valid_pkg


def _dedup(items):
    seen = set()
    out = []
    for a in items:
        pkg = extract_identifier(a["identifier"], asset_type=a["asset_type"])
        if not pkg or pkg in seen:
            continue
        seen.add(pkg)
        a["package"] = pkg
        out.append(a)
    return out


def is_valid_asset(a):
    pkg = a.get("package", "")
    if not pkg:
        return False
    # Numeric IDs are valid for iOS (App Store ID)
    if pkg.isdigit() and a.get("asset_type") in ("APPLE_STORE_APP_ID", "OTHER_IPA"):
        return True
    # URLs are valid for TestFlight
    if pkg.startswith("http") and a.get("asset_type") == "TESTFLIGHT":
        return True
    # Executables don't need to be valid package names
    if a.get("asset_type") in ("DOWNLOADABLE_EXECUTABLES", "WINDOWS_APP_STORE_APP_ID"):
        return True
    return is_valid_pkg(pkg)


def collect_assets(programs, oos=False, bounty_only=False):
    """Collect, gate, and dedup assets from fetched programs.

    Returns a dict: prog_info, unique, valid_packages, valid_oos,
    dropped_oos, dropped_nonbounty.
    """
    prog_info = {p["handle"]: p for p in programs}
    assets = []
    oos_assets = []
    dropped_oos = 0
    dropped_nonbounty = 0
    for prog in programs:
        for scope in prog.get("scopes", []):
            record = {
                "program": prog["name"],
                "handle": prog["handle"],
                "asset_type": scope["asset_type"],
                "identifier": scope["asset_identifier"],
                "eligible_for_submission": scope.get("eligible_for_submission", True),
                "eligible_for_bounty": scope.get("eligible_for_bounty"),
                "max_severity": scope.get("max_severity"),
            }
            # Gap #1: per-asset scope gating.
            if not record["eligible_for_submission"]:
                dropped_oos += 1
                oos_assets.append(record)
                continue
            if bounty_only and record["eligible_for_bounty"] is False:
                dropped_nonbounty += 1
                continue
            assets.append(record)

    unique = _dedup(assets)
    oos_unique = _dedup(oos_assets) if oos else []
    return {
        "prog_info": prog_info,
        "unique": unique,
        "valid_packages": [a for a in unique if is_valid_asset(a)],
        "valid_oos": [a for a in oos_unique if is_valid_asset(a)],
        "dropped_oos": dropped_oos,
        "dropped_nonbounty": dropped_nonbounty,
    }
