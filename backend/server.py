#!/usr/bin/env python3
"""Read-only analytics API for the local IBKR analyzer database."""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import sqlite3
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEFAULT_PORT = 8787
DEFAULT_DB = Path(os.environ.get("APPDATA", "")) / "ibkr-analyzer" / "records.sqlite3"
DEFAULT_BENCHMARK_DB = (
    Path(os.environ.get("APPDATA", "")) / "portfolio-analyze" / "benchmarks.sqlite3"
)
DEFAULT_ENV = (
    Path(os.environ.get("USERPROFILE", ""))
    / ".codex"
    / "skills"
    / "ibkr-analyzer"
    / "scripts"
    / ".env"
)
DASHBOARD_CACHE: dict[tuple[object, ...], dict[str, object]] = {}
DASHBOARD_CACHE_LOCK = threading.Lock()


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def query_ids() -> list[str]:
    config_path = Path(os.environ.get("IBKR_ANALYZER_ENV", DEFAULT_ENV))
    raw = os.environ.get("IBKR_QUERY_IDS") or parse_env(config_path).get("IBKR_QUERY_IDS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def primary_query_ids() -> list[str]:
    ids = query_ids()
    return ids[:1]


def performance_query_ids() -> list[str]:
    return query_ids()[1:]


def connect() -> sqlite3.Connection:
    path = Path(os.environ.get("IBKR_ANALYZER_DB", DEFAULT_DB))
    if not path.exists():
        raise FileNotFoundError(f"IBKR database not found: {path}")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def compact(value: date) -> str:
    return value.strftime("%Y%m%d")


def iso_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value[:8]
    if len(value) != 8 or not value.isdigit():
        return value
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def to_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def scope_clause(alias: str = "p") -> tuple[str, list[str]]:
    ids = primary_query_ids()
    if not ids:
        return "", []
    marks = ",".join("?" for _ in ids)
    return f"{alias}.query_id IN ({marks}) AND {alias}.requested_from IS NOT NULL", ids


def fx_table(
    conn: sqlite3.Connection, start: date, end: date
) -> dict[str, list[tuple[str, float]]]:
    clause, params = scope_clause("p")
    rows = conn.execute(
        f"""
        SELECT json_extract(r.data_json, '$.fromCurrency') currency,
               r.report_date day,
               CAST(json_extract(r.data_json, '$.rate') AS REAL) rate
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'ConversionRate'
          AND r.report_date BETWEEN ? AND ?
        ORDER BY currency, day
        """,
        [*params, compact(start - timedelta(days=10)), compact(end)],
    )
    result: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        if row["currency"] and row["day"] and row["rate"]:
            result[row["currency"]].append((row["day"], to_float(row["rate"])))
    return result


def fx_rate(rates: dict[str, list[tuple[str, float]]], currency: str | None, day: str) -> float:
    if not currency or currency in {"USD", "BASE_SUMMARY"}:
        return 1.0
    values = rates.get(currency)
    if not values:
        return 1.0
    days = [item[0] for item in values]
    index = bisect.bisect_right(days, day) - 1
    return values[max(0, index)][1]


def database_bounds(conn: sqlite3.Connection) -> tuple[date, date]:
    clause, params = scope_clause()
    row = conn.execute(
        f"SELECT min(requested_from) first_date, max(requested_to) last_date FROM reports p WHERE {clause}",
        params,
    ).fetchone()
    if not row or not row["first_date"] or not row["last_date"]:
        raise RuntimeError("No dated IBKR reports are available.")
    return date.fromisoformat(row["first_date"]), date.fromisoformat(row["last_date"])


def subtract_months(value: date, months: int) -> date:
    year = value.year
    month = value.month - months
    while month <= 0:
        year -= 1
        month += 12
    days = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(value.day, days[month - 1]))


def resolve_range(
    conn: sqlite3.Connection,
    preset: str,
    custom_from: str | None,
    custom_to: str | None,
) -> tuple[date, date]:
    first, latest = database_bounds(conn)
    if preset == "CUSTOM":
        if not custom_from or not custom_to:
            raise ValueError("CUSTOM requires from and to.")
        start, end = date.fromisoformat(custom_from), date.fromisoformat(custom_to)
    elif preset == "1W":
        start, end = latest - timedelta(days=6), latest
    elif preset == "MTD":
        start, end = latest.replace(day=1), latest
    elif preset == "1M":
        start, end = subtract_months(latest, 1), latest
    elif preset == "3M":
        start, end = subtract_months(latest, 3), latest
    elif preset == "YTD":
        start, end = latest.replace(month=1, day=1), latest
    elif preset == "1Y":
        start, end = latest - timedelta(days=364), latest
    else:
        start, end = first, latest
    return max(start, first), min(end, latest)


