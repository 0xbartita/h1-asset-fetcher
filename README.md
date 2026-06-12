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
- Multi-platform: **HackerOne** plus **Bugcrowd**, **Intigriti**, **YesWeHack**, **Immunefi** (`--platform`)
- Filter by program type: private BBP, public BBP, VDP, or all — plus **per-asset** in-scope / bounty-eligible filtering
- Organized output: `output/<scope>/packages.txt`, `packages.tsv`, `store_links.txt`, `packages.json`
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

### Install the `h1-asset-fetcher` command

```bash
pip install .            # or: pipx install .   → puts `h1-asset-fetcher` on your PATH
h1-asset-fetcher --help

# optional extras for the fallback downloaders:
pip install ".[telegram,browser]"
```

The legacy `python3 h1-asset-fetcher.py ...` still works (it's a thin shim over the package), and `python3 -m h1_asset_fetcher ...` is equivalent.


## Quick Start

```bash
git clone https://github.com/0xbartita/h1-asset-fetcher.git
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
| `--platform` | Source platform: `h1` (default), `bugcrowd`, `intigriti`, `yeswehack`, `immunefi` |
| `-b, --bounty-only` | Keep only assets individually eligible for bounty (per-asset `eligible_for_bounty`), not just paid programs |
| `--oos` | Also list out-of-scope assets (`eligible_for_submission=false`) into `oos_packages.txt` |
| `--columns` | Columns for `packages.tsv`: `t`=target `a`=asset_type `c`=category `h`=handle `p`=program `u`=store_url (default: `t,a,h,u`) |
| `--delimiter` | Column delimiter for `packages.tsv` (default: tab) |
| `-o, --output` | Output directory (default: `output/`) |
| `--programs-file` | Reuse cached `programs_cache.json` |

**Per-asset scoping:** HackerOne marks each asset individually, so a paid program can still contain out-of-scope or non-bounty assets. The fetcher reads `eligible_for_submission` / `eligible_for_bounty` per asset — out-of-scope assets are excluded from `packages.txt` (and listed in `oos_packages.txt` with `--oos`), and `-b` keeps only bounty-eligible ones.

**Output:**
```
output/
  android/
    packages.txt       # In-scope package names, one per line (feeds the downloader)
    packages.tsv       # Annotated columns: target, asset_type, handle, store_url
    packages.json      # Full details incl. per-asset eligibility + in_scope flag
    store_links.txt    # Play Store / App Store links
    oos_packages.txt   # Out-of-scope identifiers (with --oos)
    summary.json       # Scan metadata
  ios/
    ...
  programs_cache.json  # Reusable cache
```

### Other platforms (`--platform`)

Beyond HackerOne, the same mobile/exe pipeline can pull scope from other platforms — assets are normalized so the download/decompile steps still work:

| Platform | Auth (via `-t` or env var) | Where to get it |
|----------|----------------------------|-----------------|
| `bugcrowd` | `_bugcrowd_session` cookie · `BUGCROWD_TOKEN` | browser dev-tools cookie after logging in to bugcrowd.com |
| `intigriti` | researcher API token · `INTIGRITI_TOKEN` | app.intigriti.com → personal access tokens |
| `yeswehack` | JWT · `YESWEHACK_TOKEN` (or `-u` email + `YESWEHACK_PASSWORD`) | api.yeswehack.com |
| `immunefi` | none (public) | — (mostly web3; few mobile apps) |

```bash
python3 h1-asset-fetcher.py --platform bugcrowd -t "$BUGCROWD_TOKEN" --scope android
python3 h1-asset-fetcher.py --platform immunefi --scope all
```

<img width="3248" height="1974" alt="image" src="https://github.com/user-attachments/assets/b133a0af-b0c5-4d74-8061-98799dd059a7" />


### Downloaders — `h1_asset_fetcher.download.*`

Bulk APK downloader using [apkeep](https://github.com/EFForg/apkeep):

```bash
python3 -m h1_asset_fetcher.download.apkeep -i output/android/packages.txt -o apks/ -w 4
python3 -m h1_asset_fetcher.download.apkeep -i output/android/packages.txt -o apks/ --source apk-pure
```

<img width="3248" height="1974" alt="image" src="https://github.com/user-attachments/assets/c4f5732d-4836-4660-a7ea-fb645a8334e5" />

Fallbacks for packages apkeep can't fetch:

```bash
# headless Chromium (bypasses Cloudflare)  — needs the [browser] extra
pip install ".[browser]" && playwright install chromium
python3 -m h1_asset_fetcher.download.browser -i failed_packages.txt -o apks/

# pure-HTTP scraper
python3 -m h1_asset_fetcher.download.web -i failed_packages.txt -o apks/
```

Telegram bot downloader via [@RevEngiBot](https://t.me/RevEngiBot) — needs the `[telegram]` extra. Set your Telegram API credentials (get an `api_id`/`api_hash` at [my.telegram.org](https://my.telegram.org)), run the one-time login, then download:

```bash
pip install ".[telegram]"
export TELEGRAM_API_ID=your_api_id
export TELEGRAM_API_HASH=your_api_hash
export TELEGRAM_PHONE=+1234567890   # optional; prompted interactively if unset

# One-time login — creates the ~/.revengi_session session file
python3 -m h1_asset_fetcher.download.login

# Then download (reuses the session)
python3 -m h1_asset_fetcher.download.telegram_bot -i failed_packages.txt -o apks/
```

### Decompile — `h1_asset_fetcher/decompile/`

```bash
# jadx (thorough, single-threaded)
APKS_DIR=apks OUT_DIR=decompiled bash h1_asset_fetcher/decompile/jadx.sh

# dex2jar + procyon (fast, parallel)
bash h1_asset_fetcher/decompile/fast.sh
```

<img width="2012" height="354" alt="image" src="https://github.com/user-attachments/assets/55468c4d-817d-4975-adc3-ee1b8d4272b6" />


## Workflow

```
1. Fetch      h1-asset-fetcher -u user -t token --scope android
2. Download   python3 -m h1_asset_fetcher.download.apkeep -i output/android/packages.txt -o apks/
3. Decompile  APKS_DIR=apks OUT_DIR=decompiled bash h1_asset_fetcher/decompile/jadx.sh
4. Audit      your scanner / manual review
```

## Project layout

```
h1_asset_fetcher/
  cli.py              # argparse entry; no args → TUI (coming), flags → headless
  core/               # platform-agnostic: identifiers, output
  platforms/          # one folder per platform (plugin registry)
    hackerone/ bugcrowd/ intigriti/ yeswehack/ immunefi/
  download/           # apkeep, browser, web, telegram_bot, login
  decompile/          # jadx.sh, fast.sh
```

Adding a platform = one new folder under `platforms/` with a `Platform` subclass; the CLI picks it up automatically.


## License

[MIT](LICENSE)

## Author

0xbartita
