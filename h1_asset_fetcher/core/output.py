"""Write the organized output files (packages.txt/.tsv/.json, store_links, OOS,
summary, programs cache) from the collected assets."""
import json
import time
from pathlib import Path

from .identifiers import ASSET_CATEGORY, COLUMN_FIELDS, store_url


def json_entry(a, in_scope=True):
    """One packages.json record for an asset, with per-asset eligibility."""
    return {
        "package": a["package"],
        "program": a["program"],
        "handle": a["handle"],
        "asset_type": a["asset_type"],
        "category": ASSET_CATEGORY.get(a["asset_type"], "other"),
        "store_url": store_url(a),
        "in_scope": in_scope,
        "eligible_for_submission": a.get("eligible_for_submission", True),
        "eligible_for_bounty": a.get("eligible_for_bounty"),
        "max_severity": a.get("max_severity"),
    }


def save_output(args, valid_packages, programs, prog_info, seen_handles, unique, oos_packages=None):
    """Save all output files to organized directory."""
    oos_packages = oos_packages or []
    # Create output directory: <output>/<scope>/
    outdir = Path(args.output) / args.scope
    outdir.mkdir(parents=True, exist_ok=True)

    links = [store_url(a) for a in valid_packages]
    pkg_names = [a["package"] for a in valid_packages]

    # Save files (bare packages.txt is kept for the downloader scripts)
    (outdir / "store_links.txt").write_text("\n".join(links) + "\n")
    (outdir / "packages.txt").write_text("\n".join(pkg_names) + "\n")

    # Gap #3: annotated, composable TSV (target + category + program + url, etc.)
    cols = [c.strip() for c in args.columns.split(",") if c.strip()]
    delim = args.delimiter.replace("\\t", "\t").replace("\\n", "\n")

    def render(a):
        return delim.join(str(COLUMN_FIELDS[c](a)) for c in cols if c in COLUMN_FIELDS)

    (outdir / "packages.tsv").write_text("\n".join(render(a) for a in valid_packages) + "\n")

    # packages.json carries full per-asset detail incl. eligibility + OOS entries
    (outdir / "packages.json").write_text(json.dumps(
        [json_entry(a, in_scope=True) for a in valid_packages]
        + [json_entry(a, in_scope=False) for a in oos_packages],
        indent=2) + "\n")

    # Gap #2: out-of-scope assets in their own files (informational; not downloaded)
    if oos_packages:
        (outdir / "oos_packages.txt").write_text(
            "\n".join(a["package"] for a in oos_packages) + "\n")
        (outdir / "oos_store_links.txt").write_text(
            "\n".join(store_url(a) for a in oos_packages) + "\n")

    # Programs cache (in root output dir)
    cache_path = Path(args.output) / "programs_cache.json"
    cache_path.write_text(json.dumps(programs, indent=2) + "\n")

    # Summary JSON
    files = {
        "packages": str(outdir / "packages.txt"),
        "packages_tsv": str(outdir / "packages.tsv"),
        "packages_json": str(outdir / "packages.json"),
        "store_links": str(outdir / "store_links.txt"),
        "programs_cache": str(cache_path),
    }
    if oos_packages:
        files["oos_packages"] = str(outdir / "oos_packages.txt")
        files["oos_store_links"] = str(outdir / "oos_store_links.txt")

    summary = {
        "platform": args.platform,
        "scope": args.scope,
        "filter": args.filter,
        "bounty_only": args.bounty_only,
        "total_programs": len(seen_handles),
        "total_assets": len(valid_packages),
        "out_of_scope_assets": len(oos_packages),
        "asset_types": list(set(a["asset_type"] for a in valid_packages)),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "files": files,
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    return outdir, links
