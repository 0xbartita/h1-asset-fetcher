"""Single source of truth for the version is pyproject.toml; read it from the
installed package metadata so there's only one place to bump."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("h1-asset-fetcher")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+dev"
