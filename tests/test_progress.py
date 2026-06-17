"""In-place progress line: silent off a TTY, rewrites the same line on a TTY."""
import sys

from h1_asset_fetcher import core


class FakeTTY:
    def __init__(self):
        self.buf = []

    def isatty(self):
        return True

    def write(self, s):
        self.buf.append(s)

    def flush(self):
        pass

    @property
    def text(self):
        return "".join(self.buf)


def test_progress_silent_on_non_tty(capsys):
    core.progress("hello")
    assert capsys.readouterr().out == ""


def test_progress_writes_inplace_on_tty(monkeypatch):
    fake = FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake)
    core.progress("page 3")
    assert "\r" in fake.text          # returns to column 0
    assert "page 3" in fake.text
    assert "\033[K" in fake.text      # clears stale chars to end of line
    assert not fake.text.endswith("\n")   # stays on the same line


def test_progress_done_appends_newline(monkeypatch):
    fake = FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake)
    core.progress("final", done=True)
    assert fake.text.endswith("\n")


def test_progress_done_clears_line(monkeypatch):
    fake = FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake)
    core.progress_done()
    assert fake.text == "\r\033[K"


def test_progress_done_silent_on_non_tty(capsys):
    core.progress_done()
    assert capsys.readouterr().out == ""
