#!/usr/bin/env python3
"""
APK Bulk Downloader
Downloads APKs using apkeep from packages_list.txt output of h1-asset-fetcher.py.
Reports which packages failed to download.

Usage:
  python3 apk_downloader.py
  python3 apk_downloader.py -i packages_list.txt -o apks/
  python3 apk_downloader.py -i packages_list.json -o apks/ -w 4
  python3 apk_downloader.py -i packages_list.txt -o apks/ --source apk-pure
"""

import sys, json, os, argparse, subprocess, signal, time, threading
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

def download_apk(apkeep_bin, package, outdir, source="apk-pure", sleep_ms=0):
    pkg_name = package["package"]
    pkg_dir = Path(outdir) / pkg_name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    cmd = [apkeep_bin, "-a", pkg_name, "-d", source]
    if sleep_ms > 0:
        cmd += ["-s", str(sleep_ms)]
    cmd.append(str(pkg_dir))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        # Check if any APK/XAPK files were downloaded
        downloaded = list(pkg_dir.glob("*.apk")) + list(pkg_dir.glob("*.xapk")) + list(pkg_dir.glob("*.apkm"))
        if downloaded and result.returncode == 0:
            sizes = ", ".join(f"{f.name} ({f.stat().st_size / 1024 / 1024:.1f}MB)" for f in downloaded)
            return {"package": pkg_name, "program": package["program"], "success": True, "files": sizes}
        else:
            # Clean up empty dir
            if pkg_dir.exists() and not any(pkg_dir.iterdir()):
                pkg_dir.rmdir()
            stderr = result.stderr.strip()[:200] if result.stderr else ""
            stdout = result.stdout.strip()[:200] if result.stdout else ""
            reason = stderr or stdout or f"exit code {result.returncode}"
            return {"package": pkg_name, "program": package["program"], "success": False, "reason": reason}
    except subprocess.TimeoutExpired:
        if pkg_dir.exists() and not any(pkg_dir.iterdir()):
            pkg_dir.rmdir()
        return {"package": pkg_name, "program": package["program"], "success": False, "reason": "timeout (120s)"}
    except Exception as e:
        if pkg_dir.exists() and not any(pkg_dir.iterdir()):
            pkg_dir.rmdir()
        return {"package": pkg_name, "program": package["program"], "success": False, "reason": str(e)[:200]}

def main():
    parser = argparse.ArgumentParser(description="APK Bulk Downloader")
    parser.add_argument("-i", "--input", default="packages_list.txt",
                        help="Input file: packages_list.txt or packages_list.json (default: packages_list.txt)")
    parser.add_argument("-o", "--outdir", default="apks",
                        help="Output directory for downloaded APKs (default: apks/)")
    parser.add_argument("-w", "--workers", type=int, default=4,
                        help="Parallel downloads (default: 4)")
    parser.add_argument("-s", "--source", choices=["apk-pure", "google-play", "f-droid", "huawei-app-gallery"],
                        default="apk-pure", help="Download source (default: apk-pure)")
    parser.add_argument("--sleep", type=int, default=200,
                        help="Sleep between requests in ms to avoid rate limiting (default: 200)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only list packages, don't download")
    args = parser.parse_args()

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
    log(f"Source: {args.source} | Workers: {args.workers} | Sleep: {args.sleep}ms", "INFO")
    if already:
        log(f"Skipping {len(already)} already downloaded", "WARN")
    log(f"Downloading {len(to_download)} packages -> {args.outdir}/", "STEP")

    if args.dry_run:
        for i, p in enumerate(to_download, 1):
            log(f"  {i:>3}. {p['package']:<50} ({p['program'][:30]})", "INFO")
        log(f"Dry run complete. {len(to_download)} packages would be downloaded.", "OK")
        return

    # Download
    succeeded = []
    failed = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_apk, apkeep_bin, p, args.outdir, args.source, args.sleep): p
                   for p in to_download}

        for i, f in enumerate(as_completed(futures), 1):
            result = f.result()
            total = len(to_download)
            if result["success"]:
                succeeded.append(result)
                log(f"  [{i}/{total}] \033[92m✓\033[0m {result['package']} -> {result['files']}", "OK")
            else:
                failed.append(result)
                log(f"  [{i}/{total}] \033[91m✗\033[0m {result['package']} -> {result['reason']}", "ERR")

    elapsed = time.time() - start

    # Summary
    print(f"\n{'='*70}")
    log("DOWNLOAD SUMMARY", "STEP")
    print(f"{'='*70}")
    log(f"Total:       {len(packages)}", "INFO")
    log(f"Downloaded:  {len(succeeded)}", "OK")
    log(f"Skipped:     {len(already)} (already exist)", "WARN")
    log(f"Failed:      {len(failed)}", "ERR" if failed else "OK")
    log(f"Time:        {elapsed:.0f}s", "INFO")
    print(f"{'='*70}")

    # Write failed packages report
    if failed:
        print(f"\n{'='*70}")
        log("FAILED PACKAGES", "ERR")
        print(f"{'='*70}")
        for i, f in enumerate(failed, 1):
            log(f"  {i:>3}. {f['package']:<50} ({f['program'][:25]})", "ERR")
            log(f"       Reason: {f['reason']}", "WARN")

        # Save failed list
        failed_file = outdir / "failed_packages.txt"
        failed_json_file = outdir / "failed_packages.json"
        with open(failed_file, "w") as fp:
            for f in failed:
                fp.write(f"{f['package']}\n")
        with open(failed_json_file, "w") as fp:
            json.dump(failed, fp, indent=2)

        print(f"{'='*70}")
        log(f"Failed list saved to: {failed_file}", "WARN")
        log(f"Failed details saved to: {failed_json_file}", "WARN")
        print(f"{'='*70}")

    if succeeded:
        log(f"\nAPKs saved to: {args.outdir}/", "OK")

    log("Done!", "OK")

if __name__ == "__main__":
    main()
