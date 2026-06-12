"""The Textual dashboard.

One scrolling screen with five stacked sections. Each is locked until the one
above it is submitted; submitting collapses it to a one-line summary (with an
[edit] button) and unlocks the next:

    1. Platform  →  2. Credentials  →  3. Scope & filters  →
    4. Programs & assets  →  5. Download

Editing an upstream section re-locks everything below it.
"""
import os
import sys
import tempfile
import subprocess
from types import SimpleNamespace

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import (
    Header, Footer, Label, Select, Input, Button, Checkbox, Static, DataTable, Log)

from ..platforms import all_platforms, get_platform, PlatformAuthError
from ..core.collect import collect_assets
from ..core.output import save_output

_ORDER = ["platform", "creds", "scope", "results", "actions"]


class H1FetcherApp(App):
    TITLE = "h1-asset-fetcher"
    SUB_TITLE = "fetch → browse → download"
    BINDINGS = [("q", "quit", "Quit")]

    CSS = """
    .section { border: round $primary 50%; padding: 0 1; margin: 1 1; height: auto; }
    .section.locked { border: round $panel; color: $text-disabled; }
    .section.done { border: round $success 40%; }
    .sec-title { text-style: bold; padding: 0 0 1 0; }
    .section .summary { display: none; height: auto; }
    .section.done .summary { display: block; }
    .section.done .body { display: none; }
    .section.locked .body { display: none; }
    .summary-text { width: 1fr; color: $text-muted; padding: 0 1 0 0; }
    .edit-btn { min-width: 8; }
    #results-table { height: 14; margin: 1 0; }
    #action-log { height: 12; border: round $panel; }
    Input, Select { margin: 0 0 1 0; }
    Checkbox { margin: 0; }
    """

    def __init__(self):
        super().__init__()
        self.platform = None
        self.creds = {}
        self.scope = "android"
        self.filter = "bbp,private"
        self.bounty_only = False
        self.oos = False
        self.output = "output"
        self.programs = None
        self.result = None
        self._selected = set()
        self._row_assets = {}
        self._sel_col = None

    # ── Layout ───────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="main"):
            with Vertical(id="sec-platform", classes="section"):
                yield Label("1. Platform", classes="sec-title")
                with Vertical(classes="body"):
                    yield Select([(p.label, p.name) for p in all_platforms()],
                                 prompt="Choose a platform", id="platform-select")
                    yield Button("Next ▸", id="platform-next", variant="primary")
                with Horizontal(classes="summary"):
                    yield Static("", id="platform-summary", classes="summary-text")
                    yield Button("edit", id="platform-edit", classes="edit-btn")

            with Vertical(id="sec-creds", classes="section locked"):
                yield Label("2. Credentials", classes="sec-title")
                with Vertical(classes="body"):
                    yield Vertical(id="creds-fields")
                    yield Button("Next ▸", id="creds-next", variant="primary")
                with Horizontal(classes="summary"):
                    yield Static("", id="creds-summary", classes="summary-text")
                    yield Button("edit", id="creds-edit", classes="edit-btn")

            with Vertical(id="sec-scope", classes="section locked"):
                yield Label("3. Scope & filters", classes="sec-title")
                with Vertical(classes="body"):
                    yield Select([(s, s) for s in ("android", "ios", "exe", "all")],
                                 value="android", allow_blank=False, id="scope-select")
                    yield Input(value="bbp,private", id="filter-input",
                                placeholder="filter, e.g. bbp,private")
                    yield Checkbox("Bounty-only (-b)", id="bounty-check")
                    yield Checkbox("Include out-of-scope (--oos)", id="oos-check")
                    yield Button("Fetch ▸", id="scope-fetch", variant="success")
                with Horizontal(classes="summary"):
                    yield Static("", id="scope-summary", classes="summary-text")
                    yield Button("edit", id="scope-edit", classes="edit-btn")

            with Vertical(id="sec-results", classes="section locked"):
                yield Label("4. Programs & assets", classes="sec-title")
                with Vertical(classes="body"):
                    yield Static("", id="results-status")
                    yield DataTable(id="results-table", cursor_type="row", zebra_stripes=True)
                    with Horizontal():
                        yield Button("Save output", id="results-save", variant="primary")
                        yield Button("Download selected ▸", id="results-download", variant="success")

            with Vertical(id="sec-actions", classes="section locked"):
                yield Label("5. Download & decompile", classes="sec-title")
                with Vertical(classes="body"):
                    yield Log(id="action-log", highlight=False)
        yield Footer()

    # ── State helpers ────────────────────────────────────────
    def _set_state(self, name, state, summary=None):
        sec = self.query_one(f"#sec-{name}")
        sec.remove_class("locked")
        sec.remove_class("done")
        if state == "locked":
            sec.add_class("locked")
        elif state == "done":
            sec.add_class("done")
            if summary is not None:
                self.query_one(f"#{name}-summary", Static).update(summary)

    def _lock_downstream(self, name):
        idx = _ORDER.index(name)
        for s in _ORDER[idx + 1:]:
            self._set_state(s, "locked")
        if idx < _ORDER.index("results"):
            table = self.query_one("#results-table", DataTable)
            table.clear(columns=True)
            self.query_one("#results-status", Static).update("")
            self._selected.clear()
            self._row_assets.clear()

    async def _build_creds(self):
        plat = get_platform(self.platform)
        fields = self.query_one("#creds-fields", Vertical)
        await fields.remove_children()
        if not plat.auth:
            await fields.mount(Static("No credentials needed for this platform."))
            return
        for c in plat.auth:
            default = os.environ.get(plat.env.get(c.key, ""), "")
            await fields.mount(Label(c.label))
            await fields.mount(Input(value=default, password=c.secret,
                                     placeholder=c.label, id=f"cred-{c.key}"))

    def _read_creds(self):
        plat = get_platform(self.platform)
        creds = {}
        for c in plat.auth:
            try:
                creds[c.key] = self.query_one(f"#cred-{c.key}", Input).value
            except Exception:
                creds[c.key] = ""
        return creds

    # ── Button handling ──────────────────────────────────────
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if bid == "platform-next":
            sel = self.query_one("#platform-select", Select).value
            if sel in (None, Select.BLANK):
                self.bell()
                return
            self.platform = sel
            label = get_platform(sel).label
            self._set_state("platform", "done", f"Platform: {label}")
            await self._build_creds()
            self._set_state("creds", "active")

        elif bid == "creds-next":
            self.creds = self._read_creds()
            shown = ", ".join(
                f"{k}=***" if v else f"{k}=(empty)" for k, v in self.creds.items()) or "none"
            self._set_state("creds", "done", f"Credentials: {shown}")
            self._set_state("scope", "active")

        elif bid == "scope-fetch":
            self.scope = self.query_one("#scope-select", Select).value
            self.filter = self.query_one("#filter-input", Input).value or "bbp,private"
            self.bounty_only = self.query_one("#bounty-check", Checkbox).value
            self.oos = self.query_one("#oos-check", Checkbox).value
            flags = (" -b" if self.bounty_only else "") + (" --oos" if self.oos else "")
            self._set_state("scope", "done",
                            f"Scope: {self.scope} · {self.filter}{flags}")
            self._set_state("results", "active")
            self.query_one("#results-status", Static).update("[yellow]Fetching…[/]")
            self._fetch_worker()

        elif bid == "results-save":
            self._save()

        elif bid == "results-download":
            self._start_download()

        elif bid.endswith("-edit"):
            name = bid[:-5]
            self._set_state(name, "active")
            self._lock_downstream(name)

    # ── Fetch ────────────────────────────────────────────────
    @work(thread=True, exclusive=True)
    def _fetch_worker(self):
        try:
            programs = get_platform(self.platform).fetch(
                self.creds, self.scope, self.filter, self.oos)
        except PlatformAuthError as e:
            self.call_from_thread(self._fetch_error, str(e))
            return
        except SystemExit:
            self.call_from_thread(self._fetch_error, "Authentication failed — check credentials.")
            return
        except Exception as e:  # noqa: BLE001 — surface any fetch failure in-UI
            self.call_from_thread(self._fetch_error, f"Fetch failed: {e}")
            return
        self.call_from_thread(self._fetch_done, programs)

    def _fetch_error(self, msg):
        self.query_one("#results-status", Static).update(f"[red]{msg}[/]")

    def _fetch_done(self, programs):
        self.programs = programs
        if not programs:
            self.query_one("#results-status", Static).update("[red]No programs found.[/]")
            return
        res = collect_assets(programs, oos=self.oos, bounty_only=self.bounty_only)
        self.result = res
        vp = res["valid_packages"]
        table = self.query_one("#results-table", DataTable)
        table.clear(columns=True)
        cols = table.add_columns("sel", "program", "asset", "type")
        self._sel_col = cols[0]
        self._row_assets.clear()
        self._selected.clear()
        for a in vp:
            rk = table.add_row("[ ]", a["program"][:24], a["package"], a["asset_type"])
            self._row_assets[rk] = a
        nprog = len({a["handle"] for a in vp})
        self.query_one("#results-status", Static).update(
            f"[green]{len(vp)} assets[/] from {nprog} programs · "
            "Enter toggles selection")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "results-table":
            return
        rk = event.row_key
        if rk in self._selected:
            self._selected.discard(rk)
            mark = "[ ]"
        else:
            self._selected.add(rk)
            mark = "[x]"
        event.data_table.update_cell(rk, self._sel_col, mark)

    # ── Save / download ──────────────────────────────────────
    def _save(self):
        if not self.result:
            return
        args = SimpleNamespace(output=self.output, scope=self.scope, filter=self.filter,
                               platform=self.platform, bounty_only=self.bounty_only,
                               columns="t,a,h,u", delimiter="\t")
        seen = {a["handle"] for a in self.result["unique"]}
        outdir, _ = save_output(args, self.result["valid_packages"], self.programs,
                                self.result["prog_info"], seen, self.result["unique"],
                                self.result["valid_oos"])
        self.notify(f"Saved to {outdir}", title="Output")

    def _selected_packages(self):
        if self._selected:
            return [self._row_assets[rk]["package"] for rk in self._selected
                    if rk in self._row_assets]
        return [a["package"] for a in (self.result["valid_packages"] if self.result else [])]

    def _start_download(self):
        pkgs = self._selected_packages()
        if not pkgs:
            self.notify("Nothing to download.", severity="warning")
            return
        self._set_state("actions", "active")
        self.query_one("#action-log", Log).clear()
        self._download_worker(pkgs)

    @work(thread=True, exclusive=True)
    def _download_worker(self, packages):
        self.call_from_thread(self._log, f"Downloading {len(packages)} package(s) via apkeep…")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("\n".join(packages) + "\n")
            tmp = f.name
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "h1_asset_fetcher.download.apkeep",
                 "-i", tmp, "-o", "apks"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                self.call_from_thread(self._log, line.rstrip("\n"))
            proc.wait()
            self.call_from_thread(self._log, f"[exit {proc.returncode}]")
        except FileNotFoundError:
            self.call_from_thread(self._log, "apkeep not found — install it first.")
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self._log, f"Download error: {e}")

    def _log(self, text):
        self.query_one("#action-log", Log).write_line(text)


def run():
    """Launch the TUI."""
    H1FetcherApp().run()


if __name__ == "__main__":
    run()
