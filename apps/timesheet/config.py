"""
Time Sheet Analytics — Soft-Coded Configuration
================================================
Every connection parameter, table name, column name, business rule and
report option is defined here and overridable via environment variables.
Add a new field by editing this file only.

ENV VAR PREFIX: TIMESHEET_*

Connection:
    TIMESHEET_HOST              default 192.168.99.52
    TIMESHEET_PORT              default 1433
    TIMESHEET_USER              (required for real data)
    TIMESHEET_PASSWORD          (required for real data) — never in code
    TIMESHEET_DATABASE          (set after running the discovery wizard)
    TIMESHEET_TIMEOUT           default 10 (seconds)
    TIMESHEET_DRIVER            'pymssql' | 'pyodbc'  (auto-detected)

Schema mapping (set via .env after discovery wizard tells you the names):
    TIMESHEET_TABLE             e.g. 'dbo.AttendanceLog' or 'dbo.vw_DailyAttendance'
    TIMESHEET_COL_EMPCODE       default 'EmpCode'
    TIMESHEET_COL_EMAIL         default 'EmpEmail'
    TIMESHEET_COL_NAME          default 'EmpName'
    TIMESHEET_COL_DEPT          default 'Department'
    TIMESHEET_COL_TIME          default 'PunchTime'           (single-event schema)
    TIMESHEET_COL_TYPE          default 'PunchType'           (single-event schema)
    TIMESHEET_IN_VALUE          default 'IN'
    TIMESHEET_OUT_VALUE         default 'OUT'
    TIMESHEET_COL_LOGIN         (optional) for two-column schema, e.g. 'FirstIn'
    TIMESHEET_COL_LOGOUT        (optional) for two-column schema, e.g. 'LastOut'
    TIMESHEET_COL_DATE          (optional) date column for two-column schema

Business rules:
    TIMESHEET_EXPECTED_LOGIN    default 9   (hour-of-day)
    TIMESHEET_EXPECTED_LOGOUT   default 18
    TIMESHEET_LATE_MIN          default 15  (minutes past expected login = "late")
    TIMESHEET_FULL_DAY          default 8.0 (hours that count as a full day)
    TIMESHEET_WORK_DAYS         default 'Mon,Tue,Wed,Thu,Fri'

UI:
    TIMESHEET_POLL_SEC          default 30  (frontend auto-refresh cadence)
"""
import os
from decouple import config

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _env_int(key, default):
    try:
        return int(config(key, default=str(default)))
    except (TypeError, ValueError):
        return int(default)


def _env_float(key, default):
    try:
        return float(config(key, default=str(default)))
    except (TypeError, ValueError):
        return float(default)


def _env_csv(key, default_list):
    raw = config(key, default=','.join(default_list))
    return [p.strip() for p in raw.split(',') if p.strip()]


def _env_first(keys, default=''):
    """Return the first env var in `keys` that has a non-empty value.
    Lets us accept multiple aliases for the same setting without breaking
    older .env files."""
    for k in keys:
        v = config(k, default='')
        if v:
            return v
    return default


