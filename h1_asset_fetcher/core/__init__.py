"""Platform-agnostic core: logging, identifier resolution, output writing."""
import sys
import threading

_print_lock = threading.Lock()

_COLORS = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m",
           "ERR": "\033[91m", "STEP": "\033[96m"}


def log(msg, level="INFO"):
    """Coloured, thread-safe logger (shared by core + platform plugins)."""
    with _print_lock:
        print(f"{_COLORS.get(level, '')}[{level}]\033[0m {msg}")


def progress(msg, level="STEP", done=False):
    """A single status line that rewrites itself in place, so long loops (e.g.
    paginated API fetches) don't scroll a wall of near-identical lines. No-op
    when stdout isn't a TTY (piped/redirected) — callers should still emit a
    final log() summary, and call progress_done() to release the line first."""
    out = sys.stdout
    if not out.isatty():
        return
    end = "\n" if done else ""
    with _print_lock:
        # \r → column 0, \033[K → clear stale chars to end of line.
        out.write(f"\r{_COLORS.get(level, '')}[{level}]\033[0m {msg}\033[K{end}")
        out.flush()


def progress_done():
    """Erase the active in-place progress line (call before a normal log())."""
    out = sys.stdout
    if not out.isatty():
        return
    with _print_lock:
        out.write("\r\033[K")
        out.flush()
