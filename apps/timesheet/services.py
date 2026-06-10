"""
Query + aggregation services for the Time Sheet Analytics feature.

Every SQL identifier is read from apps.timesheet.config (soft-coded), so adding
support for a new timesheet system is a config change, not a code change.

Two supported schema variants (auto-detected from configured columns):

    1. event_stream  — one row per punch: (emp_code, punch_time, punch_type IN/OUT)
    2. two_column    — one row per day:   (emp_code, date, login_time, logout_time)

Public functions return plain dicts ready for JSON serialisation.
"""
from __future__ import annotations

import calendar
import datetime as dt
import logging
from collections import defaultdict
from typing import Iterable, Optional

from django.contrib.auth import get_user_model

from . import config as ts_config
from .sqlserver import connect, rows_to_dicts

logger = logging.getLogger(__name__)
User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# Schema helpers
# ─────────────────────────────────────────────────────────────────────────────
def _table() -> str:
    """Return the bracket-quoted [schema].[table] identifier."""
    raw = (ts_config.SCHEMA['table'] or '').strip()
    if not raw:
        raise RuntimeError('TIMESHEET_TABLE is not configured.')
    if '.' in raw:
        schema, tbl = raw.split('.', 1)
    else:
        schema, tbl = 'dbo', raw
    return f'[{_safe(schema)}].[{_safe(tbl)}]'


def _col(key: str) -> str:
    name = ts_config.SCHEMA['columns'].get(key, '')
    return f'[{_safe(name)}]' if name else ''


def _opt_select(key: str, alias: str, *, prefix: str = '', agg: str | None = None) -> str:
    """Soft-coded SELECT-fragment for an optional column.

    Returns ``'<prefix><col> AS alias,'`` (or ``'<agg>(<prefix><col>) AS alias,'``)
    when the column is configured, else an empty string. Lets any timesheet
    schema work without code changes — drop the env var and the field is gone.
    """
    col = _col(key)
    if not col:
        return ''
    expr = f'{prefix}{col}' if not agg else f'{agg}({prefix}{col})'
    return f'{expr} AS {alias}, '


def _safe(ident: str) -> str:
    return ''.join(c for c in (ident or '') if c.isalnum() or c == '_')


def _variant() -> str:
    return ts_config._detect_schema_variant()


# ─────────────────────────────────────────────────────────────────────────────
# Date helpers
# ─────────────────────────────────────────────────────────────────────────────
def _parse_date(s: Optional[str], default: Optional[dt.date] = None) -> dt.date:
    if not s:
        return default or dt.date.today()
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return default or dt.date.today()


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last_day)


