"""Interactive terminal wizard (questionary). Runs inline in the terminal —
no full-screen takeover — asking one question at a time:

    platform → credentials → scope/filters → fetch → save → download → decompile

The backend (platform registry, fetch, collect, output) is shared with the CLI.
"""
import os
import sys
import tempfile
import subprocess
from types import SimpleNamespace

from ..platforms import all_platforms, get_platform, PlatformAuthError
from ..core import log, config
from ..core.identifiers import resolve_ios_store_links
from ..core.collect import collect_assets
from ..core.output import save_output

try:
    import questionary
    from questionary import Choice
except ImportError:  # pragma: no cover - exercised only without the dep
    questionary = None
    Choice = None

ASSET_LABELS = {
    "GOOGLE_PLAY_APP_ID": "PlayStore", "OTHER_APK": "APK",
    "APPLE_STORE_APP_ID": "AppStore", "TESTFLIGHT": "TestFlight",
    "OTHER_IPA": "IPA", "DOWNLOADABLE_EXECUTABLES": "EXE",
    "WINDOWS_APP_STORE_APP_ID": "WinStore",
}

# Filter presets are declared per-platform (Platform.filters), so the menu only
# offers filters the chosen platform can actually express.
_CUSTOM_FILTER = "__custom__"

# A calm, modern prompt style (the rest follows questionary's defaults).
STYLE = None
if questionary is not None:
    STYLE = questionary.Style([
        ("qmark", "fg:#7aa2f7 bold"),
        ("question", "bold"),
        ("answer", "fg:#9ece6a"),
        ("pointer", "fg:#7aa2f7 bold"),
        ("highlighted", "fg:#7aa2f7 bold"),
        ("selected", "fg:#9ece6a"),
    ])


def _need_questionary():
    if questionary is None:
        print("The interactive wizard needs 'questionary'.\n"
              "  pip install questionary    (or: pip install '.[wizard]')\n"
              "Or use the CLI directly, e.g.: h1-asset-fetcher -s android -t <token>",
              file=sys.stderr)
        raise SystemExit(1)


def _default_for(plat, key):
    env = plat.env.get(key)
    return os.environ.get(env, "") if env else ""


def _asset_choice(a):
    label = ASSET_LABELS.get(a["asset_type"], a["asset_type"])
    return f"{a['package']:<40} {label:<10} {a['program'][:24]}"


def _ask(question):
    """Run a questionary question; abort the wizard cleanly on Ctrl-C / Esc."""
    answer = question.ask(kbi_msg="")
    if answer is None:
        raise SystemExit(0)
    return answer


def _choose_filter(presets, default=None):
    """Pick the program filter from the platform's preset menu (only filters that
    platform can express). 'Custom…' drops to a free-text box for an exact
    comma-separated string. `default` pre-selects a matching preset."""
    choices = [Choice(f"{label:<13} → {value}", value=value)
               for label, value in presets]
    choices.append(Choice("Custom…", value=_CUSTOM_FILTER))
    preset_values = {v for _, v in presets}
    picked = _ask(questionary.select(
        "Filter", choices=choices,
        default=default if default in preset_values else None, style=STYLE))
    if picked == _CUSTOM_FILTER:
        return _ask(questionary.text(
            "Filter (comma-separated: bbp,vdp,private,public,all)",
            default=default or "bbp,private", style=STYLE))
    return picked


def _has_usable_saved_creds(plat, saved):
    """Whether the saved credentials are complete enough to offer reuse: every
    REQUIRED cred is present AND (when the platform has a secret/token cred) at
    least one secret is actually saved. This stops us offering 'Use saved
    credentials?' when only a non-secret (e.g. a username) was persisted but no
    token — picking 'yes' there would send an empty token and fail auth. Note
    platforms like YesWeHack mark every cred required=False (token OR
    username+password), so a plain all-required check would be vacuously true."""
    if not (plat.auth and saved):
        return False
    required = [c.key for c in plat.auth if c.required]
    secret_keys = [c.key for c in plat.auth if c.secret]
    has_required = all(saved.get(k) for k in required)
    has_secret = (not secret_keys) or any(saved.get(k) for k in secret_keys)
    return has_required and has_secret


