#!/usr/bin/env python3
"""Synchronize and query IBKR Flex Web Service statements."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


APP_NAME = "ibkr-analyzer"
DEFAULT_BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
USER_AGENT = "ibkr-analyzer/1.0"
MAX_RANGE_DAYS = 365
POLL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 600
GET_NETWORK_RETRIES = 5
GET_NETWORK_RETRY_SECONDS = 5
RETRYABLE_GET_CODES = {
    "1001",
    "1003",
    "1004",
    "1005",
    "1006",
    "1007",
    "1008",
    "1009",
    "1018",
    "1019",
    "1021",
}
TOKEN_PACING_LOCK = threading.Lock()
TOKEN_LAST_SEND: dict[str, float] = {}
DATABASE_INIT_LOCK = threading.Lock()
INITIALIZED_DATABASES: set[str] = set()


class AnalyzerError(RuntimeError):
    pass


class FlexServiceError(AnalyzerError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"IBKR Flex error {code}: {message}".strip())


class NetworkError(AnalyzerError):
    pass


class ClosingConnection(sqlite3.Connection):
    """Make connection context managers release Windows file handles."""

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def app_home() -> Path:
    override = os.environ.get("IBKR_ANALYZER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise AnalyzerError("APPDATA is unavailable; set IBKR_ANALYZER_HOME explicitly.")
    return Path(appdata) / APP_NAME


def paths() -> dict[str, Path]:
    home = app_home()
    return {
        "home": home,
        "database": home / "records.sqlite3",
        "raw": home / "raw",
    }


def ensure_dirs() -> None:
    target = paths()
    target["home"].mkdir(parents=True, exist_ok=True)
    target["raw"].mkdir(parents=True, exist_ok=True)


def env_path() -> Path:
    override = os.environ.get("IBKR_ANALYZER_ENV")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().with_name(".env")


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise AnalyzerError(f"Could not read {path}: {exc}") from exc
        for line_number, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                raise AnalyzerError(f"Invalid .env line {line_number}: expected KEY=VALUE.")
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
    for key in ("IBKR_FLEX_TOKEN", "IBKR_QUERY_IDS"):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def load_config() -> dict[str, Any]:
    values = parse_dotenv(env_path())
    token = values.get("IBKR_FLEX_TOKEN", "").strip()
    queries = list(
        dict.fromkeys(item.strip() for item in values.get("IBKR_QUERY_IDS", "").split(",") if item.strip())
    )
    if token and not queries:
        raise AnalyzerError("IBKR_QUERY_IDS is required when IBKR_FLEX_TOKEN is configured.")
    if queries and not token:
        raise AnalyzerError("IBKR_FLEX_TOKEN is required when IBKR_QUERY_IDS is configured.")
    return {"token": token, "queries": queries}


def open_database(read_only: bool = False) -> sqlite3.Connection:
    ensure_dirs()
    database = paths()["database"]
    if read_only:
        if not database.exists():
            raise AnalyzerError("No local database exists. Run sync or import-xml first.")
        conn = sqlite3.connect(
            f"{database.as_uri()}?mode=ro",
            uri=True,
            timeout=30,
            factory=ClosingConnection,
        )
    else:
        conn = sqlite3.connect(database, timeout=30, factory=ClosingConnection)
        database_key = str(database.resolve())
        try:
            with DATABASE_INIT_LOCK:
                if database_key not in INITIALIZED_DATABASES:
                    conn.executescript(
                        """
                        PRAGMA journal_mode=WAL;
                        PRAGMA busy_timeout=30000;
                        CREATE TABLE IF NOT EXISTS reports (
                            id TEXT PRIMARY KEY,
                            query_id TEXT NOT NULL,
                            requested_from TEXT,
                            requested_to TEXT,
                            fetched_at TEXT NOT NULL,
                            sha256 TEXT NOT NULL,
                            raw_path TEXT NOT NULL,
                            root_tag TEXT NOT NULL,
                            record_count INTEGER NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS records (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            report_id TEXT NOT NULL REFERENCES reports(id),
                            query_id TEXT NOT NULL,
                            section TEXT NOT NULL,
                            account_id TEXT,
                            statement_date TEXT,
                            report_date TEXT,
                            trade_date TEXT,
                            event_date TEXT,
                            currency TEXT,
                            symbol TEXT,
                            underlying_symbol TEXT,
                            data_json TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_records_section
                            ON records(section);
                        CREATE INDEX IF NOT EXISTS idx_records_report
                            ON records(report_id);
                        CREATE INDEX IF NOT EXISTS idx_reports_query_dates
                            ON reports(query_id, requested_from, requested_to);
                        """
                    )
                    existing_columns = {
                        row[1] for row in conn.execute("PRAGMA table_info(records)")
                    }
                    dimension_columns = {
                        "statement_date": "TEXT",
                        "report_date": "TEXT",
                        "trade_date": "TEXT",
                        "event_date": "TEXT",
                        "currency": "TEXT",
                        "symbol": "TEXT",
                        "underlying_symbol": "TEXT",
                    }
                    for name, column_type in dimension_columns.items():
                        if name not in existing_columns:
                            conn.execute(
                                f"ALTER TABLE records ADD COLUMN {name} {column_type}"
                            )
                    conn.execute(
                        """
                        UPDATE records
                        SET statement_date = json_extract(data_json, '$._statement_toDate'),
                            report_date = json_extract(data_json, '$.reportDate'),
                            trade_date = json_extract(data_json, '$.tradeDate'),
                            event_date = coalesce(
                                substr(nullif(json_extract(data_json, '$.dateTime'), ''), 1, 8),
                                nullif(json_extract(data_json, '$.date'), ''),
                                nullif(json_extract(data_json, '$.tradeDate'), ''),
                                nullif(json_extract(data_json, '$.reportDate'), ''),
                                nullif(json_extract(data_json, '$._statement_toDate'), '')
                            ),
                            currency = json_extract(data_json, '$.currency'),
                            symbol = json_extract(data_json, '$.symbol'),
                            underlying_symbol = json_extract(data_json, '$.underlyingSymbol')
                        WHERE statement_date IS NULL
                          AND report_date IS NULL
                          AND trade_date IS NULL
                          AND event_date IS NULL
                          AND currency IS NULL
                          AND symbol IS NULL
                          AND underlying_symbol IS NULL
                        """
                    )
                    conn.executescript(
                        """
                        CREATE INDEX IF NOT EXISTS idx_records_query_section_statement
                            ON records(query_id, section, statement_date, account_id);
                        CREATE INDEX IF NOT EXISTS idx_records_query_section_report
                            ON records(query_id, section, report_date, account_id);
                        CREATE INDEX IF NOT EXISTS idx_records_query_section_trade
                            ON records(query_id, section, trade_date, account_id);
                        CREATE INDEX IF NOT EXISTS idx_records_query_section_event
                            ON records(query_id, section, event_date, account_id);
                        CREATE INDEX IF NOT EXISTS idx_records_section_statement_dim
                            ON records(section, statement_date, account_id);
                        CREATE INDEX IF NOT EXISTS idx_records_section_report_dim
                            ON records(section, report_date, account_id);
                        CREATE INDEX IF NOT EXISTS idx_records_section_trade_dim
                            ON records(section, trade_date, account_id);
                        CREATE INDEX IF NOT EXISTS idx_records_section_event_dim
                            ON records(section, event_date, account_id);
                        DROP INDEX IF EXISTS idx_records_section_statement_date;
                        DROP INDEX IF EXISTS idx_records_section_report_date;
                        DROP INDEX IF EXISTS idx_records_section_trade_date;
                        DROP INDEX IF EXISTS idx_records_section_date;
                        DROP INDEX IF EXISTS idx_records_section_datetime_date;
                        """
                    )
                    INITIALIZED_DATABASES.add(database_key)
                else:
                    conn.execute("PRAGMA busy_timeout=30000")
        except Exception:
            conn.close()
            raise
    conn.row_factory = sqlite3.Row
    return conn


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def response_value(root: ET.Element, name: str) -> str:
    for key, value in root.attrib.items():
        if key.lower() == name.lower():
            return value.strip()
    for node in root:
        if local_name(node.tag).lower() == name.lower():
            return (node.text or "").strip()
    return ""


def flatten_statement(xml_bytes: bytes) -> tuple[str, list[tuple[str, str | None, dict[str, Any]]]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise AnalyzerError(f"IBKR returned invalid XML: {exc}") from exc
    root_tag = local_name(root.tag)
    if root_tag == "FlexStatementResponse" and response_value(root, "Status").lower() == "fail":
        message = response_value(root, "ErrorMessage") or "Unknown Flex service error"
        code = response_value(root, "ErrorCode")
        raise FlexServiceError(code, message)

    records: list[tuple[str, str | None, dict[str, Any]]] = []
    statements = [node for node in root.iter() if local_name(node.tag) == "FlexStatement"]
    containers = statements or [root]
    for statement in containers:
        statement_meta = {f"_statement_{key}": value for key, value in statement.attrib.items()}
        for node in statement.iter():
            if node is statement:
                continue
            own_values: dict[str, Any] = dict(node.attrib)
            text = (node.text or "").strip()
            if text:
                own_values["_text"] = text
            if not own_values:
                continue
            values: dict[str, Any] = {**statement_meta, **own_values}
            section = local_name(node.tag)
            account_id = (
                values.get("accountId")
                or values.get("accountID")
                or values.get("acctId")
                or statement.attrib.get("accountId")
            )
            records.append((section, str(account_id) if account_id else None, values))
    return root_tag, records


def import_xml_bytes(
    xml_bytes: bytes,
    query_id: str,
    requested_from: str | None = None,
    requested_to: str | None = None,
) -> dict[str, Any]:
    root_tag, records = flatten_statement(xml_bytes)
    digest = hashlib.sha256(xml_bytes).hexdigest()
    identity = "\0".join(
        [query_id, requested_from or "", requested_to or "", digest]
    ).encode("utf-8")
    report_id = hashlib.sha256(identity).hexdigest()[:20]
    ensure_dirs()
    raw_dir = paths()["raw"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{report_id}.xml"

    with open_database() as conn:
        existing = conn.execute(
            "SELECT id, record_count, sha256, raw_path FROM reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        if not existing:
            existing = conn.execute(
                """
                SELECT id, record_count, sha256, raw_path
                FROM reports
                WHERE query_id = ? AND sha256 = ?
                """,
                (query_id, digest),
            ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE reports
                SET requested_from = ?, requested_to = ?
                WHERE id = ?
                """,
                (requested_from, requested_to, existing["id"]),
            )
            stored_path = Path(existing["raw_path"])
            stored_digest = ""
            if stored_path.is_file():
                stored_digest = hashlib.sha256(stored_path.read_bytes()).hexdigest()
            if stored_digest != existing["sha256"]:
                stored_path.parent.mkdir(parents=True, exist_ok=True)
                stored_path.write_bytes(xml_bytes)
            return {
                "report_id": existing["id"],
                "records": existing["record_count"],
                "duplicate": True,
                "raw_path": str(stored_path),
            }
        raw_path.write_bytes(xml_bytes)
        conn.execute(
            """
            INSERT INTO reports
                (id, query_id, requested_from, requested_to, fetched_at,
                 sha256, raw_path, root_tag, record_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                query_id,
                requested_from,
                requested_to,
                now_iso(),
                digest,
                str(raw_path),
                root_tag,
                len(records),
            ),
        )
        conn.executemany(
            """
            INSERT INTO records
                (report_id, query_id, section, account_id,
                 statement_date, report_date, trade_date, event_date,
                 currency, symbol, underlying_symbol, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    report_id,
                    query_id,
                    section,
                    account_id,
                    values.get("_statement_toDate"),
                    values.get("reportDate"),
                    values.get("tradeDate"),
                    (
                        str(values.get("dateTime") or "")[:8]
                        or values.get("date")
                        or values.get("tradeDate")
                        or values.get("reportDate")
                        or values.get("_statement_toDate")
                    ),
                    values.get("currency"),
                    values.get("symbol"),
                    values.get("underlyingSymbol"),
                    json.dumps(values, ensure_ascii=False, sort_keys=True),
                )
                for section, account_id, values in records
            ],
        )
    return {
        "report_id": report_id,
        "records": len(records),
        "duplicate": False,
        "raw_path": str(raw_path),
    }


