#!/usr/bin/env python3
"""Synchronize adjusted benchmark prices into a small local SQLite database."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SYMBOLS = ("SPY", "QQQ")
DEFAULT_START = date(2020, 5, 1)
DEFAULT_DB = Path(os.environ.get("APPDATA", "")) / "portfolio-analyze" / "benchmarks.sqlite3"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS benchmark_prices (
            symbol TEXT NOT NULL,
            day TEXT NOT NULL,
            close REAL NOT NULL,
            adjusted_close REAL NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (symbol, day)
        );
        CREATE INDEX IF NOT EXISTS idx_benchmark_prices_day
            ON benchmark_prices(day, symbol);
        """
    )
    return conn


def timestamp(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())


def fetch_prices(symbol: str, start: date, end: date) -> list[tuple[str, float, float]]:
    params = urlencode(
        {
            "period1": timestamp(start),
            "period2": timestamp(end + timedelta(days=2)),
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        }
    )
    request = Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{params}",
        headers={"User-Agent": "Mozilla/5.0 PortfolioAnalyze/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose") or []
    rows: list[tuple[str, float, float]] = []
    for index, unix_time in enumerate(timestamps):
        close = quote["close"][index] if index < len(quote["close"]) else None
        adjusted_close = adjusted[index] if index < len(adjusted) else None
        if close is None or adjusted_close is None:
            continue
        day = datetime.fromtimestamp(unix_time, timezone.utc).date().isoformat()
        rows.append((day, float(close), float(adjusted_close)))
    return rows


def sync(path: Path, requested_start: date | None, requested_end: date | None) -> dict[str, object]:
    end = requested_end or date.today()
    fetched_at = datetime.now(timezone.utc).isoformat()
    result: dict[str, object] = {"ok": True, "database": str(path), "symbols": {}}
    with connect(path) as conn:
        for symbol in SYMBOLS:
            last_row = conn.execute(
                "SELECT max(day) day FROM benchmark_prices WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            last = date.fromisoformat(last_row["day"]) if last_row and last_row["day"] else None
            start = requested_start or (last - timedelta(days=10) if last else DEFAULT_START)
            rows = fetch_prices(symbol, start, end)
            conn.executemany(
                """
                INSERT INTO benchmark_prices
                    (symbol, day, close, adjusted_close, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, day) DO UPDATE SET
                    close = excluded.close,
                    adjusted_close = excluded.adjusted_close,
                    fetched_at = excluded.fetched_at
                """,
                [(symbol, day, close, adjusted_close, fetched_at) for day, close, adjusted_close in rows],
            )
            result["symbols"][symbol] = {
                "from": rows[0][0] if rows else None,
                "to": rows[-1][0] if rows else None,
                "rows": len(rows),
            }
    return result


def status(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"ok": False, "database": str(path), "symbols": {}}
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT symbol, min(day) first_day, max(day) last_day, count(*) rows
            FROM benchmark_prices
            GROUP BY symbol
            ORDER BY symbol
            """
        ).fetchall()
    return {
        "ok": True,
        "database": str(path),
        "symbols": {
            row["symbol"]: {
                "from": row["first_day"],
                "to": row["last_day"],
                "rows": row["rows"],
            }
            for row in rows
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--from", dest="from_date", type=date.fromisoformat)
    sync_parser.add_argument("--to", dest="to_date", type=date.fromisoformat)
    subparsers.add_parser("status")
    args = parser.parse_args()
    payload = (
        sync(args.database, args.from_date, args.to_date)
        if args.command == "sync"
        else status(args.database)
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
