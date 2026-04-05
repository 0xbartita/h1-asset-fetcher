#!/usr/bin/env python3
"""
APK Web Downloader - by 0xbartita
Downloads APKs directly from APKPure/APKCombo/APKMirror web pages.
Fallback for packages that fail with apkeep.

Usage:
  python3 apk_web_downloader.py -i failed_packages.txt -o apks/
  python3 apk_web_downloader.py -i failed_packages.txt -o apks/ -w 2 --source apkcombo
"""

import sys, os, re, json, time, argparse, signal, threading, subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

signal.signal(signal.SIGINT, lambda *_: (print("\n\033[91m[!] Interrupted\033[0m"), os._exit(1)))

_print_lock = threading.Lock()
USER_AGENT = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"

def log(msg, level="INFO"):
    colors = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m", "ERR": "\033[91m", "STEP": "\033[96m"}
    with _print_lock:
        print(f"{colors.get(level, '')}[{level}]\033[0m {msg}")


def http_get(url, timeout=30, binary=False):
    """Simple HTTP GET with browser-like headers."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = Request(url, headers=headers)
    try:
        resp = urlopen(req, timeout=timeout)
        if binary:
            return resp.read(), resp.headers
        return resp.read().decode("utf-8", errors="ignore"), resp.headers
    except (URLError, HTTPError) as e:
        return None, None


def download_file(url, filepath, timeout=120):
    """Download a file with progress, using curl for reliability."""
    try:
        result = subprocess.run(
            ["curl", "-sL", "-o", str(filepath), "-A", USER_AGENT,
             "--connect-timeout", "15", "--max-time", str(timeout),
             "-w", "%{http_code}", url],
            capture_output=True, text=True, timeout=timeout + 10
        )
        http_code = result.stdout.strip()
        if filepath.exists() and filepath.stat().st_size > 10000 and http_code.startswith("2"):
            return True
        filepath.unlink(missing_ok=True)
        return False
    except Exception:
        filepath.unlink(missing_ok=True)
        return False


# ── APKPure ──────────────────────────────────────────────────

def try_apkpure(pkg_name, outdir):
    """Download APK from apkpure.com."""
    pkg_dir = Path(outdir) / pkg_name

    # Step 1: Find the app page
    search_url = f"https://apkpure.com/search?q={pkg_name}"
    html, _ = http_get(search_url)
    if not html:
        return None, "apkpure: search failed"

    # Find the app link
    # Pattern: /app-name/package.name
    pattern = rf'href="(/[^"]+/{re.escape(pkg_name)})"'
    match = re.search(pattern, html)
    if not match:
        # Try alternate pattern
        pattern = rf'href="(/[^"]+/{re.escape(pkg_name)}/download)"'
        match = re.search(pattern, html)

    if not match:
        return None, "apkpure: app not found"

    app_path = match.group(1)
    if not app_path.endswith("/download"):
        download_url = f"https://apkpure.com{app_path}/download"
    else:
        download_url = f"https://apkpure.com{app_path}"

    # Step 2: Get the download page
    html, _ = http_get(download_url)
    if not html:
        return None, "apkpure: download page failed"

    # Find the actual APK download link
    # Look for direct download links
    apk_patterns = [
        r'href="(https://download\.apkpure\.com/[^"]+\.apk[^"]*)"',
        r'href="(https://[^"]*apkpure[^"]*\.apk[^"]*)"',
        r'"download_link"\s*:\s*"([^"]+)"',
        r'id="download_link"[^>]*href="([^"]+)"',
        r'data-dt-url="([^"]+\.apk[^"]*)"',
    ]

    apk_url = None
    for pat in apk_patterns:
        m = re.search(pat, html)
        if m:
            apk_url = m.group(1)
            break

    if not apk_url:
        return None, "apkpure: no download link found"

    # Step 3: Download
    pkg_dir.mkdir(parents=True, exist_ok=True)
    filepath = pkg_dir / f"{pkg_name}.apk"
    if download_file(apk_url, filepath):
        size_mb = filepath.stat().st_size / 1024 / 1024
        return filepath, f"{size_mb:.1f}MB"

    filepath.unlink(missing_ok=True)
    if not any(pkg_dir.iterdir()):
        pkg_dir.rmdir()
    return None, "apkpure: download failed"


# ── APKCombo ─────────────────────────────────────────────────

def try_apkcombo(pkg_name, outdir):
    """Download APK from apkcombo.com."""
    pkg_dir = Path(outdir) / pkg_name

    # Step 1: Get app page
    app_url = f"https://apkcombo.com/apk/{pkg_name}/"
    html, _ = http_get(app_url)
    if not html:
        # Try search
        search_url = f"https://apkcombo.com/search/{pkg_name}/"
        html, _ = http_get(search_url)
        if not html:
            return None, "apkcombo: page failed"

        # Find app link in search results
        match = re.search(rf'href="(/[^"]+/{pkg_name}/)"', html)
        if not match:
            return None, "apkcombo: app not found"
        app_url = f"https://apkcombo.com{match.group(1)}"
        html, _ = http_get(app_url)
        if not html:
            return None, "apkcombo: app page failed"

    # Step 2: Find download link
    download_patterns = [
        r'href="(https://download\.apkcombo\.com/[^"]+\.apk[^"]*)"',
        r'href="(/download/[^"]+)"',
        r'"url"\s*:\s*"(https://[^"]*\.apk[^"]*)"',
    ]

    download_page_url = None
    apk_url = None
    for pat in download_patterns:
        m = re.search(pat, html)
        if m:
            url = m.group(1)
            if url.startswith("/"):
                url = f"https://apkcombo.com{url}"
            if ".apk" in url:
                apk_url = url
            else:
                download_page_url = url
            break

    # If we got a download page, fetch it for the actual link
    if not apk_url and download_page_url:
        html2, _ = http_get(download_page_url)
        if html2:
            for pat in download_patterns:
                m = re.search(pat, html2)
                if m:
                    url = m.group(1)
                    if url.startswith("/"):
                        url = f"https://apkcombo.com{url}"
                    apk_url = url
                    break

    if not apk_url:
        return None, "apkcombo: no download link found"

    # Step 3: Download
    pkg_dir.mkdir(parents=True, exist_ok=True)
    filepath = pkg_dir / f"{pkg_name}.apk"
    if download_file(apk_url, filepath):
        size_mb = filepath.stat().st_size / 1024 / 1024
        return filepath, f"{size_mb:.1f}MB"

    filepath.unlink(missing_ok=True)
    if not any(pkg_dir.iterdir()):
        pkg_dir.rmdir()
    return None, "apkcombo: download failed"


# ── APKMirror (search only, links to manual download) ────────

def try_apkmirror(pkg_name, outdir):
    """Try to download from APKMirror via their search."""
    pkg_dir = Path(outdir) / pkg_name

    search_url = f"https://www.apkmirror.com/?post_type=app_release&searchtype=apk&s={pkg_name}"
    html, _ = http_get(search_url)
    if not html:
        return None, "apkmirror: search failed"

    # Find app release page
    pattern = r'href="(/apk/[^"]+/)"[^>]*class="fontBlack"'
    match = re.search(pattern, html)
    if not match:
        # Broader pattern
        pattern = r'href="(/apk/[^"]*' + re.escape(pkg_name.split(".")[-1]) + r'[^"]*)"'
        match = re.search(pattern, html)

    if not match:
        return None, "apkmirror: app not found"

    app_page = f"https://www.apkmirror.com{match.group(1)}"
    html, _ = http_get(app_page)
    if not html:
        return None, "apkmirror: app page failed"

    # Find download variant page
    variant_pattern = r'href="(/apk/[^"]*download[^"]*)"'
    match = re.search(variant_pattern, html)
    if not match:
        # Look for any APK download link
        variant_pattern = r'href="(/apk/[^"]+\.apk[^"]*)"'
        match = re.search(variant_pattern, html)

    if not match:
        return None, "apkmirror: no download variant"

    download_page = f"https://www.apkmirror.com{match.group(1)}"
    html, _ = http_get(download_page)
    if not html:
        return None, "apkmirror: download page failed"

    # Find the actual APK file link
    file_patterns = [
        r'href="(https://[^"]*\.apkmirror\.com/wp-content/[^"]+\.apk[^"]*)"',
        r'href="(/wp-content/[^"]+\.apk[^"]*)"',
        r'id="download-link"[^>]*href="([^"]+)"',
    ]

    apk_url = None
    for pat in file_patterns:
        m = re.search(pat, html)
        if m:
            apk_url = m.group(1)
            if apk_url.startswith("/"):
                apk_url = f"https://www.apkmirror.com{apk_url}"
            break

    if not apk_url:
        return None, "apkmirror: no direct download link"

    pkg_dir.mkdir(parents=True, exist_ok=True)
    filepath = pkg_dir / f"{pkg_name}.apk"
    if download_file(apk_url, filepath, timeout=180):
        size_mb = filepath.stat().st_size / 1024 / 1024
        return filepath, f"{size_mb:.1f}MB"

    filepath.unlink(missing_ok=True)
    if not any(pkg_dir.iterdir()):
        pkg_dir.rmdir()
    return None, "apkmirror: download failed"


# ── Main download with fallback chain ────────────────────────

SOURCES = {
    "apkpure": try_apkpure,
    "apkcombo": try_apkcombo,
    "apkmirror": try_apkmirror,
}

# Default fallback order
DEFAULT_CHAIN = ["apkcombo", "apkpure", "apkmirror"]


def download_package(pkg_name, outdir, sources=None, sleep_sec=1):
    """Try downloading from multiple sources in order."""
    if sources is None:
        sources = DEFAULT_CHAIN

    # Check if already downloaded
    pkg_dir = Path(outdir) / pkg_name
    if pkg_dir.exists():
        existing = list(pkg_dir.glob("*.apk")) + list(pkg_dir.glob("*.xapk")) + list(pkg_dir.glob("*.apkm"))
        if existing:
            return {"package": pkg_name, "success": True, "skipped": True,
                    "files": ", ".join(f.name for f in existing)}

    errors = []
    for source_name in sources:
        func = SOURCES.get(source_name)
        if not func:
            continue

        try:
            filepath, info = func(pkg_name, outdir)
            if filepath:
                return {"package": pkg_name, "success": True, "source": source_name,
                        "files": info}
            errors.append(info)
        except Exception as e:
            errors.append(f"{source_name}: {str(e)[:80]}")

        if sleep_sec > 0:
            time.sleep(sleep_sec)

    return {"package": pkg_name, "success": False, "reason": " | ".join(errors)}


def main():
    parser = argparse.ArgumentParser(description="APK Web Downloader - Fallback for failed apkeep downloads")
    parser.add_argument("-i", "--input", required=True,
                        help="Input file with package names (one per line)")
    parser.add_argument("-o", "--outdir", default="apks",
                        help="Output directory (default: apks/)")
    parser.add_argument("-w", "--workers", type=int, default=2,
                        help="Parallel downloads (default: 2, keep low to avoid rate limiting)")
    parser.add_argument("--source", choices=list(SOURCES.keys()) + ["all"], default="all",
                        help="Download source: apkpure, apkcombo, apkmirror, all (default: all = try all)")
    parser.add_argument("--sleep", type=float, default=2,
                        help="Sleep between source attempts in seconds (default: 2)")
    parser.add_argument("--start", type=int, default=0, help="Start from Nth package")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N packages")
    args = parser.parse_args()

    print("")
    print("  \033[96m╔════════════════════════════════════════════════════════════╗\033[0m")
    print("  \033[96m║\033[93m       APK Web Downloader  |  by 0xbartita                 \033[96m║\033[0m")
    print("  \033[96m║\033[0m  Downloads from APKCombo / APKPure / APKMirror             \033[96m║\033[0m")
    print("  \033[96m╚════════════════════════════════════════════════════════════╝\033[0m")
    print("")

    # Load packages
    packages = []
    for line in open(args.input):
        pkg = line.strip()
        if pkg and not pkg.startswith("#"):
            packages.append(pkg)

    if args.start > 0:
        packages = packages[args.start:]
    if args.limit > 0:
        packages = packages[:args.limit]

    # Determine sources
    if args.source == "all":
        sources = DEFAULT_CHAIN
    else:
        sources = [args.source]

    # Skip already downloaded
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    to_download = []
    already = 0
    for pkg in packages:
        pkg_dir = outdir / pkg
        existing = (list(pkg_dir.glob("*.apk")) + list(pkg_dir.glob("*.xapk")) +
                    list(pkg_dir.glob("*.apkm"))) if pkg_dir.exists() else []
        if existing:
            already += 1
        else:
            to_download.append(pkg)

    log(f"Total: {len(packages)} | Already downloaded: {already} | To download: {len(to_download)}", "STEP")
    log(f"Sources: {' -> '.join(sources)} | Workers: {args.workers} | Sleep: {args.sleep}s", "INFO")

    if not to_download:
        log("Nothing to download!", "OK")
        return

    # Download
    succeeded = []
    failed = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_package, pkg, args.outdir, sources, args.sleep): pkg
                   for pkg in to_download}

        for i, f in enumerate(as_completed(futures), 1):
            result = f.result()
            total = len(to_download)

            if result["success"]:
                if result.get("skipped"):
                    log(f"[{i}/{total}] \033[90mSKIP\033[0m {result['package']} (already exists)", "INFO")
                else:
                    succeeded.append(result)
                    log(f"[{i}/{total}] \033[92m✓\033[0m {result['package']} <- {result.get('source','')} ({result.get('files','')})", "OK")
            else:
                failed.append(result)
                log(f"[{i}/{total}] \033[91m✗\033[0m {result['package']} -> {result.get('reason','?')[:100]}", "ERR")

    elapsed = time.time() - start

    # Summary
    print(f"\n{'='*70}")
    log("DOWNLOAD SUMMARY", "STEP")
    print(f"{'='*70}")
    log(f"Total:       {len(packages)}", "INFO")
    log(f"Downloaded:  {len(succeeded)}", "OK")
    log(f"Already had: {already}", "WARN")
    log(f"Failed:      {len(failed)}", "ERR" if failed else "OK")
    log(f"Time:        {elapsed:.0f}s", "INFO")
    print(f"{'='*70}")

    if failed:
        still_failed = outdir / "still_failed_packages.txt"
        still_failed_json = outdir / "still_failed_packages.json"
        with open(still_failed, "w") as fp:
            for f in failed:
                fp.write(f"{f['package']}\n")
        with open(still_failed_json, "w") as fp:
            json.dump(failed, fp, indent=2)
        log(f"Still failed: {still_failed}", "WARN")

    log("Done!", "OK")


if __name__ == "__main__":
    main()
