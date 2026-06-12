#!/usr/bin/env python3
"""
APK Browser Downloader
Uses headless Chromium (Playwright) to bypass Cloudflare and download APKs
from APKPure / APKCombo. No rate limits, no bot detection.

Usage:
  python3 -m h1_asset_fetcher.download.browser -i failed_packages.txt -o apks/
  python3 -m h1_asset_fetcher.download.browser -i failed_packages.txt -o apks/ --source apkpure
"""

import sys, os, re, json, time, argparse, signal
from pathlib import Path

signal.signal(signal.SIGINT, lambda *_: (print("\n\033[91m[!] Interrupted\033[0m"), os._exit(1)))

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("pip install playwright && playwright install chromium")
    sys.exit(1)


def log(msg, level="INFO"):
    colors = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m", "ERR": "\033[91m", "STEP": "\033[96m"}
    print(f"{colors.get(level, '')}[{level}]\033[0m {msg}", flush=True)


def download_from_apkpure(page, pkg_name, outdir):
    """Download APK from apkpure.com using real browser."""
    pkg_dir = Path(outdir) / pkg_name

    try:
        # Go to the app's download page directly
        url = f"https://apkpure.com/search?q={pkg_name}"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # Find the exact package link
        # Look for link containing the package name
        link = page.locator(f'a[href*="/{pkg_name}"]').first
        if link.count() == 0:
            return None, "apkpure: app not found in search"

        href = link.get_attribute("href")
        if not href:
            return None, "apkpure: no link found"

        # Navigate to app page
        if not href.startswith("http"):
            href = f"https://apkpure.com{href}"

        # Go to download page
        if "/download" not in href:
            href = href.rstrip("/") + "/download"

        page.goto(href, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # Look for download button and set up download listener
        download_selectors = [
            'a[href*=".apk"]',
            'a.download_apk_news',
            'a[id*="download"]',
            'a.da',
            'a.btn-download',
            'a[data-dt-url*=".apk"]',
            'a.is-download',
        ]

        for selector in download_selectors:
            el = page.locator(selector).first
            if el.count() > 0:
                dl_href = el.get_attribute("href") or ""
                if dl_href and (".apk" in dl_href or "download" in dl_href):
                    pkg_dir.mkdir(parents=True, exist_ok=True)
                    filepath = pkg_dir / f"{pkg_name}.apk"

                    # Use Playwright's download handling
                    try:
                        with page.expect_download(timeout=120000) as download_info:
                            el.click()
                        download = download_info.value
                        download.save_as(str(filepath))

                        if filepath.exists() and filepath.stat().st_size > 10000:
                            size_mb = filepath.stat().st_size / 1024 / 1024
                            return filepath, f"{size_mb:.1f}MB"
                        filepath.unlink(missing_ok=True)
                    except PWTimeout:
                        # Try direct navigation to the URL
                        if dl_href.startswith("http"):
                            try:
                                resp = page.request.get(dl_href)
                                if resp.ok:
                                    filepath.write_bytes(resp.body())
                                    if filepath.stat().st_size > 10000:
                                        size_mb = filepath.stat().st_size / 1024 / 1024
                                        return filepath, f"{size_mb:.1f}MB"
                                    filepath.unlink(missing_ok=True)
                            except Exception:
                                pass

        # Cleanup empty dir
        if pkg_dir.exists() and not any(pkg_dir.iterdir()):
            pkg_dir.rmdir()
        return None, "apkpure: no downloadable APK found"

    except PWTimeout:
        return None, "apkpure: timeout"
    except Exception as e:
        return None, f"apkpure: {str(e)[:80]}"


def download_from_apkcombo(page, pkg_name, outdir):
    """Download APK from apkcombo.com using real browser."""
    pkg_dir = Path(outdir) / pkg_name

    try:
        # Direct app URL
        url = f"https://apkcombo.com/apk/{pkg_name}/"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # Check if page exists
        if "404" in page.title() or "not found" in page.content().lower()[:500]:
            # Try search
            page.goto(f"https://apkcombo.com/search/{pkg_name}/", timeout=30000, wait_until="domcontentloaded")
            time.sleep(2)

            link = page.locator(f'a[href*="/{pkg_name}/"]').first
            if link.count() == 0:
                return None, "apkcombo: app not found"

            href = link.get_attribute("href")
            if href:
                if not href.startswith("http"):
                    href = f"https://apkcombo.com{href}"
                page.goto(href, timeout=30000, wait_until="domcontentloaded")
                time.sleep(2)

        # Find download button
        download_selectors = [
            'a.variant[href*="download"]',
            'a[href*="/download/"]',
            'a.btn-download',
            'a[class*="download"]',
            'a.is-success',
        ]

        download_page_url = None
        for selector in download_selectors:
            el = page.locator(selector).first
            if el.count() > 0:
                href = el.get_attribute("href")
                if href:
                    if not href.startswith("http"):
                        href = f"https://apkcombo.com{href}"
                    download_page_url = href
                    break

        if not download_page_url:
            return None, "apkcombo: no download button"

        # Go to download page
        page.goto(download_page_url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)

        # Look for actual APK download link
        apk_selectors = [
            'a[href*=".apk"]',
            'a.variant',
            'a[class*="download"]',
        ]

        for selector in apk_selectors:
            els = page.locator(selector)
            for i in range(min(els.count(), 5)):
                el = els.nth(i)
                href = el.get_attribute("href") or ""
                if ".apk" in href or "download" in href.lower():
                    pkg_dir.mkdir(parents=True, exist_ok=True)
                    filepath = pkg_dir / f"{pkg_name}.apk"

                    try:
                        with page.expect_download(timeout=120000) as download_info:
                            el.click()
                        download = download_info.value
                        download.save_as(str(filepath))

                        if filepath.exists() and filepath.stat().st_size > 10000:
                            size_mb = filepath.stat().st_size / 1024 / 1024
                            return filepath, f"{size_mb:.1f}MB"
                        filepath.unlink(missing_ok=True)
                    except PWTimeout:
                        filepath.unlink(missing_ok=True)
                        continue

        if pkg_dir.exists() and not any(pkg_dir.iterdir()):
            pkg_dir.rmdir()
        return None, "apkcombo: no downloadable APK found"

    except PWTimeout:
        return None, "apkcombo: timeout"
    except Exception as e:
        return None, f"apkcombo: {str(e)[:80]}"


def download_from_apkmirror(page, pkg_name, outdir):
    """Download APK from apkmirror.com using real browser."""
    pkg_dir = Path(outdir) / pkg_name

    try:
        # Search for the app
        url = f"https://www.apkmirror.com/?post_type=app_release&searchtype=apk&s={pkg_name}"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # Find the app in results - look for exact package match
        content = page.content()

        # Look for a link that leads to the app's download page
        app_links = page.locator('a.fontBlack')
        found_link = None

        for i in range(min(app_links.count(), 10)):
            link = app_links.nth(i)
            href = link.get_attribute("href") or ""
            text = link.inner_text().lower()
            # Check if the link text or URL matches our package
            pkg_parts = pkg_name.split(".")
            if any(part.lower() in text for part in pkg_parts if len(part) > 3):
                found_link = href
                break

        if not found_link:
            # Try first result
            if app_links.count() > 0:
                found_link = app_links.first.get_attribute("href")

        if not found_link:
            return None, "apkmirror: app not found"

        if not found_link.startswith("http"):
            found_link = f"https://www.apkmirror.com{found_link}"

        # Go to app page
        page.goto(found_link, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # Find the latest version download link
        download_btn = page.locator('a[href*="download"]').first
        if download_btn.count() == 0:
            # Try the download row
            download_btn = page.locator('.table-row a.accent_color').first

        if download_btn.count() == 0:
            return None, "apkmirror: no download link"

        href = download_btn.get_attribute("href")
        if not href.startswith("http"):
            href = f"https://www.apkmirror.com{href}"

        page.goto(href, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # Find the actual download button on the download page
        dl_btn = page.locator('a.accent_bg[href*="download"]').first
        if dl_btn.count() == 0:
            dl_btn = page.locator('a[data-google-vignette]').first
        if dl_btn.count() == 0:
            dl_btn = page.locator('a[rel="nofollow"][href*="download"]').first

        if dl_btn.count() == 0:
            return None, "apkmirror: no final download button"

        pkg_dir.mkdir(parents=True, exist_ok=True)
        filepath = pkg_dir / f"{pkg_name}.apk"

        try:
            with page.expect_download(timeout=120000) as download_info:
                dl_btn.click()
            download = download_info.value
            download.save_as(str(filepath))

            if filepath.exists() and filepath.stat().st_size > 10000:
                size_mb = filepath.stat().st_size / 1024 / 1024
                return filepath, f"{size_mb:.1f}MB"
            filepath.unlink(missing_ok=True)
        except PWTimeout:
            filepath.unlink(missing_ok=True)

        if pkg_dir.exists() and not any(pkg_dir.iterdir()):
            pkg_dir.rmdir()
        return None, "apkmirror: download failed"

    except PWTimeout:
        return None, "apkmirror: timeout"
    except Exception as e:
        return None, f"apkmirror: {str(e)[:80]}"


SOURCES = {
    "apkcombo": download_from_apkcombo,
    "apkpure": download_from_apkpure,
    "apkmirror": download_from_apkmirror,
}

DEFAULT_CHAIN = ["apkcombo", "apkpure", "apkmirror"]


def main():
    parser = argparse.ArgumentParser(description="APK Browser Downloader — Playwright-based, no rate limits")
    parser.add_argument("-i", "--input", required=True, help="Package list file")
    parser.add_argument("-o", "--outdir", default="apks", help="Output directory")
    parser.add_argument("--source", choices=list(SOURCES.keys()) + ["all"], default="all",
                        help="Source: apkpure, apkcombo, apkmirror, all")
    parser.add_argument("--sleep", type=float, default=3, help="Sleep between packages (default: 3s)")
    parser.add_argument("--start", type=int, default=0, help="Start from Nth package")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N packages")
    parser.add_argument("--headless", action="store_true", default=True, help="Run headless (default)")
    args = parser.parse_args()

    print("")
    print("  \033[96m╔════════════════════════════════════════════════════════════╗\033[0m")
    print("  \033[96m║\033[93m     APK Browser Downloader                \033[96m║\033[0m")
    print("  \033[96m║\033[0m  Headless Chrome — bypasses Cloudflare, no rate limits      \033[96m║\033[0m")
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

    sources = DEFAULT_CHAIN if args.source == "all" else [args.source]

    log(f"Total: {len(packages)} | Already: {already} | To download: {len(to_download)}", "STEP")
    log(f"Sources: {' -> '.join(sources)} | Sleep: {args.sleep}s", "INFO")

    if not to_download:
        log("Nothing to download!", "OK")
        return

    # Launch browser
    log("Launching headless Chrome...", "STEP")

    succeeded = []
    failed = []
    start_time = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            viewport={"width": 1280, "height": 720},
            accept_downloads=True,
        )
        page = context.new_page()

        for i, pkg in enumerate(to_download, 1):
            total = len(to_download)
            result_path = None
            result_info = None
            errors = []

            for source_name in sources:
                func = SOURCES[source_name]
                try:
                    result_path, result_info = func(page, pkg, args.outdir)
                    if result_path:
                        break
                    errors.append(result_info)
                except Exception as e:
                    errors.append(f"{source_name}: {str(e)[:60]}")

            if result_path:
                succeeded.append({"package": pkg, "source": source_name, "size": result_info})
                log(f"[{i}/{total}] \033[92m✓\033[0m {pkg} <- {source_name} ({result_info})", "OK")
            else:
                failed.append({"package": pkg, "reason": " | ".join(errors)})
                log(f"[{i}/{total}] \033[91m✗\033[0m {pkg} -> {' | '.join(errors)[:100]}", "ERR")

            if i < total:
                time.sleep(args.sleep)

        browser.close()

    elapsed = time.time() - start_time

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
        still_failed = outdir / "browser_failed_packages.txt"
        with open(still_failed, "w") as fp:
            for f in failed:
                fp.write(f"{f['package']}\n")
        with open(outdir / "browser_failed_packages.json", "w") as fp:
            json.dump(failed, fp, indent=2)
        log(f"Still failed: {still_failed}", "WARN")

    log("Done!", "OK")


if __name__ == "__main__":
    main()