def _collect_creds(plat):
    """Return {cred_key: value} for a platform. Reuse saved credentials when
    present (one Enter to confirm); otherwise prompt and persist them so the
    next run doesn't ask again. To change them: pick 'n', or run --forget."""
    saved = config.get_platform_creds(plat.name)
    if _has_usable_saved_creds(plat, saved):
        if _ask(questionary.confirm(
                f"Use saved {plat.label} credentials?", default=True, style=STYLE)):
            return {c.key: saved.get(c.key, "") for c in plat.auth}

    creds = {}
    for c in plat.auth:
        default = saved.get(c.key) or _default_for(plat, c.key)
        if c.secret:
            val = _ask(questionary.password(f"{plat.label} {c.label}", style=STYLE))
            val = val or default
        else:
            val = _ask(questionary.text(f"{plat.label} {c.label}", default=default, style=STYLE))
        creds[c.key] = val
    if plat.auth:
        config.set_platform_creds(plat.name, creds)
    return creds


def run():
    _need_questionary()
    print()
    questionary.print("  h1-asset-fetcher", style="bold fg:#7aa2f7")
    print()

    prefs = config.get_prefs()

    # 1. Platform (last-used first if known, else HackerOne, then alphabetically)
    plats = sorted(all_platforms(), key=lambda p: (p.name != "hackerone", p.label.lower()))
    plat_values = [p.name for p in plats]
    plat_default = prefs.get("platform")
    if plat_default not in plat_values:
        plat_default = "hackerone" if "hackerone" in plat_values else None
    plat_name = _ask(questionary.select(
        "Platform", choices=[Choice(p.label, value=p.name) for p in plats],
        default=plat_default, style=STYLE))
    plat = get_platform(plat_name)

    # 2. Credentials — reuse saved ones if present, else prompt and remember them
    creds = _collect_creds(plat)

    # 3. Scope & filters (pre-select last-used choices)
    scopes = ["android", "ios", "exe", "all"]
    scope_default = prefs.get("scope") if prefs.get("scope") in scopes else "android"
    scope = _ask(questionary.select(
        "Scope", choices=scopes, default=scope_default, style=STYLE))
    prog_filter = _choose_filter(plat.filters, default=prefs.get("filter"))
    bounty_only = _ask(questionary.confirm("Bounty-only assets?", default=True, style=STYLE))
    oos = _ask(questionary.confirm("Include out-of-scope?", default=True, style=STYLE))

    # Remember the choices for next time (persists even if the fetch fails).
    config.set_prefs(platform=plat_name, scope=scope, filter=prog_filter)

    # 4. Fetch
    print()
    try:
        programs = plat.fetch(creds, scope, prog_filter, oos)
    except PlatformAuthError as e:
        log(str(e), "ERR")
        if config.get_platform_creds(plat.name):
            log("Those were your saved credentials — re-run and answer 'n' to "
                "re-enter them, or run: h1-asset-fetcher --forget", "INFO")
        raise SystemExit(1)
    except SystemExit:
        log("Authentication failed — check your credentials.", "ERR")
        raise SystemExit(1)

    if not programs:
        log("No programs found (check credentials / filter).", "ERR")
        raise SystemExit(1)

    res = collect_assets(programs, oos=oos, bounty_only=bounty_only)
    vp = res["valid_packages"]
    if res["dropped_oos"] or res["dropped_nonbounty"]:
        log(f"Per-asset filter: dropped {res['dropped_oos']} out-of-scope, "
            f"{res['dropped_nonbounty']} non-bounty", "INFO")
    if not vp:
        log("No valid assets after filtering.", "WARN")
        raise SystemExit(0)
    nprog = len({a["handle"] for a in vp})
    log(f"{len(vp)} assets from {nprog} programs", "OK")

    if scope in ("ios", "all"):
        resolve_ios_store_links(vp)

    # 5. Save output
    args = SimpleNamespace(output="output", scope=scope, filter=prog_filter,
                           platform=plat_name, bounty_only=bounty_only,
                           columns="t,a,h,u", delimiter="\t")
    seen = {a["handle"] for a in res["unique"]}
    outdir, _ = save_output(args, vp, programs, res["prog_info"], seen,
                            res["unique"], res["valid_oos"])
    log(f"Saved output to {outdir}/", "OK")
    print()

    # 6. Download
    if not _ask(questionary.confirm("Download APKs now?", default=True, style=STYLE)):
        return
    choices = [Choice(_asset_choice(a), value=a["package"], checked=True) for a in vp]
    selected = _ask(questionary.checkbox(
        "Select assets to download", choices=choices, style=STYLE))
    if not selected:
        log("Nothing selected.", "WARN")
        return
    _download(selected)

    # 7. Decompile
    if _ask(questionary.confirm("Decompile downloaded APKs?", default=True, style=STYLE)):
        _decompile()


