# h1-asset-fetcher — TUI Redesign & Pluggable Platforms

- **Date:** 2026-06-12
- **Status:** Approved design (pending spec review)
- **Author:** 0xbartita

## 1. Summary

Evolve `h1-asset-fetcher` from a single-file CLI into an **installable package** with:

1. A **full-pipeline Textual TUI** — a single-screen, progressively-revealed dashboard that drives the whole flow: choose platform → enter credentials → set scope/filters → fetch → select assets → download → decompile → browse results.
2. A **pluggable platform architecture** — adding a bug-bounty platform (Bugcrowd, Intigriti, …) is *one new file*, with the TUI and CLI both picking it up automatically.
3. **One-command install** — `pip install` / `pipx install` puts an `h1-asset-fetcher` command on PATH.

The existing CLI keeps working unchanged; the TUI and CLI sit on a shared core so they never drift.

## 2. Goals / Non-goals

**Goals**
- Full-pipeline Textual dashboard (single screen, progressive disclosure, collapse-to-`[edit]`).
- Core/UI separation so CLI and TUI share one engine.
- Formalized platform plugin registry (auto-discovered; descriptor-driven).
- Installable package + `h1-asset-fetcher` entry point; heavy deps optional.
- Backward compatibility: `python3 h1-asset-fetcher.py …` still works.

**Non-goals (explicitly out of scope for now)**
- Saved-config / credential persistence store.
- First-run setup/onboarding wizard.
- Cross-platform (Windows) hardening beyond what already exists.
- Publishing to PyPI (the package will be *installable*; actual publish is a later decision).

## 3. Decisions (locked)

| Question | Decision |
|----------|----------|
| TUI scope | Full pipeline dashboard |
| "Easy access" means | One-command install only (no config store / wizard / Win hardening) |
| Framework | Textual |
| Name everywhere | `h1-asset-fetcher` (package dist), `h1_asset_fetcher` (import) |
| Dashboard layout | One screen, progressive reveal |
| Completed section behavior | Collapse to one-line summary with `[edit]` (re-running downstream on change) |
| Bare command (no args) | Opens the TUI; flags = headless/scripted |
| Build sequence | Phase 1 (repackage + plugin registry) first, then Phase 2 (TUI) |

## 4. Architecture

Split the monolith into a **core library** (no UI), consumed by both a **CLI** and a **Textual TUI**.

```
h1_asset_fetcher/
  __init__.py
  __main__.py            # python -m h1_asset_fetcher
  cli.py                 # argparse: headless run OR launch TUI (no args -> TUI)
  core/
    fetch.py             # program/scope fetch orchestration (from today's monolith)
    identifiers.py       # extract_identifier, store_url, iTunes lookup
    output.py            # packages.txt/.tsv/.json, OOS, eligibility (gaps #1-#3)
    download.py          # wraps apkeep + browser/web/telegram fallbacks
    decompile.py         # wraps jadx / dex2jar
    session.py           # rate-limited HTTP session helper
  platforms/
    __init__.py          # registry + auto-discovery + Platform/Cred contract
    hackerone.py         # extracted H1 fetcher (today's inline logic)
    bugcrowd.py intigriti.py yeswehack.py immunefi.py
  tui/
    app.py               # Textual App (single screen)
    sections.py          # the progressive sections (widgets)
  __version__.py
pyproject.toml           # entry point: h1-asset-fetcher = h1_asset_fetcher.cli:main
h1-asset-fetcher.py      # thin backward-compat shim -> h1_asset_fetcher.cli:main
```

**Data flow:** TUI/CLI collect (platform, creds, scope, filters) → `core.fetch` dispatches to the selected platform plugin → normalized programs/assets → `core.output` writes files → optional `core.download` → `core.decompile`. Each unit has one purpose and a defined interface so it can be tested in isolation.

## 5. Platform plugin contract (formalized)

Each platform is one module that registers a descriptor. Adding a platform = drop in a file; nothing else to wire.

```python
@register
class HackerOne(Platform):
    name  = "hackerone"
    label = "HackerOne"
    # Drives the TUI credential form AND CLI validation:
    auth  = [Cred("username"), Cred("token", secret=True)]
    env   = {"username": "H1_USERNAME", "token": "H1_API_TOKEN"}

    def fetch(self, creds, scope, filters, oos) -> list[Program]:
        ...
```

