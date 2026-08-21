#!/usr/bin/env python3
"""Copy Matrix biometric attendance from the office SQL Server to RADAI.

Run this script on a machine that can reach the office SQL Server.  It sends
idempotent batches to the production Time Sheet mirror API, allowing the
production application to obtain biometric data without a route into the LAN.

Examples:
    python scripts/timesheet_mirror_sync.py --hours 48
    python scripts/timesheet_mirror_sync.py --users --hours 48
    python scripts/timesheet_mirror_sync.py --watch --interval 300
    python scripts/timesheet_mirror_sync.py --full
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

try:
    import requests
    from decouple import AutoConfig
except ImportError as exc:  # pragma: no cover - operator setup failure
    raise SystemExit(
        "Missing sync-agent dependency. Run: "
        "pip install -r requirements-sync-agent.txt"
    ) from exc


LOG = logging.getLogger("timesheet-mirror-sync")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV = AutoConfig(search_path=str(PROJECT_ROOT))
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#@]*$")


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = ENV(name, default="")
        if str(value).strip():
            return str(value).strip()
    return default


def quote_identifier(value: str) -> str:
    """Quote a configured SQL Server identifier after strict validation."""
    parts = [part.strip().strip("[]") for part in str(value or "").split(".")]
    if not parts or any(not IDENTIFIER.fullmatch(part) for part in parts):
        raise ValueError(f"Unsafe or invalid SQL identifier: {value!r}")
    return ".".join(f"[{part}]" for part in parts)


def optional_select(column: str, alias: str) -> str:
    if column:
        return f"{quote_identifier(column)} AS {quote_identifier(alias)}"
    return f"CAST(NULL AS NVARCHAR(255)) AS {quote_identifier(alias)}"


def scalar_iso(value: Any) -> str:
    if isinstance(value, datetime):
        # The ingest API applies TIMESHEET_INGEST_TZ_OFFSET to naive values.
        return value.replace(tzinfo=None).isoformat(timespec="seconds")
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).isoformat(timespec="seconds")
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace(" ", "T")


def source_event_id(employee_code: str, event_time: str, event_type: str) -> str:
    raw = f"{employee_code.strip()}|{event_time}|{event_type.upper()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def daily_timestamp(work_date: Any, value: Any) -> str:
    """Return one full timestamp for a two-column daily attendance row."""
    if not value:
        return ""
    if isinstance(value, datetime):
        return scalar_iso(value)
    if isinstance(work_date, datetime):
        work_date = work_date.date()
    if isinstance(work_date, date) and isinstance(value, datetime_time):
        return datetime.combine(work_date, value).isoformat(timespec="seconds")
    date_text = str(work_date or "").strip().split("T")[0].split(" ")[0]
    time_text = str(value).strip()
    return f"{date_text}T{time_text}" if date_text and time_text else ""


def sql_connection():
    try:
        import pymssql
    except ImportError as exc:  # pragma: no cover - operator setup failure
        raise RuntimeError("pymssql is required on the office sync machine") from exc

    host = env_first("TIMESHEET_HOST", default="192.168.99.52")
    user = env_first("TIMESHEET_USER")
    password = env_first("TIMESHEET_PASSWORD")
    database = env_first("TIMESHEET_DATABASE")
    if not all((host, user, password, database)):
        raise RuntimeError(
            "TIMESHEET_HOST, TIMESHEET_USER, TIMESHEET_PASSWORD and "
            "TIMESHEET_DATABASE must be configured"
        )
    return pymssql.connect(
        server=host,
        port=env_first("TIMESHEET_PORT", default="1433"),
        user=user,
        password=password,
        database=database,
        timeout=int(env_first("TIMESHEET_TIMEOUT", default="10")),
        login_timeout=int(env_first("TIMESHEET_TIMEOUT", default="10")),
        as_dict=True,
    )


def attendance_schema() -> dict[str, str]:
    return {
        "table": env_first("TIMESHEET_TABLE"),
        "employee_code": env_first("TIMESHEET_COL_EMP_CODE", "TIMESHEET_COL_EMPCODE", default="EmpCode"),
        "employee_name": env_first("TIMESHEET_COL_EMP_NAME", "TIMESHEET_COL_NAME", default="EmpName"),
        "employee_email": env_first("TIMESHEET_COL_EMP_EMAIL", "TIMESHEET_COL_EMAIL"),
        "department": env_first("TIMESHEET_COL_DEPARTMENT", "TIMESHEET_COL_DEPT"),
        "punch_time": env_first("TIMESHEET_COL_PUNCH_TIME", "TIMESHEET_COL_TIME", default="PunchTime"),
        "punch_type": env_first("TIMESHEET_COL_PUNCH_TYPE", "TIMESHEET_COL_TYPE", default="PunchType"),
        "in_value": env_first("TIMESHEET_COL_IN_VALUE", "TIMESHEET_IN_VALUE", default="IN"),
        "out_value": env_first("TIMESHEET_COL_OUT_VALUE", "TIMESHEET_OUT_VALUE", default="OUT"),
        "login_time": env_first("TIMESHEET_COL_LOGIN_TIME", "TIMESHEET_COL_LOGIN"),
        "logout_time": env_first("TIMESHEET_COL_LOGOUT_TIME", "TIMESHEET_COL_LOGOUT"),
        "work_date": env_first("TIMESHEET_COL_DATE"),
    }


def _query_rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = sql_connection()
    try:
        cursor = conn.cursor(as_dict=True)
        cursor.execute(sql, params)
        return list(cursor.fetchall())
    finally:
        conn.close()


def fetch_events(hours: int, full: bool = False) -> list[dict]:
    cfg = attendance_schema()
    if not cfg["table"]:
        raise RuntimeError("TIMESHEET_TABLE must be configured")
    table = quote_identifier(cfg["table"])
    code = quote_identifier(cfg["employee_code"])

    if cfg["login_time"] and cfg["logout_time"] and cfg["work_date"]:
        work_date = quote_identifier(cfg["work_date"])
        sql = (
            f"SELECT {code} AS employee_code, "
            f"{optional_select(cfg['employee_name'], 'employee_name')}, "
            f"{optional_select(cfg['employee_email'], 'employee_email')}, "
            f"{optional_select(cfg['department'], 'department')}, "
            f"{quote_identifier(cfg['login_time'])} AS login_time, "
            f"{quote_identifier(cfg['logout_time'])} AS logout_time, "
            f"{work_date} AS work_date FROM {table}"
        )
        params: tuple = ()
        if not full:
            sql += f" WHERE {work_date} >= DATEADD(HOUR, %s, GETDATE())"
            params = (-hours,)
        sql += f" ORDER BY {work_date}, {code}"
        rows = _query_rows(sql, params)
        events: list[dict] = []
        for row in rows:
            for field, kind in (("login_time", "IN"), ("logout_time", "OUT")):
                timestamp = daily_timestamp(row.get("work_date"), row.get(field))
                if timestamp:
                    events.append(build_event(row, timestamp, kind))
        return events

    punch_time = quote_identifier(cfg["punch_time"])
    punch_type = quote_identifier(cfg["punch_type"])
    sql = (
        f"SELECT {code} AS employee_code, "
        f"{optional_select(cfg['employee_name'], 'employee_name')}, "
        f"{optional_select(cfg['employee_email'], 'employee_email')}, "
        f"{optional_select(cfg['department'], 'department')}, "
        f"{punch_time} AS punch_time, {punch_type} AS punch_type FROM {table}"
    )
    params = ()
    if not full:
        sql += f" WHERE {punch_time} >= DATEADD(HOUR, %s, GETDATE())"
        params = (-hours,)
    sql += f" ORDER BY {punch_time}, {code}"
    rows = _query_rows(sql, params)
    in_value = cfg["in_value"].strip().upper()
    out_value = cfg["out_value"].strip().upper()
    events = []
    for row in rows:
        raw_type = str(row.get("punch_type") or "").strip().upper()
        event_type = "IN" if raw_type == in_value else "OUT" if raw_type == out_value else ""
        timestamp = scalar_iso(row.get("punch_time"))
        if timestamp and event_type:
            events.append(build_event(row, timestamp, event_type))
    return events


def build_event(row: dict, timestamp: str, event_type: str) -> dict:
    code = str(row.get("employee_code") or "").strip()
    return {
        "source_event_id": source_event_id(code, timestamp, event_type),
        "employee_code": code,
        "employee_name": str(row.get("employee_name") or "").strip(),
        "employee_email": str(row.get("employee_email") or "").strip(),
        "department": str(row.get("department") or "").strip(),
        "event_time": timestamp,
        "event_type": event_type,
    }


def parse_user_columns() -> list[tuple[str, str]]:
    raw = env_first(
        "TIMESHEET_USER_DETAILS_COLUMNS",
        default="Card1,OfficeEmail:office_email,PersEmail:personal_email,FullName:full_name",
    )
    columns = []
    for entry in raw.split(","):
        source, separator, alias = entry.strip().partition(":")
        if source:
            columns.append((source, alias if separator and alias else source.lower()))
    return columns


def fetch_users() -> list[dict]:
    table = quote_identifier(env_first("TIMESHEET_USER_DETAILS_TABLE", default="dbo.Mx_VEW_UserDetails"))
    join_col = env_first("TIMESHEET_USER_DETAILS_JOIN_COL", default="UserID")
    selected = [(join_col, "employee_code"), *parse_user_columns()]
    sql = "SELECT " + ", ".join(
        f"{quote_identifier(source)} AS {quote_identifier(alias)}" for source, alias in selected
    ) + f" FROM {table}"
    rows = []
    for raw_row in _query_rows(sql):
        if not str(raw_row.get("employee_code") or "").strip():
            continue
        row = {key: value for key, value in raw_row.items() if value is not None}
        # The web enrichment layer historically calls this alias
        # matrix_full_name; the ingest contract calls it full_name.
        if row.get("matrix_full_name") and not row.get("full_name"):
            row["full_name"] = row.pop("matrix_full_name")
        rows.append(row)
    return rows


def chunks(items: list[dict], size: int) -> Iterable[list[dict]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def mirror_base_url() -> str:
    configured = env_first("TIMESHEET_MIRROR_URL", "BACKEND_URL")
    if not configured:
        raise RuntimeError("TIMESHEET_MIRROR_URL (or BACKEND_URL) must be configured")
    parsed = urlparse(configured)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError("TIMESHEET_MIRROR_URL must be an absolute HTTP(S) URL")
    marker = "/api/v1/timesheet"
    base = configured.rstrip("/")
    return base if marker in base else base + marker


def post_batches(path: str, payload_key: str, rows: list[dict], batch_size: int, dry_run: bool) -> dict:
    if dry_run:
        LOG.info("Dry run: would send %s %s records", len(rows), payload_key)
        return {"received": len(rows), "inserted": 0, "updated": 0, "skipped": 0}
    api_key = env_first("TIMESHEET_MIRROR_API_KEY")
    if not api_key:
        raise RuntimeError("TIMESHEET_MIRROR_API_KEY must be configured")
    url = f"{mirror_base_url()}/{path.strip('/')}/"
    totals = {"received": 0, "inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    timeout = int(env_first("TIMESHEET_MIRROR_HTTP_TIMEOUT", default="60"))
    with requests.Session() as session:
        # POST retries are safe because source_event_id makes event ingestion
        # idempotent and user-master rows are upserted by employee code.
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({'POST'}),
        )
        session.mount('https://', HTTPAdapter(max_retries=retry))
        session.mount('http://', HTTPAdapter(max_retries=retry))
        session.headers.update({"X-Timesheet-Mirror-Key": api_key})
        for number, batch in enumerate(chunks(rows, batch_size), start=1):
            response = session.post(url, json={payload_key: batch}, timeout=timeout)
            response.raise_for_status()
            result = response.json()
            for key in ("received", "inserted", "updated", "skipped"):
                totals[key] += int(result.get(key) or 0)
            totals["errors"] += len(result.get("errors") or [])
            LOG.info("Sent %s batch %s (%s records)", payload_key, number, len(batch))
    return totals


def sync_once(args: argparse.Namespace) -> None:
    if args.users:
        users = fetch_users()
        LOG.info("Read %s biometric user-master records", len(users))
        totals = post_batches("mirror/ingest-users", "users", users, args.batch_size, args.dry_run)
        LOG.info("User sync complete: %s", totals)

    events = fetch_events(args.hours, args.full)
    LOG.info("Read %s biometric punch events", len(events))
    totals = post_batches("mirror/ingest", "events", events, args.batch_size, args.dry_run)
    LOG.info("Attendance sync complete: %s", totals)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=48, help="rolling SQL window (default: 48)")
    parser.add_argument("--full", action="store_true", help="sync all source rows; API remains idempotent")
    parser.add_argument("--users", action="store_true", help="also sync biometric user-master records")
    parser.add_argument("--batch-size", type=int, default=500, help="records per API request (default: 500)")
    parser.add_argument("--watch", action="store_true", help="continue syncing at the configured interval")
    parser.add_argument("--interval", type=int, default=300, help="watch interval in seconds (default: 300)")
    parser.add_argument("--dry-run", action="store_true", help="read and validate SQL rows without sending")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.hours <= 0 or args.batch_size <= 0 or args.interval <= 0:
        raise SystemExit("--hours, --batch-size and --interval must be positive")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    while True:
        try:
            sync_once(args)
        except Exception:
            LOG.exception("Biometric synchronization failed")
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