# ─────────────────────────────────────────────────────────────────────────────
# RAD AI user enrichment (email-first, employee_id fallback)
# ─────────────────────────────────────────────────────────────────────────────
def _enrich_with_rad_users(rows: list[dict]) -> list[dict]:
    """Add `radai_user_id`, `radai_email`, `radai_full_name` to each row by
    matching on email (primary) then employee_id (fallback)."""
    if not rows:
        return rows

    emails = {(r.get('email') or '').strip().lower() for r in rows if r.get('email')}
    emp_codes = {str(r.get('employee_code') or '').strip() for r in rows if r.get('employee_code')}

    # Bulk lookup — never N+1
    from apps.rbac.models import UserProfile

    profiles_by_email = {}
    if emails:
        for p in UserProfile.objects.select_related('user').filter(
            user__email__in=list(emails), is_deleted=False
        ):
            profiles_by_email[(p.user.email or '').lower()] = p

    profiles_by_emp_id = {}
    if emp_codes:
        for p in UserProfile.objects.select_related('user').filter(
            employee_id__in=list(emp_codes), is_deleted=False
        ):
            profiles_by_emp_id[str(p.employee_id)] = p

    for r in rows:
        email_key = (r.get('email') or '').strip().lower()
        emp_key = str(r.get('employee_code') or '').strip()
        profile = profiles_by_email.get(email_key) or profiles_by_emp_id.get(emp_key)
        if profile:
            r['radai_user_id'] = str(profile.user.id)
            r['radai_email'] = profile.user.email
            r['radai_full_name'] = f'{profile.user.first_name or ""} {profile.user.last_name or ""}'.strip()
            r['radai_department'] = profile.department or r.get('department') or ''
            r['radai_job_title'] = profile.job_title or ''
            r['matched_by'] = 'email' if email_key and email_key in profiles_by_email else 'employee_id'
        else:
            r['radai_user_id'] = None
            r['radai_email'] = None
            r['radai_full_name'] = None
            r['radai_department'] = r.get('department') or ''
            r['radai_job_title'] = ''
            r['matched_by'] = None
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Live status — who's currently in the office
# ─────────────────────────────────────────────────────────────────────────────
def live_status() -> dict:
    """Return last-known punch per user for today + a roll-up of who's IN/OUT."""
    today = dt.date.today()
    variant = _variant()
    if variant == 'unknown':
        return {'rows': [], 'summary': _empty_summary(), 'variant': variant}

    if variant == 'event_stream':
        sql = (
            f"SELECT a.{_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email', prefix='a.')}"
            f"       a.{_col('employee_name')} AS name, "
            f"       {('a.' + _col('department') + ' AS department,') if _col('department') else ''}"
            f"       a.{_col('punch_time')} AS punch_time, "
            f"       a.{_col('punch_type')} AS punch_type "
            f"FROM {_table()} a "
            f"INNER JOIN ("
            f"    SELECT {_col('employee_code')} AS ec, MAX({_col('punch_time')}) AS mx "
            f"    FROM {_table()} "
            f"    WHERE CAST({_col('punch_time')} AS DATE) = %s "
            f"    GROUP BY {_col('employee_code')}"
            f") last ON a.{_col('employee_code')} = last.ec "
            f"      AND a.{_col('punch_time')} = last.mx "
            f"ORDER BY a.{_col('punch_time')} DESC"
        )
        params = (today,)
    else:  # two_column
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email')}"
            f"       {_col('employee_name')} AS name, "
            f"       {(_col('department') + ' AS department,') if _col('department') else ''}"
            f"       {_col('login_time')} AS login_time, "
            f"       {_col('logout_time')} AS logout_time, "
            f"       {_col('date')} AS work_date "
            f"FROM {_table()} "
            f"WHERE CAST({_col('date')} AS DATE) = %s "
            f"ORDER BY {_col('login_time')} DESC"
        )
        params = (today,)

    with connect() as cur:
        cur.execute(sql, params)
        rows = rows_to_dicts(cur, cur.fetchall())

    rows = _enrich_with_rad_users(rows)

    # Compute IN/OUT/late counters
    in_value = (ts_config.SCHEMA['columns']['in_value'] or 'IN').upper()
    summary = _empty_summary()
    for r in rows:
        if variant == 'event_stream':
            is_in = str(r.get('punch_type', '')).upper() == in_value
        else:
            is_in = bool(r.get('login_time')) and not r.get('logout_time')
        if is_in:
            summary['currently_in'] += 1
        else:
            summary['currently_out'] += 1
        if _is_late(r):
            summary['late_today'] += 1
    summary['total_seen_today'] = len(rows)
    summary['matched_to_radai'] = sum(1 for r in rows if r.get('radai_user_id'))
    return {'rows': rows, 'summary': summary, 'variant': variant, 'as_of': dt.datetime.now().isoformat()}


def _empty_summary() -> dict:
    return {
        'total_seen_today': 0,
        'currently_in': 0,
        'currently_out': 0,
        'late_today': 0,
        'matched_to_radai': 0,
    }


def _is_late(row: dict) -> bool:
    """Soft-coded late-arrival detection."""
    expected_h = ts_config.RULES['expected_login_hour']
    threshold = ts_config.RULES['late_threshold_min']
    first_in = row.get('punch_time') or row.get('login_time')
    if not first_in:
        return False
    if isinstance(first_in, str):
        try:
            first_in = dt.datetime.fromisoformat(first_in)
        except ValueError:
            return False
    expected = first_in.replace(hour=expected_h, minute=0, second=0, microsecond=0)
    return first_in > expected + dt.timedelta(minutes=threshold)


