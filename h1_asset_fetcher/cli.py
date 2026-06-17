#!/usr/bin/env python3
"""Command-line entry point. Headless when given flags; with no arguments it
launches the TUI (Phase 2). All platforms are dispatched through the registry."""
import sys
import io
import os
import json
import signal
import argparse
from pathlib import Path

from .core import log, config
from .core.identifiers import SCOPE_TYPES, SCOPE_LABELS, resolve_ios_store_links
from .core.collect import collect_assets
from .core.output import save_output
from .platforms import get_platform, all_platforms, PlatformAuthError

signal.signal(signal.SIGINT, lambda *_: (print("\n\033[91m[!] Interrupted\033[0m"), os._exit(1)))

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)


def _resolve_creds(plat, args):
    """Build {cred_key: value} for a platform. Precedence: -t/-u flags > env var
    > saved config (the wizard's remembered credentials). Keeps each platform's
    credentials isolated (no H1 token bleed)."""
    saved = config.get_platform_creds(plat.name)
    creds = {}
    for c in plat.auth:
        val = ""
        if c.key == "token":
            val = args.token
        elif c.key == "username":
            val = args.username
        if not val and c.key in plat.env:
            val = os.environ.get(plat.env[c.key], "")
        if not val:
            val = saved.get(c.key, "")
        creds[c.key] = val
    return creds