def account_condition(account: str, alias: str = "r") -> tuple[str, list[str]]:
    if not account or account == "ALL":
        return "", []
    return f" AND {alias}.account_id = ?", [account]


def security_transfer_flows(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    account: str,
    rates: dict[str, list[tuple[str, float]]],
) -> dict[str, float]:
    clause, params = scope_clause("p")
    account_sql, account_params = account_condition(account)
    transfer_rows = conn.execute(
        f"""
        SELECT r.account_id,
               r.event_date day,
               r.currency,
               CAST(json_extract(r.data_json, '$.positionAmount') AS REAL) amount
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'Transfer'
          AND r.event_date BETWEEN ? AND ?
          {account_sql}
        """,
        [*params, compact(start), compact(end), *account_params],
    )
    lot_rows = conn.execute(
        f"""
        SELECT r.account_id,
               r.event_date day,
               json_extract(r.data_json, '$.direction') direction,
               r.currency,
               CAST(json_extract(r.data_json, '$.quantity') AS REAL) quantity,
               CAST(json_extract(r.data_json, '$.transferPrice') AS REAL) price
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'TransferLot'
          AND r.event_date BETWEEN ? AND ?
          {account_sql}
        """,
        [*params, compact(start), compact(end), *account_params],
    )

    by_day: dict[str, float] = defaultdict(float)
    detailed_keys: set[tuple[str, str]] = set()
    for row in transfer_rows:
        amount = to_float(row["amount"])
        if abs(amount) < 1e-9:
            continue
        key = (row["account_id"], row["day"])
        detailed_keys.add(key)
        by_day[iso_date(row["day"]) or ""] += amount * fx_rate(
            rates, row["currency"], row["day"]
        )

    for row in lot_rows:
        key = (row["account_id"], row["day"])
        if key in detailed_keys:
            continue
        direction = 1 if row["direction"] == "IN" else -1
        amount = direction * to_float(row["quantity"]) * to_float(row["price"])
        by_day[iso_date(row["day"]) or ""] += amount * fx_rate(
            rates, row["currency"], row["day"]
        )
    return dict(by_day)


def external_flows(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    account: str,
    rates: dict[str, list[tuple[str, float]]],
) -> dict[str, float]:
    clause, params = scope_clause("p")
    account_sql, account_params = account_condition(account)
    rows = conn.execute(
        f"""
        SELECT r.statement_date day,
               sum(
                   CAST(json_extract(r.data_json, '$.deposits') AS REAL)
                   + CAST(json_extract(r.data_json, '$.withdrawals') AS REAL)
                   + coalesce(CAST(json_extract(r.data_json, '$.accountTransfers') AS REAL), 0)
               ) amount
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'CashReportCurrency'
          AND json_extract(r.data_json, '$.currency') = 'BASE_SUMMARY'
          AND r.statement_date BETWEEN ? AND ?
          {account_sql}
        GROUP BY day
        """,
        [*params, compact(start), compact(end), *account_params],
    )
    result = {iso_date(row["day"]) or "": to_float(row["amount"]) for row in rows}
    for day, amount in security_transfer_flows(conn, start, end, account, rates).items():
        result[day] = result.get(day, 0.0) + amount
    return result