# ─────────────────────────────────────────────────────────────────────────────
# Daily report — per user hours for a single date
# ─────────────────────────────────────────────────────────────────────────────
def daily_report(date: Optional[str] = None) -> dict:
    day = _parse_date(date)
    variant = _variant()
    if variant == 'unknown':
        return {'date': day.isoformat(), 'rows': [], 'variant': variant}

    if variant == 'event_stream':
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email', agg='MAX')}"
            f"       MAX({_col('employee_name')}) AS name, "
            f"       {('MAX(' + _col('department') + ') AS department,') if _col('department') else ''}"
            f"       MIN({_col('punch_time')}) AS first_in, "
            f"       MAX({_col('punch_time')}) AS last_out, "
            f"       COUNT(*) AS punch_count "
            f"FROM {_table()} "
            f"WHERE CAST({_col('punch_time')} AS DATE) = %s "
            f"GROUP BY {_col('employee_code')} "
            f"ORDER BY first_in"
        )
    else:
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email')}"
            f"       {_col('employee_name')} AS name, "
            f"       {(_col('department') + ' AS department,') if _col('department') else ''}"
            f"       {_col('login_time')} AS first_in, "
            f"       {_col('logout_time')} AS last_out "
            f"FROM {_table()} "
            f"WHERE CAST({_col('date')} AS DATE) = %s "
            f"ORDER BY {_col('login_time')}"
        )

    with connect() as cur:
        cur.execute(sql, (day,))
        rows = rows_to_dicts(cur, cur.fetchall())

    for r in rows:
        r['hours_worked'] = _hours_between(r.get('first_in'), r.get('last_out'))
        r['is_late'] = _is_late({'punch_time': r.get('first_in')})
        r['is_full_day'] = (r['hours_worked'] or 0) >= ts_config.RULES['full_day_hours']

    rows = _enrich_with_rad_users(rows)
    return {'date': day.isoformat(), 'rows': rows, 'variant': variant}


# ─────────────────────────────────────────────────────────────────────────────
# Monthly report — per user rollup for a month
# ─────────────────────────────────────────────────────────────────────────────
def monthly_report(year: Optional[int] = None, month: Optional[int] = None) -> dict:
    today = dt.date.today()
    y = int(year or today.year)
    m = int(month or today.month)
    start, end = _month_range(y, m)
    variant = _variant()
    if variant == 'unknown':
        return {'year': y, 'month': m, 'rows': [], 'variant': variant}

    if variant == 'event_stream':
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email', agg='MAX')}"
            f"       MAX({_col('employee_name')}) AS name, "
            f"       {('MAX(' + _col('department') + ') AS department,') if _col('department') else ''}"
            f"       CAST({_col('punch_time')} AS DATE) AS work_date, "
            f"       MIN({_col('punch_time')}) AS first_in, "
            f"       MAX({_col('punch_time')}) AS last_out "
            f"FROM {_table()} "
            f"WHERE {_col('punch_time')} >= %s AND {_col('punch_time')} < DATEADD(DAY, 1, %s) "
            f"GROUP BY {_col('employee_code')}, CAST({_col('punch_time')} AS DATE) "
            f"ORDER BY employee_code, work_date"
        )
    else:
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email')}"
            f"       {_col('employee_name')} AS name, "
            f"       {(_col('department') + ' AS department,') if _col('department') else ''}"
            f"       CAST({_col('date')} AS DATE) AS work_date, "
            f"       {_col('login_time')} AS first_in, "
            f"       {_col('logout_time')} AS last_out "
            f"FROM {_table()} "
            f"WHERE {_col('date')} >= %s AND {_col('date')} <= %s "
            f"ORDER BY {_col('employee_code')}, {_col('date')}"
        )

    with connect() as cur:
        cur.execute(sql, (start, end))
        raw = rows_to_dicts(cur, cur.fetchall())

    # Roll up per employee
    by_emp: dict[str, dict] = {}
    for r in raw:
        key = str(r.get('employee_code') or '')
        slot = by_emp.setdefault(key, {
            'employee_code': key,
            'email': r.get('email'),
            'name': r.get('name'),
            'department': r.get('department'),
            'days_present': 0,
            'full_days': 0,
            'half_days': 0,
            'late_arrivals': 0,
            'total_hours': 0.0,
            'days_detail': [],
        })
        hours = _hours_between(r.get('first_in'), r.get('last_out')) or 0
        slot['days_present'] += 1
        slot['total_hours'] += hours
        if hours >= ts_config.RULES['full_day_hours']:
            slot['full_days'] += 1
        else:
            slot['half_days'] += 1
        if _is_late({'punch_time': r.get('first_in')}):
            slot['late_arrivals'] += 1
        slot['days_detail'].append({
            'date': str(r.get('work_date')),
            'first_in': str(r.get('first_in')) if r.get('first_in') else None,
            'last_out': str(r.get('last_out')) if r.get('last_out') else None,
            'hours': round(hours, 2),
        })

    rows = list(by_emp.values())
    for slot in rows:
        slot['total_hours'] = round(slot['total_hours'], 2)
        slot['avg_hours_per_day'] = (
            round(slot['total_hours'] / slot['days_present'], 2) if slot['days_present'] else 0
        )
    rows = _enrich_with_rad_users(rows)
    rows.sort(key=lambda x: (x.get('radai_full_name') or x.get('name') or ''))
    return {
        'year': y,
        'month': m,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'working_days_in_month': _working_days(start, end),
        'rows': rows,
        'variant': variant,
    }


