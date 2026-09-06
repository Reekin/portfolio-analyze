#!/usr/bin/env python3
"""Idempotent Windows launcher for the API and Vite frontend."""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_process(command: list[str], log_name: str) -> None:
    LOGS.mkdir(exist_ok=True)
    with (LOGS / log_name).open("a", encoding="utf-8") as log:
        subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not (ROOT / "node_modules").exists():
        subprocess.run(["npm.cmd", "install"], cwd=ROOT, check=True)
    if not port_open(8787):
        start_process([sys.executable, "backend/server.py"], "api.log")
    if not port_open(4173):
        start_process(["npm.cmd", "run", "dev"], "web.log")

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if port_open(8787) and port_open(4173):
            if not args.no_browser:
                webbrowser.open("http://127.0.0.1:4173")
            return
        time.sleep(0.25)
    raise SystemExit("Portfolio Analyze did not start. Check logs/api.log and logs/web.log.")


if __name__ == "__main__":
    main()
