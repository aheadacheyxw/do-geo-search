#!/usr/bin/env python3
"""Repository-local entry point for the brand-neutral GEO toolkit."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo_monitoring.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
