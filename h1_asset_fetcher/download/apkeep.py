#!/usr/bin/env python3
"""
APK Bulk Downloader
Downloads APKs using apkeep from packages_list.txt output of h1-asset-fetcher.py.
Reports which packages failed to download.

Usage:
  python3 -m h1_asset_fetcher.download.apkeep
  python3 -m h1_asset_fetcher.download.apkeep -i packages_list.txt -o apks/
  python3 -m h1_asset_fetcher.download.apkeep -i packages_list.json -o apks/ -w 4
  python3 -m h1_asset_fetcher.download.apkeep -i packages_list.txt -o apks/ --source apk-pure
"""

import sys, json, os, argparse, subprocess, signal, time, threading, getpass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

signal.signal(signal.SIGINT, lambda *_: (print("\n\033[91m[!] Interrupted\033[0m"), os._exit(1)))

APKEEP_BIN = os.environ.get("APKEEP_BIN", "apkeep")
_print_lock = threading.Lock()

def log(msg, level="INFO"):
    colors = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m", "ERR": "\033[91m", "STEP": "\033[96m"}
    with _print_lock:
        print(f"{colors.get(level, '')}[{level}]\033[0m {msg}")

def find_apkeep():
    for path in [APKEEP_BIN, os.path.expanduser("~/.local/bin/apkeep"), "/usr/local/bin/apkeep"]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    # Try PATH
    try:
        subprocess.run(["apkeep", "--version"], capture_output=True, check=True)
        return "apkeep"
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return None

def load_packages(input_file):
    path = Path(input_file)
    if not path.exists():
        log(f"File not found: {input_file}", "ERR")
        sys.exit(1)

    if path.suffix == ".json":
        data = json.loads(path.read_text())
        packages = []
        for entry in data:
            pkg = entry.get("package", "").strip()
            if pkg:
                packages.append({
                    "package": pkg,
                    "program": entry.get("program", "unknown"),
                    "handle": entry.get("handle", ""),
                })
        return packages
    else:
        # Plain text, one package per line
        packages = []
        for line in path.read_text().splitlines():
            pkg = line.strip()
            if pkg and not pkg.startswith("#"):
                packages.append({"package": pkg, "program": "unknown", "handle": ""})
        return packages

# Sources apkeep can use WITHOUT credentials. `--source all` tries these in
# order until one yields an APK. (apkeep also supports google-play, but that
# needs -e <email> -t <aas token>, so it's excluded from "all".)
NO_AUTH_SOURCES = ["apk-pure", "huawei-app-gallery", "f-droid"]


def _downloaded_files(pkg_dir):
    return (list(pkg_dir.glob("*.apk")) + list(pkg_dir.glob("*.xapk"))
            + list(pkg_dir.glob("*.apkm")))


# Step-by-step instructions shown when we offer to finish failed downloads via
# Google Play (which, unlike the no-auth sources, needs an account + AAS token).
GPLAY_TOKEN_HELP = """\
  How to get a Google Play AAS token (one-time setup):

    1. Open a browser (incognito recommended) and visit:
         https://accounts.google.com/EmbeddedSetup
    2. Sign in with the Google account you want to download as.
    3. You'll reach a "Google Terms of Service / I Agree" consent screen.
       Open DevTools (F12) -> Application/Storage -> Cookies ->
       https://accounts.google.com and find the "oauth_token" cookie
       (value starts with "oauth2_4/"). If it isn't there yet, click
       "I Agree" — it shows up at that consent step.
    4. Copy that "oauth_token" value (oauth2_4/…). It is single-use and
       expires within minutes, so use it right away in the next step.
    5. Exchange it ONCE for a long-lived AAS token (downloads use the AAS
       token only):
         apkeep -e you@gmail.com --oauth-token 'oauth2_4/<paste>' .
       apkeep prints an "aas_et/…" token — that is your AAS token.
    6. Paste your email + the "aas_et/…" AAS token below. It is reusable, so
       save it: set APKEEP_GPLAY_EMAIL / APKEEP_GPLAY_TOKEN to skip this
       prompt next time. (Do NOT paste the oauth2_4/… value here — that's
        the OAuth token and will fail with "Invalid payload".)

  Details: https://github.com/EFForg/apkeep/blob/master/USAGE.md
"""


def build_apkeep_cmd(apkeep_bin, pkg_name, pkg_dir, source, sleep_ms,
                     gplay_email=None, gplay_token=None):
    """Assemble the apkeep argv for ONE package from ONE source. Google Play
    needs an account email, an AAS token, and TOS acceptance; the no-auth
    sources need none of that."""
    cmd = [apkeep_bin, "-a", pkg_name, "-d", source]
    if source == "google-play":
        if gplay_email:
            cmd += ["-e", gplay_email]
        if gplay_token:
            # Downloads always use the long-lived AAS token via -t (per apkeep's
            # USAGE-google-play). --oauth-token is deliberately NOT used here: the
            # OAuth token is single-use, and we spawn one apkeep process per
            # package, so it could only ever work for the first one. The OAuth ->
            # AAS exchange is a separate one-time step the user does up front.
            cmd += ["-t", gplay_token]
        cmd.append("--accept-tos")
    if sleep_ms > 0:
        cmd += ["-s", str(sleep_ms)]
    cmd.append(str(pkg_dir))
    return cmd


def resolve_gplay_creds(email_arg="", token_arg="", env=None):
    """Google Play (email, AAS token). Precedence: --gplay-* flags >
    APKEEP_GPLAY_EMAIL / APKEEP_GPLAY_TOKEN env vars. Returns ("", "") if
    neither is set."""
    env = os.environ if env is None else env
    email = (email_arg or env.get("APKEEP_GPLAY_EMAIL", "")).strip()
    token = (token_arg or env.get("APKEEP_GPLAY_TOKEN", "")).strip()
    return email, token


def is_oauth_token(token):
    """True if `token` is a raw Google OAuth token (the oauth2_4/… cookie value)
    rather than an exchanged AAS token. We download with the AAS token only, so
    an OAuth token must be converted first (see GPLAY_OAUTH_HINT)."""
    return bool(token) and token.startswith("oauth2_4/")


def _gplay_oauth_hint(email="you@gmail.com"):
    return (
        "  That looks like an OAuth token (oauth2_4/…), not an AAS token. It's\n"
        "  single-use, so it can't drive a bulk download. Exchange it once for a\n"
        "  reusable AAS token, then use that:\n"
        f"      apkeep -e {email or 'you@gmail.com'} --oauth-token 'oauth2_4/…' .\n"
        "  apkeep prints an 'aas_et/…' token — paste THAT (or set it in\n"
        "  APKEEP_GPLAY_TOKEN).")


def offer_gplay_retry(num_failed, *, input_fn=input, getpass_fn=getpass.getpass,
                      out=print):
    """Interactively ask whether to retry the failed packages via Google Play,
    and if so collect the email + AAS token. Returns (email, token) or None."""
    try:
        ans = input_fn(
            f"\n\033[96m[?]\033[0m Retry {num_failed} failed package(s) via "
            "Google Play? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            return None
        out(GPLAY_TOKEN_HELP)
        email = input_fn("  Google account email: ").strip()
        token = getpass_fn("  AAS token (aas_et/…, NOT the oauth2_4/… value): ").strip()
    except (EOFError, KeyboardInterrupt):
        # Ctrl-D / Ctrl-C / closed stdin: skip cleanly rather than crash.
        out("\n  Input cancelled — skipping Google Play retry.")
        return None
    if not email or not token:
        out("  Email and AAS token are both required — skipping Google Play retry.")
        return None
    if is_oauth_token(token):
        out(_gplay_oauth_hint(email))
        out("  Skipping Google Play retry — re-run with the AAS token.")
        return None
    return email, token


def write_failed_report(outdir, failed):
    """Persist the failed-package list as failed_packages.txt / .json.
    Returns (txt_path, json_path)."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    txt = outdir / "failed_packages.txt"
    js = outdir / "failed_packages.json"
    txt.write_text("".join(f"{f['package']}\n" for f in failed))
    js.write_text(json.dumps(failed, indent=2))
    return txt, js


def clear_failed_report(outdir):
    """Remove a stale failed-package report (e.g. after a successful retry)."""
    for name in ("failed_packages.txt", "failed_packages.json"):
        p = Path(outdir) / name
        if p.exists():
            p.unlink()


def _try_source(apkeep_bin, pkg_name, pkg_dir, source, sleep_ms,
                gplay_email=None, gplay_token=None):
    """Run apkeep for ONE source. Returns (ok: bool, reason: str)."""
    cmd = build_apkeep_cmd(apkeep_bin, pkg_name, pkg_dir, source, sleep_ms,
                           gplay_email, gplay_token)
    try:
        # stdin=DEVNULL: if creds are missing, apkeep would otherwise block on
        # its own interactive "Email/AAS Token" prompt — give it EOF so it
        # fails fast instead of hanging the worker.
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                stdin=subprocess.DEVNULL)
        if _downloaded_files(pkg_dir) and result.returncode == 0:
            return True, ""
        reason = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
        return False, reason[:140]
    except subprocess.TimeoutExpired:
        return False, "timeout (120s)"
    except Exception as e:
        return False, str(e)[:140]


def download_apk(apkeep_bin, package, outdir, sources=("apk-pure",), sleep_ms=0,
                 gplay_email=None, gplay_token=None):
    pkg_name = package["package"]
    pkg_dir = Path(outdir) / pkg_name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # Try each source in turn; first one to produce an APK wins.
    reasons = []
    for source in sources:
        ok, reason = _try_source(apkeep_bin, pkg_name, pkg_dir, source, sleep_ms,
                                 gplay_email, gplay_token)
        if ok:
            sizes = ", ".join(f"{f.name} ({f.stat().st_size / 1024 / 1024:.1f}MB)"
                              for f in _downloaded_files(pkg_dir))
            return {"package": pkg_name, "program": package["program"],
                    "success": True, "files": sizes, "source": source}
        reasons.append(f"{source}: {reason}")

    # All sources missed — clean up the empty dir.
    if pkg_dir.exists() and not any(pkg_dir.iterdir()):
        pkg_dir.rmdir()
    return {"package": pkg_name, "program": package["program"],
            "success": False, "reason": " | ".join(reasons)}


def run_downloads(apkeep_bin, to_download, outdir, sources, sleep_ms, workers,
                  gplay_email=None, gplay_token=None):
    """Download a batch of packages concurrently. Returns (succeeded, failed)."""
    succeeded, failed = [], []
    total = len(to_download)
    multi = len(sources) > 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_apk, apkeep_bin, p, outdir, sources,
                               sleep_ms, gplay_email, gplay_token): p
                   for p in to_download}
        for i, f in enumerate(as_completed(futures), 1):
            result = f.result()
            if result["success"]:
                succeeded.append(result)
                via = f" (via {result['source']})" if multi else ""
                log(f"  [{i}/{total}] \033[92m✓\033[0m {result['package']} -> {result['files']}{via}", "OK")
            else:
                failed.append(result)
                log(f"  [{i}/{total}] \033[91m✗\033[0m {result['package']} -> {result['reason']}", "ERR")
    return succeeded, failed


def print_summary(title, total, succeeded, already, failed, elapsed):
    print(f"\n{'='*70}")
    log(title, "STEP")
    print(f"{'='*70}")
    log(f"Total:       {total}", "INFO")
    log(f"Downloaded:  {len(succeeded)}", "OK")
    log(f"Skipped:     {len(already)} (already exist)", "WARN")
    log(f"Failed:      {len(failed)}", "ERR" if failed else "OK")
    log(f"Time:        {elapsed:.0f}s", "INFO")
    print(f"{'='*70}")


def print_failed(failed, outdir):
    """Print the failed-package detail block and persist the report."""
    print(f"\n{'='*70}")
    log("FAILED PACKAGES", "ERR")
    print(f"{'='*70}")
    for i, f in enumerate(failed, 1):
        log(f"  {i:>3}. {f['package']:<50} ({f['program'][:25]})", "ERR")
        log(f"       Reason: {f['reason']}", "WARN")
    txt, js = write_failed_report(outdir, failed)
    print(f"{'='*70}")
    log(f"Failed list saved to: {txt}", "WARN")
    log(f"Failed details saved to: {js}", "WARN")
    print(f"{'='*70}")

def main():
    parser = argparse.ArgumentParser(description="APK Bulk Downloader")
    parser.add_argument("-i", "--input", default="packages_list.txt",
                        help="Input file: packages_list.txt or packages_list.json (default: packages_list.txt)")
    parser.add_argument("-o", "--outdir", default="apks",
                        help="Output directory for downloaded APKs (default: apks/)")
    parser.add_argument("-w", "--workers", type=int, default=4,
                        help="Parallel downloads (default: 4)")
    parser.add_argument("-s", "--source",
                        choices=["all", "apk-pure", "google-play", "f-droid", "huawei-app-gallery"],
                        default="apk-pure",
                        help="Download source (default: apk-pure). 'all' tries "
                             "apk-pure, huawei-app-gallery, f-droid in turn.")
    parser.add_argument("--sleep", type=int, default=200,
                        help="Sleep between requests in ms to avoid rate limiting (default: 200)")
    parser.add_argument("--gplay-email", default="",
                        help="Google account email for Google Play downloads "
                             "(or APKEEP_GPLAY_EMAIL). With --gplay-token, "
                             "auto-retries failed downloads via google-play.")
    parser.add_argument("--gplay-token", default="",
                        help="Google Play AAS token (or APKEEP_GPLAY_TOKEN). "
                             "See the on-screen help for how to obtain one.")
    parser.add_argument("--no-gplay-retry", action="store_true",
                        help="Don't prompt to retry failed downloads via Google Play")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only list packages, don't download")
    args = parser.parse_args()
    sources = NO_AUTH_SOURCES if args.source == "all" else [args.source]

    print("")
    print("  \033[96m╔════════════════════════════════════════════════════════════╗\033[0m")
    print("  \033[96m║\033[93m         APK Bulk Downloader               \033[96m║\033[0m")
    print("  \033[96m║\033[0m  Downloads APKs via apkeep from h1-asset-fetcher output      \033[96m║\033[0m")
    print("  \033[96m╚════════════════════════════════════════════════════════════╝\033[0m")
    print("")

    # Find apkeep
    apkeep_bin = find_apkeep()
    if not apkeep_bin:
        log("apkeep not found. Install: https://github.com/EFForg/apkeep/releases", "ERR")
        log("Or set APKEEP_BIN=/path/to/apkeep", "ERR")
        sys.exit(1)
    log(f"Using apkeep: {apkeep_bin}", "OK")

    # Load packages
    packages = load_packages(args.input)
    if not packages:
        log("No packages found in input file", "ERR")
        sys.exit(1)

    # Skip already downloaded
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    already = []
    to_download = []
    for p in packages:
        pkg_dir = outdir / p["package"]
        existing = list(pkg_dir.glob("*.apk")) + list(pkg_dir.glob("*.xapk")) + list(pkg_dir.glob("*.apkm")) if pkg_dir.exists() else []
        if existing:
            already.append(p["package"])
        else:
            to_download.append(p)

    log(f"Loaded {len(packages)} packages from {args.input}", "INFO")
    log(f"Source: {' → '.join(sources)} | Workers: {args.workers} | Sleep: {args.sleep}ms", "INFO")
    if already:
        log(f"Skipping {len(already)} already downloaded", "WARN")
    log(f"Downloading {len(to_download)} packages -> {args.outdir}/", "STEP")

    if args.dry_run:
        for i, p in enumerate(to_download, 1):
            log(f"  {i:>3}. {p['package']:<50} ({p['program'][:30]})", "INFO")
        log(f"Dry run complete. {len(to_download)} packages would be downloaded.", "OK")
        return

    # Google Play creds (flags > env). Forwarded to the primary pass too, so a
    # direct `--source google-play` works, and reused for the retry offer.
    gplay = resolve_gplay_creds(args.gplay_email, args.gplay_token)
    if "google-play" in sources and not (gplay[0] and gplay[1]):
        log("google-play needs --gplay-email/--gplay-token (or APKEEP_GPLAY_* "
            "env vars) — those downloads will fail without them.", "WARN")
    elif "google-play" in sources and is_oauth_token(gplay[1]):
        log("google-play token is an OAuth token, not an AAS token — refusing "
            "(every download would fail with 'Invalid payload').", "ERR")
        print(_gplay_oauth_hint(gplay[0]))
        sys.exit(1)

    # Download
    start = time.time()
    succeeded, failed = run_downloads(apkeep_bin, to_download, args.outdir,
                                      sources, args.sleep, args.workers,
                                      gplay_email=gplay[0], gplay_token=gplay[1])
    elapsed = time.time() - start
    print_summary("DOWNLOAD SUMMARY", len(packages), succeeded, already, failed, elapsed)

    # Persist the failed list (the report exists before we prompt), then offer to
    # finish them off via Google Play — the one source that needs credentials, so
    # it isn't part of the first pass. The failed block is printed once, at the
    # end, reflecting the final state after any retry.
    if failed:
        write_failed_report(outdir, failed)
        if "google-play" not in sources and not args.no_gplay_retry:
            failed = _gplay_retry(apkeep_bin, failed, args, gplay, succeeded)
        if failed:
            print_failed(failed, outdir)
        else:
            clear_failed_report(outdir)
            log("All previously-failed packages recovered via Google Play.", "OK")
    else:
        # A clean run supersedes any stale report from a previous run.
        clear_failed_report(outdir)

    if succeeded:
        log(f"\nAPKs saved to: {args.outdir}/", "OK")
    log("Done!", "OK")


def _gplay_retry(apkeep_bin, failed, args, gplay, succeeded):
    """Auto-run (creds pre-supplied) or interactively offer a Google Play retry
    of the failed packages. Extends `succeeded` in place with any recoveries and
    returns the still-failing list (the caller prints/persists the final state)."""
    email, token = gplay
    if email and token and is_oauth_token(token):
        log("Google Play token looks like an OAuth token, not an AAS token:", "ERR")
        print(_gplay_oauth_hint(email))
        return failed
    if email and token:
        log("Google Play credentials supplied — retrying failed packages "
            "via google-play...", "STEP")
        creds = (email, token)
    elif sys.stdin.isatty():
        creds = offer_gplay_retry(len(failed))
    else:
        log("Tip: re-run with --gplay-email/--gplay-token (or set "
            "APKEEP_GPLAY_EMAIL/APKEEP_GPLAY_TOKEN) to retry via Google Play.",
            "INFO")
        creds = None

    if not creds:
        return failed

    retry_pkgs = [{"package": f["package"], "program": f["program"]} for f in failed]
    log(f"Retrying {len(retry_pkgs)} package(s) via google-play "
        f"(workers: {args.workers})...", "STEP")
    start = time.time()
    recovered, still_failed = run_downloads(
        apkeep_bin, retry_pkgs, args.outdir, ["google-play"], args.sleep,
        args.workers, gplay_email=creds[0], gplay_token=creds[1])
    elapsed = time.time() - start
    succeeded.extend(recovered)

    print_summary("GOOGLE PLAY RETRY SUMMARY", len(retry_pkgs), recovered, [],
                  still_failed, elapsed)
    return still_failed

if __name__ == "__main__":
    main()