def flex_request(endpoint: str, params: dict[str, str], timeout: int = 60) -> bytes:
    base = os.environ.get("IBKR_FLEX_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}/{endpoint}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise NetworkError(f"IBKR Flex HTTP error {exc.code} during {endpoint}.") from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"Could not reach IBKR Flex service during {endpoint}: {exc.reason}") from exc


def parse_flex_response(xml_bytes: bytes, stage: str) -> ET.Element:
    if not xml_bytes.lstrip().startswith(b"<"):
        if stage == "GetStatement":
            raise AnalyzerError(
                "IBKR GetStatement returned a non-XML report. Set the Flex Query Format to XML."
            )
        raise AnalyzerError(f"IBKR {stage} returned a non-XML response.")
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise AnalyzerError(f"IBKR {stage} returned invalid XML: {exc}") from exc


def pace_send_request(token: str) -> None:
    token_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    while True:
        with TOKEN_PACING_LOCK:
            current = time.monotonic()
            wait = 6.1 - (current - TOKEN_LAST_SEND.get(token_key, 0.0))
            if wait <= 0:
                TOKEN_LAST_SEND[token_key] = current
                return
        time.sleep(wait)


def fetch_statement(
    token: str,
    query_id: str,
    from_date: date | None,
    to_date: date | None,
) -> bytes:
    params = {"t": token, "q": query_id, "v": "3"}
    if from_date and to_date:
        params["fd"] = from_date.strftime("%Y%m%d")
        params["td"] = to_date.strftime("%Y%m%d")
    pace_send_request(token)
    response = parse_flex_response(flex_request("SendRequest", params), "SendRequest")
    status = response_value(response, "Status").lower()
    if status != "success":
        code = response_value(response, "ErrorCode")
        message = response_value(response, "ErrorMessage") or "Flex request failed"
        raise FlexServiceError(code, message)
    reference = response_value(response, "ReferenceCode")
    if not reference:
        raise AnalyzerError("IBKR Flex response did not contain a reference code.")

    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    network_failures = 0
    while True:
        try:
            result = flex_request("GetStatement", {"q": reference, "t": token, "v": "3"})
            network_failures = 0
        except NetworkError:
            network_failures += 1
            if network_failures > GET_NETWORK_RETRIES or time.monotonic() >= deadline:
                raise
            print(
                f"GetStatement network retry {network_failures}/{GET_NETWORK_RETRIES}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(GET_NETWORK_RETRY_SECONDS)
            continue
        root = parse_flex_response(result, "GetStatement")
        if local_name(root.tag) != "FlexStatementResponse":
            return result
        code = response_value(root, "ErrorCode")
        if code not in RETRYABLE_GET_CODES:
            message = response_value(root, "ErrorMessage") or "Statement retrieval failed"
            raise FlexServiceError(code, message)
        if time.monotonic() >= deadline:
            raise AnalyzerError("Timed out waiting for IBKR to generate the Flex statement.")
        time.sleep(POLL_SECONDS)


def date_chunks(
    start: date | None,
    end: date | None,
    chunk_days: int = MAX_RANGE_DAYS,
) -> list[tuple[date | None, date | None]]:
    if not 1 <= chunk_days <= MAX_RANGE_DAYS:
        raise AnalyzerError(f"--chunk-days must be between 1 and {MAX_RANGE_DAYS}.")
    if start is None and end is None:
        return [(None, None)]
    if start is None or end is None:
        raise AnalyzerError("--from and --to must be supplied together.")
    if start > end:
        raise AnalyzerError("--from must not be after --to.")
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=chunk_days - 1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def parse_flex_date(value: str) -> date:
    value = value.strip()
    try:
        if len(value) == 8 and value.isdigit():
            return datetime.strptime(value, "%Y%m%d").date()
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AnalyzerError(f"Unsupported Flex statement date: {value}") from exc


def statement_date_range(xml_bytes: bytes) -> tuple[date, date]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise AnalyzerError(f"IBKR returned invalid statement XML: {exc}") from exc
    statements = [node for node in root.iter() if local_name(node.tag) == "FlexStatement"]
    ranges = [
        (parse_flex_date(node.attrib["fromDate"]), parse_flex_date(node.attrib["toDate"]))
        for node in statements
        if node.attrib.get("fromDate") and node.attrib.get("toDate")
    ]
    if not ranges:
        raise AnalyzerError("Flex statement did not include fromDate/toDate metadata.")
    return min(item[0] for item in ranges), max(item[1] for item in ranges)


def filter_statement_range(xml_bytes: bytes, start: date, end: date) -> bytes:
    if start > end:
        raise AnalyzerError("Import --from must not be after --to.")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise AnalyzerError(f"IBKR returned invalid statement XML: {exc}") from exc

    kept = 0
    for parent in root.iter():
        for child in list(parent):
            if local_name(child.tag) != "FlexStatement":
                continue
            child_start = parse_flex_date(child.attrib.get("fromDate", ""))
            child_end = parse_flex_date(child.attrib.get("toDate", ""))
            if child_end < start or child_start > end:
                parent.remove(child)
            else:
                if child_start < start or child_end > end:
                    raise AnalyzerError(
                        "Cannot partially trim a multi-day FlexStatement; export with Breakout by Day."
                    )
                kept += 1
        if local_name(parent.tag) == "FlexStatements":
            parent.attrib["count"] = str(
                sum(1 for child in parent if local_name(child.tag) == "FlexStatement")
            )
    if kept == 0:
        raise AnalyzerError("No FlexStatement records remain in the requested import range.")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def next_business_day(value: date) -> date:
    cursor = value + timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor


def existing_coverage(query_id: str) -> list[tuple[date, date]]:
    if not paths()["database"].exists():
        return []
    with open_database(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT requested_from, requested_to
            FROM reports
            WHERE query_id = ?
              AND requested_from IS NOT NULL
              AND requested_to IS NOT NULL
            """,
            (query_id,),
        )
        return [
            (date.fromisoformat(row["requested_from"]), date.fromisoformat(row["requested_to"]))
            for row in rows
        ]


def coverage_bounds(query_id: str) -> tuple[date, date] | None:
    coverage = existing_coverage(query_id)
    if not coverage:
        return None
    return min(item[0] for item in coverage), max(item[1] for item in coverage)


def uncovered_business_spans(
    query_id: str,
    start: date,
    end: date,
) -> list[tuple[date, date]]:
    coverage = existing_coverage(query_id)
    missing = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and not any(left <= cursor <= right for left, right in coverage):
            missing.append(cursor)
        cursor += timedelta(days=1)
    if not missing:
        return []

    spans = []
    span_start = previous = missing[0]
    for current in missing[1:]:
        if current != next_business_day(previous):
            spans.append((span_start, previous))
            span_start = current
        previous = current
    spans.append((span_start, previous))
    return spans


def smaller_chunks(start: date, end: date) -> list[tuple[date, date]]:
    days = (end - start).days + 1
    if days <= 1:
        return []
    if days > 7:
        chunks = date_chunks(start, end, 7)
    else:
        chunks = [(cursor, cursor) for cursor in (start + timedelta(days=i) for i in range(days))]

    result = []
    for left, right in chunks:
        while left <= right and left.weekday() >= 5:
            left += timedelta(days=1)
        while right >= left and right.weekday() >= 5:
            right -= timedelta(days=1)
        if left <= right and (left, right) != (start, end):
            result.append((left, right))
    return result


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected YYYY-MM-DD.") from exc


def sync_config(
    config: dict[str, Any],
    chunks: list[tuple[date | None, date | None]],
) -> dict[str, Any]:
    token = config["token"]
    reports = []
    skipped = 0
    splits = 0
    requests = 0
    for query_id in config["queries"]:
        queue = [(start, end, 0) for start, end in chunks]
        while queue:
            start, end, split_depth = queue.pop(0)
            if start and end:
                uncovered = uncovered_business_spans(str(query_id), start, end)
                if not uncovered:
                    skipped += 1
                    print(
                        f"[resume] covered {start.isoformat()}..{end.isoformat()}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                if uncovered != [(start, end)]:
                    queue[0:0] = [(left, right, split_depth) for left, right in uncovered]
                    continue

            period = (
                f"{start.isoformat()}..{end.isoformat()}"
                if start and end
                else "saved query period"
            )
            requests += 1
            print(f"[request {requests}] {period}", file=sys.stderr, flush=True)
            try:
                xml_bytes = fetch_statement(token, str(query_id), start, end)
            except FlexServiceError as exc:
                replacement = (
                    smaller_chunks(start, end)
                    if exc.code == "1001" and start and end and split_depth < 2
                    else []
                )
                if not replacement:
                    raise
                splits += 1
                queue[0:0] = [
                    (left, right, split_depth + 1) for left, right in replacement
                ]
                print(
                    f"[split] {period} -> {len(replacement)} smaller ranges",
                    file=sys.stderr,
                    flush=True,
                )
                continue

            stored_from, stored_to = start, end
            if start is None or end is None:
                stored_from, stored_to = statement_date_range(xml_bytes)
                if not uncovered_business_spans(str(query_id), stored_from, stored_to):
                    skipped += 1
                    print(
                        f"[resume] covered {stored_from.isoformat()}..{stored_to.isoformat()}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
            report = {
                "query_id": str(query_id),
                "from": stored_from.isoformat(),
                "to": stored_to.isoformat(),
                **import_xml_bytes(
                    xml_bytes,
                    str(query_id),
                    stored_from.isoformat(),
                    stored_to.isoformat(),
                ),
            }
            reports.append(report)
            print(
                f"[request {requests}] imported {report['records']} records",
                file=sys.stderr,
                flush=True,
            )
    return {
        "ok": True,
        "reports": reports,
        "requests": requests,
        "skipped": skipped,
        "splits": splits,
    }


def emit_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def print_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    if not rows:
        print("No records.")
        return
    columns = columns or list(rows[0].keys())
    widths = {key: len(key) for key in columns}
    rendered = []
    for row in rows:
        item = {}
        for key in columns:
            value = row.get(key, "")
            text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            text = text if len(text) <= 60 else text[:57] + "..."
            item[key] = text
            widths[key] = min(60, max(widths[key], len(text)))
        rendered.append(item)
    print("  ".join(key.ljust(widths[key]) for key in columns))
    print("  ".join("-" * widths[key] for key in columns))
    for row in rendered:
        print("  ".join(row[key].ljust(widths[key]) for key in columns))


def query_scope(args: argparse.Namespace, report_alias: str = "p") -> tuple[list[str], list[Any]]:
    if getattr(args, "all_queries", False):
        return [], []
    configured = load_config()["queries"]
    query_ids = getattr(args, "query_id", None) or configured
    if not query_ids:
        return [], []
    placeholders = ",".join("?" for _ in query_ids)
    clauses = [
        f"{report_alias}.query_id IN ({placeholders})",
        f"""
        (
            {report_alias}.requested_from IS NOT NULL
            OR NOT EXISTS (
                SELECT 1 FROM reports dated
                WHERE dated.query_id = {report_alias}.query_id
                  AND dated.requested_from IS NOT NULL
            )
        )
        """,
    ]
    return clauses, list(query_ids)


def add_query_scope_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--query-id", action="append", help="Query only this Flex Query ID.")
    group.add_argument(
        "--all-queries",
        action="store_true",
        help="Include old Query IDs and undated manual imports.",
    )


def command_sync(args: argparse.Namespace) -> int:
    config = load_config()
    if not config["token"]:
        raise AnalyzerError(f"IBKR_FLEX_TOKEN and IBKR_QUERY_IDS are not configured in {env_path()}.")
    chunks = date_chunks(args.from_date, args.to_date, args.chunk_days)
    result = sync_config(config, chunks)
    emit_json(result)
    return 0


def command_sync_history(args: argparse.Namespace) -> int:
    config = load_config()
    if not config["token"]:
        raise AnalyzerError(f"IBKR_FLEX_TOKEN and IBKR_QUERY_IDS are not configured in {env_path()}.")
    if len(config["queries"]) != 1:
        raise AnalyzerError("sync-history requires exactly one configured Query ID.")
    if args.days < 1:
        raise AnalyzerError("--days must be at least 1.")

    query_id = str(config["queries"][0])
    print("[latest] requesting saved query period", file=sys.stderr, flush=True)
    latest_xml = fetch_statement(config["token"], query_id, None, None)
    _, end = statement_date_range(latest_xml)
    start = end - timedelta(days=args.days - 1)
    print(
        f"[history] {start.isoformat()}..{end.isoformat()}",
        file=sys.stderr,
        flush=True,
    )
    chunks = date_chunks(start, end, args.chunk_days)
    result = sync_config(config, chunks)
    result["from"] = start.isoformat()
    result["to"] = end.isoformat()
    result["days"] = args.days
    emit_json(result)
    return 0


def command_sync_update(args: argparse.Namespace) -> int:
    config = load_config()
    if not config["token"]:
        raise AnalyzerError(f"IBKR_FLEX_TOKEN and IBKR_QUERY_IDS are not configured in {env_path()}.")
    if len(config["queries"]) != 1:
        raise AnalyzerError("sync-update requires exactly one configured Query ID.")

    query_id = str(config["queries"][0])
    print("[latest] requesting saved query period", file=sys.stderr, flush=True)
    latest_xml = fetch_statement(config["token"], query_id, None, None)
    _, end = statement_date_range(latest_xml)
    bounds = coverage_bounds(query_id)
    start = bounds[0] if bounds else end - timedelta(days=364)
    print(
        f"[update] checking {start.isoformat()}..{end.isoformat()}",
        file=sys.stderr,
        flush=True,
    )
    chunks = date_chunks(start, end, args.chunk_days)
    result = sync_config(config, chunks)
    result["from"] = start.isoformat()
    result["to"] = end.isoformat()
    emit_json(result)
    return 0


def command_import(args: argparse.Namespace) -> int:
    source = Path(args.path).expanduser().resolve()
    if not source.is_file():
        raise AnalyzerError(f"XML file not found: {source}")
    xml_bytes = source.read_bytes()
    if (args.from_date is None) != (args.to_date is None):
        raise AnalyzerError("import-xml --from and --to must be supplied together.")
    if args.from_date and args.to_date:
        xml_bytes = filter_statement_range(xml_bytes, args.from_date, args.to_date)
    actual_from, actual_to = statement_date_range(xml_bytes)
    stored_from = args.from_date or actual_from
    stored_to = args.to_date or actual_to
    result = import_xml_bytes(
        xml_bytes,
        args.query_id,
        stored_from.isoformat(),
        stored_to.isoformat(),
    )
    emit_json(result)
    return 0


def command_sections(args: argparse.Namespace) -> int:
    with open_database(read_only=True) as conn:
        where, params = query_scope(args)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT r.section, count(*) AS records,
                       count(DISTINCT r.account_id) AS accounts,
                       count(DISTINCT r.report_id) AS reports
                FROM records r
                JOIN reports p ON p.id = r.report_id
                {clause}
                GROUP BY r.section ORDER BY r.section COLLATE NOCASE
                """,
                params,
            )
        ]
    if args.json:
        emit_json(rows)
    else:
        print_table(rows)
    return 0


def split_predicates(values: list[str] | None, option: str) -> list[tuple[str, str]]:
    result = []
    for value in values or []:
        if "=" not in value:
            raise AnalyzerError(f"{option} expects KEY=VALUE.")
        key, expected = value.split("=", 1)
        result.append((key, expected))
    return result


def collect_records(args: argparse.Namespace, unlimited: bool = False) -> list[dict[str, Any]]:
    with open_database(read_only=True) as conn:
        where, params = query_scope(args)
        if getattr(args, "section", None):
            where.append("r.section = ? COLLATE NOCASE")
            params.append(args.section)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = conn.execute(
            f"""
            SELECT r.id, r.report_id, r.query_id, r.section, r.account_id, r.data_json
            FROM records r
            JOIN reports p ON p.id = r.report_id
            {clause} ORDER BY r.id
            """,
            params,
        )
        exact = split_predicates(getattr(args, "where", None), "--where")
        contains = split_predicates(getattr(args, "contains", None), "--contains")
        output = []
        limit = 0 if unlimited else getattr(args, "limit", 100)
        for row in rows:
            data = json.loads(row["data_json"])
            if any(str(data.get(key, "")) != value for key, value in exact):
                continue
            if any(value.lower() not in str(data.get(key, "")).lower() for key, value in contains):
                continue
            output.append(
                {
                    "_id": row["id"],
                    "_report_id": row["report_id"],
                    "_query_id": row["query_id"],
                    "_section": row["section"],
                    "_account_id": row["account_id"],
                    **data,
                }
            )
            if limit and len(output) >= limit:
                break
        return output


def command_records(args: argparse.Namespace) -> int:
    rows = collect_records(args)
    if args.jsonl:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    elif args.json:
        emit_json(rows)
    else:
        columns = ["_id", "_section", "_account_id"]
        common = ["dateTime", "tradeDate", "symbol", "description", "quantity", "proceeds"]
        columns.extend(key for key in common if any(key in row for row in rows))
        print_table(rows, columns)
    return 0


def command_export(args: argparse.Namespace) -> int:
    rows = collect_records(args, unlimited=True)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "jsonl":
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        fields = sorted({key for row in rows for key in row})
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    emit_json({"output": str(output), "records": len(rows), "format": args.format})
    return 0


def command_reports(args: argparse.Namespace) -> int:
    with open_database(read_only=True) as conn:
        where, params = query_scope(args)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT p.id, p.query_id, p.requested_from, p.requested_to,
                       fetched_at, record_count, raw_path
                FROM reports p
                {clause}
                ORDER BY fetched_at DESC
                """,
                params,
            )
        ]
    if args.json:
        emit_json(rows)
    else:
        print_table(rows, ["id", "query_id", "requested_from", "requested_to", "record_count"])
    return 0


def command_raw(args: argparse.Namespace) -> int:
    with open_database(read_only=True) as conn:
        row = conn.execute("SELECT raw_path FROM reports WHERE id = ?", (args.report_id,)).fetchone()
    if not row:
        raise AnalyzerError(f"Unknown report ID: {args.report_id}")
    raw_path = Path(row["raw_path"])
    if not raw_path.is_file():
        raise AnalyzerError(f"Raw XML file is missing: {raw_path}")
    sys.stdout.buffer.write(raw_path.read_bytes())
    return 0


def command_summary(args: argparse.Namespace) -> int:
    with open_database(read_only=True) as conn:
        where, params = query_scope(args)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT r.section, coalesce(r.account_id, '') AS account_id, count(*) AS records
                FROM records r
                JOIN reports p ON p.id = r.report_id
                {clause}
                GROUP BY r.section, r.account_id
                ORDER BY r.section, r.account_id
                """,
                params,
            )
        ]
    if args.json:
        emit_json(rows)
    else:
        print_table(rows)
    return 0