def _download(packages):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(packages) + "\n")
        tmp = f.name
    apks_dir = os.path.abspath("apks")
    log(f"Downloading {len(packages)} package(s) via apkeep...", "STEP")
    try:
        # --no-gplay-retry: the child must not drop into its own raw credential
        # prompt mid-wizard; we drive the Google Play retry here via questionary.
        subprocess.run([sys.executable, "-m", "h1_asset_fetcher.download.apkeep",
                        "-i", tmp, "-o", apks_dir, "--source", "all",
                        "--no-gplay-retry"], check=False)
    except FileNotFoundError:
        log("apkeep not found — install it first (see README).", "ERR")
        return
    _gplay_retry(apks_dir)


def _gplay_retry(apks_dir):
    """If the download left failures, offer to finish them via Google Play."""
    from ..download.apkeep import (GPLAY_TOKEN_HELP, is_oauth_token,
                                   _gplay_oauth_hint, gplay_supported, find_apkeep,
                                   GPLAY_DISABLED_MSG)
    failed_file = os.path.join(apks_dir, "failed_packages.txt")
    if not os.path.isfile(failed_file):
        return
    failed = [p for p in open(failed_file).read().splitlines() if p.strip()]
    if not failed:
        return
    if not gplay_supported(find_apkeep()):
        log(GPLAY_DISABLED_MSG, "WARN")
        return
    if not _ask(questionary.confirm(
            f"Retry {len(failed)} failed package(s) via Google Play?",
            default=False, style=STYLE)):
        return
    print(GPLAY_TOKEN_HELP)
    email = _ask(questionary.text(
        "Google account email", default=os.environ.get("APKEEP_GPLAY_EMAIL", ""),
        style=STYLE)).strip()
    token = _ask(questionary.password(
        "AAS token (aas_et/…, NOT the oauth2_4/… value)", style=STYLE)).strip()
    if not email or not token:
        log("Email and AAS token are both required — skipping.", "WARN")
        return
    if is_oauth_token(token):
        log("That looks like an OAuth token (oauth2_4/…), not an AAS token — skipping.", "WARN")
        print(_gplay_oauth_hint(email))
        return
    log(f"Retrying {len(failed)} package(s) via google-play...", "STEP")
    try:
        subprocess.run([sys.executable, "-m", "h1_asset_fetcher.download.apkeep",
                        "-i", failed_file, "-o", apks_dir, "--source", "google-play",
                        "--gplay-email", email, "--gplay-token", token,
                        "--no-gplay-retry"], check=False)
    except FileNotFoundError:
        log("apkeep not found — install it first (see README).", "ERR")


def _decompile():
    from ..decompile import script_path
    apks_dir = os.path.abspath("apks")
    if not os.path.isdir(apks_dir):
        log(f"No APKs to decompile ({apks_dir} not found).", "WARN")
        return
    log("Decompiling with jadx...", "STEP")
    # Absolute paths so jadx.sh finds them regardless of its own working dir.
    env = dict(os.environ, APKS_DIR=apks_dir, OUT_DIR=os.path.abspath("decompiled"))
    try:
        subprocess.run(["bash", script_path("jadx")], env=env, check=False)
    except FileNotFoundError:
        log("bash/jadx not available.", "ERR")


if __name__ == "__main__":
    run()
