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
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

try:
    import requests
    from decouple import AutoConfig, Config, RepositoryEnv
except ImportError as exc:  # pragma: no cover - operator setup failure
    raise SystemExit(
        "Missing sync-agent dependency. Run: "
        "pip install -r requirements-sync-agent.txt"
    ) from exc


LOG = logging.getLogger("timesheet-mirror-sync")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV = AutoConfig(search_path=str(PROJECT_ROOT))
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#@]*$")
DEFAULT_STATE_FILE = PROJECT_ROOT / "scripts" / "timesheet_mirror.state.json"


def configure_env_file(path: str) -> None:
    """Use a dedicated agent env file instead of the backend's main .env."""
    global ENV
    env_path = Path(path).expanduser().resolve()
    if not env_path.is_file():
        raise RuntimeError(f"Sync-agent env file not found: {env_path}")
    ENV = Config(RepositoryEnv(str(env_path)))


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
        # EntryExitType is numeric in Matrix (0=IN, 1=OUT).  Do not use
        # ``value or ''`` here: integer 0 is falsy and was therefore dropped,
        # causing production to receive only OUT punches.
        raw_value = row.get("punch_type")
        raw_type = "" if raw_value is None else str(raw_value).strip().upper()
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


def load_seen_event_ids(path: str | Path) -> set[str]:
    state_path = Path(path)
    if not state_path.is_file():
        return set()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        return {
            str(value).strip()
            for value in payload.get("seen_event_ids", [])
            if str(value).strip()
        }
    except (OSError, ValueError, TypeError) as exc:
        LOG.warning("Ignoring unreadable sync state %s: %s", state_path, exc)
        return set()


def save_seen_event_ids(path: str | Path, events: list[dict]) -> None:
    """Atomically retain IDs in the current rolling SQL window."""
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    event_ids = sorted({str(event.get("source_event_id") or "").strip() for event in events} - {""})
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"seen_event_ids": event_ids, "saved_at": datetime.now().isoformat()}, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, state_path)


