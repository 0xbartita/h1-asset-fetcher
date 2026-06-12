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
from ..core import log
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


def run():
    _need_questionary()
    print()
    questionary.print("  h1-asset-fetcher", style="bold fg:#7aa2f7")
    print()

    # 1. Platform (HackerOne first, then the rest alphabetically)
    plats = sorted(all_platforms(), key=lambda p: (p.name != "hackerone", p.label.lower()))
    plat_name = _ask(questionary.select(
        "Platform", choices=[Choice(p.label, value=p.name) for p in plats], style=STYLE))
    plat = get_platform(plat_name)

    # 2. Credentials (built from the platform's auth descriptor)
    creds = {}
    for c in plat.auth:
        default = _default_for(plat, c.key)
        if c.secret:
            val = _ask(questionary.password(f"{plat.label} {c.label}", style=STYLE))
            val = val or default
        else:
            val = _ask(questionary.text(f"{plat.label} {c.label}", default=default, style=STYLE))
        creds[c.key] = val

    # 3. Scope & filters
    scope = _ask(questionary.select(
        "Scope", choices=["android", "ios", "exe", "all"], default="android", style=STYLE))
    prog_filter = _ask(questionary.text("Filter", default="bbp,private", style=STYLE))
    bounty_only = _ask(questionary.confirm("Bounty-only assets?", default=False, style=STYLE))
    oos = _ask(questionary.confirm("Include out-of-scope?", default=False, style=STYLE))

    # 4. Fetch
    print()
    try:
        programs = plat.fetch(creds, scope, prog_filter, oos)
    except PlatformAuthError as e:
        log(str(e), "ERR")
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
    if not _ask(questionary.confirm("Download APKs now?", default=False, style=STYLE)):
        return
    choices = [Choice(_asset_choice(a), value=a["package"], checked=True) for a in vp]
    selected = _ask(questionary.checkbox(
        "Select assets to download", choices=choices, style=STYLE))
    if not selected:
        log("Nothing selected.", "WARN")
        return
    _download(selected)

    # 7. Decompile
    if _ask(questionary.confirm("Decompile downloaded APKs?", default=False, style=STYLE)):
        _decompile()


def _download(packages):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(packages) + "\n")
        tmp = f.name
    log(f"Downloading {len(packages)} package(s) via apkeep...", "STEP")
    try:
        subprocess.run([sys.executable, "-m", "h1_asset_fetcher.download.apkeep",
                        "-i", tmp, "-o", "apks"], check=False)
    except FileNotFoundError:
        log("apkeep not found — install it first (see README).", "ERR")


def _decompile():
    from ..decompile import script_path
    log("Decompiling with jadx...", "STEP")
    env = dict(os.environ, APKS_DIR="apks", OUT_DIR="decompiled")
    try:
        subprocess.run(["bash", script_path("jadx")], env=env, check=False)
    except FileNotFoundError:
        log("bash/jadx not available.", "ERR")


if __name__ == "__main__":
    run()
