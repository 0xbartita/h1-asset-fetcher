"""h1-asset-fetcher — fetch, download, and decompile mobile/exe assets from
bug bounty programs (HackerOne, Bugcrowd, Intigriti, YesWeHack, Immunefi)."""
import warnings as _warnings

# `requests` emits a noisy RequestsDependencyWarning when the installed
# urllib3 / chardet / charset_normalizer versions fall outside its supported
# range. It's harmless for our basic GET/POST usage, so silence it here —
# before any submodule imports requests — to keep the CLI/wizard output clean.
_warnings.filterwarnings("ignore", message=r".*doesn't match a supported version.*")

from .__version__ import __version__

__all__ = ["__version__"]
