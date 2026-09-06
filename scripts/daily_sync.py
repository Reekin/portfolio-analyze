#!/usr/bin/env python3
"""Run a bounded daily IBKR sync without a saved-period probe."""

from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


def main() -> int:
    end = date.today() - timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=7)
    analyzer = Path(__file__).with_name("ibkr_analyzer.py")
    result = subprocess.run(
        [
            sys.executable,
            str(analyzer),
            "sync",
            "--from",
            start.isoformat(),
            "--to",
            end.isoformat(),
            "--chunk-days",
            "8",
        ],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
