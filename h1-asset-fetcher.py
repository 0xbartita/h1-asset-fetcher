#!/usr/bin/env python3
"""Backward-compat shim. The implementation now lives in the h1_asset_fetcher
package. `python3 h1-asset-fetcher.py ...`, `python3 -m h1_asset_fetcher ...`,
and the installed `h1-asset-fetcher` command are all equivalent."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from h1_asset_fetcher.cli import main

if __name__ == "__main__":
    main()