def performance_series(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    account: str,
    rates: dict[str, list[tuple[str, float]]],
) -> tuple[list[dict[str, float | str | None]], dict[str, float | None]]:
    clause, params = scope_clause("p")
    account_sql, account_params = account_condition(account)
    equity = conn.execute(
        f"""
        SELECT day, sum(value) value
        FROM (
            SELECT r.report_date day,
                   r.account_id,
                   max(CAST(json_extract(r.data_json, '$.total') AS REAL)) value
            FROM records r JOIN reports p ON p.id = r.report_id
            WHERE {clause}
              AND r.section = 'EquitySummaryByReportDateInBase'
              AND r.report_date BETWEEN ? AND ?
              {account_sql}
            GROUP BY r.report_date, r.account_id
        )
        GROUP BY day
        """,
        [*params, compact(start), compact(end), *account_params],
    )
    values = {
        iso_date(row["day"]) or "": to_float(row["value"])
        for row in equity
    }
    flows = external_flows(conn, start, end, account, rates)
    ordered = [(date.fromisoformat(day), value) for day, value in sorted(values.items()) if value]
    if not ordered:
        return [], {"simple": None, "twr": None, "mwr": None}

    initial = ordered[0][1]
    origin = ordered[0][0]
    twr_factor = 1.0
    previous = initial
    flow_history: list[tuple[date, float]] = []
    result: list[dict[str, float | str | None]] = []
    for index, (day, nlv) in enumerate(ordered):
        flow = flows.get(day.isoformat(), 0.0)
        if index:
            if previous:
                twr_factor *= 1 + ((nlv - flow - previous) / previous)
            if flow:
                flow_history.append((day, flow))
        simple = ((nlv / initial) - 1) if initial else 0
        elapsed = max(1, (day - origin).days)
        weighted_flows = sum(
            amount * ((day - flow_day).days / elapsed) for flow_day, amount in flow_history
        )
        denominator = initial + weighted_flows
        dietz = (
            (nlv - initial - sum(amount for _, amount in flow_history)) / denominator
            if denominator
            else None
        )
        result.append(
            {
                "date": day.isoformat(),
                "nlv": round(nlv, 2),
                "flow": round(flow, 2),
                "simple": round(simple * 100, 4),
                "twr": round((twr_factor - 1) * 100, 4),
                "mwr": round(dietz * 100, 4) if dietz is not None else None,
            }
        )
        previous = nlv
    last = result[-1]
    return result, {
        "simple": last["simple"],
        "twr": last["twr"],
        "mwr": last["mwr"],
    }


def security_map(conn: sqlite3.Connection) -> dict[str, str]:
    clause, params = scope_clause("p")
    rows = conn.execute(
        f"""
        SELECT r.symbol,
               max(nullif(r.underlying_symbol, '')) underlying
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause} AND r.section = 'SecurityInfo'
        GROUP BY symbol
        """,
        params,
    )
    return {row["symbol"]: row["underlying"] or row["symbol"] for row in rows if row["symbol"]}


def underlying_symbol(symbol: str, explicit: str | None, mapping: dict[str, str]) -> str:
    if explicit:
        return explicit
    mapped = mapping.get(symbol)
    if mapped and mapped != symbol:
        return mapped
    return symbol.split()[0]


def holdings(
    conn: sqlite3.Connection,
    end: date,
    account: str,
    mapping: dict[str, str],
    rates: dict[str, list[tuple[str, float]]],
) -> tuple[str | None, list[dict[str, object]]]:
    clause, params = scope_clause("p")
    account_sql, account_params = account_condition(account)
    latest_row = conn.execute(
        f"""
        SELECT max(r.statement_date) day
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'OpenPosition'
          AND r.statement_date <= ?
          {account_sql}
        """,
        [*params, compact(end), *account_params],
    ).fetchone()
    latest = latest_row["day"] if latest_row else None
    if not latest:
        return None, []
    rows = conn.execute(
        f"""
        SELECT r.account_id,
               r.symbol,
               json_extract(r.data_json, '$.assetCategory') asset_class,
               r.currency,
               sum(CAST(json_extract(r.data_json, '$.position') AS REAL)) quantity,
               sum(CAST(json_extract(r.data_json, '$.positionValue') AS REAL)) market_value,
               sum(CAST(json_extract(r.data_json, '$.costBasisMoney') AS REAL)) cost_basis,
               sum(CAST(json_extract(r.data_json, '$.fifoPnlUnrealized') AS REAL)) unrealized,
               max(CAST(json_extract(r.data_json, '$.markPrice') AS REAL)) mark_price
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'OpenPosition'
          AND coalesce(json_extract(r.data_json, '$.percentOfNAV'), '') <> ''
          AND r.statement_date = ?
          {account_sql}
        GROUP BY r.account_id, symbol, asset_class, currency
        ORDER BY abs(market_value) DESC
        """,
        [*params, latest, *account_params],
    )
    data = []
    for row in rows:
        rate = fx_rate(rates, row["currency"], latest)
        value = to_float(row["market_value"]) * rate
        cost = to_float(row["cost_basis"]) * rate
        unrealized = to_float(row["unrealized"]) * rate
        data.append(
            {
                "account": row["account_id"],
                "symbol": row["symbol"],
                "underlying": underlying_symbol(row["symbol"], None, mapping),
                "assetClass": row["asset_class"],
                "currency": row["currency"],
                "quantity": round(to_float(row["quantity"]), 6),
                "markPrice": round(to_float(row["mark_price"]), 6),
                "marketValue": round(value, 2),
                "costBasis": round(cost, 2),
                "unrealized": round(unrealized, 2),
                "unrealizedPercent": round((unrealized / abs(cost)) * 100, 2)
                if cost
                else 0,
            }
        )
    return iso_date(latest), data


