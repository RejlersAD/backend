"""
Mirror-mode query services — produce the same JSON shapes as `services.py`
but reading from the Postgres `TimesheetEvent` table (populated by the
office-side sync agent) instead of the on-prem SQL Server.

Used when `TIMESHEET_DATA_SOURCE=mirror` (Railway/production). Same
employee-enrichment helper from `services.py` is reused so the frontend
never sees a different payload shape.
"""
from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from typing import Optional

from django.db.models import Max, Min
from django.utils import timezone

from . import config as ts_config
from .models import TimesheetEvent
from .services import _enrich_with_rad_users, _hours_between, _is_late, _empty_summary, _working_days, _parse_date


_VARIANT = 'mirror'


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last_day)


def _to_naive(t):
    """Strip tz to keep frontend formatting consistent with sqlserver path."""
    if t is None:
        return None
    if hasattr(t, 'tzinfo') and t.tzinfo is not None:
        return t.astimezone(timezone.get_current_timezone()).replace(tzinfo=None)
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Live status — latest punch per user today
# ─────────────────────────────────────────────────────────────────────────────
def live_status() -> dict:
    today = timezone.localdate()
    qs = TimesheetEvent.objects.filter(event_time__date=today)

    # Latest punch per employee_code
    latest_by_emp: dict[str, TimesheetEvent] = {}
    for ev in qs.order_by('employee_code', 'event_time'):
        latest_by_emp[ev.employee_code] = ev  # last write wins → latest

    rows = []
    for ev in latest_by_emp.values():
        rows.append({
            'employee_code': ev.employee_code,
            'email': ev.employee_email or None,
            'name': ev.employee_name,
            'department': ev.department,
            'punch_time': _to_naive(ev.event_time),
            'punch_type': ev.event_type,
        })

    rows = _enrich_with_rad_users(rows)

    summary = _empty_summary()
    for r in rows:
        if str(r.get('punch_type', '')).upper() == TimesheetEvent.EVENT_IN:
            summary['currently_in'] += 1
        else:
            summary['currently_out'] += 1
        if _is_late(r):
            summary['late_today'] += 1
    summary['total_seen_today'] = len(rows)
    summary['matched_to_radai'] = sum(1 for r in rows if r.get('radai_user_id'))

    rows.sort(key=lambda r: r.get('punch_time') or dt.datetime.min, reverse=True)
    return {
        'rows': rows,
        'summary': summary,
        'variant': _VARIANT,
        'as_of': dt.datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Daily report
# ─────────────────────────────────────────────────────────────────────────────
def daily_report(date: Optional[str] = None) -> dict:
    day = _parse_date(date)
    qs = (
        TimesheetEvent.objects
        .filter(event_time__date=day)
        .values('employee_code')
        .annotate(first_in=Min('event_time'), last_out=Max('event_time'))
    )

    # Need names/departments — fetch once per employee
    meta = {
        e['employee_code']: e
        for e in TimesheetEvent.objects
        .filter(event_time__date=day)
        .values('employee_code', 'employee_name', 'employee_email', 'department')
        .distinct()
    }

    rows = []
    for r in qs:
        m = meta.get(r['employee_code'], {})
        first_in = _to_naive(r['first_in'])
        last_out = _to_naive(r['last_out'])
        hours = _hours_between(first_in, last_out) or 0
        rows.append({
            'employee_code': r['employee_code'],
            'email': m.get('employee_email') or None,
            'name': m.get('employee_name', ''),
            'department': m.get('department', ''),
            'first_in': first_in,
            'last_out': last_out,
            'hours_worked': hours,
            'is_late': _is_late({'punch_time': first_in}),
            'is_full_day': hours >= ts_config.RULES['full_day_hours'],
        })

    rows = _enrich_with_rad_users(rows)
    rows.sort(key=lambda r: r.get('first_in') or dt.datetime.min)
    return {'date': day.isoformat(), 'rows': rows, 'variant': _VARIANT}


# ─────────────────────────────────────────────────────────────────────────────
# Monthly report
# ─────────────────────────────────────────────────────────────────────────────
def monthly_report(year: Optional[int] = None, month: Optional[int] = None) -> dict:
    today = dt.date.today()
    y = int(year or today.year)
    m = int(month or today.month)
    start, end = _month_range(y, m)
    end_exclusive = end + dt.timedelta(days=1)

    qs = (
        TimesheetEvent.objects
        .filter(event_time__gte=start, event_time__lt=end_exclusive)
        .values('employee_code', 'event_time__date')
        .annotate(first_in=Min('event_time'), last_out=Max('event_time'))
    )

    meta = {
        e['employee_code']: e
        for e in TimesheetEvent.objects
        .filter(event_time__gte=start, event_time__lt=end_exclusive)
        .values('employee_code', 'employee_name', 'employee_email', 'department')
        .distinct()
    }

    by_emp: dict[str, dict] = {}
    for r in qs:
        key = r['employee_code']
        m_ = meta.get(key, {})
        slot = by_emp.setdefault(key, {
            'employee_code': key,
            'email': m_.get('employee_email') or None,
            'name': m_.get('employee_name', ''),
            'department': m_.get('department', ''),
            'days_present': 0,
            'full_days': 0,
            'half_days': 0,
            'late_arrivals': 0,
            'total_hours': 0.0,
            'days_detail': [],
        })
        first_in = _to_naive(r['first_in'])
        last_out = _to_naive(r['last_out'])
        hours = _hours_between(first_in, last_out) or 0
        slot['days_present'] += 1
        slot['total_hours'] += hours
        if hours >= ts_config.RULES['full_day_hours']:
            slot['full_days'] += 1
        else:
            slot['half_days'] += 1
        if _is_late({'punch_time': first_in}):
            slot['late_arrivals'] += 1
        slot['days_detail'].append({
            'date': str(r['event_time__date']),
            'first_in': str(first_in) if first_in else None,
            'last_out': str(last_out) if last_out else None,
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
        'variant': _VARIANT,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-user drill-down
# ─────────────────────────────────────────────────────────────────────────────
def user_history(employee_code: Optional[str] = None,
                 email: Optional[str] = None,
                 from_date: Optional[str] = None,
                 to_date: Optional[str] = None,
                 include_punches: bool = False) -> dict:
    today = dt.date.today()
    start = _parse_date(from_date, today - dt.timedelta(days=30))
    end = _parse_date(to_date, today)
    end_exclusive = end + dt.timedelta(days=1)

    if not (employee_code or email):
        return {'rows': [], 'error': 'employee_code or email required'}

    qs = TimesheetEvent.objects.filter(event_time__gte=start, event_time__lt=end_exclusive)
    if employee_code:
        qs = qs.filter(employee_code=str(employee_code))
    elif email:
        qs = qs.filter(employee_email__iexact=str(email))

    per_day = defaultdict(lambda: {'first_in': None, 'last_out': None, 'punches': 0})
    raw_punches: list[dict] = []  # optional, per-event detail
    employee_meta = {'employee_code': '', 'employee_name': '', 'employee_email': '', 'department': ''}

    for ev in qs.order_by('event_time'):
        d = ev.event_time.date().isoformat()
        slot = per_day[d]
        ts = _to_naive(ev.event_time)
        if slot['first_in'] is None or ts < slot['first_in']:
            slot['first_in'] = ts
        if slot['last_out'] is None or ts > slot['last_out']:
            slot['last_out'] = ts
        slot['punches'] += 1
        # First seen wins for the employee header metadata
        if not employee_meta['employee_code']:
            employee_meta = {
                'employee_code':  ev.employee_code,
                'employee_name':  ev.employee_name,
                'employee_email': ev.employee_email,
                'department':     ev.department,
            }
        if include_punches:
            raw_punches.append({
                'event_time': ts.isoformat() if ts else None,
                'event_type': ev.event_type,
                'date':       d,
            })

    # Per-day rows (same shape as before)
    rows = []
    for d, slot in sorted(per_day.items()):
        hours = _hours_between(slot['first_in'], slot['last_out']) or 0
        rows.append({
            'date':         d,
            'first_in':     str(slot['first_in']) if slot['first_in'] else None,
            'last_out':     str(slot['last_out']) if slot['last_out'] else None,
            'hours_worked': round(hours, 2),
            'punch_count':  slot['punches'],
        })

    # ── Consolidated summary across the whole range
    full_day_hours = float(ts_config.RULES.get('full_day_hours', 8.0))
    total_hours    = sum(r['hours_worked'] for r in rows)
    total_punches  = sum(r['punch_count'] or 0 for r in rows)
    days_present   = len(rows)
    days_full      = sum(1 for r in rows if (r['hours_worked'] or 0) >= full_day_hours)
    days_partial   = days_present - days_full

    # Average punch-in / punch-out time (HH:MM)
    def _avg_time(values):
        valid = [v for v in values if v]
        if not valid:
            return None
        secs = [v.hour * 3600 + v.minute * 60 + v.second for v in valid]
        avg = sum(secs) // len(secs)
        return f'{avg // 3600:02d}:{(avg % 3600) // 60:02d}'

    first_ins = []
    last_outs = []
    for d, slot in per_day.items():
        if slot['first_in']:
            first_ins.append(slot['first_in'])
        if slot['last_out'] and slot['last_out'] != slot['first_in']:
            last_outs.append(slot['last_out'])

    summary = {
        'total_hours':         round(total_hours, 2),
        'total_punches':       total_punches,
        'days_present':        days_present,
        'days_full':           days_full,
        'days_partial':        days_partial,
        'avg_hours_per_day':   round(total_hours / days_present, 2) if days_present else 0,
        'avg_first_in':        _avg_time(first_ins),
        'avg_last_out':        _avg_time(last_outs),
        'range_days':          (end - start).days + 1,
    }

    # ── Monthly breakdown (one entry per YYYY-MM in range)
    monthly_buckets: dict[str, dict] = defaultdict(lambda: {
        'hours': 0.0, 'days': 0, 'punches': 0,
    })
    for r in rows:
        ym = r['date'][:7]  # 'YYYY-MM'
        b = monthly_buckets[ym]
        b['hours']   += r['hours_worked'] or 0
        b['days']    += 1
        b['punches'] += r['punch_count'] or 0
    monthly_breakdown = [
        {
            'month':        ym,
            'hours':        round(b['hours'], 2),
            'days_present': b['days'],
            'punches':      b['punches'],
            'avg_per_day':  round(b['hours'] / b['days'], 2) if b['days'] else 0,
        }
        for ym, b in sorted(monthly_buckets.items())
    ]

    return {
        'employee':          employee_meta,
        'from':              start.isoformat(),
        'to':                end.isoformat(),
        'rows':              rows,
        'summary':           summary,
        'monthly_breakdown': monthly_breakdown,
        'punches':           raw_punches if include_punches else None,
        'variant':           _VARIANT,
    }