def _working_days(start: dt.date, end: dt.date) -> int:
    workdays = {d[:3] for d in ts_config.RULES['working_days']}
    name_map = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    count = 0
    cur = start
    while cur <= end:
        if name_map[cur.weekday()] in workdays:
            count += 1
        cur += dt.timedelta(days=1)
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Per-user drill-down
# ─────────────────────────────────────────────────────────────────────────────
def user_history(employee_code: Optional[str] = None,
                 email: Optional[str] = None,
                 from_date: Optional[str] = None,
                 to_date: Optional[str] = None) -> dict:
    today = dt.date.today()
    start = _parse_date(from_date, today - dt.timedelta(days=30))
    end = _parse_date(to_date, today)
    variant = _variant()
    if variant == 'unknown':
        return {'rows': [], 'variant': variant}
    if not (employee_code or email):
        return {'rows': [], 'error': 'employee_code or email required'}

    cols = ts_config.SCHEMA['columns']
    where = []
    params: list = []
    if employee_code:
        where.append(f"{_col('employee_code')} = %s")
        params.append(employee_code)
    if email and cols['employee_email']:
        where.append(f"{_col('employee_email')} = %s")
        params.append(email)
    where_sql = ' AND '.join(where) if where else '1=1'

    if variant == 'event_stream':
        sql = (
            f"SELECT {_col('punch_time')} AS punch_time, "
            f"       {_col('punch_type')} AS punch_type, "
            f"       {_col('employee_code')} AS employee_code "
            f"FROM {_table()} "
            f"WHERE ({where_sql}) "
            f"  AND {_col('punch_time')} >= %s "
            f"  AND {_col('punch_time')} < DATEADD(DAY, 1, %s) "
            f"ORDER BY {_col('punch_time')}"
        )
        params.extend([start, end])
    else:
        sql = (
            f"SELECT {_col('date')} AS work_date, "
            f"       {_col('login_time')} AS first_in, "
            f"       {_col('logout_time')} AS last_out, "
            f"       {_col('employee_code')} AS employee_code "
            f"FROM {_table()} "
            f"WHERE ({where_sql}) "
            f"  AND {_col('date')} >= %s AND {_col('date')} <= %s "
            f"ORDER BY {_col('date')}"
        )
        params.extend([start, end])

    with connect() as cur:
        cur.execute(sql, tuple(params))
        rows = rows_to_dicts(cur, cur.fetchall())

    if variant == 'event_stream':
        # collapse to per-day in/out pairs
        per_day = defaultdict(lambda: {'first_in': None, 'last_out': None, 'punches': 0})
        for r in rows:
            t = r.get('punch_time')
            if isinstance(t, str):
                try:
                    t = dt.datetime.fromisoformat(t)
                except ValueError:
                    continue
            d = t.date().isoformat()
            slot = per_day[d]
            if slot['first_in'] is None or t < slot['first_in']:
                slot['first_in'] = t
            if slot['last_out'] is None or t > slot['last_out']:
                slot['last_out'] = t
            slot['punches'] += 1
        daily_rows = [
            {
                'date': d,
                'first_in': v['first_in'].isoformat() if v['first_in'] else None,
                'last_out': v['last_out'].isoformat() if v['last_out'] else None,
                'hours': _hours_between(v['first_in'], v['last_out']) or 0,
                'punches': v['punches'],
            }
            for d, v in sorted(per_day.items())
        ]
    else:
        daily_rows = [
            {
                'date': str(r.get('work_date')),
                'first_in': str(r.get('first_in')) if r.get('first_in') else None,
                'last_out': str(r.get('last_out')) if r.get('last_out') else None,
                'hours': _hours_between(r.get('first_in'), r.get('last_out')) or 0,
                'punches': None,
            }
            for r in rows
        ]
    return {
        'employee_code': employee_code,
        'email': email,
        'from': start.isoformat(),
        'to': end.isoformat(),
        'rows': daily_rows,
        'variant': variant,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Math helpers
# ─────────────────────────────────────────────────────────────────────────────
def _hours_between(start, end) -> float:
    if not start or not end:
        return 0.0
    if isinstance(start, str):
        try:
            start = dt.datetime.fromisoformat(start)
        except ValueError:
            return 0.0
    if isinstance(end, str):
        try:
            end = dt.datetime.fromisoformat(end)
        except ValueError:
            return 0.0
    delta = end - start
    return max(0.0, round(delta.total_seconds() / 3600, 2))