def _build_parser():
    platform_names = ["h1"] + [p.name for p in all_platforms()]
    parser = argparse.ArgumentParser(
        prog="h1-asset-fetcher",
        description="Fetch Android/iOS/Exe assets from bug bounty programs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -u user -t token --scope android                        # Android from private BBPs
  %(prog)s -u user -t token --scope all -f all                     # Everything from all programs
  %(prog)s --platform bugcrowd -t "$BUGCROWD_TOKEN" --scope android
  %(prog)s --programs-file output/programs_cache.json --scope ios  # offline re-filter

Filters (combine with comma): bbp,private (default) | bbp,public | vdp,private |
  vdp,public | bbp | vdp | private | public | all

Per-asset filtering / output:
  -b, --bounty-only   Keep only assets individually flagged eligible_for_bounty
  --oos               Also list out-of-scope assets in oos_packages.txt
  --columns t,c,h,u   Annotated packages.tsv columns (t=target a=asset_type
                      c=category h=handle p=program u=store_url)

Platforms (--platform):
  h1 (default)   HackerOne          -u user -t token   (H1_USERNAME / H1_API_TOKEN)
  bugcrowd       Bugcrowd           -t <_bugcrowd_session cookie>  (BUGCROWD_TOKEN)
  intigriti      Intigriti          -t <researcher API token>      (INTIGRITI_TOKEN)
  yeswehack      YesWeHack          -t <JWT> | -u email + YESWEHACK_PASSWORD  (YESWEHACK_TOKEN)
  immunefi       Immunefi           no auth (public; mostly web3, few mobile apps)

Get your H1 API token at: https://hackerone.com/settings/api_token/edit
        """)
    parser.add_argument("-u", "--username", default="",
                        help="Platform username/email (HackerOne: H1_USERNAME env var)")
    parser.add_argument("-t", "--token", default="",
                        help="Platform API token (HackerOne: H1_API_TOKEN env var)")
    parser.add_argument("-o", "--output", default="output",
                        help="Output directory (default: output/)")
    parser.add_argument("-f", "--filter", default="bbp,private",
                        help="Filter (comma-separated): -f bbp,private | -f vdp,public | -f all (default: bbp,private)")
    parser.add_argument("-s", "--scope", choices=["android", "ios", "exe", "all"],
                        required=True, help="Asset scope: android, ios, exe, all")
    parser.add_argument("--programs-file", default=None,
                        help="Reuse cached programs_cache.json instead of fetching")
    parser.add_argument("--platform", default="h1", choices=platform_names,
                        help="Bug bounty platform to fetch scope from (default: h1)")
    parser.add_argument("-b", "--bounty-only", action="store_true",
                        help="Keep only assets individually eligible for bounty "
                             "(per-asset eligible_for_bounty), not just paid programs")
    parser.add_argument("--oos", action="store_true",
                        help="Also collect out-of-scope assets (eligible_for_submission=false) "
                             "into oos_packages.txt / oos_store_links.txt")
    parser.add_argument("--columns", default="t,a,h,u",
                        help="Columns for packages.tsv: t=target a=asset_type c=category "
                             "h=handle p=program u=store_url (default: t,a,h,u)")
    parser.add_argument("--delimiter", default="\t",
                        help="Delimiter for packages.tsv columns (default: tab; use '\\t')")
    parser.add_argument("--forget", action="store_true",
                        help="Delete saved credentials/preferences "
                             "(~/.config/h1-asset-fetcher/config.json) and exit")
    return parser


def _run_cli(argv):
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --platform accepts 'h1' as an alias for 'hackerone'.
    if args.platform in ("h1", "hackerone"):
        args.platform = "hackerone"

    # Validate filter
    valid_parts = {"bbp", "vdp", "private", "public", "all"}
    filter_parts = [p.strip().lower() for p in args.filter.replace("-", ",").split(",")]
    for p in filter_parts:
        if p not in valid_parts:
            log(f"Invalid filter part: '{p}'", "ERR")
            log("  Valid: bbp, vdp, private, public, all", "ERR")
            log("  Combine with comma: -f bbp,private  or  -f vdp,public", "ERR")
            sys.exit(1)

    asset_types = SCOPE_TYPES[args.scope]

    print("")
    print("  \033[96m╔════════════════════════════════════════════════════════════╗\033[0m")
    print("  \033[96m║\033[93m         H1 Asset Fetcher                 \033[96m║\033[0m")
    print("  \033[96m╚════════════════════════════════════════════════════════════╝\033[0m")
    print("")

    log(f"Platform: {args.platform} | Scope: {SCOPE_LABELS[args.scope]}", "INFO")
    log(f"Filter: {args.filter} | Output: {args.output}/{args.scope}/", "INFO")
    if args.bounty_only:
        log("Bounty-only: keeping only per-asset bounty-eligible assets", "INFO")

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
        plat = get_platform(args.platform)
        creds = _resolve_creds(plat, args)
        try:
            programs = plat.fetch(creds, args.scope, args.filter, args.oos)
        except PlatformAuthError as e:
            log(str(e), "ERR")
            sys.exit(1)

    if not programs:
        log("No programs found.", "ERR")
        log("  - Check your credentials and filter (try --filter all)", "ERR")
        log("  - Make sure you have accepted program invitations on the platform", "ERR")
        sys.exit(1)

    # Step 2: Collect, gate, and deduplicate assets (shared with the TUI)
    res = collect_assets(programs, oos=args.oos, bounty_only=args.bounty_only)
    prog_info = res["prog_info"]
    unique = res["unique"]
    valid_packages = res["valid_packages"]
    valid_oos = res["valid_oos"]

    if res["dropped_oos"] or res["dropped_nonbounty"]:
        extra = (f", {res['dropped_nonbounty']} non-bounty" if args.bounty_only else "")
        tail = " (saved to oos_*; --oos)" if args.oos else " (use --oos to list them)"
        log(f"Per-asset filter: dropped {res['dropped_oos']} out-of-scope{extra}{tail}", "INFO")

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
        # .get(k, default) only falls back when the key is ABSENT; a platform may
        # set these to None explicitly (e.g. Intigriti submission_state), so coerce.
        name = (info.get("name") or a["program"] or "")[:35]
        n = len(info.get("scopes", []))
        st = info.get("submission_state") or "?"
        sc = "\033[92m" if st == "open" else "\033[91m"
        print(f"{len(seen_handles):<4} {name:<35} {flag(info.get('fast_payments')):<17} {flag(info.get('triage_active')):<17} {flag(info.get('gold_standard_safe_harbor')):<17} {flag(info.get('allows_bounty_splitting')):<16} {sc}{st:<12}\033[0m {n}")

    print(f"{'='*100}")

    # Step 4: valid_packages / valid_oos already computed by collect_assets()
    print(f"\n\033[92m[OK]\033[0m {len(valid_packages)} valid assets from {len(seen_handles)} programs"
          + (f" (+{len(valid_oos)} out-of-scope)" if valid_oos else "") + "\n")
    for i, a in enumerate(valid_packages, 1):
        asset_label = {"GOOGLE_PLAY_APP_ID": "PlayStore", "OTHER_APK": "APK",
                       "APPLE_STORE_APP_ID": "AppStore", "TESTFLIGHT": "TestFlight",
                       "OTHER_IPA": "IPA", "DOWNLOADABLE_EXECUTABLES": "EXE",
                       "WINDOWS_APP_STORE_APP_ID": "WinStore"}.get(a["asset_type"], a["asset_type"])
        display_pkg = f"id{a['package']}" if a["package"].isdigit() else a["package"]
        log(f"  {i:>3}. {display_pkg:<50} [{asset_label}] ({a['program'][:25]})", "INFO")

    # Step 5: Resolve iOS store links
    if args.scope in ("ios", "all"):
        resolve_ios_store_links(valid_packages)

    # Step 6: Save output
    outdir, links = save_output(args, valid_packages, programs, prog_info, seen_handles, unique, valid_oos)

    print(f"\n{'='*70}")
    log("OUTPUT", "STEP")
    print(f"{'='*70}")
    log(f"  {outdir}/packages.txt      — {len(valid_packages)} package identifiers", "OK")
    log(f"  {outdir}/packages.tsv      — Annotated columns ({args.columns})", "OK")
    log(f"  {outdir}/packages.json     — Full details incl. eligibility (JSON)", "OK")
    log(f"  {outdir}/store_links.txt   — {len(links)} store links", "OK")
    if args.oos and valid_oos:
        log(f"  {outdir}/oos_packages.txt  — {len(valid_oos)} out-of-scope identifiers", "OK")
    log(f"  {outdir}/summary.json      — Scan summary", "OK")
    log(f"  {args.output}/programs_cache.json — Cached programs (--programs-file)", "OK")
    print(f"{'='*70}")

    # Scope-specific tips
    if args.scope in ("android", "all"):
        log("\nNext: Download APKs — python3 -m h1_asset_fetcher.download.apkeep "
            f"-i {outdir}/packages.txt -o apks/", "INFO")
    if args.scope in ("ios", "all"):
        log("\nNext: Download IPAs with ipatool or from a jailbroken device", "INFO")
    if args.scope in ("exe", "all"):
        log("\nNext: Download executables from the program's scope page", "INFO")

    log("Done!", "OK")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--forget" in argv:
        path = config.forget()
        log(f"Forgot saved credentials and preferences ({path})", "OK")
        return 0
    if not argv:
        # No arguments → launch the TUI (Phase 2). Until it exists, show help.
        try:
            from .tui.app import run as run_tui
        except ImportError:
            print("TUI not available yet (coming in Phase 2). "
                  "Run with --help for the CLI options.", file=sys.stderr)
            argv = ["--help"]
        else:
            return run_tui()
    return _run_cli(argv)


if __name__ == "__main__":
    main()
