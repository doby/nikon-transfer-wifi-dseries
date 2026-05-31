#!/usr/bin/env python3
"""GUI entry-point used by PyInstaller to build the standalone macOS .app."""

from nikon_transfer.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