def command_sql(args: argparse.Namespace) -> int:
    normalized = args.query.lstrip().lower()
    if not normalized.startswith(("select", "with", "explain")):
        raise AnalyzerError("Only read-only SELECT, WITH, or EXPLAIN statements are allowed.")
    with open_database(read_only=True) as conn:
        cursor = conn.execute(args.query)
        rows = [dict(row) for row in cursor]
    if args.json:
        emit_json(rows)
    else:
        print_table(rows)
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    ensure_dirs()
    config = load_config()
    checks = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "env_file": str(env_path()),
        "env_file_exists": env_path().is_file(),
        "home": str(paths()["home"]),
        "configured": bool(config["token"]),
        "queries": len(config["queries"]),
        "database_exists": paths()["database"].exists(),
    }
    emit_json(checks)
    return 0 if checks["configured"] else 2


def command_self_test(args: argparse.Namespace) -> int:
    sample = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="Self Test">
  <FlexStatements count="1">
    <FlexStatement accountId="U123" fromDate="20260101" toDate="20260131">
      <Trades>
        <Trade accountId="U123" symbol="AAPL" tradeDate="20260112" quantity="10" proceeds="-1500"/>
      </Trades>
      <CashTransactions>
        <CashTransaction accountId="U123" type="Dividends" amount="12.34" currency="USD"/>
      </CashTransactions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""
    original_home = os.environ.get("IBKR_ANALYZER_HOME")
    original_env = os.environ.get("IBKR_ANALYZER_ENV")
    with tempfile.TemporaryDirectory(prefix="ibkr-analyzer-") as temp:
        os.environ["IBKR_ANALYZER_HOME"] = temp
        test_env = Path(temp) / ".env"
        os.environ["IBKR_ANALYZER_ENV"] = str(test_env)
        try:
            secret = "test-token-not-real"
            test_env.write_text(
                "IBKR_FLEX_TOKEN=test-token-not-real\n"
                "IBKR_QUERY_IDS=1,2\n",
                encoding="utf-8",
            )
            config = load_config()
            if config["token"] != secret or config["queries"] != ["1", "2"]:
                raise AnalyzerError(".env configuration parsing failed.")
            imported = import_xml_bytes(sample, "1", "2026-01-01", "2026-01-31")
            second = import_xml_bytes(sample, "2", "2026-01-01", "2026-01-31")
            with open_database(read_only=True) as conn:
                count = conn.execute("SELECT count(*) FROM records").fetchone()[0]
            if imported["records"] != 2 or second["report_id"] == imported["report_id"]:
                raise AnalyzerError("XML flattening or report identity failed.")
            if count != imported["records"] + second["records"]:
                raise AnalyzerError("XML import/database round trip failed.")
            raw_path = Path(imported["raw_path"])
            raw_path.unlink()
            duplicate = import_xml_bytes(sample, "1", "2026-01-01", "2026-01-31")
            if not duplicate["duplicate"] or raw_path.read_bytes() != sample:
                raise AnalyzerError("Raw XML repair failed.")
            if len(date_chunks(date(2025, 1, 1), date(2026, 1, 1))) != 2:
                raise AnalyzerError("Date range chunking failed.")
            if len(date_chunks(date(2025, 1, 1), date(2025, 12, 31), 30)) != 13:
                raise AnalyzerError("Custom date chunking failed.")
            if statement_date_range(sample) != (date(2026, 1, 1), date(2026, 1, 31)):
                raise AnalyzerError("Statement date metadata parsing failed.")
            if uncovered_business_spans("1", date(2026, 1, 1), date(2026, 1, 31)):
                raise AnalyzerError("Coverage resume detection failed.")
            split = smaller_chunks(date(2025, 12, 26), date(2026, 1, 24))
            if len(split) != 5 or split[-1] != (date(2026, 1, 23), date(2026, 1, 23)):
                raise AnalyzerError("Adaptive range splitting failed.")

            send_response = b"""<FlexStatementResponse>
                <Status>Success</Status>
                <ReferenceCode>123456</ReferenceCode>
                <Url>unused</Url>
            </FlexStatementResponse>"""
            responses = iter([send_response, NetworkError("temporary"), sample])
            original_request = globals()["flex_request"]
            original_pacing = globals()["pace_send_request"]
            original_retry_seconds = globals()["GET_NETWORK_RETRY_SECONDS"]

            def fake_request(endpoint: str, params: dict[str, str], timeout: int = 60) -> bytes:
                item = next(responses)
                if isinstance(item, Exception):
                    raise item
                return item

            globals()["flex_request"] = fake_request
            globals()["pace_send_request"] = lambda token: None
            globals()["GET_NETWORK_RETRY_SECONDS"] = 0
            try:
                if fetch_statement(secret, "1", None, None) != sample:
                    raise AnalyzerError("Official Flex response parsing failed.")
            finally:
                globals()["flex_request"] = original_request
                globals()["pace_send_request"] = original_pacing
                globals()["GET_NETWORK_RETRY_SECONDS"] = original_retry_seconds
        finally:
            if original_home is None:
                os.environ.pop("IBKR_ANALYZER_HOME", None)
            else:
                os.environ["IBKR_ANALYZER_HOME"] = original_home
            if original_env is None:
                os.environ.pop("IBKR_ANALYZER_ENV", None)
            else:
                os.environ["IBKR_ANALYZER_ENV"] = original_env
    emit_json(
        {
            "ok": True,
            "checks": [
                "dotenv-config",
                "xml",
                "sqlite",
                "raw-repair",
                "date-chunks",
                "resume-coverage",
                "adaptive-split",
                "flex-protocol",
                "get-network-retry",
            ],
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize and query IBKR Flex trading records.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="Download and index Flex statements.")
    sync.add_argument("--from", dest="from_date", type=parse_date)
    sync.add_argument("--to", dest="to_date", type=parse_date)
    sync.add_argument(
        "--chunk-days",
        type=int,
        default=MAX_RANGE_DAYS,
        help=f"Split explicit ranges into chunks of at most this many days (1-{MAX_RANGE_DAYS}).",
    )
    sync.set_defaults(func=command_sync)

    history = sub.add_parser(
        "sync-history",
        help="Sync a trailing history window ending at IBKR's latest available report date.",
    )
    history.add_argument("--days", type=int, default=365)
    history.add_argument("--chunk-days", type=int, default=30)
    history.set_defaults(func=command_sync_history)

    update = sub.add_parser(
        "sync-update",
        help="Fill every missing business day from the database's first date to IBKR's latest date.",
    )
    update.add_argument("--chunk-days", type=int, default=30)
    update.set_defaults(func=command_sync_update)

    importer = sub.add_parser("import-xml", help="Import an existing Flex XML file.")
    importer.add_argument("path")
    importer.add_argument("--query-id", default="manual")
    importer.add_argument("--from", dest="from_date", type=parse_date)
    importer.add_argument("--to", dest="to_date", type=parse_date)
    importer.set_defaults(func=command_import)

    sections = sub.add_parser("sections", help="List indexed Flex record types.")
    add_query_scope_args(sections)
    sections.add_argument("--json", action="store_true")
    sections.set_defaults(func=command_sections)

    records = sub.add_parser("records", help="Query flattened Flex records.")
    add_query_scope_args(records)
    records.add_argument("--section")
    records.add_argument("--where", action="append", help="Exact KEY=VALUE filter.")
    records.add_argument("--contains", action="append", help="Substring KEY=VALUE filter.")
    records.add_argument("--limit", type=int, default=100, help="0 means unlimited.")
    output_mode = records.add_mutually_exclusive_group()
    output_mode.add_argument("--json", action="store_true")
    output_mode.add_argument("--jsonl", action="store_true")
    records.set_defaults(func=command_records)

    exporter = sub.add_parser("export", help="Export filtered records.")
    add_query_scope_args(exporter)
    exporter.add_argument("--section")
    exporter.add_argument("--where", action="append")
    exporter.add_argument("--contains", action="append")
    exporter.add_argument("--format", choices=["csv", "jsonl"], required=True)
    exporter.add_argument("--output", required=True)
    exporter.set_defaults(func=command_export)

    reports = sub.add_parser("reports", help="List downloaded raw statements.")
    add_query_scope_args(reports)
    reports.add_argument("--json", action="store_true")
    reports.set_defaults(func=command_reports)

    raw = sub.add_parser("raw", help="Print one preserved raw Flex XML report.")
    raw.add_argument("report_id")
    raw.set_defaults(func=command_raw)

    summary = sub.add_parser("summary", help="Count records by section and IBKR account.")
    add_query_scope_args(summary)
    summary.add_argument("--json", action="store_true")
    summary.set_defaults(func=command_summary)

    sql = sub.add_parser("sql", help="Run a read-only SQLite query.")
    sql.add_argument("query")
    sql.add_argument("--json", action="store_true")
    sql.set_defaults(func=command_sql)

    doctor = sub.add_parser("doctor", help="Check local readiness without exposing secrets.")
    doctor.set_defaults(func=command_doctor)

    self_test = sub.add_parser("self-test", help="Run isolated local integration checks.")
    self_test.set_defaults(func=command_self_test)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except AnalyzerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
