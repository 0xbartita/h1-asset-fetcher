"""Platform-agnostic core: logging, identifier resolution, output writing."""
import threading

_print_lock = threading.Lock()

_COLORS = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m",
           "ERR": "\033[91m", "STEP": "\033[96m"}


def log(msg, level="INFO"):
    """Coloured, thread-safe logger (shared by core + platform plugins)."""
    with _print_lock:
        print(f"{_COLORS.get(level, '')}[{level}]\033[0m {msg}")