def closed_trades(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    account: str,
    mapping: dict[str, str],
    rates: dict[str, list[tuple[str, float]]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    clause, params = scope_clause("p")
    account_sql, account_params = account_condition(account)
    rows = conn.execute(
        f"""
        SELECT r.account_id,
               r.symbol,
               r.underlying_symbol underlying,
               r.trade_date day,
               r.currency,
               CAST(json_extract(r.data_json, '$.fifoPnlRealized') AS REAL) pnl
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'Lot'
          AND r.trade_date BETWEEN ? AND ?
          {account_sql}
        """,
        [*params, compact(start), compact(end), *account_params],
    )
    by_underlying: dict[str, dict[str, object]] = {}
    by_contract: dict[str, dict[str, object]] = {}
    daily: dict[tuple[str, str], float] = defaultdict(float)

    def add(grouped: dict[str, dict[str, object]], key: str, row: sqlite3.Row, pnl: float) -> None:
        item = grouped.setdefault(
            key,
            {
                "symbol": key,
                "realized": 0.0,
                "closedLots": 0,
                "firstClose": None,
                "lastClose": None,
            },
        )
        item["realized"] = to_float(item["realized"]) + pnl
        item["closedLots"] = int(item["closedLots"]) + 1
        day = iso_date(row["day"])
        if day:
            item["firstClose"] = min(filter(None, [item["firstClose"], day]), default=day)
            item["lastClose"] = max(filter(None, [item["lastClose"], day]), default=day)

    for row in rows:
        symbol = row["symbol"] or "UNKNOWN"
        underlying = underlying_symbol(symbol, row["underlying"], mapping)
        pnl = to_float(row["pnl"]) * fx_rate(
            rates, row["currency"], row["day"]
        )
        day = iso_date(row["day"])
        add(by_underlying, underlying, row, pnl)
        add(by_contract, symbol, row, pnl)
        if day:
            daily[(day, underlying)] += pnl

    def finish(grouped: dict[str, dict[str, object]]) -> list[dict[str, object]]:
        data = list(grouped.values())
        for item in data:
            item["realized"] = round(to_float(item["realized"]), 2)
        return sorted(data, key=lambda item: abs(to_float(item["realized"])), reverse=True)

    daily_data = [
        {"date": day, "symbol": symbol, "realized": round(realized, 2)}
        for (day, symbol), realized in sorted(daily.items())
    ]
    return finish(by_underlying), finish(by_contract), daily_data


def money_breakdown(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    account: str,
    rates: dict[str, list[tuple[str, float]]],
) -> dict[str, object]:
    clause, params = scope_clause("p")
    account_sql, account_params = account_condition(account)
    cash_rows = conn.execute(
        f"""
        SELECT r.event_date day,
               json_extract(r.data_json, '$.type') type,
               r.symbol,
               r.currency,
               sum(CAST(json_extract(r.data_json, '$.amount') AS REAL)) amount
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'CashTransaction'
          AND r.event_date BETWEEN ? AND ?
          {account_sql}
        GROUP BY day, type, currency, r.symbol
        ORDER BY day
        """,
        [*params, compact(start), compact(end), *account_params],
    )
    flow_rows = conn.execute(
        f"""
        SELECT r.statement_date day,
               sum(CAST(json_extract(r.data_json, '$.deposits') AS REAL))
                   + max(0, sum(coalesce(CAST(json_extract(r.data_json, '$.accountTransfers') AS REAL), 0))) deposits,
               sum(CAST(json_extract(r.data_json, '$.withdrawals') AS REAL))
                   + min(0, sum(coalesce(CAST(json_extract(r.data_json, '$.accountTransfers') AS REAL), 0))) withdrawals
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'CashReportCurrency'
          AND json_extract(r.data_json, '$.currency') = 'BASE_SUMMARY'
          AND r.statement_date BETWEEN ? AND ?
          {account_sql}
        GROUP BY day
        """,
        [*params, compact(start), compact(end), *account_params],
    )
    commission_rows = conn.execute(
        f"""
        SELECT r.event_date day,
               r.currency,
               -sum(CAST(json_extract(r.data_json, '$.totalCommission') AS REAL)) amount
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'UnbundledCommissionDetail'
          AND r.event_date BETWEEN ? AND ?
          {account_sql}
        GROUP BY day, currency
        """,
        [*params, compact(start), compact(end), *account_params],
    )
    tax_rows = conn.execute(
        f"""
        SELECT r.event_date day,
               r.currency,
               sum(abs(CAST(coalesce(json_extract(r.data_json, '$.taxAmount'), '0') AS REAL))) amount
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'TransactionTaxDetail'
          AND r.event_date BETWEEN ? AND ?
          {account_sql}
        GROUP BY day, currency
        """,
        [*params, compact(start), compact(end), *account_params],
    )

    totals = defaultdict(float)
    dividend_rows = defaultdict(float)
    daily: dict[str, dict[str, float | str]] = {}

    def point(day: str) -> dict[str, float | str]:
        iso = iso_date(day) or day
        return daily.setdefault(
            iso,
            {
                "date": iso,
                "deposits": 0.0,
                "withdrawals": 0.0,
                "securityTransfers": 0.0,
                "dividends": 0.0,
                "interestIncome": 0.0,
                "income": 0.0,
                "commissions": 0.0,
                "financing": 0.0,
                "fees": 0.0,
                "taxes": 0.0,
            },
        )

    for row in cash_rows:
        kind = row["type"] or "Other"
        amount = to_float(row["amount"]) * fx_rate(rates, row["currency"], row["day"])
        entry = point(row["day"])
        if kind in {"Dividends", "Payment In Lieu Of Dividends"}:
            dividend_rows[(iso_date(row["day"]), row["symbol"] or "未标注标的")] += amount
            entry["dividends"] = to_float(entry["dividends"]) + amount
            entry["income"] = to_float(entry["income"]) + amount
            totals["income"] += amount
            totals[kind] += amount
        elif kind == "Broker Interest Received":
            entry["interestIncome"] = to_float(entry["interestIncome"]) + amount
            entry["income"] = to_float(entry["income"]) + amount
            totals["income"] += amount
            totals[kind] += amount
        elif kind == "Broker Interest Paid":
            entry["financing"] = to_float(entry["financing"]) + abs(amount)
            entry["fees"] = to_float(entry["fees"]) + abs(amount)
            totals["interestPaid"] += abs(amount)
        elif kind == "Withholding Tax":
            entry["taxes"] = to_float(entry["taxes"]) + abs(amount)
            totals["withholdingTax"] += abs(amount)
        elif kind == "Other Fees":
            entry["financing"] = to_float(entry["financing"]) + max(0, -amount)
            entry["fees"] = to_float(entry["fees"]) + max(0, -amount)
            totals["otherFees"] += max(0, -amount)

    for row in flow_rows:
        deposits = max(0, to_float(row["deposits"]))
        withdrawals = abs(min(0, to_float(row["withdrawals"])))
        entry = point(row["day"])
        entry["deposits"] = to_float(entry["deposits"]) + deposits
        entry["withdrawals"] = to_float(entry["withdrawals"]) + withdrawals
        totals["deposits"] += deposits
        totals["withdrawals"] += withdrawals

    for day, amount in security_transfer_flows(conn, start, end, account, rates).items():
        point(day)["securityTransfers"] = amount
        totals["securityTransfers"] += amount

    commission_total = 0.0
    for row in commission_rows:
        amount = to_float(row["amount"]) * fx_rate(rates, row["currency"], row["day"])
        entry = point(row["day"])
        entry["commissions"] = to_float(entry["commissions"]) + amount
        entry["fees"] = to_float(entry["fees"]) + amount
        commission_total += amount
    transaction_tax = 0.0
    for row in tax_rows:
        amount = to_float(row["amount"]) * fx_rate(rates, row["currency"], row["day"])
        point(row["day"])["taxes"] = to_float(point(row["day"])["taxes"]) + amount
        transaction_tax += amount

    totals["commissions"] = commission_total
    totals["transactionTax"] = transaction_tax
    totals["fees"] = commission_total + totals["interestPaid"] + totals["otherFees"]
    totals["taxes"] = totals["withholdingTax"] + transaction_tax
    return {
        "totals": {key: round(value, 2) for key, value in totals.items()},
        "dividendsBySymbol": [
            {"date": day, "symbol": symbol, "amount": amount}
            for (day, symbol), amount in sorted(dividend_rows.items())
        ],
        "daily": list(sorted(daily.values(), key=lambda item: str(item["date"]))),
    }


def mtm_breakdown(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    account: str,
) -> dict[str, object]:
    clause, params = scope_clause("p")
    account_sql, account_params = account_condition(account)
    row = conn.execute(
        f"""
        SELECT min(nullif(r.report_date, '')) first_day,
               max(nullif(r.report_date, '')) last_day,
               count(DISTINCT nullif(r.report_date, '')) days,
               sum(CAST(json_extract(r.data_json, '$.priorOpenMtm') AS REAL)) prior_open,
               sum(CAST(json_extract(r.data_json, '$.transactionMtm') AS REAL)) transactions,
               sum(CAST(json_extract(r.data_json, '$.commissions') AS REAL)) commissions,
               sum(CAST(json_extract(r.data_json, '$.otherWithAccruals') AS REAL)) other,
               sum(CAST(json_extract(r.data_json, '$.totalWithAccruals') AS REAL)) reported_total
        FROM records r JOIN reports p ON p.id = r.report_id
        WHERE {clause}
          AND r.section = 'MTMPerformanceSummaryUnderlying'
          AND nullif(r.report_date, '') BETWEEN ? AND ?
          {account_sql}
        """,
        [*params, compact(start), compact(end), *account_params],
    ).fetchone()
    prior_open = to_float(row["prior_open"]) if row else 0
    transactions = to_float(row["transactions"]) if row else 0
    commissions = to_float(row["commissions"]) if row else 0
    other = to_float(row["other"]) if row else 0
    reported_total = to_float(row["reported_total"]) if row else 0
    component_total = prior_open + transactions + commissions + other
    return {
        "coverageFrom": iso_date(row["first_day"]) if row else None,
        "coverageTo": iso_date(row["last_day"]) if row else None,
        "days": int(row["days"] or 0) if row else 0,
        "priorOpen": prior_open,
        "transactions": transactions,
        "commissions": round(commissions, 2),
        "otherWithAccruals": round(other, 2),
        "reportedTotal": round(reported_total, 2),
        "componentTotal": round(component_total, 2),
        "difference": round(component_total - reported_total, 6),
    }


def income_components(
    conn: sqlite3.Connection, start: date, end: date, account: str,
    mtm: dict[str, object],
) -> list[dict[str, object]]:
    clause, params = scope_clause('p')
    account_sql, account_params = account_condition(account)
    fields = ('commissions', 'transactionTax', 'dividends', 'paymentInLieu',
              'withholdingTax', 'brokerInterest', 'otherFees')
    columns = ', '.join(
        f"sum(CAST(json_extract(r.data_json, '$.{field}') AS REAL)) {field}"
        for field in fields
    )
    cash = conn.execute(f"""
        SELECT {columns} FROM records r JOIN reports p ON p.id=r.report_id
        WHERE {clause} AND r.section='CashReportCurrency'
          AND r.currency='BASE_SUMMARY' AND r.statement_date BETWEEN ? AND ?
          {account_sql}
    """, [*params, compact(start), compact(end), *account_params]).fetchone()

    def accruals(day: date) -> tuple[float, float]:
        row = conn.execute(f"""
            WITH snapshots AS (
                SELECT r.data_json, row_number() OVER (
                    PARTITION BY r.account_id ORDER BY r.report_date DESC,
                    p.fetched_at DESC, r.id DESC
                ) rank
                FROM records r JOIN reports p ON p.id=r.report_id
                WHERE {clause} AND r.section='EquitySummaryByReportDateInBase'
                  AND r.report_date <= ? {account_sql}
            )
            SELECT sum(CAST(json_extract(data_json,'$.interestAccruals') AS REAL)) interest,
                   sum(CAST(json_extract(data_json,'$.dividendAccruals') AS REAL)) dividend
            FROM snapshots WHERE rank=1
        """, [*params, compact(day), *account_params]).fetchone()
        return to_float(row['interest']), to_float(row['dividend'])

    opening = accruals(start - timedelta(days=1))
    ending = accruals(end)
    return [
        {'label': '持仓 MTM', 'value': mtm['priorOpen']},
        {'label': '交易 MTM', 'value': mtm['transactions']},
        {'label': '交易佣金', 'value': to_float(cash['commissions'])},
        {'label': '分红及代息股息', 'value': to_float(cash['dividends']) + to_float(cash['paymentInLieu'])},
        {'label': '净利息', 'value': to_float(cash['brokerInterest'])},
        {'label': '税费', 'value': to_float(cash['transactionTax']) + to_float(cash['withholdingTax'])},
        {'label': '其他费用', 'value': to_float(cash['otherFees'])},
        {'label': '应计利息变动', 'value': ending[0] - opening[0]},
        {'label': '应计股息变动', 'value': ending[1] - opening[1]},
    ]


def add_benchmarks(
    series: list[dict[str, float | str | None]],
) -> dict[str, float | None]:
    path = Path(os.environ.get("PORTFOLIO_BENCHMARK_DB", DEFAULT_BENCHMARK_DB))
    if not series or not path.exists():
        return {"spy": None, "qqq": None}
    first_day = date.fromisoformat(str(series[0]["date"]))
    last_day = date.fromisoformat(str(series[-1]["date"]))
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT symbol, day, adjusted_close
            FROM benchmark_prices
            WHERE day BETWEEN ? AND ?
              AND symbol IN ('SPY', 'QQQ')
            ORDER BY symbol, day
            """,
            ((first_day - timedelta(days=10)).isoformat(), last_day.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    by_symbol: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        by_symbol[row["symbol"]].append((row["day"], to_float(row["adjusted_close"])))

    results: dict[str, float | None] = {}
    for symbol in ("SPY", "QQQ"):
        prices = by_symbol.get(symbol, [])
        if not prices:
            results[symbol.lower()] = None
            continue
        price_index = 0
        current_price: float | None = None
        while price_index < len(prices) and prices[price_index][0] <= first_day.isoformat():
            current_price = prices[price_index][1]
            price_index += 1
        if current_price is None:
            current_price = prices[0][1]
        baseline = current_price
        last_return = 0.0
        for point in series:
            point_day = str(point["date"])
            while price_index < len(prices) and prices[price_index][0] <= point_day:
                current_price = prices[price_index][1]
                price_index += 1
            last_return = ((current_price / baseline) - 1) * 100 if baseline else 0.0
            point[symbol.lower()] = round(last_return, 4)
        results[symbol.lower()] = round(last_return, 4)
    return results


def downsample(data: list[dict[str, object]], target: int = 320) -> list[dict[str, object]]:
    if len(data) <= target:
        return data
    step = math.ceil(len(data) / target)
    sampled = data[::step]
    if sampled[-1] is not data[-1]:
        sampled.append(data[-1])
    return sampled


def dashboard_payload(params: dict[str, list[str]]) -> dict[str, object]:
    database_mtime = DEFAULT_DB.stat().st_mtime_ns
    benchmark_path = Path(os.environ.get("PORTFOLIO_BENCHMARK_DB", DEFAULT_BENCHMARK_DB))
    benchmark_mtime = benchmark_path.stat().st_mtime_ns if benchmark_path.exists() else 0
    cache_key = (
        database_mtime,
        benchmark_mtime,
        tuple(sorted((key, tuple(values)) for key, values in params.items())),
    )
    with DASHBOARD_CACHE_LOCK:
        cached = DASHBOARD_CACHE.get(cache_key)
    if cached is not None:
        return cached

    preset = params.get("range", ["1Y"])[0].upper()
    method = params.get("method", ["TWR"])[0].upper()
    account = params.get("account", ["ALL"])[0]
    with connect() as conn:
        start, end = resolve_range(
            conn,
            preset,
            params.get("from", [None])[0],
            params.get("to", [None])[0],
        )
        mapping = security_map(conn)
        rates = fx_table(conn, start, end)
        series, returns = performance_series(conn, start, end, account, rates)
        benchmark_returns = add_benchmarks(series)
        position_date, position_rows = holdings(conn, end, account, mapping, rates)
        closed, closed_contracts, closed_daily = closed_trades(
            conn, start, end, account, mapping, rates
        )
        money = money_breakdown(conn, start, end, account, rates)
        mtm = mtm_breakdown(conn, start, end, account)
        selected_return = returns.get(method.lower())
        first_nlv = to_float(series[0]["nlv"]) if series else 0
        latest_nlv = to_float(series[-1]["nlv"]) if series else 0
        realized = sum(to_float(item["realized"]) for item in closed)
        unrealized = sum(to_float(item["unrealized"]) for item in position_rows)
        commissions = to_float(money["totals"].get("commissions"))
        financing = to_float(money["totals"].get("interestPaid")) + to_float(
            money["totals"].get("otherFees")
        )
        starting_position_rows: list[dict[str, object]] = []
        if series:
            _, starting_position_rows = holdings(
                conn,
                date.fromisoformat(str(series[0]["date"])),
                account,
                mapping,
                rates,
            )
        starting_unrealized = sum(
            to_float(item["unrealized"]) for item in starting_position_rows
        )
        unrealized_change = unrealized - starting_unrealized
        cash_net_contributions = (
            to_float(money["totals"].get("deposits"))
            - to_float(money["totals"].get("withdrawals"))
        )
        security_net_transfers = to_float(money["totals"].get("securityTransfers"))
        pnl_components = income_components(conn, start, end, account, mtm)
        total_pnl = round(sum(item['value'] for item in pnl_components), 2)
        elapsed_days = (
            date.fromisoformat(str(series[-1]['date']))
            - date.fromisoformat(str(series[0]['date']))
        ).days if series else 0
        annualized_return = None
        if elapsed_days > 0 and selected_return is not None and selected_return >= -100:
            try:
                value = ((1 + selected_return / 100) ** (365.25 / elapsed_days) - 1) * 100
                annualized_return = round(value, 4) if math.isfinite(value) else None
            except OverflowError:
                pass
        payload = {
            "range": {"preset": preset, "from": start.isoformat(), "to": end.isoformat()},
            "account": account,
            "algorithm": method,
            "metrics": {
                "nlv": round(latest_nlv, 2),
                "nlvChange": round(latest_nlv - first_nlv, 2),
                "startingNlv": round(first_nlv, 2),
                "returnPercent": selected_return,
                "annualizedReturnPercent": annualized_return,
                "realized": round(realized, 2),
                "unrealized": round(unrealized, 2),
                "unrealizedChange": round(unrealized_change, 2),
                "totalPnl": total_pnl,
                "netContributions": round(cash_net_contributions + security_net_transfers, 2),
                "cashNetContributions": round(cash_net_contributions, 2),
                "securityNetTransfers": round(security_net_transfers, 2),
                "income": money["totals"].get("income", 0),
                "fees": round(commissions, 2),
                "financing": round(financing, 2),
                "taxes": money["totals"].get("taxes", 0),
            },
            "returns": returns,
            "benchmarkReturns": benchmark_returns,
            "series": downsample(series),
            "holdingsDate": position_date,
            "holdings": position_rows,
            "closedTrades": closed,
            "closedTradeContracts": closed_contracts,
            "closedPnlDaily": closed_daily,
            "mtm": mtm,
            "pnlComponents": pnl_components,
            "money": money,
        }
        with DASHBOARD_CACHE_LOCK:
            if len(DASHBOARD_CACHE) >= 32:
                DASHBOARD_CACHE.clear()
            DASHBOARD_CACHE[cache_key] = payload
        return payload


def meta_payload() -> dict[str, object]:
    with connect() as conn:
        first, last = database_bounds(conn)
        clause, params = scope_clause("p")
        accounts = [
            row["account_id"]
            for row in conn.execute(
                f"""
                SELECT DISTINCT r.account_id
                FROM records r JOIN reports p ON p.id = r.report_id
                WHERE {clause} AND r.account_id IS NOT NULL
                ORDER BY r.account_id
                """,
                params,
            )
        ]
        row = conn.execute(
            f"SELECT max(fetched_at) fetched_at, count(*) reports, sum(record_count) records FROM reports p WHERE {clause}",
            params,
        ).fetchone()
        return {
            "accounts": accounts,
            "firstDate": first.isoformat(),
            "lastDate": last.isoformat(),
            "lastSync": row["fetched_at"] if row else None,
            "reports": row["reports"] if row else 0,
            "records": row["records"] if row else 0,
            "database": str(Path(os.environ.get("IBKR_ANALYZER_DB", DEFAULT_DB))),
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "PortfolioAnalyze/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self.send_json({"ok": True})
            elif parsed.path == "/api/meta":
                self.send_json(meta_payload())
            elif parsed.path == "/api/dashboard":
                self.send_json(dashboard_payload(parse_qs(parsed.query)))
            else:
                self.send_error(404)
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"error": f"Unexpected API error: {exc}"}, status=500)

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[api] {self.address_string()} {format % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Portfolio Analyze API: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
