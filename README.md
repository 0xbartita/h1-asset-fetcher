# H1 Asset Fetcher

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8+-blue.svg" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-lightgrey.svg" alt="Platform">
</p>

Automated toolkit for fetching, downloading, and decompiling mobile app assets from [HackerOne](https://hackerone.com) bug bounty programs. Supports **Android**, **iOS**, and **Executable** scopes.

Built for bug bounty hunters who want to audit mobile apps at scale.

## Features

- Fetch **Android** (Play Store / APK), **iOS** (App Store / TestFlight / IPA), and **Executable** assets
- Filter by program type: private BBP, public BBP, VDP, or all
- Organized output: `output/<scope>/packages.txt`, `store_links.txt`, `packages.json`
- Bulk APK download via [apkeep](https://github.com/EFForg/apkeep) with fallback downloaders
- Batch decompilation with [jadx](https://github.com/skylot/jadx) and dex2jar/procyon
- One-command setup with `install.sh`


## Installation

### Automatic

```bash
./install.sh
```

Installs: Python deps, apkeep, jadx, apktool, dex2jar, procyon.

### Manual

```bash
pip install -r requirements.txt

# Required
# - apkeep: https://github.com/EFForg/apkeep/releases
# - jadx:   https://github.com/skylot/jadx/releases

# Optional
# - apktool:  https://apktool.org/
# - dex2jar:  https://github.com/pxb1988/dex2jar
# - procyon:  https://github.com/mstrobel/procyon
# - playwright: pip install playwright && playwright install chromium
```


## Quick Start

```bash
git clone https://github.com/<username>/h1-asset-fetcher.git
cd h1-asset-fetcher
./install.sh

# Fetch Android assets from your private BBPs
python3 h1-asset-fetcher.py -u <h1_username> -t <api_token> --scope android

# Fetch iOS assets
python3 h1-asset-fetcher.py -u <username> -t <token> --scope ios

# Fetch everything
python3 h1-asset-fetcher.py -u <username> -t <token> --scope all --filter all
```

> Get your API token at [hackerone.com/settings/api_token/edit](https://hackerone.com/settings/api_token/edit)

## Usage

### `h1-asset-fetcher.py` — HackerOne Asset Fetcher

```bash
python3 h1-asset-fetcher.py -u <username> -t <token> --scope <scope> [options]
```

| Flag | Description |
|------|-------------|
| `-u, --username` | HackerOne API username (or `H1_USERNAME` env var) |
| `-t, --token` | HackerOne API token (or `H1_API_TOKEN` env var) |
| `-s, --scope` | **Required.** `android`, `ios`, `exe`, or `all` |
| `-f, --filter` | Comma-separated filter: `-f bbp,private` `-f vdp,public` `-f bbp` `-f all` (default: `bbp,private`) |
| `-o, --output` | Output directory (default: `output/`) |
| `--programs-file` | Reuse cached `programs_cache.json` |

**Output:**
```
output/
  android/
    packages.txt       # Package names, one per line
    packages.json      # Full details with program info
    store_links.txt    # Play Store / App Store links
    summary.json       # Scan metadata
  ios/
    ...
  programs_cache.json  # Reusable cache
```

<img width="3248" height="1974" alt="image" src="https://github.com/user-attachments/assets/b133a0af-b0c5-4d74-8061-98799dd059a7" />


### `apk_downloader.py` — APK Bulk Downloader

Downloads APKs using [apkeep](https://github.com/EFForg/apkeep).

```bash
python3 apk_downloader.py -i output/android/packages.txt -o apks/ -w 4
python3 apk_downloader.py -i output/android/packages.txt -o apks/ --source apk-pure
```

<img width="3248" height="1974" alt="image" src="https://github.com/user-attachments/assets/c4f5732d-4836-4660-a7ea-fb645a8334e5" />


### `apk_browser_downloader.py` — Browser-Based Downloader

Headless Chromium downloader that bypasses Cloudflare. Fallback for packages that fail with apkeep.

```bash
pip install playwright && playwright install chromium
python3 apk_browser_downloader.py -i failed_packages.txt -o apks/
```

### `revengi_downloader.py` — Telegram Bot Downloader

Downloads APKs via [@RevEngiBot](https://t.me/RevEngiBot) on Telegram.

```bash
export TELEGRAM_API_ID=your_api_id
export TELEGRAM_API_HASH=your_api_hash
python3 revengi_downloader.py -i failed_packages.txt -o apks/
```

### Decompile Scripts

```bash
# jadx (thorough, single-threaded)
APKS_DIR=apks OUT_DIR=decompiled ./decompile_all.sh

# dex2jar + procyon (fast, parallel)
./decompile_fast.sh
```

<img width="2012" height="354" alt="image" src="https://github.com/user-attachments/assets/55468c4d-817d-4975-adc3-ee1b8d4272b6" />


## Workflow

```
1. Fetch      python3 h1-asset-fetcher.py -u user -t token --scope android
2. Download   python3 apk_downloader.py -i output/android/packages.txt -o apks/
3. Decompile  ./decompile_all.sh
4. Audit      your scanner / manual review
```


## License

[MIT](LICENSE)

## Author

0xbartita