- `Program` / `Scope` are lightweight normalized records (the shape today's `platforms/__init__.py` already defines: `asset_type` in the H1 vocabulary, `asset_identifier`, `eligible_for_submission`, `eligible_for_bounty`).
- A **registry** auto-discovers every module in `platforms/` at import.
- The TUI reads `auth`/`env` to render the credential fields dynamically; switching platform changes which fields appear.
- The four ported platforms (Bugcrowd/Intigriti/YesWeHack/Immunefi) migrate from today's function form into this class form during Phase 1.

## 6. TUI design (single screen, progressive disclosure)

One scrolling screen with stacked sections. Each is locked until the one above it is submitted; submitting reveals the next. Completed sections collapse to a one-line summary with `[edit]`; editing an upstream section re-runs everything below it.

Sections:
1. **Platform** — selector (HackerOne / Bugcrowd / …).
2. **Credentials** — fields generated from the platform's `auth` descriptor (prefilled from env vars when present).
3. **Scope & filters** — scope (android/ios/exe/all), program filter (bbp/vdp/private/public), toggles for `-b/--bounty-only` and `--oos`.
4. **Programs & assets** — live `DataTable` of fetched programs/assets; multi-select assets to act on.
5. **Download & decompile** — run download (apkeep + fallbacks) then decompile (jadx/dex2jar) on the selection, with progress streamed in place.

**On launch** only section 1 is active; the rest show `· locked ·`. Errors (auth failure, missing external tool like `apkeep`/`jadx`, network) surface as in-TUI modals/inline status — never tracebacks.

## 7. CLI behavior

- `h1-asset-fetcher` (no args) → launches the TUI.
- `h1-asset-fetcher --scope android -t … --platform hackerone …` → headless run, exactly today's behavior (plus the new flags already added: `--platform`, `-b/--bounty-only`, `--oos`, `--columns`, `--delimiter`).
- `python3 h1-asset-fetcher.py …` → still works via the root shim.

## 8. Packaging

- `pyproject.toml` with `project.scripts`: `h1-asset-fetcher = h1_asset_fetcher.cli:main`.
- Base dependencies: `requests`, `textual`.
- **Optional extras** keep the base install light:
  - `[telegram]` → `telethon` (for `revengi_downloader`)
  - `[browser]` → `playwright` (for the Cloudflare-bypass downloader)
- `pipx install .` / `pip install -e .` → `h1-asset-fetcher` on PATH.

## 9. Phasing

**Phase 1 — Repackage + plugin registry (behavior-preserving).**
- Extract the monolith into `h1_asset_fetcher/core/` and `platforms/`.
- Add the `Platform`/`Cred`/`register` registry; migrate the 5 platforms onto it.
- Add `pyproject.toml`, the `h1-asset-fetcher` entry point, the root shim, optional extras.
- No TUI yet; CLI output identical. Ships an installable, pluggable tool.
- Its own implementation plan.

**Phase 2 — Textual TUI.**
- `tui/app.py` + sections on top of the Phase-1 core.
- Single-screen progressive flow → fetch table → download/decompile → results.
- Its own implementation plan.

## 10. Backward compatibility

- `h1-asset-fetcher.py` (root) becomes a shim importing `h1_asset_fetcher.cli:main`, so existing invocations and the downloader/decompile scripts that reference output paths keep working.
- Output file layout (`output/<scope>/…`) is unchanged.

## 11. Testing

- **Core** units (identifiers, output, filter parsing, eligibility/OOS gating) get unit tests with synthetic fixtures — the same synthetic-cache approach already used to verify gaps #1–#3.
- **Platform plugins**: registry discovery + descriptor validation tested; live-API fetch paths remain unverifiable without real credentials (documented limitation — they are faithful ports, defensively coded).
- **TUI**: Textual's test harness (`run_test`, snapshot tests) for the section state machine (lock/unlock/collapse/edit-rerun).

## 12. Risks / open items

- **Untested live platform auth.** The 4 ported platforms (Bugcrowd/Intigriti/YesWeHack/Immunefi) have not been exercised against live APIs (no credentials). Need a real-credential smoke test before relying on them.
- **External tools.** Download/decompile depend on `apkeep`, `jadx`, `java`, etc.; the TUI must detect their absence and degrade gracefully.
- **Scope creep.** Saved-config, setup wizard, and PyPI publish are intentionally deferred; revisit after Phase 2.
