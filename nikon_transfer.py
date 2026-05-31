#!/usr/bin/env python3
"""
Compatibility entry-point — delegates to the nikon_transfer package.

Usage (without installing):
    python3 nikon_transfer.py [options]

See README.md for full documentation.
"""

from nikon_transfer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