def _env_first_int(keys, default):
    raw = _env_first(keys, default=str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _env_first_float(keys, default):
    raw = _env_first(keys, default=str(default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


# ─────────────────────────────────────────────────────────────────────────────
# Public soft-coded config
# ─────────────────────────────────────────────────────────────────────────────
SQLSERVER = {
    'host':     config('TIMESHEET_HOST',     default='192.168.99.52'),
    'port':     _env_int('TIMESHEET_PORT', 1433),
    'user':     config('TIMESHEET_USER',     default=''),
    'password': config('TIMESHEET_PASSWORD', default=''),
    'database': config('TIMESHEET_DATABASE', default=''),
    'timeout':  _env_int('TIMESHEET_TIMEOUT', 10),
    'driver':   config('TIMESHEET_DRIVER',   default='auto'),  # 'pymssql' | 'pyodbc' | 'auto'
}

SCHEMA = {
    'table': _env_first(['TIMESHEET_TABLE'], default=''),
    'columns': {
        'employee_code':  _env_first(['TIMESHEET_COL_EMP_CODE',  'TIMESHEET_COL_EMPCODE'], default='EmpCode'),
        # Empty default = "no email column in this schema" (e.g. Matrix biometric).
        # services.py emits the email field conditionally — same pattern as 'department'.
        'employee_email': _env_first(['TIMESHEET_COL_EMP_EMAIL', 'TIMESHEET_COL_EMAIL'],   default=''),
        'employee_name':  _env_first(['TIMESHEET_COL_EMP_NAME',  'TIMESHEET_COL_NAME'],    default='EmpName'),
        'department':     _env_first(['TIMESHEET_COL_DEPARTMENT', 'TIMESHEET_COL_DEPT'],   default=''),
        # Schema variant A: one row per punch with a type column
        'punch_time':     _env_first(['TIMESHEET_COL_PUNCH_TIME', 'TIMESHEET_COL_TIME'],   default='PunchTime'),
        'punch_type':     _env_first(['TIMESHEET_COL_PUNCH_TYPE', 'TIMESHEET_COL_TYPE'],   default='PunchType'),
        'in_value':       _env_first(['TIMESHEET_COL_IN_VALUE',   'TIMESHEET_IN_VALUE'],   default='IN'),
        'out_value':      _env_first(['TIMESHEET_COL_OUT_VALUE',  'TIMESHEET_OUT_VALUE'],  default='OUT'),
        # Schema variant B: one row per (user, day) with separate in/out columns
        'login_time':     _env_first(['TIMESHEET_COL_LOGIN_TIME',  'TIMESHEET_COL_LOGIN'],  default=''),
        'logout_time':    _env_first(['TIMESHEET_COL_LOGOUT_TIME', 'TIMESHEET_COL_LOGOUT'], default=''),
        'date':           _env_first(['TIMESHEET_COL_DATE'],                                 default=''),
    },
}

RULES = {
    'expected_login_hour':  _env_first_int(['TIMESHEET_EXPECTED_LOGIN_HOUR',  'TIMESHEET_EXPECTED_LOGIN'],  9),
    'expected_logout_hour': _env_first_int(['TIMESHEET_EXPECTED_LOGOUT_HOUR', 'TIMESHEET_EXPECTED_LOGOUT'], 18),
    'late_threshold_min':   _env_first_int(['TIMESHEET_LATE_THRESHOLD_MIN',   'TIMESHEET_LATE_MIN'],        15),
    'full_day_hours':       _env_first_float(['TIMESHEET_FULL_DAY_HOURS',    'TIMESHEET_FULL_DAY'],        8.0),
    'working_days':         _env_csv('TIMESHEET_WORK_DAYS', ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']),
}

UI = {
    'polling_seconds': _env_int('TIMESHEET_POLL_SEC', 30),
}

# Soft-coded user-mapping priority (email primary, employee_id fallback)
USER_MAPPING_PRIORITY = ['email', 'employee_id']


def is_configured() -> bool:
    """Quick check: do we have the bare minimum to attempt a query?"""
    return bool(
        SQLSERVER['host']
        and SQLSERVER['user']
        and SQLSERVER['password']
        and SQLSERVER['database']
        and SCHEMA['table']
    )


def configuration_status() -> dict:
    """Detailed health snapshot for the Setup wizard."""
    return {
        'host':              bool(SQLSERVER['host']),
        'credentials':       bool(SQLSERVER['user'] and SQLSERVER['password']),
        'database_selected': bool(SQLSERVER['database']),
        'table_selected':    bool(SCHEMA['table']),
        'schema_variant':    _detect_schema_variant(),
        'configured':        is_configured(),
        'polling_seconds':   UI['polling_seconds'],
    }


def _detect_schema_variant() -> str:
    cols = SCHEMA['columns']
    if cols['login_time'] and cols['logout_time']:
        return 'two_column'   # one row per user/day with separate in/out
    if cols['punch_time'] and cols['punch_type']:
        return 'event_stream'  # one row per punch with a type
    return 'unknown'


# ─────────────────────────────────────────────────────────────────────────────
# Celery beat schedule — picked up by config/celery.py via merge.
#   TIMESHEET_MONTHLY_REPORT_CRON  e.g. "0 8 1 * *"  (default: 1st of month, 08:00 UTC)
#   TIMESHEET_MONTHLY_REPORT_ENABLED  set 'false' to disable
# ─────────────────────────────────────────────────────────────────────────────
BEAT_SCHEDULE = {}
if config('TIMESHEET_MONTHLY_REPORT_ENABLED', default='true').lower() in ('1', 'true', 'yes', 'on'):
    _cron_raw = config('TIMESHEET_MONTHLY_REPORT_CRON', default='0 8 1 * *')
    try:
        from celery.schedules import crontab
        _parts = _cron_raw.split()
        if len(_parts) == 5:
            _minute, _hour, _dom, _month, _dow = _parts
            BEAT_SCHEDULE['timesheet-monthly-report'] = {
                'task': 'timesheet.send_monthly_report',
                'schedule': crontab(
                    minute=_minute, hour=_hour,
                    day_of_month=_dom, month_of_year=_month, day_of_week=_dow,
                ),
            }
    except Exception:
        # Don't let a malformed cron expression break Django boot.
        pass
