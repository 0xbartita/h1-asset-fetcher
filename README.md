# H1 Asset Fetcher

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8+-blue.svg" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-lightgrey.svg" alt="Platform">
</p>

A terminal tool for bug bounty hunters that fetches, downloads, and decompiles mobile app assets from bug bounty programs.

It pulls in-scope **Android**, **iOS**, and **Executable** assets from **HackerOne**, **Bugcrowd**, **Intigriti**, **YesWeHack**, and **Immunefi**, bulk-downloads the APKs, and decompiles them for review — all from one guided, interactive prompt.

## Install

```bash
pipx install h1-asset-fetcher      # or: pip install h1-asset-fetcher
```

This gives you the `h1-asset-fetcher` command. The APK **download** and **decompile** steps also need `apkeep` and `jadx`, which the bundled installer fetches for you:

```bash
git clone https://github.com/0xbartita/h1-asset-fetcher.git
cd h1-asset-fetcher && ./install.sh
```

## Usage

Just run it with **no arguments** — it walks you through the whole pipeline, one question at a time, inline in your terminal:

```bash
h1-asset-fetcher
```

It guides you through, in order:

1. **Platform** — HackerOne, Bugcrowd, Intigriti, YesWeHack, or Immunefi
2. **Credentials** — token (and username where needed); reused on later runs
3. **Scope & filters** — `android` / `ios` / `exe` / `all`, program filter, bounty-only, out-of-scope
4. **Fetch** — pulls in-scope assets to `output/<scope>/`
5. **Download** — pick which assets to grab; APKs fetched via apkeep (apk-pure → huawei → f-droid)
6. **Decompile** — optionally run jadx on the downloaded APKs

Press **Enter** to accept each default, or **Esc / Ctrl-C** to quit.

```
  h1-asset-fetcher

? Platform   (↑↓)
❯ HackerOne
  Bugcrowd
  Intigriti
  YesWeHack
  Immunefi

? HackerOne username ›  0xbartita
? HackerOne token    ›  ••••••••••
? Scope                 android
? Filter                Private BBP → bbp,private
? Bounty-only assets?   (Y/n)
? Include out-of-scope? (Y/n)

✔ 87 assets from 42 programs
✔ Saved output to output/android/

? Download APKs now?    Yes
? Select assets to download   (space toggles, ↵ confirms)
❯ ◉ com.acme.app        APK   Acme Corp
  ◯ com.globex.app      APK   Globex
```

The wizard remembers your credentials and last platform/scope/filter, so the next run just asks **"Use saved credentials? (Y/n)"** — press Enter to reuse. To wipe saved settings: `h1-asset-fetcher --forget`.

<img width="3248" height="1974" alt="image" src="https://github.com/user-attachments/assets/b133a0af-b0c5-4d74-8061-98799dd059a7" />

<img width="3248" height="1974" alt="image" src="https://github.com/user-attachments/assets/c4f5732d-4836-4660-a7ea-fb645a8334e5" />

<img width="2012" height="354" alt="image" src="https://github.com/user-attachments/assets/55468c4d-817d-4975-adc3-ee1b8d4272b6" />

## License

MIT