def unseen_events(events: list[dict], seen_event_ids: set[str]) -> list[dict]:
    return [
        event for event in events
        if str(event.get("source_event_id") or "").strip() not in seen_event_ids
    ]


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
    totals = {
        "received": 0, "inserted": 0, "updated": 0,
        "unchanged": 0, "skipped": 0, "errors": 0,
    }
    timeout = int(env_first("TIMESHEET_MIRROR_HTTP_TIMEOUT", default="60"))
    with requests.Session() as session:
        # POST retries are safe because source_event_id makes event ingestion
        # idempotent and user-master rows are upserted by employee code.
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry = Retry(
            total=3,
            connect=3,
            read=0,
            status=3,
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
            for key in ("received", "inserted", "updated", "unchanged", "skipped"):
                totals[key] += int(result.get(key) or 0)
            totals["errors"] += len(result.get("errors") or [])
            LOG.info("Sent %s batch %s (%s records)", payload_key, number, len(batch))
    return totals


def check_connections(hours: int) -> None:
    """Verify SQL access, source mapping, production URL and shared key."""
    events = fetch_events(hours, full=False)
    LOG.info("SQL Server check passed; read %s recent event(s)", len(events))

    api_key = env_first("TIMESHEET_MIRROR_API_KEY")
    if not api_key:
        raise RuntimeError("TIMESHEET_MIRROR_API_KEY must be configured")
    url = f"{mirror_base_url()}/mirror/ingest/"
    timeout = int(env_first("TIMESHEET_MIRROR_HTTP_TIMEOUT", default="60"))
    response = requests.post(
        url,
        json={"events": []},
        headers={"X-Timesheet-Mirror-Key": api_key},
        timeout=timeout,
    )
    response.raise_for_status()
    LOG.info("Production mirror authentication passed: %s", url)


def sync_once(
    args: argparse.Namespace,
    *,
    seen_event_ids: set[str] | None = None,
    sync_users: bool = True,
) -> list[dict]:
    if args.users and sync_users:
        users = fetch_users()
        LOG.info("Read %s biometric user-master records", len(users))
        totals = post_batches("mirror/ingest-users", "users", users, args.batch_size, args.dry_run)
        LOG.info("User sync complete: %s", totals)

    events = fetch_events(args.hours, args.full)
    LOG.info("Read %s biometric punch events", len(events))
    pending = unseen_events(events, seen_event_ids) if seen_event_ids is not None else events
    LOG.info("Selected %s new biometric punch event(s)", len(pending))
    if len(pending) > args.max_events_per_run and not args.allow_large_replay:
        raise RuntimeError(
            f"Refusing to send {len(pending)} events in one run; safety limit is "
            f"{args.max_events_per_run}. Prime watch state first or use "
            "--allow-large-replay only during a controlled maintenance window."
        )
    totals = post_batches("mirror/ingest", "events", pending, args.batch_size, args.dry_run)
    LOG.info("Attendance sync complete: %s", totals)
    return events


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=48, help="rolling SQL window (default: 48)")
    parser.add_argument("--full", action="store_true", help="sync all source rows; API remains idempotent")
    parser.add_argument("--users", action="store_true", help="also sync biometric user-master records")
    parser.add_argument("--batch-size", type=int, default=100, help="records per API request (default: 100)")
    parser.add_argument(
        "--max-events-per-run", type=int, default=1000,
        help="safety limit before posting (default: 1000)",
    )
    parser.add_argument(
        "--allow-large-replay", action="store_true",
        help="override the per-run safety limit for controlled maintenance only",
    )
    parser.add_argument("--watch", action="store_true", help="continue syncing at the configured interval")
    parser.add_argument("--interval", type=int, default=300, help="watch interval in seconds (default: 300)")
    parser.add_argument("--dry-run", action="store_true", help="read and validate SQL rows without sending")
    parser.add_argument("--check", action="store_true", help="verify SQL, production endpoint and API key, then exit")
    parser.add_argument("--state-file", help="watch checkpoint file (default: scripts/timesheet_mirror.state.json)")
    parser.add_argument(
        "--prime-state", action="store_true",
        help="record the current rolling window as already sent, then exit without posting",
    )
    parser.add_argument(
        "--env-file",
        help="dedicated agent env file (recommended: scripts/timesheet_mirror.env)",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.env_file:
        configure_env_file(args.env_file)
    args.state_file = args.state_file or env_first(
        "TIMESHEET_MIRROR_STATE_FILE", default=str(DEFAULT_STATE_FILE),
    )
    if any(value <= 0 for value in (
        args.hours, args.batch_size, args.interval, args.max_events_per_run,
    )):
        raise SystemExit(
            "--hours, --batch-size, --interval and --max-events-per-run must be positive"
        )
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.check:
        try:
            check_connections(args.hours)
            return 0
        except Exception:
            LOG.exception("Biometric synchronization preflight failed")
            return 1

    if args.prime_state:
        try:
            events = fetch_events(args.hours, args.full)
            save_seen_event_ids(args.state_file, events)
            LOG.info("Primed sync state with %s event(s): %s", len(events), args.state_file)
            return 0
        except Exception:
            LOG.exception("Biometric synchronization state priming failed")
            return 1

    seen_event_ids = load_seen_event_ids(args.state_file) if args.watch else None
    first_cycle = True
    while True:
        try:
            events = sync_once(
                args,
                seen_event_ids=seen_event_ids,
                sync_users=first_cycle,
            )
            if args.watch and not args.dry_run:
                save_seen_event_ids(args.state_file, events)
                seen_event_ids = {
                    str(event.get("source_event_id") or "").strip()
                    for event in events
                } - {""}
        except Exception:
            LOG.exception("Biometric synchronization failed")
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        first_cycle = False
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
